"""Shared paths, canonical source semantics, provenance and atomic publication."""
from pathlib import Path
from contextlib import contextmanager
import hashlib,json,os,sqlite3,tempfile,time
import pandas as pd

NFL_SCHEMA='1.2.0'
DEFAULT_SNAPSHOT='nfl-team-index/snapshot-v3'
DEFAULT_EXPORTS='nfl-team-index/exports-v3'
DEFAULT_INPUTS='madden-matcher/data/inputs-v3'
ADAPTER_VERSION='role-route-adapter-2.0'

def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def file_hash(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()

def source_signature(path):
 p=Path(path)
 return {'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':file_hash(p)}

def atomic_json(path,value):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 fd,name=tempfile.mkstemp(prefix=p.name+'.',suffix='.tmp',dir=p.parent)
 try:
  with os.fdopen(fd,'w') as f:
   json.dump(value,f,sort_keys=True,allow_nan=False,separators=(',',':'));f.flush();os.fsync(f.fileno())
  os.replace(name,p)
 finally:Path(name).unlink(missing_ok=True)

def eligible_routes(frame):
 return frame[frame.route_ran.notna() & frame.play_nullified_by_penalty.eq('N')].copy()

def motion_value(value):
 if pd.isna(value):return 'UNKNOWN'
 if isinstance(value,str):
  normalized=value.strip().upper()
  if normalized in ['TRUE','T','1','YES']:return 'TRUE'
  if normalized in ['FALSE','F','0','NO']:return 'FALSE'
  return 'UNKNOWN'
 return 'TRUE' if bool(value) else 'FALSE'

def stable_feature(kind,dimensions):return kind.lower()+'-'+digest(dimensions)[:24]

def read_snapshot_state(snapshot):
 p=Path(snapshot);db=p/'team_index.sqlite'
 if not db.is_file():raise FileNotFoundError(f'Missing snapshot database: {db}')
 state=p/'snapshot-state.json'
 if state.exists():
  result=json.loads(state.read_text())
  if result['database_sha256']!=file_hash(db):raise ValueError('Snapshot artifact hash mismatch')
  return result
 # Explicit legacy identity; never infer a schema version from a directory name.
 manifest=p/'manifest.json';source=json.loads(manifest.read_text()) if manifest.exists() else {}
 if source.get('fingerprint',{}).get('schema_version')==NFL_SCHEMA or source.get('fingerprint',{}).get('version')==NFL_SCHEMA:raise ValueError('New snapshot is missing its publication state')
 return {'snapshot_id':file_hash(db),'source_id':digest(source.get('fingerprint',{})),'schema_version':source.get('fingerprint',{}).get('version','LEGACY_UNDECLARED'),'capabilities':['legacy_unverified_manifest'],'database_sha256':file_hash(db)}

def publish_snapshot_state(snapshot,stages=None,capabilities=None):
 p=Path(snapshot);state_path=p/'snapshot-state.json'
 old=json.loads(state_path.read_text()) if state_path.exists() else {}
 merged={**old.get('stages',{}),**(stages or {})};sha=file_hash(p/'team_index.sqlite')
 state={'schema_version':NFL_SCHEMA,'database_sha256':sha,'snapshot_id':digest({'database':sha,'stages':merged}),'stages':merged,'capabilities':sorted(set(old.get('capabilities',[]))|set(capabilities or []))}
 atomic_json(state_path,state);return state

@contextmanager
def staged_database(snapshot,stage,fingerprint,capabilities=()):
 """Derived writes are isolated; readers retain the old DB until publication.

 A crash between DB/state publication fails hash verification, never silently
 admits a partially published snapshot. Run stages serially, not concurrently.
 """
 p=Path(snapshot);read_snapshot_state(p);db=p/'team_index.sqlite'
 fd,name=tempfile.mkstemp(prefix=stage+'.',suffix='.sqlite',dir=p);os.close(fd)
 conn=None
 try:
  with sqlite3.connect(db) as source,sqlite3.connect(name) as target:source.backup(target)
  conn=sqlite3.connect(name);yield conn;conn.commit();conn.close();conn=None
  os.replace(name,db)
  publish_snapshot_state(p,{stage:digest(fingerprint)},capabilities)
  atomic_json(p/(stage+'_manifest.json'),{'fingerprint':fingerprint,'completed_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
 finally:
  if conn is not None:conn.close()
  Path(name).unlink(missing_ok=True)
