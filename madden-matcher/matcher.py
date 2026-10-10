"""Evidence-aware Madden concept matching and bounded marginal reconstruction.

Scores measure compatibility, not calibrated probabilities or predicted football value.
"""
from dataclasses import dataclass,asdict
from collections import defaultdict
import json,hashlib,math,time
import numpy as np
from scipy.optimize import minimize,linear_sum_assignment
VERSION='madden-matcher-1.0'
ROUTES={
 'GO':('GO',1.),'STREAK':('GO',.9),'VERTICAL':('GO',.85),'FADE':('GO',.8),'SEAM':('GO',.8),
 'HITCH':('HITCH',1.),'CURL':('HITCH',.8),'COMEBACK':('HITCH',.55),'STOP':('HITCH',.8),'HOOK':('HITCH',.75),
 'IN':('IN',1.),'DIG':('IN',.95),'DEEP_IN':('IN',.9),'SPEED_IN':('IN',.9),'SHORT_IN':('IN',.9),
 'OUT':('OUT',1.),'SPEED_OUT':('OUT',.9),'QUICK_OUT':('OUT',.9),'DEEP_OUT':('OUT',.9),'SHORT_OUT':('OUT',.9),
 'CROSS':('CROSS',1.),'CROSSER':('CROSS',.95),'DRAG':('CROSS',.8),'DEEP_CROSS':('CROSS',.9),'OVER':('CROSS',.85),
 'SLANT':('SLANT',1.),'POST':('POST',1.),'DEEP_POST':('POST',.9),'SKINNY_POST':('POST',.9),'CORNER':('CORNER',1.),'DEEP_CORNER':('CORNER',.9),
 'SCREEN':('SCREEN',1.),'SCREEN_PASS':('SCREEN',.8),'SLIP_SCREEN':('SCREEN',.85),'BUBBLE_SCREEN':('SCREEN',.85),'HB_SCREEN':('SCREEN',.85),'WR_SCREEN':('SCREEN',.85),
 'FLAT':('FLAT',1.),'SWING':('FLAT',.8),'ARROW':('FLAT',.8),'TABLE':('FLAT',.8),
 'WHEEL':('WHEEL',1.),'ANGLE':('ANGLE',1.),'TEXAS':('ANGLE',.9)}
FAMILY={**dict.fromkeys(['GO','POST','CORNER','WHEEL'],'VERTICAL'),**dict.fromkeys(['IN','OUT','HITCH'],'BREAK_SETTLE'),**dict.fromkeys(['CROSS','SLANT','ANGLE'],'CROSSING'),**dict.fromkeys(['SCREEN','FLAT'],'UNDERNEATH')}
FORMS={'SHOTGUN':'SHOTGUN','GUN':'SHOTGUN','SINGLEBACK':'SINGLEBACK','SINGLE_BACK':'SINGLEBACK','PISTOL':'PISTOL','I_FORM':'I_FORM','I':'I_FORM','STRONG_I':'STRONG_I','WEAK_I':'WEAK_I','WEAK':'WEAK_I','GOAL_LINE_OFFENSE':'GOAL_LINE','GOAL_LINE':'GOAL_LINE','EMPTY':'EMPTY'}
def token(x):return str(x or '').upper().strip().replace('-','_').replace(' ','_')
def formation(x):return FORMS.get(token(x),'UNSUPPORTED:'+token(x))
def route(x):return ROUTES.get(token(x),(None,0.))
def shape(x):
 try:
  parts=str(x).lower().split('x');a,b=map(int,parts)
  if min(a,b)<0 or a+b>5:return None
  return f'{max(a,b)}x{min(a,b)}'
 except (ValueError,TypeError):return None
@dataclass(frozen=True)
class Config:
 max_plays:int=24
 max_sets:int=8
 top_k:int=5
 minimum_score:float=.65
 prior_routes:float=20.
 max_play_weight:float=.35
 ridge:float=1e-5
 include_hybrid:bool=False
 locked_play_ids:tuple=()
 excluded_play_ids:tuple=()
 allowed_personnel:tuple=()
 joint_weight:float=.2
 def validate(self):
  if any(not isinstance(x,int) for x in [self.max_plays,self.max_sets,self.top_k]):raise ValueError('Budgets must be integers')
  if any(not math.isfinite(x) for x in [self.minimum_score,self.max_play_weight,self.prior_routes,self.ridge,self.joint_weight]):raise ValueError('Parameters must be finite')
  if self.max_plays<1 or self.max_sets<1 or self.top_k<1:raise ValueError('Budgets must be positive')
  if not 0<self.minimum_score<=1 or not 0<self.max_play_weight<=1:raise ValueError('Invalid score/weight limit')
  if self.prior_routes<0 or self.ridge<0:raise ValueError('Negative regularization')
  if not 0<=self.joint_weight<=1:raise ValueError('Invalid joint feature weight')
  if math.ceil(1/self.max_play_weight)>self.max_plays:raise ValueError('Play budget cannot satisfy max weight')
  if set(map(str,self.locked_play_ids))&set(map(str,self.excluded_play_ids)):raise ValueError('A play cannot be both locked and excluded')
  if len(set(map(str,self.locked_play_ids)))>self.max_plays:raise ValueError('Locked plays exceed budget')
def canonical_routes(play):
 raw=play.get('routes',[]);out=[]
 # Coordinate-based role proxies; never depth-chart WR3 == slot by fiat.
 eligible=[r for r in raw if r.get('x') is not None and r.get('y') is not None and (token(r.get('position')) in ['WR','TE'] or float(r['y'])>=-3.5)]
 counts=[sum(float(r['x'])<0 for r in eligible),sum(float(r['x'])>0 for r in eligible)]
 derived_shape=shape(f'{counts[0]}x{counts[1]}') if len(eligible)>=2 and sum(counts)==len(eligible) else None
 for r in raw:
  code,confidence=route(r.get('routeName'));pos=token(r.get('position'));pos='RB' if pos=='HB' else pos
  align=None
  if pos=='WR' and r.get('x') is not None:
   x=float(r['x']);same=[z for z in eligible if float(z['x'])*x>0]
   if same:
    outer=max(abs(float(z['x'])) for z in same);delta=outer-abs(x)
    if delta>=.75:align='SLOT_WR'
    elif delta<.01:align='WR'
  out.append({'code':code,'route_alias_strength':confidence,'position':pos,'role_proxy':align,'source_route':r.get('routeName'),'source_confidence':r.get('confidence'),'depth_chart_slot':r.get('depthChartSlot'),'slot':r.get('slot')})
 return out,derived_shape
class CatalogIndex:
 def __init__(self,catalog):
  if catalog.get('game')!='Madden NFL 27':raise ValueError('Catalog game version mismatch')
  semantic={'game':catalog['game'],'schema_version':catalog.get('schema_version'),'plays':sorted(catalog.get('plays',[]),key=lambda p:str(p['id']))}
  self.fingerprint=hashlib.sha256(json.dumps(semantic,sort_keys=True,separators=(',',':')).encode()).hexdigest();self.plays={};self.by_form=defaultdict(list);self.by_feature=defaultdict(list)
  for p in catalog.get('plays',[]):
   pid=str(p['id'])
   if pid in self.plays:raise ValueError('Duplicate catalog play ID')
   annotations,distribution=canonical_routes(p);tags={token(t) for t in p.get('tags',[])}
   normalized={**p,'id':pid,'canonical_formation':formation(p['formation']),'canonical_routes':annotations,'derived_distribution':distribution,'route_tags':[route(t) for t in tags if route(t)[0]],'pure_pass':'PASS_PLAY' in tags and 'RUN_PLAY' not in tags,'hybrid':'PASS_PLAY' in tags and 'RUN_PLAY' in tags}
   self.plays[pid]=normalized;self.by_form[normalized['canonical_formation']].append(normalized)
   codes={r['code'] for r in annotations if r['code']}|{r[0] for r in normalized['route_tags']}
   for code in codes:self.by_feature[(normalized['canonical_formation'],code)].append(normalized)
  for values in self.by_form.values():values.sort(key=lambda p:p['id'])
 def candidates(self,req,config):
  if req.get('kind')=='PARTIAL_JOINT':
   pools=[{p['id'] for p in self.candidates({**req,**s,'kind':'INDIVIDUAL'},config)} for s in req['observed_slots']]
   return [self.plays[i] for i in sorted(set.intersection(*pools))] if pools else []
  # Include same-family fallback but prune all incompatible formation families.
  # Proven upper bounds under the documented score: family-only <= .61625,
  # tag-only <= .495. Pruning never discards an accepted candidate.
  codes=[req['route']] if config.minimum_score>.61625 else [c for c,f in FAMILY.items() if f==FAMILY.get(req['route'])]
  ids={p['id'] for code in codes for p in self.by_feature.get((req['formation'],code),[])};excluded=set(map(str,config.excluded_play_ids));out=[]
  for i in sorted(ids):
   p=self.plays[i]
   if i in excluded or (config.allowed_personnel and p.get('personnel') not in config.allowed_personnel):continue
   if not (p['pure_pass'] or (config.include_hybrid and p['hybrid'])):continue
   if req['distribution'] and p['derived_distribution'] and req['distribution']!=p['derived_distribution']:continue
   if config.minimum_score>.495:
    positions=['WR'] if req['role'] in ['WR','SLOT_WR'] else [req['role']]
    if not any(r['code'] in codes and r['position'] in positions for r in p['canonical_routes']):continue
   out.append(p)
  return out
def requirements(payload,config):
 if payload.get('schema_version') not in ['1.1.0']:raise ValueError('Unsupported NFL mapping schema; rebuild corrected SLOT_WR export')
 groups={};ignored=[]
 for cell in payload.get('route_requirements',[]):
  n=float(cell.get('routes',0))
  if not math.isfinite(n) or n<0:raise ValueError('Invalid route count')
  if n==0:continue
  code=token(cell.get('route_ran'));form=formation(cell.get('offense_formation'));distribution=shape(cell.get('receiver_alignment'));role=token(cell.get('lined_up_position'))
  if code not in FAMILY:ignored.append({'reason':'unsupported_route','route':code,'routes':n});continue
  if role not in ['WR','SLOT_WR','TE','RB','FB']:ignored.append({'reason':'unsupported_role','role':role,'routes':n});continue
  key=(form,distribution,role,code)
  if key not in groups:groups[key]={'formation':form,'distribution':distribution,'role':role,'route':code,'routes':0.,'source_cells':0,'max_cell_games':0.,'contexts':[]}
  g=groups[key];g['routes']+=n;g['source_cells']+=1;g['max_cell_games']=max(g['max_cell_games'],float(cell.get('games') or 0));g['contexts'].append({'down':cell.get('down'),'motion':cell.get('motion'),'routes':n,'sparse':cell.get('sparse')})
 rows=sorted(groups.values(),key=lambda r:(r['formation'],r['distribution'] or '',r['role'],r['route']));total=sum(r['routes'] for r in rows)
 for i,r in enumerate(rows):r.update(requirement_id=f'r{i:04d}',mass=r['routes']/total,reliability=r['routes']/(r['routes']+config.prior_routes) if config.prior_routes else 1.)
 for r in rows:r['kind']='INDIVIDUAL';r['route_mass']=r['mass']
 joint=[]
 for cell in payload.get('joint_requirements',[]):
  n=float(cell.get('plays',0))
  if not math.isfinite(n) or n<0:raise ValueError('Invalid joint play count')
  if not n:continue
  slots=[{'role':token(s.get('role')),'route':token(s.get('route'))} for s in cell.get('observed_slots',[])]
  if len(slots)<2 or any(s['route'] not in FAMILY or s['role'] not in ['WR','SLOT_WR','TE','RB','FB'] for s in slots):continue
  joint.append({'kind':'PARTIAL_JOINT','formation':formation(cell.get('formation')),'distribution':shape(cell.get('distribution')),'observed_slots':sorted(slots,key=lambda s:(s['role'],s['route'])),'plays':n,'complete_concept_verified':False})
 joint.sort(key=lambda r:(r['formation'],r['distribution'] or '',json.dumps(r['observed_slots'],sort_keys=True)))
 jt=sum(r['plays'] for r in joint)
 if jt and rows and config.joint_weight:
  for r in rows:r['mass']*=1-config.joint_weight
  for i,r in enumerate(joint):r.update(requirement_id=f'j{i:04d}',mass=config.joint_weight*r['plays']/jt,reliability=r['plays']/(r['plays']+config.prior_routes) if config.prior_routes else 1.)
  rows.extend(joint)
 return rows,ignored
def compatibility(req,play):
 if req['formation']!=play['canonical_formation']:return None
 if req['distribution'] and play['derived_distribution'] and req['distribution']!=play['derived_distribution']:return None
 if req.get('kind')=='PARTIAL_JOINT':
  slots=play['canonical_routes'];observed=req['observed_slots']
  if len(slots)<len(observed):return None
  scores=np.zeros((len(observed),len(slots)));details={}
  for i,s in enumerate(observed):
   for j,r in enumerate(slots):
    d=compatibility({**req,**s,'kind':'INDIVIDUAL'},{**play,'canonical_routes':[r],'route_tags':[]})
    if d:scores[i,j]=d['score'];details[(i,j)]=d
  # Maximize the worst pair first, then sum, without reusing a receiver slot.
  thresholds=sorted(set(scores.flat),reverse=True);assignment=None
  for threshold in thresholds:
   if threshold<=0:break
   ii,jj=linear_sum_assignment(np.where(scores>=threshold,-scores,1e6))
   if np.all(scores[ii,jj]>=threshold):assignment=list(zip(ii,jj));break
  if assignment is None:return None
  return {'score':float(min(scores[i,j] for i,j in assignment)),'evidence':'PARTIAL_JOINT_INJECTIVE_PUBLISHER_SLOT_ASSIGNMENT','tier':'PARTIAL_JOINT','projected_count':1.,'complete_concept_verified':False,'slot_assignments':[{'observed':observed[i],'catalog_slot_index':int(j),**details[(i,j)]} for i,j in assignment]}
 if req['formation']!=play['canonical_formation']:return None
 known_shape=play['derived_distribution'];wanted=req['distribution']
 if known_shape and wanted and known_shape!=wanted:return None
 shape_strength=1. if known_shape and wanted else .35;best=None;count=0.
 for r in play['canonical_routes']:
  if r['code'] is None:continue
  if r['code']==req['route']:route_strength=r['route_alias_strength'];tier='EXACT_OR_ALIAS'
  elif FAMILY.get(r['code'])==FAMILY.get(req['route']):route_strength=.5*r['route_alias_strength'];tier='FAMILY_ONLY'
  else:continue
  role=req['role'];pos=r['position']
  if role in ['WR','SLOT_WR']:
   if pos!='WR':continue
   position_strength=1. if role==r['role_proxy'] else (.65 if role=='WR' and r['role_proxy']=='SLOT_WR' else .5 if r['role_proxy'] is None else .35)
  elif role!=pos:continue
  else:position_strength=1.
  score=(.55*route_strength+.2*position_strength+.15+.1*shape_strength)*.85
  count+=route_strength*position_strength
  if best is None or score>best['score']:best={'score':score,'route_strength':route_strength,'role_strength':position_strength,'shape_strength':shape_strength,'evidence':'PUBLISHER_ROUTE_ANNOTATION_WITH_COORDINATE_ROLE_PROXY','tier':tier,'slot':r['depth_chart_slot'],'source_route':r['source_route'],'source_confidence':r['source_confidence'],'role_evidence':'CATALOG_COORDINATE_PROXY' if role in ['WR','SLOT_WR'] else 'PUBLISHER_POSITION','distribution_evidence':'CATALOG_COORDINATE_PROXY' if known_shape else 'UNKNOWN'}
 # Tags support presence, not position assignments or route multiplicity.
 if best is None:
  exact=[strength for code,strength in play['route_tags'] if code==req['route']]
  if exact:best={'score':(.55*max(exact)+.1+.15+.1*shape_strength)*.55,'route_strength':max(exact),'role_strength':.5,'shape_strength':shape_strength,'evidence':'PUBLIC_PLAY_TAG_ROLE_UNKNOWN','tier':'TAG_ONLY','slot':None,'source_route':None,'source_confidence':None,'role_evidence':'UNKNOWN','distribution_evidence':'CATALOG_COORDINATE_PROXY' if known_shape else 'UNKNOWN'};count=1.
 if best is not None:best['projected_count']=count
 return best
def curate(rows,matches,index,config):
 ids=sorted({m['play_id'] for values in matches for m in values if m['accepted']}|set(map(str,config.locked_play_ids)))
 if not ids:return {'status':'NO_SUPPORTED_CANDIDATES','plays':[],'unmatched_mass':1.,'coverage_mass':0.}
 unknown=set(map(str,config.locked_play_ids))-set(index.plays)
 if unknown:raise ValueError('Unknown locked play IDs')
 if any(config.allowed_personnel and index.plays[str(pid)].get('personnel') not in config.allowed_personnel for pid in config.locked_play_ids):raise ValueError('Locked play violates personnel filter')
 if any(not index.plays[str(pid)]['pure_pass'] and not (config.include_hybrid and index.plays[str(pid)]['hybrid']) for pid in config.locked_play_ids):raise ValueError('Locked play is not an allowed passing candidate')
 score=np.zeros((len(rows),len(ids)));counts=np.zeros_like(score);lookup={p:j for j,p in enumerate(ids)}
 for i,values in enumerate(matches):
  for m in values:
   if m['accepted']:j=lookup[m['play_id']];score[i,j]=m['score'];counts[i,j]=m['projected_count']
 mass=np.array([r['mass'] for r in rows]);rel=np.array([r['reliability'] for r in rows]);weights=mass*(.5+.5*rel);selected=[lookup[str(x)] for x in config.locked_play_ids];selected=list(dict.fromkeys(selected));sets={index.plays[ids[j]]['set_id'] for j in selected}
 if len(selected)>config.max_plays or len(sets)>config.max_sets:raise ValueError('Locked plays exceed budgets')
 covered=score[:,selected].max(axis=1) if selected else np.zeros(len(rows))
 while len(selected)<min(config.max_plays,len(ids)):
  choices=[]
  for j,pid in enumerate(ids):
   if j in selected:continue
   new_set=index.plays[pid]['set_id'] not in sets
   if new_set and len(sets)>=config.max_sets:continue
   gain=float(weights@np.maximum(score[:,j]-covered,0));choices.append((gain/(1.05 if new_set else 1.),pid,j))
  if not choices:break
  gain,pid,j=sorted(choices,key=lambda x:(-x[0],x[1]))[0]
  if gain<=1e-12 and len(selected)>=math.ceil(1/config.max_play_weight):break
  selected.append(j);sets.add(index.plays[pid]['set_id']);covered=np.maximum(covered,score[:,j])
 if len(selected)<math.ceil(1/config.max_play_weight):return {'status':'WEIGHT_CAP_INFEASIBLE_WITH_SUPPORTED_CANDIDATES','plays':[],'unmatched_mass':float(mass[covered==0].sum()),'coverage_mass':float(mass[covered>0].sum())}
 matrix=counts[:,selected];denom=matrix.sum(axis=0)
 if np.any(denom==0):return {'status':'LOCKED_PLAY_HAS_NO_SUPPORTED_FEATURES','plays':[],'unmatched_mass':float(mass[covered==0].sum()),'coverage_mass':float(mass[covered>0].sum())}
 matrix=matrix/denom;weighted=.5+.5*rel
 def loss(w):e=matrix@w-mass;return float(np.dot(weighted*e,e)+config.ridge*np.dot(w,w))
 def jac(w):return 2*matrix.T@(weighted*(matrix@w-mass))+2*config.ridge*w
 initial=np.full(len(selected),1/len(selected));fit=minimize(loss,initial,jac=jac,method='SLSQP',bounds=[(0,config.max_play_weight)]*len(selected),constraints=[{'type':'eq','fun':lambda w:w.sum()-1,'jac':lambda w:np.ones(len(w))}],options={'ftol':1e-11,'maxiter':400})
 if not fit.success:raise RuntimeError('Mixture optimization failed: '+fit.message)
 fitted=matrix@fit.x;chosen=[]
 for j,w in zip(selected,fit.x):
  p=index.plays[ids[j]];chosen.append({'play_id':p['id'],'name':p['name'],'formation':p['formation'],'set':p['set_name'],'personnel':p.get('personnel'),'proposed_weight':float(w),'source_url':p['source_url'],'route_evidence':p['route_evidence'],'derived_distribution':p['derived_distribution'],'assignments':[{**r,'canonical_route':route(r.get('routeName'))[0],'evidence':'PUBLISHER_ANNOTATION_NOT_NFL_VERIFICATION'} for r in p.get('routes',[])],'protection_rules':None,'read_progression':None,'diagram_version_mismatch':p.get('diagram_version_mismatch',False),'supported_requirement_ids':[rows[i]['requirement_id'] for i in np.flatnonzero(score[:,j]>0)]})
 return {'status':'CURATED','plays':sorted(chosen,key=lambda p:(-p['proposed_weight'],p['play_id'])),'set_count':len(sets),'coverage_mass':float(mass[covered>0].sum()),'unmatched_mass':float(mass[covered==0].sum()),'projected_total_variation':float(np.abs(fitted-mass).sum()/2),'fit_objective':float(fit.fun),'optimizer_iterations':int(fit.nit),'feature_residuals':[{'requirement_id':r['requirement_id'],'observed_mass':r['mass'],'projected_mass':float(f),'residual':float(f-r['mass'])} for r,f in zip(rows,fitted)],'weight_semantics':'ASSUMED_MARGINAL_RECONSTRUCTION_MIX_NOT_IDENTIFIED_NFL_CALL_FREQUENCY','objective_semantics':'PROJECTED_COMPATIBLE_OBSERVED_ROUTE_FEATURES_NOT_EPA_OR_WIN_VALUE'}
def match(payload,index,config=Config()):
 config.validate();start=time.perf_counter();rows,ignored=requirements(payload,config);matches=[];evaluations=0
 if set(map(str,config.locked_play_ids))-set(index.plays):raise ValueError('Unknown locked play IDs')
 if not rows:return {'schema_version':VERSION,'team':payload.get('team'),'season':payload.get('season'),'status':'NO_ELIGIBLE_OBSERVATIONS','playbook':{'status':'NO_ELIGIBLE_OBSERVATIONS','plays':[]},'requirements':[],'ignored':ignored}
 for req in rows:
  values=[]
  for p in index.candidates(req,config):
   evaluations+=1;detail=compatibility(req,p)
   if detail:values.append({'play_id':p['id'],'play_name':p['name'],'source_url':p['source_url'],**detail,'accepted':detail['score']>=config.minimum_score})
  values.sort(key=lambda m:(-m['score'],m['play_id']));matches.append(values)
 book=curate(rows,matches,index,config)
 included=sum(r['routes'] for r in rows if r['kind']=='INDIVIDUAL');ignored_count=sum(r['routes'] for r in ignored);input_fraction=included/(included+ignored_count) if included+ignored_count else 0.
 selected={p['play_id'] for p in book['plays']}
 book['route_coverage_mass']=sum(r['route_mass'] for r,v in zip(rows,matches) if r['kind']=='INDIVIDUAL' and any(m['accepted'] and m['play_id'] in selected for m in v))
 book['overall_input_coverage_mass']=book['route_coverage_mass']*input_fraction
 for req,values in zip(rows,matches):req['ranked_candidates']=values[:config.top_k];req['accepted_candidate_count']=sum(v['accepted'] for v in values);req['status']='SUPPORTED_COMPATIBILITY' if req['accepted_candidate_count'] else 'ABSTAIN';req['top_score_margin']=values[0]['score']-values[1]['score'] if len(values)>1 else None
 return {'schema_version':VERSION,'team':payload.get('team'),'season':payload.get('season'),'catalog_sha256':index.fingerprint,'config':asdict(config),'coverage':payload.get('coverage',[]),'included_route_fraction':input_fraction,'ignored_route_fraction':1-input_fraction,'score_semantics':'HEURISTIC_COMPATIBILITY_NOT_CALIBRATED_PROBABILITY','requirements':rows,'ignored':ignored,'playbook':book,'runtime':{'seconds':time.perf_counter()-start,'candidate_evaluations':evaluations,'dense_evaluation_count':len(rows)*len(index.plays)},'limitations':['Supplied prospect cohort is not the full offense','Publisher route annotations and coordinate role proxies are not verified NFL concepts','Formation membership does not verify every play belongs to the Bears book','Unknown progression, protection and intended adjustments remain unknown','Outcomes are excluded from the matching and curation objective','Madden game-native binary import is not implemented; exports support the modeling tool and manual assembly']}
