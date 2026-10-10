"""Recover source responsibilities; keep center-relative alignment explicitly provisional."""
from pathlib import Path
import argparse,json,sqlite3
import pandas as pd,numpy as np
from pipeline import TeamStore,sqlwrite,KEY

def audit(out,root='.'):
 s=TeamStore(out);r=s.query("SELECT * FROM receiver_play WHERE route_ran IS NOT NULL AND play_nullified_by_penalty='N'")
 defense=s.query("SELECT game_id,play_id,nfl_id,team_abbr,lined_up_position,coverage_assignment,coverage_assignment_at_snap FROM player_play WHERE team_abbr<>possession_team")
 agg=defense.groupby(['game_id','play_id','team_abbr']).agg(observed_defenders=('nfl_id','nunique'),observed_defender_ids=('nfl_id',lambda v:json.dumps(v.tolist())),observed_assignment_categories=('coverage_assignment',lambda v:json.dumps(sorted(v.dropna().unique().tolist()))),observed_snap_assignment_categories=('coverage_assignment_at_snap',lambda v:json.dumps(sorted(v.dropna().unique().tolist())))).reset_index().rename(columns={'team_abbr':'defense_team'})
 d=r[KEY+['team_abbr','season']].merge(agg,on=['game_id','play_id'],how='left',validate='many_to_one');d['actual_primary_defender_identified']=False
 # Snap coordinates from staged Chicago players, including observed centers.
 parts=[]
 for source in sorted((Path(root)/'restored/data/raw').glob('game_tracking_*.csv')):
  for f in pd.read_csv(source,usecols=KEY+['time','x','y','event'],chunksize=500000):parts.append(f[f.event.eq('ball_snap')])
 snaps=pd.concat(parts,ignore_index=True).drop_duplicates(KEY+['time'])
 raw_snap_rows=len(snaps);raw_missing_player_ids=int(snaps.nfl_id.isna().sum());raw_zero_player_ids=int(snaps.nfl_id.eq(0).sum())
 counts=snaps.groupby(KEY).size();valid=counts[counts.eq(1)].reset_index()[KEY];snaps=snaps.merge(valid,on=KEY,validate='many_to_one')
 roles=s.query('SELECT game_id,play_id,nfl_id,team_abbr,season,lined_up_position,play_direction FROM player_play');snaps=snaps.merge(roles,on=KEY,validate='one_to_one')
 centers=snaps[snaps.lined_up_position.eq('C')][['game_id','play_id','team_abbr','y']].rename(columns={'y':'center_y_yards'})
 counts=centers.groupby(['game_id','play_id','team_abbr']).size();unique=counts[counts.eq(1)].reset_index()[['game_id','play_id','team_abbr']];centers=centers.merge(unique,on=['game_id','play_id','team_abbr'],validate='many_to_one')
 aligned=snaps[snaps.lined_up_position.isin(['WR','SLOT_WR','TE','RB','FB'])].merge(centers,on=['game_id','play_id','team_abbr'],how='left',validate='many_to_one')
 aligned['lateral_from_center_yards']=(aligned.y-aligned.center_y_yards)*np.where(aligned.play_direction.eq('left'),-1,1)
 aligned['center_relative_side']=np.select([aligned.center_y_yards.isna(),aligned.lateral_from_center_yards.abs().le(1),aligned.lateral_from_center_yards.gt(1)],['UNKNOWN','NEAR_CENTER','OFFENSE_LEFT'],default='OFFENSE_RIGHT')
 aligned['side_evidence']='OBSERVED_CENTER_POSITION_PROXY_NOT_BALL';aligned['individual_role']=aligned.lined_up_position.map({'SLOT_WR':'SOURCE_SLOT','WR':'SOURCE_WR','TE':'SOURCE_TE','RB':'SOURCE_BACK','FB':'SOURCE_BACK'})
 cols=KEY+['team_abbr','season','individual_role','center_relative_side','lateral_from_center_yards','side_evidence']
 with sqlite3.connect(s.db) as c:sqlwrite(c,'receiver_defender_context',d);sqlwrite(c,'snap_alignment_context',aligned[cols]);c.execute('CREATE UNIQUE INDEX receiver_defender_key ON receiver_defender_context(game_id,play_id,nfl_id)');c.execute('CREATE UNIQUE INDEX snap_alignment_key ON snap_alignment_context(game_id,play_id,nfl_id)')
 report={'source_role_routes':r.lined_up_position.value_counts().to_dict(),'observed_signature_counts':s.query('SELECT observed_route_count,COUNT(*) n FROM observed_signatures GROUP BY observed_route_count').to_dict('records'),'routes_with_any_observed_defender':int(d.observed_defenders.gt(0).sum()),'routes_total':len(r),'routes_with_source_slot_tag':int(r.lined_up_position.eq('SLOT_WR').sum()),'source_snap_rows':raw_snap_rows,'source_snap_missing_player_ids':raw_missing_player_ids,'source_snap_zero_player_ids':raw_zero_player_ids,'center_proxy_alignment_rows':int(aligned.center_y_yards.notna().sum()),'all_team_receiver_snap_rows':len(aligned),'center_proxy_side_counts':aligned.center_relative_side.value_counts().to_dict(),'defender_identity_limit':'Observed defensive prospects are not necessarily the actual coverage defender; assignment fields are categories, not receiver IDs','slot_semantics':'SLOT_WR is a source role; WR is preserved as SOURCE_WR without claiming fully verified wide geometry'}
 (s.path/'context_audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out',default='nfl-team-index/snapshot-v2');p.add_argument('--root',default='.');a=p.parse_args();audit(a.out,a.root)
