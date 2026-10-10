"""Read-only snapshot checks and explicit exports, independent of folder names."""
from pathlib import Path
import argparse,json,time,statistics
import pandas as pd
from pipeline import TeamStore
from research_contracts import DEFAULT_SNAPSHOT,DEFAULT_EXPORTS,atomic_json,file_hash


def require(condition,message):
    if not condition:raise ValueError(message)


def validate_export(out=DEFAULT_SNAPSHOT,exports=DEFAULT_EXPORTS,root='.',benchmark=False):
    store=TeamStore(out);state=store.identity();checks={'source_snapshot_id':state['snapshot_id']}
    tables=set(store.query("SELECT name FROM sqlite_master WHERE type IN ('table','view')").name)
    checks['canonical_teams']=store.query('SELECT DISTINCT team_abbr FROM player_play ORDER BY team_abbr').team_abbr.tolist()
    require(bool(checks['canonical_teams']),'No team metadata')
    checks['integrity']=store.query('PRAGMA integrity_check').iloc[0,0];require(checks['integrity']=='ok','SQLite integrity failed')
    if 'receiver_context' in tables:
        checks['receiver_context_rows']=int(store.query('SELECT COUNT(*) n FROM receiver_context').n.iloc[0])
        base=int(store.query('SELECT COUNT(*) n FROM receiver_play').n.iloc[0]);require(checks['receiver_context_rows']==base,'Receiver context fan-out')
        checks['receiver_public_join_rate']=float(store.query('SELECT AVG(actual_passer_gsis_id IS NOT NULL) rate FROM receiver_context WHERE target=1').rate.iloc[0] or 0)
    checks['route_counts_reconcile']=int(store.query('SELECT COALESCE(SUM(routes),0) n FROM route_tendencies').n.iloc[0])
    observed=int(store.query("SELECT COUNT(*) n FROM receiver_play WHERE route_ran IS NOT NULL AND play_nullified_by_penalty='N'").n.iloc[0])
    require(checks['route_counts_reconcile']==observed,'Route counts do not reconcile')
    shares=store.query('SELECT SUM(observed_route_share) s FROM route_tendencies GROUP BY team_abbr,season,lined_up_position,offense_formation,receiver_alignment,motion,down')
    require(shares.empty or shares.s.sub(1).abs().max()<1e-9,'Within-context shares invalid')
    shard_manifest=store.query('SELECT * FROM tracking_shards')
    for shard in shard_manifest.to_dict('records'):
        path=store.path/shard['path']
        require(path.is_file() and path.stat().st_size==shard['bytes'],'Promised tracking shard missing or wrong size')
        if shard.get('sha256'):require(file_hash(path)==shard['sha256'],'Tracking shard hash mismatch')
    checks['tracking_shards_verified']=len(shard_manifest)
    tracking=store.query('SELECT game_id,play_id,nfl_id,COUNT(*) n FROM tracking_keys GROUP BY game_id,play_id,nfl_id ORDER BY n DESC LIMIT 1')
    if tracking.empty:
        materialized='tracking_materialized' in state['capabilities']
        require(not materialized,'Promised materialized tracking keys missing')
        checks['tracking_status']='NOT_MATERIALIZED'
    else:
        key=tracking.iloc[0];trajectory=store.tracking(int(key.game_id),int(key.play_id),int(key.nfl_id))
        require(len(trajectory)>0 and trajectory.time.is_monotonic_increasing,'Trajectory reassembly failed')
        checks.update(tracking_status='VALIDATED',trajectory_shards_reassembled=int(key.n),trajectory_frames=len(trajectory))
    if benchmark:
        raw_times=[];index_times=[];game_ids=set(store.query('SELECT game_id FROM games WHERE season=2023').game_id)
        for _ in range(3):
            start=time.perf_counter();d=pd.read_csv(Path(root)/'restored/data/raw/player_play.csv',low_memory=False)
            selected=d[d.team_abbr.eq('CHI')&d.game_id.isin(game_ids)];raw_times.append(time.perf_counter()-start)
            start=time.perf_counter();indexed=store.table('player_play','CHI',2023);index_times.append(time.perf_counter()-start)
        require(set(map(tuple,selected[['game_id','play_id','nfl_id']].to_numpy()))==set(map(tuple,indexed[['game_id','play_id','nfl_id']].to_numpy())),'Benchmark scopes differ')
        checks['benchmark']={'raw_seconds':statistics.median(raw_times),'indexed_seconds':statistics.median(index_times),'scope':'same CHI 2023 keys; warm filesystem'}
    dest=Path(exports);dest.mkdir(parents=True,exist_ok=True);files=[]
    teams=store.query('SELECT DISTINCT team_abbr,season FROM player_play ORDER BY team_abbr,season')
    for team,year in teams.itertuples(index=False,name=None):
        name=f'{team}-{int(year)}-mapping-input.json';atomic_json(dest/name,store.mapping_input(team,int(year)))
        files.append({'path':name,'team':team,'season':int(year),'sha256':file_hash(dest/name)})
    atomic_json(dest/'input-manifest.json',{'schema_version':'mapping-input-batch-1.0','source_snapshot_id':state['snapshot_id'],'files':files})
    checks['team_seasons']=len(files);atomic_json(dest/'validation.json',checks);print(json.dumps(checks,indent=2));return checks

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default=DEFAULT_SNAPSHOT);p.add_argument('--exports',default=DEFAULT_EXPORTS)
    p.add_argument('--root',default='.');p.add_argument('--benchmark',action='store_true');a=p.parse_args()
    validate_export(a.out,a.exports,a.root,a.benchmark)
