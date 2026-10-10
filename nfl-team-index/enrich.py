"""Independent enrichment stage, reusable without rescanning NFL tracking."""
from pathlib import Path
import sqlite3,json,time
import pandas as pd
from pipeline import sqlwrite,signature,norm,VERSION
from research_contracts import DEFAULT_SNAPSHOT,staged_database,read_snapshot_state,atomic_json

def enrich(root,out):
 root=Path(root);out=Path(out);raw=root/'restored/data/raw';pub=root/'nfl-success-study/outputs';publicraw=root/'nfl-success-study/raw'
 files=[raw/'combine_tracking.csv',raw/'player_career_successes.csv',pub/'injury_reports.csv.gz']+[publicraw/f'{kind}_{year}.{ext}' for year in [2023,2024,2025] for kind,ext in [('pbp','csv.gz'),('snap_counts','csv')]]
 finger={'version':VERSION,'core':read_snapshot_state(out).get('stages',{}).get('core'),'sources':[signature(p) for p in files]};marker=out/'enrichment_manifest.json'
 if marker.exists():
  if json.loads(marker.read_text())['fingerprint']==finger:
   read_snapshot_state(out);print('Enrichment reused');return
  raise RuntimeError('Enrichment inputs changed; create a new snapshot')
 start=time.perf_counter();counts={}
 with staged_database(out,'enrichment',finger,['public_context','combine_tracking','injury_context']) as c:
  for name,file in [('combine_tracking',raw/'combine_tracking.csv'),('career_summary',raw/'player_career_successes.csv'),('injury_reports',pub/'injury_reports.csv.gz')]:
   d=pd.read_csv(file,low_memory=False)
   if name=='injury_reports':d=d[d.season.between(2023,2025)].copy();d['team']=norm(d.team)
   sqlwrite(c,name,d);counts[name]=len(d)
  c.execute('CREATE INDEX IF NOT EXISTS combine_tracking_player ON combine_tracking(nfl_id,draft_year,event_id)');c.execute('CREATE INDEX IF NOT EXISTS injury_team ON injury_reports(team,season,week)')
  c.execute('CREATE VIEW IF NOT EXISTS team_career AS SELECT t.team_abbr,t.season,c.* FROM team_players t JOIN career_summary c USING(nfl_id)')
  games=pd.read_sql_query('SELECT DISTINCT game_id,old_game_id FROM team_game',c)
  snap=[];pbp=[]
  columns=['game_id','play_id','posteam','defteam','passer_player_id','receiver_player_id','rusher_player_id','qtr','down','ydstogo','yardline_100','score_differential','qb_dropback','pass_attempt','rush_attempt','sack','interception','fumble_lost','epa','success','yards_gained','air_yards','cp','cpoe','complete_pass','shotgun','no_huddle','drive']
  for year in [2023,2024,2025]:
   s=pd.read_csv(publicraw/f'snap_counts_{year}.csv');s=s[s.game_type.eq('REG')].copy();s['team']=norm(s.team);snap.append(s)
   b=pd.read_csv(publicraw/f'pbp_{year}.csv.gz',usecols=columns,low_memory=False);b=b.merge(games,on='game_id',validate='many_to_one');b['season']=year;b['posteam']=norm(b.posteam);b['defteam']=norm(b.defteam);pbp.append(b)
  for name,d in [('snap_counts',pd.concat(snap,ignore_index=True)),('public_play',pd.concat(pbp,ignore_index=True))]:sqlwrite(c,name,d);counts[name]=len(d)
  c.execute('CREATE INDEX IF NOT EXISTS snap_team ON snap_counts(team,season,game_id)');c.execute('CREATE UNIQUE INDEX IF NOT EXISTS public_play_key ON public_play(old_game_id,play_id)');c.execute('CREATE INDEX IF NOT EXISTS pbp_offense ON public_play(posteam,season)');c.execute('CREATE INDEX IF NOT EXISTS pbp_defense ON public_play(defteam,season)')
  # Individual game passer and broad game context. No postgame aggregates used as pregame predictions.
  c.execute('DROP VIEW IF EXISTS receiver_context')
  c.execute('''CREATE VIEW receiver_context AS SELECT r.*,b.passer_player_id AS actual_passer_gsis_id,b.cp AS public_completion_probability,b.air_yards AS public_air_yards,t.opponent,t.opponent_elo,t.coach,t.roof,t.temp,t.wind,t.rest FROM receiver_play r LEFT JOIN public_play b ON r.game_id=b.old_game_id AND r.play_id=b.play_id LEFT JOIN team_game t ON r.game_id=t.old_game_id AND r.team_abbr=t.team''')
 atomic_json(marker,{'fingerprint':finger,'rows':counts,'seconds':time.perf_counter()-start});print(json.dumps(counts))
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--root',default='.');p.add_argument('--out',default=DEFAULT_SNAPSHOT);a=p.parse_args();enrich(a.root,a.out)
