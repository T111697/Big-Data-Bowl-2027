"""Formal role-route adapter with shared extraction, reconciliation and lineage."""
from pathlib import Path
import sys,json,argparse,collections
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'nfl-team-index')]
from matcher import formation_axes,shape,token
from pipeline import TeamStore,prepare_routes,source_feature_id,ROUTE_GROUPS
from research_contracts import DEFAULT_SNAPSHOT,DEFAULT_INPUTS,ADAPTER_VERSION,atomic_json,file_hash,stable_feature


def build(snapshot,out):
    store=TeamStore(snapshot);identity=store.identity();dest=Path(out);dest.mkdir(parents=True,exist_ok=True)
    teams=store.query('SELECT DISTINCT team_abbr,season FROM player_play ORDER BY team_abbr,season')
    files,lineage_files,summary=[],[],[]
    for team,season in teams.itertuples(index=False,name=None):
        payload=store.mapping_input(team,int(season));r=prepare_routes(store.table('receiver_play',team,int(season)))
        expected=sum(cell['routes'] for cell in payload['route_requirements'])
        if expected!=len(r):raise ValueError('Individual adapter counts do not reconcile')
        counts=collections.Counter();joint_members=collections.defaultdict(list)
        for key,g in r.groupby(['game_id','play_id'],sort=False):
            if len(g)<2:continue
            if g[['offense_formation','receiver_alignment']].drop_duplicates().shape[0]!=1:
                raise ValueError('Conflicting shared formation attributes')
            slots=tuple(sorted(zip(g.lined_up_position,g.route_ran)))
            signature=(str(g.offense_formation.iloc[0]),str(g.receiver_alignment.iloc[0]),slots)
            counts[signature]+=1;joint_members[signature].append({'game_id':int(key[0]),'play_id':int(key[1]),'nfl_ids':sorted(map(int,g.nfl_id))})
        joint=[];lineage={}
        for (f,d,slots),n in sorted(counts.items()):
            dimensions={'formation':f,'distribution':d,'slots':slots}
            feature=stable_feature('source_joint',dimensions)
            joint.append({'formation':f,'distribution':d,'observed_slots':[{'role':p,'route':rt} for p,rt in slots],
                          'plays':n,'feature_id':feature,'complete_concept_verified':False})
            lineage[feature]=joint_members[f,d,slots]
        for dims,g in r.groupby(ROUTE_GROUPS,dropna=False,sort=True):
            feature=source_feature_id(dict(zip(ROUTE_GROUPS,dims)))
            lineage[feature]=[{'game_id':int(v.game_id),'play_id':int(v.play_id),'nfl_id':int(v.nfl_id)} for v in g.itertuples()]
        name=f'{team}-{int(season)}';lineage_path='lineage/'+name+'.json'
        atomic_json(dest/lineage_path,{'source_snapshot_id':identity['snapshot_id'],'memberships':lineage})
        lineage_files.append({'path':lineage_path,'sha256':file_hash(dest/lineage_path)})
        support={}
        for dims,g in r.groupby(['offense_formation','receiver_alignment','lined_up_position','route_ran'],sort=True):
            f,b=formation_axes(dims[0]);dimensions={'formation':f,'backfield':b,'distribution':shape(dims[1]),'role':token(dims[2]),'route':token(dims[3])}
            fid=stable_feature('INDIVIDUAL',dimensions)
            if fid not in support:support[fid]={'games':set(),'players':set()}
            support[fid]['games'].update(map(int,g.game_id));support[fid]['players'].update(map(int,g.nfl_id))
        for cell in joint:
            f,b=formation_axes(cell['formation']);dimensions={'formation':f,'backfield':b,'distribution':shape(cell['distribution']),'observed_slots':sorted(cell['observed_slots'],key=lambda s:(s['role'],s['route']))}
            fid=stable_feature('PARTIAL_JOINT',dimensions)
            if fid not in support:support[fid]={'games':set(),'players':set()}
            for membership in lineage[cell['feature_id']]:
                support[fid]['games'].add(membership['game_id']);support[fid]['players'].update(membership['nfl_ids'])
        payload['independent_support']={fid:{'games':len(v['games']),'players':len(v['players'])} for fid,v in support.items()}
        payload.update(joint_requirements=joint,adapter_version=ADAPTER_VERSION,
                       capabilities=sorted(set(payload['capabilities'])|{'partial_joint_routes','source_key_lineage'}),
                       lineage_ref=lineage_path,joint_evidence_scope='PARTIAL_SAME_PLAY_ROLE_ROUTE_MULTISET; UNOBSERVED_RECEIVERS_UNKNOWN',
                       observed_joint_play_count=sum(counts.values()))
        path=name+'-mapping-input.json';atomic_json(dest/path,payload)
        files.append({'path':path,'team':team,'season':int(season),'sha256':file_hash(dest/path)})
        summary.append({'team':team,'season':int(season),'joint_cells':len(counts),'joint_plays':sum(counts.values())})
    manifest={'schema_version':'mapping-input-batch-1.0','adapter_version':ADAPTER_VERSION,
              'source_snapshot_id':identity['snapshot_id'],'files':files,'lineage_files':lineage_files}
    atomic_json(dest/'input-manifest.json',manifest)
    print(json.dumps({'team_seasons':len(summary),'joint_cells':sum(s['joint_cells'] for s in summary)},indent=2));return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',default=DEFAULT_SNAPSHOT);p.add_argument('--out',default=DEFAULT_INPUTS)
    a=p.parse_args();build(a.snapshot,a.out)
