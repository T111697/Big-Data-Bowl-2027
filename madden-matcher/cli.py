"""Immutable run publication, frozen inputs and hash-verified result reuse."""
from pathlib import Path
from functools import lru_cache
import argparse,csv,json,shutil,tempfile,time,os,hashlib
from matcher import CatalogIndex,Config,match,VERSION
from research_contracts import DEFAULT_INPUTS,atomic_json,digest,file_hash

def implementation_digest():
    root=Path(__file__).resolve().parents[1]
    return digest({str(p.relative_to(root)):file_hash(p) for p in [root/'research_contracts.py',root/'madden-matcher/matcher.py',root/'madden-matcher/cli.py']})


@lru_cache(maxsize=4)
def compile_catalog(raw):
    return CatalogIndex(json.loads(raw))


def assembly_sheet(result,dest):
    fields=['team','season','formation','set','play','play_id','personnel','proposed_weight','route_evidence','source_url']
    with Path(dest).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for p in result['playbook']['plays']:
            writer.writerow({'team':result['team'],'season':result['season'],'formation':p['formation'],
                             'set':p['set'],'play':p['name'],'play_id':p['play_id'],'personnel':p['personnel'],
                             'proposed_weight':p['proposed_weight'],'route_evidence':p['route_evidence'],'source_url':p['source_url']})


def load_inputs(directory,teams=None):
    directory=Path(directory)
    if not directory.is_dir():raise FileNotFoundError(f'Missing input directory: {directory}')
    marker=directory/'input-manifest.json'
    if marker.exists():
        manifest=json.loads(marker.read_text())
        if manifest.get('schema_version')!='mapping-input-batch-1.0':raise ValueError('Unsupported input manifest')
        files=[]
        for entry in manifest['files']:
            p=directory/entry['path']
            if not p.resolve().is_relative_to(directory.resolve()) or file_hash(p)!=entry['sha256']:raise ValueError('Input artifact hash mismatch')
            files.append(p)
    else:
        files=sorted(directory.glob('*-mapping-input.json'))
    if not files:raise ValueError('No mapping inputs found')
    records=[];keys=set()
    for p in sorted(files):
        raw=p.read_bytes().decode('utf-8');payload=json.loads(raw);input_sha=hashlib.sha256(raw.encode('utf-8')).hexdigest()
        if marker.exists():
            expected=next(e['sha256'] for e in manifest['files'] if e['path']==str(p.relative_to(directory)))
            if input_sha!=expected:raise ValueError('Input changed during acquisition')
        if payload.get('schema_version')=='1.2.0' and not marker.exists():raise ValueError('New-schema inputs require producer manifest')
        key=(payload.get('team'),payload.get('season'))
        if key in keys:raise ValueError('Duplicate team-season input')
        keys.add(key)
        if teams and key[0] not in teams:continue
        lineage=None
        if payload.get('lineage_ref'):
            lp=directory/payload['lineage_ref']
            declared={e['path']:e['sha256'] for e in manifest.get('lineage_files',[])} if marker.exists() else {}
            if not lp.resolve().is_relative_to(directory.resolve()) or payload['lineage_ref'] not in declared or file_hash(lp)!=declared[payload['lineage_ref']]:raise ValueError('Lineage artifact hash/path mismatch')
            lineage={'path':payload['lineage_ref'],'raw':lp.read_text(),'sha256':file_hash(lp)}
        records.append({'file':p,'raw':raw,'payload':payload,'sha256':input_sha,'lineage':lineage})
    missing=set(teams or [])-{r['payload']['team'] for r in records}
    if missing:raise ValueError('Requested teams missing: '+','.join(sorted(missing)))
    if not records:raise ValueError('No selected team-season profiles')
    return records


def resolve_run(out):
    out=Path(out);pointer=json.loads((out/'run-manifest.json').read_text())
    root=out/pointer['manifest_path']
    if not root.resolve().is_relative_to(out.resolve()) or file_hash(root)!=pointer['manifest_sha256']:
        raise ValueError('Run pointer hash/path mismatch')
    manifest=json.loads(root.read_text());return root.parent,manifest


def verify_run(root,manifest):
    for artifact in manifest['artifacts']:
        p=root/artifact['path']
        if not p.resolve().is_relative_to(root.resolve()) or file_hash(p)!=artifact['sha256']:
            raise ValueError('Run artifact hash/path mismatch: '+artifact['path'])
    if len(manifest['profiles'])!=len(manifest['inputs']):raise ValueError('Run profile/input reconciliation failed')


def execute(catalog_path,input_dir,out_dir,teams=None,max_plays=24,max_sets=8,personnel=(),locked=(),excluded=(),config_overrides=None):
    start=time.perf_counter();raw_catalog=Path(catalog_path).read_text();index=compile_catalog(raw_catalog)
    options={'max_plays':max_plays,'max_sets':max_sets,'allowed_personnel':tuple(personnel),
             'locked_play_ids':tuple(locked),'excluded_play_ids':tuple(excluded)}
    options.update(config_overrides or {});config=Config(**options);config.validate()
    records=load_inputs(input_dir,teams);out=Path(out_dir);(out/'runs').mkdir(parents=True,exist_ok=True)
    identity={'algorithm':VERSION,'implementation_sha256':implementation_digest(),'catalog_sha256':index.fingerprint,'config':config.__dict__,
              'inputs':[{'team':r['payload']['team'],'season':r['payload']['season'],'sha256':r['sha256'],'lineage_sha256':r['lineage']['sha256'] if r['lineage'] else None} for r in records]}
    run_id=digest(identity);final=out/'runs'/run_id;cached=final.is_dir()
    if cached:
        manifest=json.loads((final/'run-manifest.json').read_text());verify_run(final,manifest)
    else:
        stage=Path(tempfile.mkdtemp(prefix='.building-',dir=out/'runs'))
        try:
            (stage/'inputs').mkdir();(stage/'catalog.json').write_text(raw_catalog)
            profiles=[];inputs=[];lineage_files=[]
            for record in records:
                payload=record['payload'];name=f"{payload['team']}-{payload['season']}"
                result=match(payload,index,config);result['run_id']=run_id;result['cache_key']=digest({**identity,'profile':name})
                filename=name+'-custom-playbook.json';atomic_json(stage/filename,result)
                assembly_sheet(result,(stage/filename).with_suffix('.csv'))
                frozen='inputs/'+name+'-mapping-input.json';(stage/frozen).write_text(record['raw'])
                if record['lineage']:
                    lp=stage/'inputs'/record['lineage']['path'];lp.parent.mkdir(parents=True,exist_ok=True);lp.write_text(record['lineage']['raw'])
                    lineage_files.append({'path':record['lineage']['path'],'sha256':record['lineage']['sha256']})
                inputs.append({'team':payload['team'],'season':payload['season'],'path':frozen,'sha256':record['sha256'],
                               'source_snapshot_id':payload.get('source_snapshot_id')})
                profiles.append({'team':payload['team'],'season':payload['season'],'path':filename,
                                 'status':result['playbook']['status']})
            atomic_json(stage/'inputs/input-manifest.json', {'schema_version':'mapping-input-batch-1.0','files':[{'path':Path(s['path']).name,'sha256':s['sha256']} for s in inputs],'lineage_files':lineage_files})
            artifacts=[{'path':str(p.relative_to(stage)),'sha256':file_hash(p)} for p in sorted(stage.rglob('*')) if p.is_file()]
            manifest={'schema_version':'madden-run-1.0','run_id':run_id,**identity,'identity':identity,'profiles':profiles,
                      'inputs':inputs,'artifacts':artifacts,'cold_seconds':time.perf_counter()-start}
            atomic_json(stage/'run-manifest.json',manifest)
            os.replace(stage,final)
        finally:
            if stage.exists():shutil.rmtree(stage)
    results=[]
    for profile in manifest['profiles']:
        r=json.loads((final/profile['path']).read_text());b=r['playbook']
        results.append({'team':r['team'],'season':r['season'],'status':b['status'],'plays':len(b['plays']),
                        'selected_book_coverage':b['selected_book_coverage'],
                        'positive_weight_support_coverage':b['positive_weight_support_coverage'],
                        'route_coverage_mass':b['route_coverage_mass'],'overall_input_coverage_mass':b['overall_input_coverage_mass'],
                        'total_variation':b.get('projected_total_variation'),'cached':cached})
    pointer={'run_id':run_id,'manifest_path':str((final/'run-manifest.json').relative_to(out)),
             'manifest_sha256':file_hash(final/'run-manifest.json')}
    atomic_json(out/'run-manifest.json',pointer)
    summary={'version':VERSION,'run_id':run_id,'catalog_plays':len(index.plays),'catalog_sha256':index.fingerprint,
             'seconds':time.perf_counter()-start,'results':results}
    atomic_json(out/'batch-summary.json',summary);print(json.dumps(summary,indent=2));return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--catalog',default='madden-matcher/data/catalog.json')
    p.add_argument('--inputs',default=DEFAULT_INPUTS);p.add_argument('--out',default='madden-matcher/outputs')
    p.add_argument('--teams',default='ALL');p.add_argument('--max-plays',type=int,default=24);p.add_argument('--max-sets',type=int,default=8)
    p.add_argument('--personnel',default='');p.add_argument('--lock-id',action='append',default=[]);p.add_argument('--exclude-id',action='append',default=[])
    p.add_argument('--config');a=p.parse_args()
    execute(a.catalog,a.inputs,a.out,None if a.teams=='ALL' else a.teams.split(','),a.max_plays,a.max_sets,
            tuple(a.personnel.split(',')) if a.personnel else (),a.lock_id,a.exclude_id,
            json.loads(Path(a.config).read_text()) if a.config else None)
