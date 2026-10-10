"""Review regressions and a real source → adapter → matcher integration fixture."""
import contextlib,io,json,tempfile,unittest,sqlite3,os
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from test_matcher import play,catalog,payload,index
from matcher import Config,CatalogIndex,match,requirements,compatibility
from cli import execute,resolve_run
from validate import validate
from build_inputs import build as build_inputs
from pipeline import build as build_snapshot,aggregate_route_tendencies,TeamStore
from geometry import derive,GEOMETRY_COLUMNS
from context_audit import clean_snap_coordinates,audit
from validate_export import validate_export
from enrich import enrich
from research_contracts import atomic_json,file_hash,read_snapshot_state,source_signature,staged_database
from acquire import PublicCache,coverage_plan


def fixture(root):
    raw=root/'restored/data/raw';pub=root/'nfl-success-study/outputs';external=root/'nfl-success-study/raw'
    for d in [raw,pub,external]:d.mkdir(parents=True)
    def write(path,records,columns=None):pd.DataFrame(records,columns=columns).to_csv(path,index=False)
    write(raw/'games.csv',[{'game_id':1,'season':2025,'season_type':'REG','week':1,'home_team_abbr':'CHI','visitor_team_abbr':'DEN'}])
    players=[]
    for nfl_id,role,team,route,target in [(1,'WR','CHI','GO',True),(2,'SLOT_WR','CHI','GO',False),(3,'C','CHI',None,False),(4,'CB','DEN',None,False)]:
        players.append({'game_id':1,'play_id':10,'nfl_id':nfl_id,'team_abbr':team,'possession_team':'CHI',
                        'lined_up_position':role,'route_ran':route,'play_direction':'right','quarter':1,'down':1,
                        'yards_to_go':10,'offense_formation':'SHOTGUN','receiver_alignment':'2x0',
                        'team_coverage_type':'MAN','team_coverage_man_zone':'MAN','expected_points_added':.2,
                        'pass_result':'C','play_nullified_by_penalty':'N','in_motion_at_ball_snap':None if nfl_id==2 else False,
                        'target':target,'coverage_assignment':'MAN' if team=='DEN' else None,'coverage_assignment_at_snap':'MAN' if team=='DEN' else None})
    write(raw/'player_play.csv',players)
    for name in ['players','combine_results']:write(raw/(name+'.csv'),[{'nfl_id':i} for i in range(1,5)])
    write(raw/'combine_tracking.csv',[{'nfl_id':1,'draft_year':2025,'event_id':1}])
    write(raw/'player_career_successes.csv',[{'nfl_id':1,'career_success':1}])
    write(pub/'prospect_id_bridge.csv',[{'nfl_id':1,'gsis_id':'WR1'}])
    context={'game_id':'2025_01_DEN_CHI','old_game_id':1,'season':2025,'dropbacks':1,'opponent_elo':1500,
             'coach':'fixture','roof':'outdoors','temp':60,'wind':5,'rest':7}
    write(pub/'team_game.csv.gz',[{**context,'team':'CHI','opponent':'DEN'},{**context,'team':'DEN','opponent':'CHI'}])
    write(pub/'player_box_scores.csv.gz',[{'team':'CHI','season':2025,'game_id':'2025_01_DEN_CHI'}])
    write(pub/'injury_reports.csv.gz',[{'team':'CHI','season':2025,'week':1}])
    pbp=['game_id','play_id','posteam','defteam','passer_player_id','receiver_player_id','rusher_player_id','qtr','down','ydstogo','yardline_100','score_differential','qb_dropback','pass_attempt','rush_attempt','sack','interception','fumble_lost','epa','success','yards_gained','air_yards','cp','cpoe','complete_pass','shotgun','no_huddle','drive']
    for year in [2023,2024,2025]:
        row={c:0 for c in pbp};row.update(game_id=context['game_id'],play_id=10,posteam='CHI',defteam='DEN',passer_player_id='QB1',receiver_player_id='WR1')
        write(external/f'pbp_{year}.csv.gz',[row] if year==2025 else [],pbp)
        write(external/f'snap_counts_{year}.csv',[{'game_type':'REG','team':'CHI','season':2025,'game_id':context['game_id']}] if year==2025 else [],['game_type','team','season','game_id'])
    frames=[]
    for nfl_id,y in [(1,10),(2,16),(3,20),(4,21)]:
        for tick,event in [(0,'ball_snap'),(1,None),(2,'pass_forward')]:
            frames.append({'game_id':1,'play_id':10,'nfl_id':nfl_id,'time':f'2025-09-01T00:00:0{tick}.000Z',
                           'x':40+tick,'y':y,'event':event})
    write(raw/'game_tracking_2025.csv',frames)


class ReviewRegressions(unittest.TestCase):
    def test_joint_only_and_individual_only_channels(self):
        j=[{'formation':'SHOTGUN','distribution':'1x1','observed_slots':[{'role':'WR','route':'GO'}]*2,'plays':30}]
        for weight in [0,.2,1]:
            r=match(payload(joint=j),index(),Config(joint_weight=weight))
            self.assertLess(r['playbook']['projected_total_variation'],1e-8)
            expected={'INDIVIDUAL'} if weight==0 else {'PARTIAL_JOINT'} if weight==1 else {'INDIVIDUAL','PARTIAL_JOINT'}
            self.assertEqual(set(r['playbook']['channel_diagnostics']),expected)

    def test_empty_backfield_does_not_invent_parent(self):
        ps=[play(str(i),set_id='empty') for i in range(3)]
        for p in ps:p['set_name']='Empty Base Trio'
        p=payload();p['route_requirements'][0]['offense_formation']='EMPTY'
        r=match(p,CatalogIndex(catalog(ps)))
        self.assertEqual(r['playbook']['status'],'CURATED')
        req=r['requirements'][0];self.assertEqual(req['formation'],'UNKNOWN_PARENT')
        self.assertTrue(req['ranked_candidates'][0]['parent_family_unknown'])
        ps[0]['set_name']='Filled';idx=CatalogIndex(catalog(ps))
        self.assertIsNone(compatibility(req,idx.plays['0']))

    def test_greedy_move_preserves_feasible_set_capacity(self):
        ps=[play('A','Go',set_id='A')]+[play('B'+str(i),'Streak',set_id='B') for i in range(3)]
        r=match(payload(),CatalogIndex(catalog(ps)),Config(max_plays=3,max_sets=1))
        self.assertEqual(r['playbook']['status'],'CURATED')
        self.assertEqual({p['set_id'] for p in r['playbook']['plays']},{'B'})

    def test_subthreshold_slot_does_not_inflate_count(self):
        p=play('mixed');p['routes'][1]['routeName']='Post';idx=CatalogIndex(catalog([p]))
        req=requirements(payload(),Config())[0][0]
        self.assertEqual(compatibility(req,idx.plays['mixed'])['projected_count'],1)

    def test_generic_WR_does_not_claim_outside(self):
        p=play('slot',shape_x=(-10,-4));idx=CatalogIndex(catalog([p]));req=requirements(payload(),Config())[0][0];req['distribution']='2x0'
        a=compatibility(req,{**idx.plays['slot'],'canonical_routes':[idx.plays['slot']['canonical_routes'][0]]})
        b=compatibility(req,{**idx.plays['slot'],'canonical_routes':[idx.plays['slot']['canonical_routes'][1]]})
        self.assertEqual(a['role_strength'],b['role_strength']);self.assertIn('NOT_VERIFIED_OUTSIDE',a['role_evidence'])

    def test_stable_ids_and_ignored_joint_mass(self):
        a=requirements(payload(),Config())[0][0]['requirement_id']
        b=requirements(payload(['HITCH','GO']),Config())[0]
        self.assertEqual(a,next(r for r in b if r['route']=='GO')['requirement_id'])
        p=payload(joint=[{'formation':'SHOTGUN','distribution':'1x1','observed_slots':[{'role':'WR','route':'UNKNOWN'}]*2,'plays':7}])
        self.assertEqual(match(p,index())['ignored_joint_play_count'],7)

    def test_missing_inputs_and_requested_teams_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cat=root/'catalog.json';cat.write_text(json.dumps(catalog(list(index().plays.values()))))
            with self.assertRaises(FileNotFoundError):execute(cat,root/'missing',root/'out')
            (root/'inputs').mkdir()
            with self.assertRaises(ValueError):execute(cat,root/'inputs',root/'out')
            (root/'inputs/TEST-2025-mapping-input.json').write_text(json.dumps(payload()))
            with self.assertRaises(ValueError):execute(cat,root/'inputs',root/'out',teams=['ABSENT'])

    def test_read_only_validation_custom_config_and_subset(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp);inputs=root/'inputs';inputs.mkdir();cat=root/'catalog.json'
            cat.write_text(json.dumps(catalog(list(index().plays.values()))))
            for team in ['TEST','OTHER']:
                p=payload();p['team']=team;(inputs/f'{team}-2025-mapping-input.json').write_text(json.dumps(p))
            out=root/'out';execute(cat,inputs,out,teams=['TEST'],max_plays=3,max_sets=1)
            before={str(p):file_hash(p) for p in out.rglob('*') if p.is_file()}
            result=validate(out=out,report=root/'validation.json')
            self.assertEqual(result['team_seasons'],1)
            self.assertEqual(before,{str(p):file_hash(p) for p in out.rglob('*') if p.is_file()})
            # A new subset publishes a separate run; validator does not pick up old profiles.
            execute(cat,inputs,out,teams=['OTHER']);result=validate(out=out,report=root/'other.json')
            self.assertEqual([r['team'] for r in result['records']],['OTHER'])

    def test_artifact_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp);inputs=root/'inputs';inputs.mkdir();cat=root/'catalog.json';cat.write_text(json.dumps(catalog(list(index().plays.values()))))
            (inputs/'TEST-2025-mapping-input.json').write_text(json.dumps(payload()));out=root/'out';execute(cat,inputs,out)
            run,manifest=resolve_run(out);(run/manifest['profiles'][0]['path']).write_text('{}')
            with self.assertRaises(ValueError):execute(cat,inputs,out)
            with self.assertRaises(ValueError):validate(out=out,report=root/'v.json')

    def test_coverage_metrics_positive_weight_ordering(self):
        r=match(payload(['GO','HITCH']),index(),Config(prune_zero_weight=False))['playbook']
        self.assertLessEqual(r['positive_weight_support_coverage'],r['selected_book_coverage'])
        positive={rid for p in r['plays'] if p['proposed_weight']>r['positive_weight_threshold'] for rid in p['supported_requirement_ids']}
        rows=match(payload(['GO','HITCH']),index(),Config(prune_zero_weight=False))['requirements']
        self.assertAlmostEqual(r['positive_weight_support_coverage'],sum(x['mass'] for x in rows if x['requirement_id'] in positive))

    def test_unknown_motion_and_target_EPA_stay_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fixture(root);r=pd.read_csv(root/'restored/data/raw/player_play.csv')
            r=r[r.lined_up_position.isin(['WR','SLOT_WR'])];r['season']=2025;r.loc[r.nfl_id.eq(1),'expected_points_added']=float('nan')
            agg=aggregate_route_tendencies(r)
            self.assertIn('UNKNOWN',agg.motion.tolist());self.assertTrue(agg.loc[agg.targets.eq(1),'positive_target_rate'].isna().all())

    def test_conflicting_and_identical_snap_duplicates(self):
        a={'game_id':1,'play_id':1,'nfl_id':1,'time':'t','x':1,'y':2,'event':'ball_snap'}
        clean,n=clean_snap_coordinates(pd.DataFrame([a,a]));self.assertEqual((len(clean),n),(1,0))
        clean,n=clean_snap_coordinates(pd.DataFrame([a,{**a,'y':20}]))
        self.assertTrue(clean.empty);self.assertEqual(n,1)

    def test_end_to_end_NONE_and_materialized(self):
        for tracking in [[],['CHI']]:
            with self.subTest(tracking=tracking),tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
                root=Path(temp);fixture(root);snapshot=root/'snapshot';build_snapshot(root,snapshot,tracking)
                enrich(root,snapshot);geometry=derive(snapshot);audit(snapshot,root)
                self.assertEqual(geometry['status'],'NOT_MATERIALIZED' if not tracking else 'DERIVED')
                store=TeamStore(snapshot);self.assertEqual(store.query('PRAGMA table_info(route_geometry)').name.tolist(),GEOMETRY_COLUMNS)
                if tracking:
                    with patch('pipeline.pd.read_csv',wraps=pd.read_csv) as read:
                        store.tracking_batch([(1,10,1),(1,10,2)]);store.tracking(1,10,1)
                        self.assertEqual(read.call_count,1)
                exported=validate_export(snapshot,root/'exports');self.assertEqual(exported['team_seasons'],2)
                inputs=root/'inputs';build_inputs(snapshot,inputs)
                cat=root/'catalog.json';cat.write_text(json.dumps(catalog([play(str(i),set_id='set',shape_x=(-10,-4)) for i in range(4)])))
                summary=execute(cat,inputs,root/'out',teams=['CHI'],max_plays=3,max_sets=1)
                self.assertEqual(summary['results'][0]['status'],'CURATED')
                run,m=resolve_run(root/'out');p=json.loads((run/m['profiles'][0]['path']).read_text())
                self.assertIsNotNone(p['source_snapshot_id']);self.assertTrue(any(r['source_feature_ids'] for r in p['requirements']))
                validate(out=root/'out',report=root/'report.json')
                # Snapshot corruption must invalidate the declared artifact identity.
                with (snapshot/'team_index.sqlite').open('ab') as f:f.write(b'corrupt')
                with self.assertRaises(ValueError):read_snapshot_state(snapshot)

    def test_public_cache_refresh_TTL_and_snapshot(self):
        html='<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':{'value':1}}})+'</script>Madden 27'
        class Response:
            url='https://madden.tools/playbooks'
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return html.encode()
        with tempfile.TemporaryDirectory() as temp,patch('acquire.urllib.request.urlopen',return_value=Response()) as fetch:
            url=Response.url
            self.assertEqual(PublicCache(temp).get(url)['value'],1)
            PublicCache(temp).get(url);self.assertEqual(fetch.call_count,1)
            PublicCache(temp,mode='refresh').get(url);self.assertEqual(fetch.call_count,2)
            PublicCache(temp,ttl=0).get(url);self.assertEqual(fetch.call_count,3)
            snap=PublicCache(temp,mode='snapshot');snap.get(url);self.assertEqual(fetch.call_count,3)
            self.assertIsNotNone(snap.records[url]['fetched_at_utc'])
            with self.assertRaises(ValueError):snap.get('https://madden.tools/missing')

    def test_content_hash_detects_same_size_same_mtime_change(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'source';p.write_text('alpha');old=source_signature(p);stamp=p.stat().st_mtime_ns
            p.write_text('bravo');os.utime(p,ns=(stamp,stamp));new=source_signature(p)
            self.assertEqual(old['bytes'],new['bytes']);self.assertNotEqual(old['sha256'],new['sha256'])

    def test_failed_derived_stage_keeps_original_database(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp);fixture(root);snapshot=root/'snapshot';build_snapshot(root,snapshot,[])
            before=file_hash(snapshot/'team_index.sqlite');state=file_hash(snapshot/'snapshot-state.json')
            with self.assertRaises(RuntimeError):
                with staged_database(snapshot,'failed',{'version':'test'}) as c:
                    c.execute('CREATE TABLE incomplete(value INT)');raise RuntimeError('injected failure')
            self.assertEqual(before,file_hash(snapshot/'team_index.sqlite'));self.assertEqual(state,file_hash(snapshot/'snapshot-state.json'))

    def test_store_identity_refreshes_after_published_stage(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp);fixture(root);snapshot=root/'snapshot';build_snapshot(root,snapshot,[])
            store=TeamStore(snapshot);before=store.identity()['snapshot_id']
            with staged_database(snapshot,'extra',{'version':'test'}) as c:c.execute('CREATE TABLE extra(value INT)')
            self.assertNotEqual(before,store.identity()['snapshot_id'])

    def test_missing_promised_shard_fails_validation(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp);fixture(root);snapshot=root/'snapshot';build_snapshot(root,snapshot,['CHI'])
            store=TeamStore(snapshot);path=store.query('SELECT path FROM tracking_shards').path.iloc[0];(snapshot/path).unlink()
            with self.assertRaises(ValueError):validate_export(snapshot,root/'exports')

    def test_new_schema_requires_explicit_identity(self):
        p=payload();p['schema_version']='1.2.0'
        with self.assertRaises(ValueError):match(p,index())
        with self.assertRaises(ValueError):CatalogIndex({'game':'Madden NFL 27','plays':[]})

    def test_coverage_plan_determinism(self):
        ps=[play(str(i),set_id='A' if i<4 else 'B') for i in range(8)]
        for p in ps:p['tags'].append('GO' if p['set_id']=='A' else 'HITCH')
        a=coverage_plan(ps,3);b=coverage_plan(list(reversed(ps)),3)
        self.assertEqual([p['id'] for p in a],[p['id'] for p in b]);self.assertEqual({p['set_id'] for p in a},{'A','B'})

if __name__=='__main__':unittest.main()
