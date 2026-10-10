import unittest,tempfile,sqlite3
from pathlib import Path
import pandas as pd
from pipeline import TeamStore,norm
class StoreTests(unittest.TestCase):
 def test_aliases(self):
  self.assertEqual(norm(pd.Series(['LAR','LA','OAK','LV','SD','STL','CHI'])).tolist(),['LA','LA','LV','LV','LAC','LA','CHI'])
 def test_reassembly_and_parameterized_filters(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);key=dict(game_id=1,play_id=2,nfl_id=3)
   for path,times in [('a.csv.gz',['2023-01-01T00:00:00.2','2023-01-01T00:00:00.3']),('b.csv.gz',['2023-01-01T00:00:00.0','2023-01-01T00:00:00.1'])]:pd.DataFrame([{**key,'time':t,'x':4,'y':5} for t in times]).to_csv(p/path,index=False)
   with sqlite3.connect(p/'team_index.sqlite') as c:
    pd.DataFrame([dict(shard_id=1,path='a.csv.gz'),dict(shard_id=2,path='b.csv.gz')]).to_sql('tracking_shards',c,index=False)
    pd.DataFrame([{**key,'shard_id':1},{**key,'shard_id':2}]).to_sql('tracking_keys',c,index=False)
    pd.DataFrame([dict(team_abbr='LA',season=2023,nfl_id=3),dict(team_abbr='CHI',season=2023,nfl_id=4)]).to_sql('player_play',c,index=False)
   s=TeamStore(p);d=s.tracking(1,2,3);self.assertEqual(len(d),4);self.assertTrue(d.time.is_monotonic_increasing)
   self.assertEqual(s.table('player_play','LAR',2023,columns=['nfl_id']).nfl_id.tolist(),[3])
   self.assertEqual(len(s.table('player_play',"CHI' OR 1=1 --",2023)),0)
   with self.assertRaises(ValueError):s.table('invalid','CHI',2023)
   with self.assertRaises(ValueError):s.table('player_play','CHI',2023,columns=['bad_column'])
if __name__=='__main__':unittest.main()
