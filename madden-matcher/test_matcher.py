"""Synthetic software tests; these do not establish NFL concept accuracy."""
import unittest,json,copy,tempfile
from pathlib import Path
from matcher import CatalogIndex,Config,match,compatibility,requirements,route
from cli import execute


def play(pid,code='Go',set_id='s1',form='Shotgun',shape_x=(-10,10),tags=('PASS_PLAY',)):
 return {'id':pid,'name':pid,'formation':form,'set_id':set_id,'set_name':set_id,'personnel':'11','tags':list(tags),'source_url':'https://example.test/'+pid,'route_evidence':'SYNTHETIC','routes':[{'routeName':code,'position':'WR','depthChartSlot':f'WR{i+1}','slot':i,'x':x,'y':-1,'confidence':'synthetic'} for i,x in enumerate(shape_x)]}
def catalog(plays):return {'schema_version':'madden-catalog-1.0','game':'Madden NFL 27','plays':plays}
def payload(routes=('GO',),joint=None):
 cells=[{'route_ran':r,'lined_up_position':'WR','offense_formation':'SHOTGUN','receiver_alignment':'1x1','routes':30,'games':3,'motion':False,'down':1} for r in routes]
 return {'schema_version':'1.1.0','team':'TEST','season':2025,'route_requirements':cells,'joint_requirements':joint or []}
def index():return CatalogIndex(catalog([play(str(i),set_id='s'+str(i%2)) for i in range(8)]))

class MatchingTests(unittest.TestCase):
 def test_unknown_routes_are_not_invented(self):
  for r in ['Option Route','Post Corner','Pass Block','Clown','Block and Release']:self.assertEqual(route(r),(None,0.))
  self.assertEqual(route('Deep Cross')[0],'CROSS')
 def test_formation_and_shape_conflicts(self):
  req=requirements(payload(),Config())[0][0];p=index().plays['0']
  self.assertIsNotNone(compatibility(req,p))
  self.assertIsNone(compatibility({**req,'formation':'PISTOL'},p))
  self.assertIsNone(compatibility({**req,'distribution':'3x1'},p))
 def test_tag_only_abstains_at_default_threshold(self):
  p=play('tag');p['routes']=[];p['tags'].append('GO')
  result=match(payload(),CatalogIndex(catalog([p])))
  self.assertEqual(result['requirements'][0]['status'],'ABSTAIN')
 def test_role_mismatch(self):
  req=requirements(payload(),Config())[0][0];p=index().plays['0']
  self.assertIsNone(compatibility({**req,'role':'TE'},p))
 def test_slot_proxy_uses_coordinates(self):
  p=play('slot',shape_x=(-10,-4,10));idx=CatalogIndex(catalog([p]));rs=idx.plays['slot']['canonical_routes']
  self.assertEqual(rs[0]['role_proxy'],'WR');self.assertEqual(rs[1]['role_proxy'],'SLOT_WR');self.assertEqual(rs[2]['role_proxy'],'WR')
 def test_insufficient_candidates_and_personnel(self):
  r=match(payload(),CatalogIndex(catalog([play('one')])));self.assertEqual(r['playbook']['status'],'WEIGHT_CAP_INFEASIBLE_WITH_SUPPORTED_CANDIDATES')
  with self.assertRaises(ValueError):match(payload(),index(),Config(locked_play_ids=('0',),allowed_personnel=('12',)))
  r=match(payload(),index(),Config(allowed_personnel=('12',)));self.assertEqual(r['playbook']['status'],'NO_SUPPORTED_CANDIDATES')
 def test_joint_cannot_reuse_one_slot(self):
  p=play('one','Go');p['routes'][1]['routeName']='Flat'
  idx=CatalogIndex(catalog([p]));req={'kind':'PARTIAL_JOINT','formation':'SHOTGUN','distribution':'1x1','observed_slots':[{'role':'WR','route':'GO'}]*2}
  self.assertIsNone(compatibility(req,idx.plays['one']))
  good=compatibility(req,index().plays['0'])
  self.assertEqual(len({s['catalog_slot_index'] for s in good['slot_assignments']}),2)
  self.assertFalse(good['complete_concept_verified'])
 def test_bounds_budgets_and_determinism(self):
  a=match(payload(),index(),Config(max_plays=4,max_sets=2));b=match(payload(),CatalogIndex(catalog(list(reversed(list(index().plays.values()))))),Config(max_plays=4,max_sets=2))
  self.assertEqual([p['play_id'] for p in a['playbook']['plays']],[p['play_id'] for p in b['playbook']['plays']])
  self.assertLessEqual(len(a['playbook']['plays']),4);self.assertLessEqual(a['playbook']['set_count'],2)
  ws=[p['proposed_weight'] for p in a['playbook']['plays']];self.assertAlmostEqual(sum(ws),1,places=7);self.assertTrue(all(0<=w<=.350001 for w in ws))
 def test_outcomes_do_not_change_matching(self):
  p=payload();q=copy.deepcopy(p)
  for r in q['route_requirements']:r.update(target_epa=1000,positive_target_rate=1,targets=9999)
  a=match(p,index());b=match(q,index());a.pop('runtime');b.pop('runtime');self.assertEqual(a,b)
 def test_top_k_does_not_limit_curation_pool(self):
  r=match(payload(),index(),Config(top_k=1));self.assertGreater(len(r['playbook']['plays']),1);self.assertEqual(len(r['requirements'][0]['ranked_candidates']),1)
 def test_invalid_constraints(self):
  for c in [Config(max_plays=2),Config(joint_weight=2),Config(locked_play_ids=('0',),excluded_play_ids=('0',))]:
   with self.assertRaises(ValueError):match(payload(),index(),c)
  with self.assertRaises(ValueError):match(payload(),index(),Config(locked_play_ids=('absent',)))
  r=match(payload(),index(),Config(excluded_play_ids=('0',),max_sets=1))
  self.assertNotIn('0',[p['play_id'] for p in r['playbook']['plays']]);self.assertLessEqual(r['playbook']['set_count'],1)
 def test_empty_unsupported_and_schema(self):
  self.assertEqual(match(payload([]),index())['status'],'NO_ELIGIBLE_OBSERVATIONS')
  self.assertEqual(match(payload(['UNKNOWN']),index())['status'],'NO_ELIGIBLE_OBSERVATIONS')
  p=payload();p['schema_version']='1.0.0'
  with self.assertRaises(ValueError):match(p,index())
  for n in [-1,float('nan')]:
   p=payload();p['route_requirements'][0]['routes']=n
   with self.assertRaises(ValueError):match(p,index())
 def test_joint_mass_and_scope(self):
  joint=[{'formation':'SHOTGUN','distribution':'1x1','observed_slots':[{'role':'WR','route':'GO'}]*2,'plays':20}]
  r=match(payload(joint=joint),index());self.assertAlmostEqual(sum(x['mass'] for x in r['requirements']),1)
  self.assertAlmostEqual(sum(x['mass'] for x in r['requirements'] if x['kind']=='PARTIAL_JOINT'),.2)
 def test_semantic_catalog_fingerprint(self):
  c=catalog([play('0')]);a=CatalogIndex(c);c['asof']='tomorrow';c['acquisition_seconds']=99
  self.assertEqual(a.fingerprint,CatalogIndex(c).fingerprint)
  c['plays'][0]['routes'][0]['routeName']='Flat';self.assertNotEqual(a.fingerprint,CatalogIndex(c).fingerprint)
 def test_cache_reuse_and_invalidation(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);inputs=root/'inputs';inputs.mkdir();cat=root/'catalog.json';cat.write_text(json.dumps(catalog(list(index().plays.values()))));src=inputs/'TEST-2025-mapping-input.json';src.write_text(json.dumps(payload()));out=root/'out'
   a=execute(cat,inputs,out);b=execute(cat,inputs,out);self.assertFalse(a['results'][0]['cached']);self.assertTrue(b['results'][0]['cached'])
   p=payload();p['route_requirements'][0]['routes']=31;src.write_text(json.dumps(p));self.assertFalse(execute(cat,inputs,out)['results'][0]['cached'])
 def test_index_matches_dense_accepted_candidates(self):
  idx=index();req=requirements(payload(),Config())[0][0]
  indexed={p['id'] for p in idx.candidates(req,Config()) if (d:=compatibility(req,p)) and d['score']>=.65}
  dense={p['id'] for p in idx.plays.values() if (d:=compatibility(req,p)) and d['score']>=.65};self.assertEqual(indexed,dense)
 def test_pruning_bounds_at_custom_thresholds(self):
  ps=[play('exact'),play('family','Post'),play('wrong-role')];ps[2]['routes'][0]['position']='TE';ps[2]['routes'][1]['position']='TE'
  tagged=play('tag');tagged['routes']=[];tagged['tags'].append('GO');ps.append(tagged);idx=CatalogIndex(catalog(ps));req=requirements(payload(),Config())[0][0]
  for threshold in [.2,.45,.55,.61625,.65,.85]:
   cfg=Config(minimum_score=threshold)
   a={p['id'] for p in idx.candidates(req,cfg) if (d:=compatibility(req,p)) and d['score']>=threshold}
   b={p['id'] for p in idx.plays.values() if (d:=compatibility(req,p)) and d['score']>=threshold};self.assertEqual(a,b)

if __name__=='__main__':unittest.main()
