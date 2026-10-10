"""Validate exported constraints and benchmark indexed vs dense compatibility.

No film labels exist here: this checks software and reconstruction, not accuracy.
"""
import argparse,json,time,collections
from pathlib import Path
import numpy as np
from matcher import CatalogIndex,Config,requirements,compatibility
from cli import execute


def validate(catalog_path,inputs,out,report):
 index=CatalogIndex(json.loads(Path(catalog_path).read_text()));records=[]
 for file in sorted(Path(out).glob('*-custom-playbook.json')):
  r=json.loads(file.read_text());b=r['playbook'];ps=b['plays'];cfg=r.get('config',{})
  if b['status']=='CURATED':
   weights=np.array([p['proposed_weight'] for p in ps]);assert np.isfinite(weights).all()
   assert abs(weights.sum()-1)<1e-7 and weights.min()>=-1e-9 and weights.max()<=cfg['max_play_weight']+1e-7
   assert len(ps)<=cfg['max_plays'] and b['set_count']<=cfg['max_sets']
   assert len({p['play_id'] for p in ps})==len(ps)
   assert all(p['read_progression'] is None and p['protection_rules'] is None for p in ps)
  for req in r['requirements']:
   if req['kind']=='PARTIAL_JOINT':assert not req['complete_concept_verified']
   for candidate in req['ranked_candidates']:
    if candidate.get('slot_assignments'):
     slots=[s['catalog_slot_index'] for s in candidate['slot_assignments']];assert len(slots)==len(set(slots))
  records.append({'team':r['team'],'season':r['season'],'status':b['status'],'route_coverage_mass':b.get('route_coverage_mass',0.),'weighted_feature_coverage':b.get('coverage_mass',0.),'overall_input_coverage_mass':b.get('overall_input_coverage_mass',0.),'total_variation':b.get('projected_total_variation'),'plays':len(ps)})
 # Compare the accepted candidates, not rejected low-scoring rows omitted by indexing.
 payload=json.loads((Path(inputs)/'CHI-2025-mapping-input.json').read_text());cfg=Config();rows,_=requirements(payload,cfg)
 def evaluate(indexed):
  values=[];n=0;start=time.perf_counter()
  for req in rows:
   ps=index.candidates(req,cfg) if indexed else [p for p in index.plays.values() if p['pure_pass']]
   candidates=[]
   for p in ps:
    n+=1;d=compatibility(req,p)
    if d and d['score']>=cfg.minimum_score:candidates.append((p['id'],round(d['score'],12)))
   values.append(sorted(candidates))
  return values,n,time.perf_counter()-start
 a,n,seconds=evaluate(True);b,dense,dseconds=evaluate(False);assert a==b
 cold=json.loads((Path(out)/'batch-summary.json').read_text())
 warm=execute(catalog_path,inputs,out)
 assert all(r['cached'] for r in warm['results'])
 result={'validation_scope':'Structural constraints, deterministic candidate parity, cache reuse; no ground truth NFL concept accuracy','catalog_plays':len(index.plays),'annotation_plays':sum(bool(p['routes']) for p in index.plays.values()),'team_seasons':len(records),'teams':len({r['team'] for r in records}),'statuses':dict(collections.Counter(r['status'] for r in records)),'cold_batch_seconds':cold['seconds'],'warm_batch_seconds':warm['seconds'],'all_cache_hits':True,'benchmark':{'team':'CHI','season':2025,'requirements':len(rows),'indexed_evaluations':n,'dense_passing_evaluations':dense,'indexed_seconds':seconds,'dense_seconds':dseconds,'evaluation_reduction':1-n/dense,'speedup':dseconds/seconds,'accepted_candidate_parity':True},'records':records}
 Path(report).write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='records'},indent=2));return result

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--catalog',default='madden-matcher/data/catalog.json');p.add_argument('--inputs',default='madden-matcher/data/inputs');p.add_argument('--outputs',default='madden-matcher/outputs/final');p.add_argument('--report',default='madden-matcher/outputs/validation.json');a=p.parse_args();validate(a.catalog,a.inputs,a.outputs,a.report)
