from pathlib import Path
import sqlite3,time,json,statistics
import pandas as pd
from pipeline import TeamStore
import argparse
parser=argparse.ArgumentParser();parser.add_argument('--out',default=str(Path(__file__).parent/'snapshot-v1'));parser.add_argument('--root',default='.');args=parser.parse_args()
P=Path(__file__).parent;S=Path(args.out);store=TeamStore(S)
checks={}
checks['canonical_teams']=store.query('SELECT DISTINCT team_abbr FROM player_play ORDER BY team_abbr').team_abbr.tolist();assert len(checks['canonical_teams'])==32
checks['integrity']=store.query('PRAGMA integrity_check').iloc[0,0];assert checks['integrity']=='ok'
checks['receiver_context_rows']=int(store.query('SELECT COUNT(*) n FROM receiver_context').n.iloc[0]);base=int(store.query('SELECT COUNT(*) n FROM receiver_play').n.iloc[0]);assert checks['receiver_context_rows']==base
checks['route_counts_reconcile']=int(store.query('SELECT SUM(routes) n FROM route_tendencies').n.iloc[0]);assert checks['route_counts_reconcile']==json.loads((S/'manifest.json').read_text())['labeled_routes']
shares=store.query('SELECT SUM(observed_route_share) s FROM route_tendencies GROUP BY team_abbr,season,lined_up_position,offense_formation,receiver_alignment,motion,down');assert shares.s.sub(1).abs().max()<1e-9
# A trajectory may cross a source chunk boundary: retrieve every indexed shard.
key=store.query('SELECT game_id,play_id,nfl_id,COUNT(*) n FROM tracking_keys GROUP BY game_id,play_id,nfl_id ORDER BY n DESC LIMIT 1').iloc[0];trajectory=store.tracking(int(key.game_id),int(key.play_id),int(key.nfl_id));assert len(trajectory)>0;assert trajectory.time.is_monotonic_increasing
checks['trajectory_shards_reassembled']=int(key.n);checks['trajectory_frames']=len(trajectory)
raw_times=[];index_times=[]
game_ids=set(store.query('SELECT game_id FROM games WHERE season=2023').game_id)
for _ in range(3):
 start=time.perf_counter();d=pd.read_csv(Path(args.root)/'restored/data/raw/player_play.csv',low_memory=False);selected=d[d.team_abbr.eq('CHI')&d.game_id.isin(game_ids)];raw_times.append(time.perf_counter()-start)
 start=time.perf_counter();indexed=store.table('player_play','CHI',2023);index_times.append(time.perf_counter()-start)
assert set(map(tuple,selected[['game_id','play_id','nfl_id']].to_numpy()))==set(map(tuple,indexed[['game_id','play_id','nfl_id']].to_numpy()))
checks['benchmark']={'raw_csv_read_filter_median_seconds':statistics.median(raw_times),'indexed_team_season_median_seconds':statistics.median(index_times),'indexed_rows':len(indexed),'raw_filter_scope':'same CHI regular-season 2023 keys, full-column reads','cache_conditions':'same process, three runs, warm filesystem; not universal speed guarantee'}
checks['benchmark']['ratio']=checks['benchmark']['raw_csv_read_filter_median_seconds']/checks['benchmark']['indexed_team_season_median_seconds']
checks['receiver_public_join_rate']=float(store.query('SELECT AVG(actual_passer_gsis_id IS NOT NULL) rate FROM receiver_context WHERE target=1').rate.iloc[0])
exports=P/('exports-v2' if S.name.endswith('v2') else 'exports');exports.mkdir(exist_ok=True)
for team in checks['canonical_teams']:
 for year in [2023,2024,2025]:
  obj=store.mapping_input(team,year);(exports/f'{team}-{year}-mapping-input.json').write_text(json.dumps(obj,allow_nan=False,separators=(',',':')))
(P/'validation.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks,indent=2))
