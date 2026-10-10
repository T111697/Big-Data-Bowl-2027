"""Bounded game batches; every shard decompressed once per team-season stage."""
from pathlib import Path
import argparse,sqlite3,tempfile,json,time
import numpy as np,pandas as pd
from pipeline import TeamStore,sqlwrite,KEY,VERSION,deduplicate_frames
from research_contracts import DEFAULT_SNAPSHOT,staged_database,atomic_json,file_hash

GEOMETRY_COLUMNS=KEY+['team_abbr','season','seconds_to_throw','snap_field_x_yards','snap_field_y_yards',
 'forward_at_throw_m','lateral_at_throw_m','max_forward_m','path_distance_m','mean_path_speed_mps','individual_alignment']+[
 f'{axis}_{fraction}_m' for fraction in [25,50,75,100] for axis in ['x','y']]


def summarize_trajectory(key,g,audit):
    try:g=deduplicate_frames(g)
    except ValueError:audit['conflicting_duplicate_times']+=1;return None
    g=g.sort_values('time');sn=g[g.event.eq('ball_snap')];throw=g[g.event.eq('pass_forward')]
    if len(sn)!=1 or len(throw)!=1:audit['missing_snap_or_throw']+=1;return None
    snap=sn.iloc[0];times=pd.to_datetime(g.time,format='ISO8601')
    t=(times-pd.to_datetime(snap.time)).dt.total_seconds().to_numpy()
    release=(pd.to_datetime(throw.iloc[0].time)-pd.to_datetime(snap.time)).total_seconds()
    mask=(t>=0)&(t<=release);h=g[mask];tt=t[mask]
    if len(h)<3 or release<=0:audit['insufficient_trajectory']+=1;return None
    if snap.play_direction not in ['left','right']:audit['unknown_play_direction']+=1;return None
    sign=-1 if snap.play_direction=='left' else 1
    x=(h.x.to_numpy()-snap.x)*sign*.9144;y=(h.y.to_numpy()-snap.y)*sign*.9144
    if not np.isfinite(x).all() or not np.isfinite(y).all():audit['nonfinite_coordinates']+=1;return None
    distance=np.hypot(np.diff(x),np.diff(y)).sum();row=dict(zip(KEY,map(int,key)))
    row.update(team_abbr=snap.team_abbr,season=int(snap.season),seconds_to_throw=release,
               snap_field_x_yards=float(snap.x),snap_field_y_yards=float(snap.y),forward_at_throw_m=float(x[-1]),
               lateral_at_throw_m=float(y[-1]),max_forward_m=float(x.max()),path_distance_m=float(distance),
               mean_path_speed_mps=float(distance/release),individual_alignment='UNRESOLVED_WITHOUT_BALL_AND_ALL_ELIGIBLES')
    for fraction in [.25,.5,.75,1.]:
        row[f'x_{int(fraction*100)}_m']=float(np.interp(release*fraction,tt,x))
        row[f'y_{int(fraction*100)}_m']=float(np.interp(release*fraction,tt,y))
    return row


def derive(out,batch_games=1):
    if type(batch_games) is not int or batch_games<1:raise ValueError('batch_games must be positive')
    store=TeamStore(out);state=store.identity();refs=store.query('SELECT path,team_abbr,season FROM tracking_shards ORDER BY team_abbr,season,path')
    fingerprint={'version':VERSION,'core':state.get('stages',{}).get('core',state['snapshot_id']),
                 'shards':[{'path':p,'sha256':file_hash(store.path/p)} for p in refs.path],'batch_games':batch_games}
    marker=store.path/'geometry_manifest.json'
    if marker.exists() and json.loads(marker.read_text())['fingerprint']==fingerprint:
        return json.loads((store.path/'geometry_audit.json').read_text())
    start=time.perf_counter();rows=[]
    audit={'eligible_frames':0,'conflicting_duplicate_times':0,'missing_snap_or_throw':0,'insufficient_trajectory':0,
           'unknown_play_direction':0,'nonfinite_coordinates':0,'shards_read':0,'peak_batch_frames':0,
           'batch_games':batch_games,'status':'NOT_MATERIALIZED' if refs.empty else 'DERIVED'}
    frame_columns=KEY+['time','x','y','event']
    for (team,season),team_refs in refs.groupby(['team_abbr','season'],sort=True):
        receivers=store.table('receiver_play',team,int(season),columns=KEY+['team_abbr','season','play_direction','route_ran','play_nullified_by_penalty'])
        receivers=receivers[receivers.route_ran.notna()&receivers.play_nullified_by_penalty.eq('N')]
        if receivers.empty:continue
        with tempfile.TemporaryDirectory(dir=store.path,prefix='geometry-spool-') as temp:
            with sqlite3.connect(Path(temp)/'frames.sqlite') as spool:
                written=False
                for path in team_refs.path:
                    audit['shards_read']+=1
                    for chunk in pd.read_csv(store.path/path,usecols=frame_columns,chunksize=100000):
                        matched=chunk.merge(receivers.drop(columns=['route_ran','play_nullified_by_penalty']),on=KEY,validate='many_to_one')
                        if matched.empty:continue
                        matched.to_sql('frames',spool,index=False,if_exists='append' if written else 'replace');written=True
                        audit['eligible_frames']+=len(matched)
                if not written:continue
                spool.execute('CREATE INDEX frame_game ON frames(game_id)')
                games=pd.read_sql_query('SELECT DISTINCT game_id FROM frames ORDER BY game_id',spool).game_id.tolist()
                for offset in range(0,len(games),batch_games):
                    subset=games[offset:offset+batch_games];q='SELECT * FROM frames WHERE game_id IN ('+','.join('?' for _ in subset)+')'
                    frames=pd.read_sql_query(q,spool,params=subset);audit['peak_batch_frames']=max(audit['peak_batch_frames'],len(frames))
                    for key,g in frames.groupby(KEY,sort=True):
                        row=summarize_trajectory(key,g,audit)
                        if row is not None:rows.append(row)
    result=pd.DataFrame(rows,columns=GEOMETRY_COLUMNS)
    with staged_database(store.path,'geometry',fingerprint,['geometry' if len(result) else 'geometry_empty']) as c:
        sqlwrite(c,'route_geometry',result)
        c.execute('CREATE UNIQUE INDEX route_geometry_key ON route_geometry(game_id,play_id,nfl_id)')
        c.execute('CREATE INDEX route_geometry_team ON route_geometry(team_abbr,season)')
    audit.update(geometry_rows=len(result),seconds=time.perf_counter()-start)
    atomic_json(store.path/'geometry_audit.json',audit);print(json.dumps(audit,indent=2));return audit

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',default=DEFAULT_SNAPSHOT);p.add_argument('--batch-games',type=int,default=1)
    a=p.parse_args();derive(a.out,a.batch_games)
