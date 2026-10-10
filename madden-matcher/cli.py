from pathlib import Path
import argparse,json,hashlib,time,csv
from matcher import CatalogIndex,Config,match,VERSION

def assembly_sheet(result,dest):
 """Manual Madden assembly aid, not a game-native import file."""
 fields=['team','season','formation','set','play','play_id','personnel','proposed_weight','route_evidence','source_url']
 with Path(dest).open('w',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
  for p in result['playbook']['plays']:
   writer.writerow({'team':result['team'],'season':result['season'],'formation':p['formation'],'set':p['set'],'play':p['name'],'play_id':p['play_id'],'personnel':p.get('personnel'),'proposed_weight':p['proposed_weight'],'route_evidence':p['route_evidence'],'source_url':p['source_url']})

def execute(catalog_path,input_dir,out_dir,teams=None,max_plays=24,max_sets=8,personnel=(),locked=(),excluded=(),config_overrides=None):
 catalog=json.loads(Path(catalog_path).read_text());index=CatalogIndex(catalog);out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
 options={'max_plays':max_plays,'max_sets':max_sets,'allowed_personnel':tuple(personnel),'locked_play_ids':tuple(locked),'excluded_play_ids':tuple(excluded)}
 options.update(config_overrides or {});config=Config(**options);config.validate();results=[];start=time.perf_counter()
 for file in sorted(Path(input_dir).glob('*-mapping-input.json')):
  payload=json.loads(file.read_text())
  if teams and payload['team'] not in teams:continue
  key=hashlib.sha256((VERSION+index.fingerprint+file.read_text()+json.dumps(config.__dict__,sort_keys=True)).encode()).hexdigest();dest=out/(file.stem.replace('-mapping-input','-custom-playbook')+'.json');cached=False
  if dest.exists():
   result=json.loads(dest.read_text());cached=result.get('cache_key')==key
  if not cached:result=match(payload,index,config);result['cache_key']=key;dest.write_text(json.dumps(result,allow_nan=False,separators=(',',':')))
  assembly_sheet(result,dest.with_suffix('.csv'))
  book=result['playbook'];results.append({'team':payload['team'],'season':payload['season'],'status':book['status'],'plays':len(book['plays']),'coverage_mass':book.get('coverage_mass',0.),'route_coverage_mass':book.get('route_coverage_mass',0.),'overall_input_coverage_mass':book.get('overall_input_coverage_mass',0.),'unmatched_mass':book.get('unmatched_mass',1.),'total_variation':book.get('projected_total_variation'),'seconds':result.get('runtime',{}).get('seconds'),'cached':cached})
 summary={'version':VERSION,'catalog_plays':len(index.plays),'catalog_sha256':index.fingerprint,'seconds':time.perf_counter()-start,'results':results};(out/'batch-summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2));return summary
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--catalog',default='madden-matcher/data/catalog.json');p.add_argument('--inputs',default='nfl-team-index/exports-v2');p.add_argument('--out',default='madden-matcher/outputs');p.add_argument('--teams',default='ALL');p.add_argument('--max-plays',type=int,default=24);p.add_argument('--max-sets',type=int,default=8);p.add_argument('--personnel',default='');p.add_argument('--lock-id',action='append',default=[]);p.add_argument('--exclude-id',action='append',default=[]);p.add_argument('--config',help='JSON Config overrides, taking precedence over individual flags');a=p.parse_args();execute(a.catalog,a.inputs,a.out,None if a.teams=='ALL' else a.teams.split(','),a.max_plays,a.max_sets,tuple(a.personnel.split(',')) if a.personnel else (),a.lock_id,a.exclude_id,json.loads(Path(a.config).read_text()) if a.config else None)
