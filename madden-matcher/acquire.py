"""Fetch only publicly rendered Madden Tools pages; cache and retain provenance."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import urllib.request,urllib.parse,re,json,hashlib,time,argparse,threading,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research_contracts import atomic_json,file_hash
ROOT='https://madden.tools'
def page_props(html):
 match=re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',html,re.S)
 if not match:raise ValueError('Unsupported public page: missing SSR payload')
 if 'Madden 27' not in html:raise ValueError('Page does not identify Madden 27')
 return json.loads(match.group(1))['props']['pageProps']
class PublicCache:
 def __init__(self,path,mode='revalidate',ttl=86400):
  if mode not in ['snapshot','revalidate','refresh'] or ttl<0:raise ValueError('Invalid cache policy')
  self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True);self.records={}
  self.mode=mode;self.ttl=ttl;self.lock=threading.Lock();self.stats={'cache_hits':0,'fetched':0,'failures':0}
 def get(self,url):
  parsed=urllib.parse.urlparse(url)
  if parsed.scheme!='https' or parsed.netloc!='madden.tools':raise ValueError('Unexpected source host/scheme')
  key=hashlib.sha256(url.encode()).hexdigest();file=self.path/(key+'.html');meta=self.path/(key+'.meta.json')
  metadata=json.loads(meta.read_text()) if meta.exists() else {}
  valid=file.exists() and (not metadata or metadata.get('sha256')==file_hash(file))
  fresh=valid and metadata.get('fetched_at_epoch') is not None and time.time()-metadata['fetched_at_epoch']<self.ttl
  use=valid and (self.mode=='snapshot' or (self.mode=='revalidate' and fresh))
  if self.mode=='snapshot' and not use:raise ValueError('Requested snapshot page missing/corrupt')
  if use:
   b=file.read_bytes()
   with self.lock:self.stats['cache_hits']+=1
  else:
   for attempt in range(2):
    try:
     request=urllib.request.Request(url,headers={'User-Agent':'NFL-Tendency-Research/0.3 (public snapshot)'})
     with urllib.request.urlopen(request,timeout=25) as r:
      if urllib.parse.urlparse(r.url).netloc!='madden.tools':raise ValueError('Unexpected redirect')
      b=r.read()
     page_props(b.decode());file.write_bytes(b)
     metadata={'sha256':hashlib.sha256(b).hexdigest(),'fetched_at_epoch':time.time(),
               'fetched_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
     atomic_json(meta,metadata)
     with self.lock:self.stats['fetched']+=1
     break
    except Exception:
     if attempt:
      with self.lock:self.stats['failures']+=1
      raise
     time.sleep(.5)
  record={'url':url,'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'cache_file':str(file),
          'observed_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
          'fetched_at_utc':metadata.get('fetched_at_utc'),'freshness':'KNOWN_FETCH_TIME' if metadata.get('fetched_at_utc') else 'LEGACY_FETCH_TIME_UNKNOWN'}
  with self.lock:self.records[url]=record
  return page_props(b.decode())

def coverage_plan(plays,details):
 # Balance observed metadata coverage rather than selecting first IDs per set.
 selected=[];counts={};remaining={p['id']:p for p in plays}
 def features(p):
  route_tags=[t for t in p['tags'] if t not in ['PASS_PLAY','RUN_PLAY','PLAY_ACTION']]
  return [('formation',p['formation']),('set',p['set_id']),('personnel',p['personnel'])]+[('tag',t) for t in route_tags]
 while remaining and len(selected)<details:
  def gain(p):return sum((2 if k in ['personnel','tag'] else 1)/(1+counts.get((k,v),0)) for k,v in features(p))
  best=sorted(remaining.values(),key=lambda p:(-gain(p),p['id']))[0];selected.append(best);remaining.pop(best['id'])
  for f in features(best):counts[f]=counts.get(f,0)+1
 return selected

def acquire(cache,out,details=160,workers=4,cache_mode="revalidate",ttl=86400):
 start=time.perf_counter();client=PublicCache(cache,cache_mode,ttl);book_url=ROOT+'/playbooks/book/madden-bears-offense';book=client.get(book_url)['book'];sets=[(f,st) for f in book['formations'] for st in f['sets']];items={};errors=[]
 def fetch_set(pair):
  f,st=pair;url=ROOT+f"/playbooks/formation/{f['slug']}/{st['slug']}";return url,client.get(url)['setData']
 with ThreadPoolExecutor(max_workers=workers) as pool:
  submitted={pool.submit(fetch_set,pair):pair for pair in sets}
  for future in as_completed(submitted):
   try:
    url,st=future.result()
    for pl in st['plays']:
     playurl=ROOT+f"/playbooks/formation/{pl['formationSlug']}/{pl['setSlug']}/{pl['slug']}"
     items[str(pl['id'])]=dict(id=str(pl['id']),name=pl['name'],formation=pl['formationName'],set_name=pl['setName'],set_id=str(pl['setId']),personnel=st.get('personnelGroup',{}).get('name'),tags=[t['name'] for t in pl.get('tags',[])],source_url=playurl,set_source_url=url,diagram_url=pl.get('diagramUrl'),routes=[],route_evidence='PUBLIC_TAGS_ONLY',catalog_scope='PUBLIC_PLAYS_IN_SETS_LISTED_BY_BEARS_BOOK_NOT_VERIFIED_BEARS_PLAY_MEMBERSHIP')
   except Exception as exc:
    f,st=submitted[future];errors.append({'stage':'set','source_url':ROOT+f"/playbooks/formation/{f['slug']}/{st['slug']}",'error':str(exc)})
 print(f'Sets retrieved: {len(sets)-len(errors)}/{len(sets)}; unique plays: {len(items)}',flush=True)
 purepass=[p for p in items.values() if 'PASS_PLAY' in p['tags'] and 'RUN_PLAY' not in p['tags']]
 selected=coverage_plan(purepass,details)
 def fetch_play(p):return p['id'],client.get(p['source_url'])['play']
 with ThreadPoolExecutor(max_workers=workers) as pool:
  submitted={pool.submit(fetch_play,p):p for p in selected}
  for n,future in enumerate(as_completed(submitted),1):
   try:
    pid,pl=future.result();item=items[pid];item['routes']=[{k:r.get(k) for k in ['slot','position','depthChartSlot','side','x','y','routeName','confidence']} for r in pl.get('routes',[])];item['route_evidence']='PUBLISHER_ROUTE_ANNOTATIONS' if item['routes'] else 'PUBLIC_TAGS_ONLY';item['is_pass']=pl.get('isPass');item['diagram_version_mismatch']=bool(item['diagram_url'] and '/madden-26/' in item['diagram_url'])
   except Exception as exc:errors.append({'stage':'play','source_url':submitted[future]['source_url'],'play_id':submitted[future]['id'],'error':str(exc)})
   if n%40==0:print(f'Detail pages completed: {n}/{len(selected)}',flush=True)
 out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);result={'schema_version':'madden-catalog-1.0','game':'Madden NFL 27','as_of_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source_publisher':'Madden Tools','scope':f'{len(sets)} sets listed by Bears book, union of public plays in those sets; includes plays from other books','membership_verified':False,'plays':sorted(items.values(),key=lambda p:p['id']),'cache_policy':{'mode':cache_mode,'ttl_seconds':ttl},'detail_selection':'metadata-coverage-greedy-v1','acquisition':{'seconds':time.perf_counter()-start,'set_pages_requested':len(sets),'detail_pages_requested':len(selected),'errors':errors,**client.stats},'sources':sorted(client.records.values(),key=lambda x:x['url'])};atomic_json(out,result);print(json.dumps(result['acquisition']),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--cache',default='madden-matcher/data/cache/public');p.add_argument('--out',default='madden-matcher/data/catalog.json');p.add_argument('--details',type=int,default=160);p.add_argument('--workers',type=int,default=4);p.add_argument('--cache-mode',choices=['snapshot','revalidate','refresh'],default='revalidate');p.add_argument('--ttl',type=float,default=86400);a=p.parse_args()
 if not 1<=a.workers<=4:raise SystemExit('workers must be 1–4')
 if a.details<0:raise SystemExit("details must be nonnegative")
 acquire(a.cache,a.out,a.details,a.workers,a.cache_mode,a.ttl)
