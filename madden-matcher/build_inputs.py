"""Read merged TeamStore; add partial, role-aware co-occurrence evidence."""
from pathlib import Path
import sys,json,argparse,collections
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'nfl-team-index'))
from pipeline import TeamStore

def build(snapshot,out):
 store=TeamStore(snapshot);dest=Path(out);dest.mkdir(parents=True,exist_ok=True);teams=store.query('SELECT DISTINCT team_abbr,season FROM player_play ORDER BY team_abbr,season');summary=[]
 for team,season in teams.itertuples(index=False,name=None):
  payload=store.mapping_input(team,int(season));r=store.table('receiver_play',team,int(season),columns=['game_id','play_id','lined_up_position','route_ran','offense_formation','receiver_alignment','play_nullified_by_penalty']);r=r[r.route_ran.notna()&r.play_nullified_by_penalty.eq('N')];counts=collections.Counter()
  for _,g in r.groupby(['game_id','play_id'],sort=False):
   if len(g)<2:continue
   slots=tuple(sorted(zip(g.lined_up_position,g.route_ran)));counts[(str(g.offense_formation.iloc[0]),str(g.receiver_alignment.iloc[0]),slots)]+=1
  payload['joint_requirements']=[{'formation':f,'distribution':d,'observed_slots':[{'role':p,'route':rt} for p,rt in slots],'plays':n,'complete_concept_verified':False} for (f,d,slots),n in sorted(counts.items(),key=lambda x:(-x[1],x[0]))]
  payload['joint_evidence_scope']='PARTIAL_SAME_PLAY_ROLE_ROUTE_MULTISET; UNOBSERVED_RECEIVERS_UNKNOWN';payload['observed_joint_play_count']=sum(counts.values());file=dest/f'{team}-{int(season)}-mapping-input.json';file.write_text(json.dumps(payload,allow_nan=False,separators=(',',':')));summary.append({'team':team,'season':int(season),'joint_cells':len(counts),'joint_plays':sum(counts.values())})
 print(json.dumps({'team_seasons':len(summary),'joint_cells':sum(r['joint_cells'] for r in summary)},indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--snapshot',default='nfl-team-index/snapshot-v2');p.add_argument('--out',default='madden-matcher/data/inputs');a=p.parse_args();build(a.snapshot,a.out)
