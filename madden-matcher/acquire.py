"""Fetch only publicly rendered Madden Tools pages; cache and retain provenance."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import urllib.request,urllib.parse,re,json,hashlib,time,argparse
ROOT='https://madden.tools'
def page_props(html):
 match=re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',html,re.S)
 if not match:raise ValueError('Unsupported public page: missing SSR payload')
 if 'Madden 27' not in html:raise ValueError('Page does not identify Madden 27')
 return json.loads(match.group(1))['props']['pageProps']
class PublicCache:
 def __init__(self,path):self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True);self.records={};self.stats={'cache_hits':0,'fetched':0,'failures':0}
 def get(self,url):
  if urllib.parse.urlparse(url).netloc!='madden.tools':raise ValueError('Unexpected source host')
  key=hashlib.sha256(url.encode()).hexdigest();file=self.path/(key+'.html')
  if file.exists():b=file.read_bytes();self.stats['cache_hits']+=1
  else:
   for attempt in range(2):
    try:
     request=urllib.request.Request(url,headers={'User-Agent':'NFL-Tendency-Research/0.2 (public catalog snapshot)'})
     with urllib.request.urlopen(request,timeout=25) as r:
      if urllib.parse.urlparse(r.url).netloc!='madden.tools':raise ValueError('Unexpected redirect')
      b=r.read()
     page_props(b.decode());file.write_bytes(b);self.stats['fetched']+=1;break
    except Exception:
     if attempt: self.stats['failures']+=1;raise
     time.sleep(.5)
  self.records[url]={'url':url,'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'cache_file':str(file)}
  return page_props(b.decode())
def acquire(cache,out,details=160,workers=4):
 start=time.perf_counter();client=PublicCache(cache);book_url=ROOT+'/playbooks/book/madden-bears-offense';book=client.get(book_url)['book'];sets=[(f,st) for f in book['formations'] for st in f['sets']];items={};errors=[]
 def fetch_set(pair):
  f,st=pair;url=ROOT+f"/playbooks/formation/{f['slug']}/{st['slug']}";return url,client.get(url)['setData']
 with ThreadPoolExecutor(max_workers=workers) as pool:
  for future in as_completed([pool.submit(fetch_set,pair) for pair in sets]):
   try:
    url,st=future.result()
    for pl in st['plays']:
     playurl=ROOT+f"/playbooks/formation/{pl['formationSlug']}/{pl['setSlug']}/{pl['slug']}"
     items[str(pl['id'])]=dict(id=str(pl['id']),name=pl['name'],formation=pl['formationName'],set_name=pl['setName'],set_id=str(pl['setId']),personnel=st.get('personnelGroup',{}).get('name'),tags=[t['name'] for t in pl.get('tags',[])],source_url=playurl,set_source_url=url,diagram_url=pl.get('diagramUrl'),routes=[],route_evidence='PUBLIC_TAGS_ONLY',catalog_scope='PUBLIC_PLAYS_IN_SETS_LISTED_BY_BEARS_BOOK_NOT_VERIFIED_BEARS_PLAY_MEMBERSHIP')
   except Exception as exc:errors.append({'stage':'set','error':str(exc)})
 print(f'Sets retrieved: {len(sets)-len(errors)}/{len(sets)}; unique plays: {len(items)}',flush=True)
 # Round-robin across sets, not an alphabetical route-family sampling bias.
 purepass=[p for p in items.values() if 'PASS_PLAY' in p['tags'] and 'RUN_PLAY' not in p['tags']];buckets={}
 for p in sorted(purepass,key=lambda p:(p['set_id'],p['id'])):buckets.setdefault(p['set_id'],[]).append(p)
 selected=[]
 while len(selected)<min(details,len(purepass)):
  for key in sorted(buckets):
   if buckets[key] and len(selected)<details:selected.append(buckets[key].pop(0))
 def fetch_play(p):return p['id'],client.get(p['source_url'])['play']
 with ThreadPoolExecutor(max_workers=workers) as pool:
  for n,future in enumerate(as_completed([pool.submit(fetch_play,p) for p in selected]),1):
   try:
    pid,pl=future.result();item=items[pid];item['routes']=[{k:r.get(k) for k in ['slot','position','depthChartSlot','side','x','y','routeName','confidence']} for r in pl.get('routes',[])];item['route_evidence']='PUBLISHER_ROUTE_ANNOTATIONS' if item['routes'] else 'PUBLIC_TAGS_ONLY';item['is_pass']=pl.get('isPass');item['diagram_version_mismatch']=bool(item['diagram_url'] and '/madden-26/' in item['diagram_url'])
   except Exception as exc:errors.append({'stage':'play','error':str(exc)})
   if n%40==0:print(f'Detail pages completed: {n}/{len(selected)}',flush=True)
 out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);result={'schema_version':'madden-catalog-1.0','game':'Madden NFL 27','as_of_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source_publisher':'Madden Tools','scope':f'{len(sets)} sets listed by Bears book, union of public plays in those sets; includes plays from other books','membership_verified':False,'plays':sorted(items.values(),key=lambda p:p['id']),'acquisition':{'seconds':time.perf_counter()-start,'set_pages_requested':len(sets),'detail_pages_requested':len(selected),'errors':errors,**client.stats},'sources':sorted(client.records.values(),key=lambda x:x['url'])};out.write_text(json.dumps(result,separators=(',',':')));print(json.dumps(result['acquisition']),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--cache',default='madden-matcher/data/cache/public');p.add_argument('--out',default='madden-matcher/data/catalog.json');p.add_argument('--details',type=int,default=160);p.add_argument('--workers',type=int,default=4);a=p.parse_args()
 if not 1<=a.workers<=4:raise SystemExit('workers must be 1–4')
 acquire(a.cache,a.out,a.details,a.workers)
