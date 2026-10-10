"""Team-indexed research snapshot. Standard library + pandas/numpy only."""
from pathlib import Path
import argparse,sqlite3,json,time,hashlib,os
import pandas as pd,numpy as np
KEY=['game_id','play_id','nfl_id']
VERSION='1.1.0'
ALIASES={'OAK':'LV','SD':'LAC','STL':'LA','LAR':'LA'}
def norm(s):return s.replace(ALIASES)
def sqlwrite(c,name,d):d.to_sql(name,c,index=False,if_exists='replace',chunksize=2000)
def signature(p):
 st=p.stat();return dict(path=str(p.resolve()),bytes=st.st_size,mtime_ns=st.st_mtime_ns)
def select_receivers(offense):
 receiver=offense[offense.lined_up_position.isin(['WR','SLOT_WR','TE','RB','FB'])].copy()
 receiver['route_labeled']=receiver.route_ran.notna()
 receiver['individual_alignment']=receiver.lined_up_position.map({'SLOT_WR':'SOURCE_SLOT','WR':'SOURCE_WR','TE':'SOURCE_TE','RB':'SOURCE_BACK','FB':'SOURCE_BACK'})
 return receiver
def build(root,out,tracking_teams=None):
 start=time.perf_counter();out=Path(out);out.mkdir(parents=True,exist_ok=True)
 raw=root/'restored/data/raw';pub=root/'nfl-success-study/outputs'
 sources=[raw/n for n in ['games.csv','player_play.csv','players.csv','combine_results.csv']]+[pub/n for n in ['team_game.csv.gz','player_box_scores.csv.gz','prospect_id_bridge.csv']]
 frames=sorted(raw.glob('game_tracking_*.csv'));sources+=frames
 fingerprint={'version':VERSION,'sources':[signature(p) for p in sources],'tracking_teams':tracking_teams}
 manifest=out/'manifest.json'
 if manifest.exists():
  old=json.loads(manifest.read_text())
  if old['fingerprint']==fingerprint:print('Unchanged snapshot: reused');return
  raise RuntimeError('Source/config changed; build into a new snapshot directory.')
 db=out/'team_index.sqlite';tmp=out/'team_index.building.sqlite';tmp.unlink(missing_ok=True)
 c=sqlite3.connect(tmp);c.execute('PRAGMA journal_mode=OFF');c.execute('PRAGMA synchronous=OFF')
 games=pd.read_csv(raw/'games.csv');all_game_ids=set(games.game_id);games=games[games.season_type.eq('REG')&games.season.between(2023,2025)].copy()
 for col in ['home_team_abbr','visitor_team_abbr']:games[col]=norm(games[col])
 sqlwrite(c,'games',games)
 pp=pd.read_csv(raw/'player_play.csv',low_memory=False);pp['team_abbr']=norm(pp.team_abbr);pp['possession_team']=norm(pp.possession_team)
 pp=pp.merge(games[['game_id','season','week']],on='game_id',validate='many_to_one')
 assert not pp.duplicated(KEY).any()
 sqlwrite(c,'player_play',pp)
 for name,file in [('players','players.csv'),('combine_results','combine_results.csv')]:sqlwrite(c,name,pd.read_csv(raw/file))
 bridge=pd.read_csv(pub/'prospect_id_bridge.csv');sqlwrite(c,'player_identity',bridge)
 tg=pd.read_csv(pub/'team_game.csv.gz');tg=tg[tg.season.between(2023,2025)];sqlwrite(c,'team_game',tg)
 box=pd.read_csv(pub/'player_box_scores.csv.gz',low_memory=False);box=box[box.season.between(2023,2025)];sqlwrite(c,'player_game',box)
 # One row per observed offensive play. Retain all prospect participation separately.
 offense=pp[pp.team_abbr.eq(pp.possession_team)]
 attrs=['game_id','play_id','team_abbr','season','week','quarter','down','yards_to_go','offense_formation','receiver_alignment','team_coverage_type','team_coverage_man_zone','expected_points_added','pass_result','play_nullified_by_penalty']
 plays=offense[attrs].drop_duplicates()
 assert not plays.duplicated(['game_id','play_id']).any(),'Conflicting shared play attributes'
 sqlwrite(c,'plays',plays)
 receiver=select_receivers(offense);sqlwrite(c,'receiver_play',receiver)
 for stmt in ['CREATE UNIQUE INDEX player_play_key ON player_play(game_id,play_id,nfl_id)','CREATE INDEX pp_team ON player_play(team_abbr,season,game_id)','CREATE INDEX receiver_team ON receiver_play(team_abbr,season,game_id,play_id)','CREATE UNIQUE INDEX play_key ON plays(game_id,play_id)','CREATE INDEX play_team ON plays(team_abbr,season)','CREATE INDEX combine_player ON combine_results(nfl_id)','CREATE INDEX box_team ON player_game(team,season,game_id)','CREATE INDEX context_team ON team_game(team,season,old_game_id)']:c.execute(stmt)
 c.execute('CREATE VIEW team_players AS SELECT DISTINCT team_abbr,season,nfl_id FROM player_play')
 c.execute('CREATE VIEW team_combine AS SELECT t.team_abbr,t.season,c.* FROM team_players t JOIN combine_results c USING(nfl_id)')
 # A coverage denominator of known team dropbacks helps expose cohort incompleteness.
 cov=plays.groupby(['team_abbr','season']).size().rename('observed_offensive_plays').reset_index()
 routes=receiver[receiver.route_labeled&receiver.play_nullified_by_penalty.eq('N')].copy()
 rcounts=routes.groupby(['team_abbr','season']).agg(labeled_routes=('play_id','size'),observed_receivers=('nfl_id','nunique')).reset_index()
 cov=cov.merge(rcounts,how='left',on=['team_abbr','season']);cov=cov.merge(tg.groupby(['team','season']).dropbacks.sum().rename('public_team_dropbacks').reset_index().rename(columns={'team':'team_abbr'}),on=['team_abbr','season'],how='left')
 sqlwrite(c,'observation_coverage',cov);cov.to_csv(out/'coverage.csv',index=False)
 # Maintain route-count denominator within each observed context, not all team routes.
 routes['motion']=routes.in_motion_at_ball_snap.fillna(False).astype(str)
 groups=['team_abbr','season','lined_up_position','offense_formation','receiver_alignment','motion','down','route_ran']
 routes['target_flag']=routes.target.fillna(False).astype(int);routes['target_epa']=routes.expected_points_added.where(routes.target_flag.eq(1));routes['positive_target']=routes.expected_points_added.gt(0).astype(float).where(routes.target_flag.eq(1))
 for k in groups:routes[k]=routes[k].fillna('UNKNOWN')
 agg=routes.groupby(groups,dropna=False).agg(routes=('play_id','size'),players=('nfl_id','nunique'),games=('game_id','nunique'),targets=('target_flag','sum'),target_epa=('target_epa','mean'),positive_target_rate=('positive_target','mean')).reset_index()
 denom=agg.groupby(groups[:-1]).routes.transform('sum');agg['observed_route_share']=agg.routes/denom;agg['target_rate']=agg.targets/agg.routes
 agg['sparse']=agg.routes.lt(20)|agg.games.lt(3);agg['individual_alignment']=agg.lined_up_position.map({'SLOT_WR':'SOURCE_SLOT','WR':'SOURCE_WR','TE':'SOURCE_TE','RB':'SOURCE_BACK','FB':'SOURCE_BACK'});agg['coverage_scope']='SUPPLIED_COHORT_LABELED_ROUTES';sqlwrite(c,'route_tendencies',agg)
 # Observed multi-receiver signatures are partial unless independently verified.
 sig=routes.groupby(['team_abbr','season','game_id','play_id']).agg(observed_route_signature=('route_ran',lambda x:'+'.join(sorted(x))),observed_route_count=('nfl_id','size')).reset_index();sig['complete_concept_verified']=False;sqlwrite(c,'observed_signatures',sig)
 shard_rows=[];shard_keys=[];tracking_audit=[];trackdir=out/'tracking';trackdir.mkdir(exist_ok=True)
 lookup=pp[KEY+['team_abbr','season']]
 for f in frames:
  audit=dict(source=f.name,read=0,matched=0,unmatched=0,excluded_game_frames=0,eligible_game_unmatched_frames=0,written=0);part=0
  for chunk in pd.read_csv(f,chunksize=500000):
   audit['read']+=len(chunk);joined=chunk.merge(lookup,on=KEY,how='left',validate='many_to_one');audit['matched']+=int(joined.team_abbr.notna().sum());audit['unmatched']+=int(joined.team_abbr.isna().sum())
   audit['excluded_game_frames']+=int((~chunk.game_id.isin(games.game_id)).sum());audit['eligible_game_unmatched_frames']+=int((joined.team_abbr.isna()&joined.game_id.isin(games.game_id)).sum())
   joined=joined[joined.team_abbr.notna()]
   if tracking_teams is not None:joined=joined[joined.team_abbr.isin(tracking_teams)]
   for (team,season),sub in joined.groupby(['team_abbr','season']):
    relative=f'tracking/{team}/{int(season)}/{f.stem}-{part:05d}.csv.gz';dest=out/relative;dest.parent.mkdir(parents=True,exist_ok=True)
    sub.to_csv(dest,index=False,compression={'method':'gzip','compresslevel':1,'mtime':0})
    shard_id=len(shard_rows);shard_rows.append(dict(shard_id=shard_id,team_abbr=team,season=int(season),path=relative,rows=len(sub),bytes=dest.stat().st_size))
    sk=sub[KEY].drop_duplicates();sk['shard_id']=shard_id;shard_keys.append(sk);audit['written']+=len(sub)
   part+=1
  tracking_audit.append(audit);print(json.dumps(audit),flush=True)
 sqlwrite(c,'tracking_shards',pd.DataFrame(shard_rows,columns=['shard_id','team_abbr','season','path','rows','bytes']))
 sqlwrite(c,'tracking_keys',pd.concat(shard_keys,ignore_index=True) if shard_keys else pd.DataFrame(columns=KEY+['shard_id']))
 c.execute('CREATE INDEX tracking_lookup ON tracking_keys(game_id,play_id,nfl_id)');c.commit();c.close();os.replace(tmp,db)
 result=dict(fingerprint=fingerprint,seconds=time.perf_counter()-start,teams=sorted(pp.team_abbr.unique()),player_play_rows=len(pp),receiver_rows=len(receiver),labeled_routes=len(routes),tendency_cells=len(agg),tracking=tracking_audit,tracking_shards=len(shard_rows),tracking_bytes=sum(x['bytes'] for x in shard_rows))
 manifest.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='fingerprint'},indent=2))
class TeamStore:
 def __init__(self,path):self.path=Path(path);self.db=self.path/'team_index.sqlite'
 def query(self,sql,params=()):
  with sqlite3.connect(self.db) as c:return pd.read_sql_query(sql,c,params=params)
 def table(self,name,team,season,columns=None):
  allowed={'player_play':'team_abbr','receiver_play':'team_abbr','plays':'team_abbr','team_game':'team','player_game':'team','team_combine':'team_abbr','route_tendencies':'team_abbr','observation_coverage':'team_abbr','observed_signatures':'team_abbr','receiver_context':'team_abbr','injury_reports':'team','snap_counts':'team','team_career':'team_abbr','public_play':'posteam','route_geometry':'team_abbr','receiver_defender_context':'team_abbr','snap_alignment_context':'team_abbr'}
  if name not in allowed:raise ValueError('Unknown team table')
  cols=self.query(f'PRAGMA table_info({name})')['name'].tolist()
  if columns and not set(columns)<=set(cols):raise ValueError('Unknown column')
  projection=','.join('"'+x+'"' for x in columns) if columns else '*'
  return self.query(f'SELECT {projection} FROM {name} WHERE {allowed[name]}=? AND season=?',(ALIASES.get(team,team),int(season)))
 def tracking(self,game_id,play_id,nfl_id):
  refs=self.query('SELECT DISTINCT s.path FROM tracking_keys k JOIN tracking_shards s USING(shard_id) WHERE k.game_id=? AND k.play_id=? AND k.nfl_id=?',(int(game_id),int(play_id),int(nfl_id)))
  parts=[]
  for ref in refs.path:
   d=pd.read_csv(self.path/ref);parts.append(d[d.game_id.eq(game_id)&d.play_id.eq(play_id)&d.nfl_id.eq(nfl_id)])
  return pd.concat(parts,ignore_index=True).sort_values('time') if parts else pd.DataFrame()
 def mapping_input(self,team,season):
  return dict(schema_version=VERSION,team=team,season=int(season),source='NFL_OBSERVED_TENDENCIES',alignment_semantics='receiver_alignment is formation distribution; individual role from lined_up_position; left/right not yet derived',coverage=self.table('observation_coverage',team,season).replace({np.nan:None}).to_dict('records'),route_requirements=self.table('route_tendencies',team,season).replace({np.nan:None}).to_dict('records'),concept_policy='Partial observed signatures are not verified full concepts',madden_matches=[],call_policy='Observed conditional route shares are not identified play-call frequencies')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',default='.');p.add_argument('--out',default='nfl-team-index/snapshot-v1');p.add_argument('--tracking-teams',default='CHI',help='comma-separated canonical teams, ALL, or NONE');a=p.parse_args();teams=None if a.tracking_teams=='ALL' else ([] if a.tracking_teams=='NONE' else a.tracking_teams.split(','));build(Path(a.root),a.out,teams)
