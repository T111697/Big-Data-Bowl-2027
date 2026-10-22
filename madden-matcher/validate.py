"""Read-only validation bound to a frozen run. Benchmarks use temporary outputs."""
import argparse,collections,contextlib,io,json,tempfile,time
from pathlib import Path
import numpy as np
from matcher import CatalogIndex,Config,requirements,compatibility,VERSION
from cli import execute,resolve_run,verify_run,implementation_digest
from research_contracts import atomic_json,file_hash,digest


def require(condition,message):
    if not condition:raise ValueError(message)


def validate(catalog_path=None,inputs=None,out='madden-matcher/outputs',report='madden-matcher/validation.json'):
    # catalog_path/inputs are retained as compatibility arguments, not mutable data sources.
    root,manifest=resolve_run(out);verify_run(root,manifest)
    require(manifest.get('schema_version')=='madden-run-1.0' and bool(manifest.get('profiles')),'Invalid/empty run manifest')
    require(digest(manifest['identity'])==manifest['run_id'],'Run identity digest mismatch')
    require(manifest['identity']['config']==manifest['config'],'Run config identity mismatch')
    require(manifest['algorithm']==VERSION and manifest['implementation_sha256']==implementation_digest(),'Use the recorded implementation to benchmark this historical run')
    if Path(report).resolve().is_relative_to(Path(out).resolve()):raise ValueError('Validation reports must be outside the run output directory')
    catalog=json.loads((root/'catalog.json').read_text());index=CatalogIndex(catalog)
    require(index.fingerprint==manifest['catalog_sha256'],'Catalog identity mismatch')
    config=Config(**manifest['config']);config.validate();records=[]
    for profile,source in zip(manifest['profiles'],manifest['inputs']):
        r=json.loads((root/profile['path']).read_text());b=r['playbook'];ps=b['plays']
        require((r['team'],r['season'])==(source['team'],source['season']),'Profile identity mismatch')
        require(r['run_id']==manifest['run_id'] and r['config']==manifest['config'],'Profile run/config mismatch')
        if b['status']=='CURATED':
            ws=np.array([p['proposed_weight'] for p in ps])
            require(np.isfinite(ws).all() and abs(ws.sum()-1)<1e-7,'Invalid mixture weights')
            require(ws.min()>=-1e-9 and ws.max()<=config.max_play_weight+1e-7,'Weight cap violated')
            require(len(ps)<=config.max_plays and len({p['set_id'] for p in ps})<=config.max_sets,'Book budgets violated')
            require(len({p['play_id'] for p in ps})==len(ps),'Duplicate book plays')
            require(set(map(str,config.locked_play_ids))<={p['play_id'] for p in ps},'Locked play missing')
            require(not set(map(str,config.excluded_play_ids))&{p['play_id'] for p in ps},'Excluded play present')
            require(all(not config.allowed_personnel or p['personnel'] in config.allowed_personnel for p in ps),'Personnel violated')
            require(b['positive_weight_support_coverage']<=b['selected_book_coverage']+1e-9,'Coverage metric ordering violated')
            require(all(p['read_progression'] is None and p['protection_rules'] is None for p in ps),'Unknown responsibilities promoted')
        for req in r['requirements']:
            if req['kind']=='PARTIAL_JOINT':require(not req['complete_concept_verified'],'Partial concept promoted')
            for candidate in req['ranked_candidates']:
                if candidate.get('slot_assignments'):
                    slots=[s['catalog_slot_index'] for s in candidate['slot_assignments']]
                    require(len(slots)==len(set(slots)),'Receiver slot reused')
        records.append({'team':r['team'],'season':r['season'],'status':b['status'],'plays':len(ps),
                        'route_coverage_mass':b['route_coverage_mass'],
                        'selected_book_coverage':b['selected_book_coverage'],
                        'positive_weight_support_coverage':b['positive_weight_support_coverage'],
                        'total_variation':b.get('projected_total_variation')})
    # Prefer CHI 2025, but subset runs benchmark a profile that actually exists.
    source=next((s for s in manifest['inputs'] if s['team']=='CHI' and s['season']==2025),manifest['inputs'][0])
    payload=json.loads((root/source['path']).read_text());rows,_=requirements(payload,config)
    def evaluate(indexed):
        values=[];n=0;start=time.perf_counter()
        for req in rows:
            pool=index.candidates(req,config) if indexed else [p for p in index.plays.values()
                 if p['id'] not in set(map(str,config.excluded_play_ids))
                 and (not config.allowed_personnel or p['personnel'] in config.allowed_personnel)
                 and (p['pure_pass'] or (config.include_hybrid and p['hybrid']))]
            accepted=[]
            for p in pool:
                n+=1;d=compatibility(req,p,config.minimum_score)
                if d and d['score']>=config.minimum_score:accepted.append((p['id'],round(d['score'],12)))
            values.append(sorted(accepted))
        return values,n,time.perf_counter()-start
    a,n,seconds=evaluate(True);b,dense,dseconds=evaluate(False);require(a==b,'Indexed/dense accepted-candidate mismatch')
    with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
        cold=execute(root/'catalog.json',root/'inputs',Path(temp)/'outputs',config_overrides=manifest['config'])
        warm=execute(root/'catalog.json',root/'inputs',Path(temp)/'outputs',config_overrides=manifest['config'])
    require(all(r['cached'] for r in warm['results']),'Isolated warm cache was not reused')
    # Recheck immutability after benchmarking, including the pointer/manifest.
    verify_run(root,manifest)
    result={'schema_version':'run-validation-2.0','run_id':manifest['run_id'],'run_manifest_sha256':file_hash(root/'run-manifest.json'),
            'validation_scope':'Structural reconstruction checks, not NFL concept accuracy','team_seasons':len(records),
            'teams':len({r['team'] for r in records}),'statuses':dict(collections.Counter(r['status'] for r in records)),
            'cold_batch_seconds':cold['seconds'],'warm_batch_seconds':warm['seconds'],'all_cache_hits':True,
            'benchmark':{'team':payload['team'],'season':payload['season'],'requirements':len(rows),
                         'indexed_evaluations':n,'dense_evaluations':dense,'indexed_seconds':seconds,'dense_seconds':dseconds,
                         'evaluation_reduction':1-n/dense if dense else None,'accepted_candidate_parity':True},'records':records}
    atomic_json(report,result);print(json.dumps({k:v for k,v in result.items() if k!='records'},indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--outputs',default='madden-matcher/outputs');p.add_argument('--report',default='madden-matcher/validation.json')
    a=p.parse_args();validate(out=a.outputs,report=a.report)
