"""Observed pre-throw geometry, not inferred slot/outside assignments."""
from pathlib import Path
import argparse,sqlite3,json
import numpy as np,pandas as pd
from pipeline import TeamStore,sqlwrite,KEY

def derive(out):
 store=TeamStore(out);refs=store.query('SELECT path FROM tracking_shards');parts=[pd.read_csv(store.path/p) for p in refs.path]
 frames=pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()
 receivers=store.query("SELECT game_id,play_id,nfl_id,team_abbr,season,play_direction FROM receiver_play WHERE route_ran IS NOT NULL AND play_nullified_by_penalty='N'")
 frames=frames.drop(columns=['team_abbr','season']).merge(receivers,on=KEY,validate='many_to_one');rows=[];audit={'eligible_frames':len(frames),'conflicting_duplicate_times':0,'missing_snap_or_throw':0}
 for key,g in frames.groupby(KEY,sort=False):
  duplicate=g.duplicated(KEY+['time'],keep=False)
  if duplicate.any():
   if g[duplicate].drop_duplicates().duplicated(KEY+['time']).any():audit['conflicting_duplicate_times']+=1;continue
   g=g.drop_duplicates()
  g=g.sort_values('time');sn=g[g.event.eq('ball_snap')];throw=g[g.event.eq('pass_forward')]
  if len(sn)!=1 or len(throw)!=1:audit['missing_snap_or_throw']+=1;continue
  snap=sn.iloc[0];times=pd.to_datetime(g.time,format='ISO8601');t=(times-pd.to_datetime(snap.time)).dt.total_seconds().to_numpy();release=(pd.to_datetime(throw.iloc[0].time)-pd.to_datetime(snap.time)).total_seconds()
  mask=(t>=0)&(t<=release);h=g[mask];tt=t[mask]
  if len(h)<3 or release<=0:continue
  sign=-1 if snap.play_direction=='left' else 1
  x=(h.x.to_numpy()-snap.x)*sign*.9144;y=(h.y.to_numpy()-snap.y)*sign*.9144;distance=np.hypot(np.diff(x),np.diff(y)).sum()
  row=dict(zip(KEY,[int(k) for k in key]));row.update(team_abbr=snap.team_abbr,season=int(snap.season),seconds_to_throw=release,snap_field_x_yards=snap.x,snap_field_y_yards=snap.y,forward_at_throw_m=x[-1],lateral_at_throw_m=y[-1],max_forward_m=x.max(),path_distance_m=distance,mean_path_speed_mps=distance/release,individual_alignment='UNRESOLVED_WITHOUT_BALL_AND_ALL_ELIGIBLES')
  for fraction in [.25,.5,.75,1.]:
   row[f'x_{int(fraction*100)}_m']=float(np.interp(release*fraction,tt,x));row[f'y_{int(fraction*100)}_m']=float(np.interp(release*fraction,tt,y))
  rows.append(row)
 result=pd.DataFrame(rows)
 with sqlite3.connect(store.db) as c:sqlwrite(c,'route_geometry',result);c.execute('CREATE UNIQUE INDEX IF NOT EXISTS route_geometry_key ON route_geometry(game_id,play_id,nfl_id)');c.execute('CREATE INDEX IF NOT EXISTS route_geometry_team ON route_geometry(team_abbr,season)')
 audit['geometry_rows']=len(result);(store.path/'geometry_audit.json').write_text(json.dumps(audit,indent=2));print(audit)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out',default='nfl-team-index/snapshot-v1');a=p.parse_args();derive(a.out)
