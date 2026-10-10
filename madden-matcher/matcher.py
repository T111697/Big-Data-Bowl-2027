"""Evidence-aware Madden concept matching and bounded marginal reconstruction.

Scores measure compatibility, not calibrated probabilities or predicted football value.
"""
from dataclasses import dataclass,asdict
from collections import defaultdict
import json,hashlib,math,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research_contracts import stable_feature,digest
import numpy as np
from scipy.optimize import minimize,linear_sum_assignment
VERSION='madden-matcher-2.0.0'
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
    max_plays: int = 24
    max_sets: int = 8
    top_k: int = 5
    minimum_score: float = .65
    prior_routes: float = 20.
    max_play_weight: float = .35
    ridge: float = 1e-5
    include_hybrid: bool = False
    locked_play_ids: tuple = ()
    excluded_play_ids: tuple = ()
    allowed_personnel: tuple = ()
    joint_weight: float = .2
    positive_weight_threshold: float = 1e-8
    prune_zero_weight: bool = True

    def validate(self):
        budgets = [self.max_plays, self.max_sets, self.top_k]
        if any(type(x) is not int or x < 1 for x in budgets):
            raise ValueError('Budgets must be positive integers')
        scalars = [self.minimum_score, self.max_play_weight, self.prior_routes,
                   self.ridge, self.joint_weight, self.positive_weight_threshold]
        if any(type(x) not in [float,int] or not math.isfinite(x) for x in scalars):
            raise ValueError('Parameters must be finite numbers')
        if not 0 < self.minimum_score <= 1 or not 0 < self.max_play_weight <= 1:
            raise ValueError('Invalid score/weight limit')
        if self.prior_routes < 0 or self.ridge < 0 or not 0 <= self.joint_weight <= 1:
            raise ValueError('Invalid channel weight or regularization')
        if not 0 <= self.positive_weight_threshold < self.max_play_weight:
            raise ValueError('Invalid positive-weight threshold')
        if math.ceil(1 / self.max_play_weight) > self.max_plays:
            raise ValueError('Play budget cannot satisfy max weight')
        if set(map(str, self.locked_play_ids)) & set(map(str, self.excluded_play_ids)):
            raise ValueError('A play cannot be locked and excluded')
        if len(set(map(str, self.locked_play_ids))) > self.max_plays:
            raise ValueError('Locked plays exceed budget')
        if type(self.include_hybrid) is not bool or type(self.prune_zero_weight) is not bool:
            raise ValueError('Boolean flags must be booleans')
        for values in [self.locked_play_ids, self.excluded_play_ids, self.allowed_personnel]:
            if not isinstance(values, (list, tuple)):
                raise ValueError('ID/personnel filters must be arrays')


def canonical_routes(play):
    raw = play.get('routes', [])
    eligible = []
    for r in raw:
        for coordinate in ['x', 'y']:
            if r.get(coordinate) is not None and not math.isfinite(float(r[coordinate])):
                raise ValueError('Nonfinite catalog coordinate')
        if r.get('x') is not None and r.get('y') is not None:
            if token(r.get('position')) in ['WR', 'TE'] or float(r['y']) >= -3.5:
                eligible.append(r)
    counts = [sum(float(r['x']) < 0 for r in eligible),
              sum(float(r['x']) > 0 for r in eligible)]
    distribution = shape(f'{counts[0]}x{counts[1]}') if len(eligible) >= 2 and sum(counts) == len(eligible) else None
    normalized = []
    for r in raw:
        code, strength = route(r.get('routeName'))
        pos = token(r.get('position'))
        pos = 'RB' if pos == 'HB' else pos
        proxy = None
        if pos == 'WR' and r.get('x') is not None:
            x = float(r['x'])
            same = [z for z in eligible if float(z['x']) * x > 0]
            if same:
                delta = max(abs(float(z['x'])) for z in same) - abs(x)
                if delta >= .75:
                    proxy = 'SLOT_WR'
                elif delta < .01:
                    proxy = 'WR'
        normalized.append({'code': code, 'route_alias_strength': strength,
                           'position': pos, 'role_proxy': proxy,
                           'source_route': r.get('routeName'),
                           'source_confidence': r.get('confidence'),
                           'depth_chart_slot': r.get('depthChartSlot'), 'slot': r.get('slot')})
    return normalized, distribution


def formation_axes(value):
    if token(value) == 'EMPTY':
        return 'UNKNOWN_PARENT', 'EMPTY'
    return formation(value), None


def formation_strength(req, play):
    if req.get('backfield') == 'EMPTY' or req['formation'] == 'EMPTY':
        return .75 if play.get('backfield') == 'EMPTY' else None
    if req['formation'].startswith('UNSUPPORTED:'):
        return None
    return 1. if req['formation'] == play['canonical_formation'] else None


class CatalogIndex:
    def __init__(self, catalog):
        if catalog.get('game') != 'Madden NFL 27' or catalog.get('schema_version') != 'madden-catalog-1.0':
            raise ValueError('Unsupported catalog game/schema')
        if not isinstance(catalog.get('plays'), list):
            raise ValueError('Catalog plays must be an array')
        semantic = {'game': catalog['game'], 'schema_version': catalog['schema_version'],
                    'plays': sorted(catalog['plays'], key=lambda p: str(p['id']))}
        self.fingerprint = digest(semantic)
        self.plays = {}
        self.by_form = defaultdict(list)
        self.by_feature = defaultdict(list)
        self.by_empty_feature = defaultdict(list)
        for p in catalog['plays']:
            required = ['id', 'name', 'formation', 'set_id', 'set_name', 'personnel',
                        'tags', 'routes', 'source_url', 'route_evidence']
            if any(k not in p for k in required):
                raise ValueError('Missing catalog play fields')
            pid = str(p['id'])
            if pid in self.plays:
                raise ValueError('Duplicate catalog play ID')
            annotations, distribution = canonical_routes(p)
            tags = sorted({token(t) for t in p['tags']})
            # A source-backed name proxy, not verification of an NFL formation.
            empty = p.get('backfield') == 'EMPTY' or token(p['set_name']).startswith('EMPTY')
            normalized = {**p, 'id': pid, 'canonical_formation': formation(p['formation']),
                          'canonical_routes': annotations, 'derived_distribution': distribution,
                          'backfield': 'EMPTY' if empty else None,
                          'backfield_evidence': 'PUBLISHER_EMPTY_SET_PROXY' if empty else 'UNKNOWN',
                          'route_tags': [route(t) for t in tags if route(t)[0]],
                          'pure_pass': 'PASS_PLAY' in tags and 'RUN_PLAY' not in tags,
                          'hybrid': 'PASS_PLAY' in tags and 'RUN_PLAY' in tags}
            self.plays[pid] = normalized
            self.by_form[normalized['canonical_formation']].append(normalized)
            codes = {r['code'] for r in annotations if r['code']} | {r[0] for r in normalized['route_tags']}
            for code in codes:
                self.by_feature[(normalized['canonical_formation'], code)].append(normalized)
                if empty:
                    self.by_empty_feature[code].append(normalized)

    def candidates(self, req, config):
        if req.get('kind') == 'PARTIAL_JOINT':
            pools = [{p['id'] for p in self.candidates({**req, **s, 'kind': 'INDIVIDUAL'}, config)}
                     for s in req['observed_slots']]
            return [self.plays[i] for i in sorted(set.intersection(*pools))] if pools else []
        # These upper bounds stay conservative for generic WR and unknown parent formation.
        codes = [req['route']] if config.minimum_score > .61625 else [
            c for c, f in FAMILY.items() if f == FAMILY.get(req['route'])]
        empty = req.get('backfield') == 'EMPTY' or req['formation'] == 'EMPTY'
        pools = [self.by_empty_feature[c] if empty else self.by_feature[(req['formation'], c)] for c in codes]
        ids = {p['id'] for pool in pools for p in pool}
        excluded = set(map(str, config.excluded_play_ids))
        out = []
        for pid in sorted(ids):
            p = self.plays[pid]
            if pid in excluded or (config.allowed_personnel and p['personnel'] not in config.allowed_personnel):
                continue
            if not (p['pure_pass'] or (config.include_hybrid and p['hybrid'])):
                continue
            if req['distribution'] and p['derived_distribution'] and req['distribution'] != p['derived_distribution']:
                continue
            if config.minimum_score > .495:
                positions = ['WR'] if req['role'] in ['WR', 'SLOT_WR'] else [req['role']]
                if not any(r['code'] in codes and r['position'] in positions for r in p['canonical_routes']):
                    continue
            out.append(p)
        return out


def nonnegative_count(value):
    if isinstance(value,bool):raise ValueError('Counts must be numbers, not booleans')
    n = float(value)
    if not math.isfinite(n) or n < 0:
        raise ValueError('Counts must be finite and nonnegative')
    return n


def requirements(payload, config):
    if payload.get('schema_version') not in ['1.1.0', '1.2.0']:
        raise ValueError('Unsupported NFL mapping schema; corrected SLOT_WR schema required')
    if payload.get('schema_version')=='1.2.0':
        if not isinstance(payload.get('source_snapshot_id'),str) or not isinstance(payload.get('capabilities'),list):
            raise ValueError('New-schema inputs require snapshot identity and capabilities')
    if not isinstance(payload.get('team'), str) or not payload['team'] or type(payload.get('season')) is not int:
        raise ValueError('Team and integer season are required')
    if not isinstance(payload.get('route_requirements'), list):
        raise ValueError('route_requirements must be an array')
    groups, ignored = {}, []
    for cell in payload['route_requirements']:
        if any(k not in cell for k in ['routes', 'route_ran', 'offense_formation', 'receiver_alignment', 'lined_up_position']):
            raise ValueError('Missing individual route fields')
        n = nonnegative_count(cell['routes'])
        if not n:
            continue
        code, role = token(cell['route_ran']), token(cell['lined_up_position'])
        if code not in FAMILY or role not in ['WR', 'SLOT_WR', 'TE', 'RB', 'FB']:
            ignored.append({'channel': 'INDIVIDUAL', 'reason': 'unsupported_route_or_role', 'routes': n,
                            'route': code, 'role': role})
            continue
        form, backfield = formation_axes(cell['offense_formation'])
        distribution = shape(cell['receiver_alignment'])
        key = (form, backfield, distribution, role, code)
        if key not in groups:
            groups[key] = {'kind': 'INDIVIDUAL', 'formation': form, 'backfield': backfield,
                           'distribution': distribution, 'role': role, 'route': code, 'routes': 0.,
                           'contexts': [], 'source_cells': 0, 'max_cell_games': 0.,
                           'max_cell_players': 0., 'source_feature_ids': []}
        g = groups[key]
        g['routes'] += n
        g['source_cells'] += 1
        g['max_cell_games'] = max(g['max_cell_games'], nonnegative_count(cell.get('games') or 0))
        g['max_cell_players'] = max(g['max_cell_players'], nonnegative_count(cell.get('players') or 0))
        if cell.get('feature_id'):
            g['source_feature_ids'].append(cell['feature_id'])
        g['contexts'].append({'down': cell.get('down'), 'motion': cell.get('motion'),
                              'routes': n, 'sparse': cell.get('sparse')})
    rows = sorted(groups.values(), key=lambda r: (r['formation'], r['backfield'] or '',
                  r['distribution'] or '', r['role'], r['route']))
    joint_groups = {}
    for cell in payload.get('joint_requirements', []):
        if any(k not in cell for k in ['plays', 'formation', 'distribution', 'observed_slots']):
            raise ValueError('Missing joint requirement fields')
        n = nonnegative_count(cell['plays'])
        if not n:
            continue
        slots = sorted([{'role': token(s.get('role')), 'route': token(s.get('route'))}
                        for s in cell['observed_slots']], key=lambda s: (s['role'], s['route']))
        if len(slots) < 2 or any(s['route'] not in FAMILY or s['role'] not in ['WR', 'SLOT_WR', 'TE', 'RB', 'FB'] for s in slots):
            ignored.append({'channel': 'PARTIAL_JOINT', 'reason': 'unsupported_joint_slot', 'plays': n,
                            'observed_slots': slots})
            continue
        form, backfield = formation_axes(cell['formation'])
        distribution = shape(cell['distribution'])
        key = (form, backfield, distribution, json.dumps(slots, sort_keys=True))
        if key not in joint_groups:
            joint_groups[key] = {'kind': 'PARTIAL_JOINT', 'formation': form, 'backfield': backfield,
                                 'distribution': distribution, 'observed_slots': slots,
                                 'plays': 0., 'complete_concept_verified': False, 'source_feature_ids': []}
        g = joint_groups[key]
        g['plays'] += n
        if cell.get('feature_id'):
            g['source_feature_ids'].append(cell['feature_id'])
    joint = sorted(joint_groups.values(), key=lambda r: (r['formation'], r['backfield'] or '',
                   r['distribution'] or '', json.dumps(r['observed_slots'], sort_keys=True)))
    channels = {'INDIVIDUAL': 1 - config.joint_weight if joint else 1.,
                'PARTIAL_JOINT': config.joint_weight if rows else 1.}
    for kind, values, count_field in [('INDIVIDUAL', rows, 'routes'), ('PARTIAL_JOINT', joint, 'plays')]:
        total = sum(r[count_field] for r in values)
        for r in values:
            dimensions = {k: r[k] for k in ['formation', 'backfield', 'distribution']}
            dimensions.update({k: r[k] for k in (['role', 'route'] if kind == 'INDIVIDUAL' else ['observed_slots'])})
            r['requirement_id'] = stable_feature(kind, dimensions)
            independent=payload.get('independent_support',{}).get(r['requirement_id'])
            r['independent_games']=nonnegative_count(independent['games']) if independent else None
            r['independent_players']=nonnegative_count(independent['players']) if independent else None
            r['independent_support_evidence']='SOURCE_KEY_UNIQUE_COUNTS' if independent else 'NOT_AVAILABLE'
            r['channel_weight'] = channels[kind]
            r['channel_mass'] = r[count_field] / total
            r['mass'] = r['channel_mass'] * channels[kind]
            r['support_weight'] = r[count_field] / (r[count_field] + config.prior_routes) if config.prior_routes else 1.
            r['support_semantics'] = 'COUNT_HEURISTIC_NOT_CALIBRATED_UNCERTAINTY'
            r['source_feature_ids'] = sorted(set(r['source_feature_ids']))
            if kind == 'INDIVIDUAL':
                r['route_mass'] = r['channel_mass']
                r['source_role_evidence'] = 'SOURCE_SLOT' if r['role'] == 'SLOT_WR' else 'SOURCE_WR_GEOMETRY_UNVERIFIED' if r['role'] == 'WR' else 'SOURCE_POSITION'
    return rows + joint, ignored


def compatibility(req, play, minimum_score=.65):
    form_strength = formation_strength(req, play)
    if form_strength is None:
        return None
    known_shape, wanted = play['derived_distribution'], req['distribution']
    if known_shape and wanted and known_shape != wanted:
        return None
    if req.get('kind') == 'PARTIAL_JOINT':
        slots, observed = play['canonical_routes'], req['observed_slots']
        if len(slots) < len(observed):
            return None
        scores = np.zeros((len(observed), len(slots)))
        details = {}
        for i, s in enumerate(observed):
            for j, r in enumerate(slots):
                detail = compatibility({**req, **s, 'kind': 'INDIVIDUAL'},
                                       {**play, 'canonical_routes': [r], 'route_tags': []}, minimum_score)
                if detail:
                    scores[i, j] = detail['score']
                    details[i, j] = detail
        assignment = None
        for threshold in sorted(set(scores.flat), reverse=True):
            if threshold <= 0:
                break
            ii, jj = linear_sum_assignment(np.where(scores >= threshold, -scores, 1e6))
            if np.all(scores[ii, jj] >= threshold):
                assignment = list(zip(ii, jj))
                break
        if assignment is None:
            return None
        score = float(min(scores[i, j] for i, j in assignment))
        return {'score': score, 'evidence': 'PARTIAL_JOINT_INJECTIVE_PUBLISHER_SLOT_ASSIGNMENT',
                'tier': 'PARTIAL_JOINT', 'projected_count': 1. if score >= minimum_score else 0.,
                'complete_concept_verified': False,
                'slot_assignments': [{'observed': observed[i], 'catalog_slot_index': int(j),
                                     **details[i, j]} for i, j in assignment]}
    shape_strength = 1. if known_shape and wanted else .35
    best, count = None, 0.
    for r in play['canonical_routes']:
        if r['code'] is None:
            continue
        if r['code'] == req['route']:
            route_strength, tier = r['route_alias_strength'], 'EXACT_OR_ALIAS'
        elif FAMILY.get(r['code']) == FAMILY.get(req['route']):
            route_strength, tier = .5 * r['route_alias_strength'], 'FAMILY_ONLY'
        else:
            continue
        role, pos = req['role'], r['position']
        if role in ['WR', 'SLOT_WR']:
            if pos != 'WR':
                continue
            if role == 'WR':
                role_strength, role_evidence = .75, 'SOURCE_WR_GENERIC_NOT_VERIFIED_OUTSIDE'
            else:
                role_strength = 1. if r['role_proxy'] == 'SLOT_WR' else .5 if r['role_proxy'] is None else .35
                role_evidence = 'SOURCE_SLOT_VS_CATALOG_COORDINATE_PROXY'
        elif role != pos:
            continue
        else:
            role_strength, role_evidence = 1., 'PUBLISHER_POSITION'
        score = (.55 * route_strength + .2 * role_strength + .15 * form_strength + .1 * shape_strength) * .85
        if score >= minimum_score:
            count += 1.  # Accepted distinct slots, not subthreshold fuzzy route counts.
        detail = {'score': score, 'route_strength': route_strength, 'role_strength': role_strength,
                  'formation_strength': form_strength, 'shape_strength': shape_strength,
                  'evidence': 'PUBLISHER_ROUTE_ANNOTATION_WITH_COORDINATE_ROLE_PROXY', 'tier': tier,
                  'slot': r['depth_chart_slot'], 'source_route': r['source_route'],
                  'source_confidence': r['source_confidence'], 'role_evidence': role_evidence,
                  'distribution_evidence': 'CATALOG_COORDINATE_PROXY' if known_shape else 'UNKNOWN',
                  'backfield_evidence': play.get('backfield_evidence', 'UNKNOWN'),
                  'parent_family_unknown': form_strength < 1}
        if best is None or score > best['score']:
            best = detail
    if best is None:
        exact = [s for c, s in play['route_tags'] if c == req['route']]
        if exact:
            best = {'score': (.55 * max(exact) + .1 + .15 * form_strength + .1 * shape_strength) * .55,
                    'evidence': 'PUBLIC_PLAY_TAG_ROLE_UNKNOWN', 'tier': 'TAG_ONLY',
                    'role_evidence': 'UNKNOWN', 'slot': None}
            # Presence tags do not establish receiver multiplicity or executable assignments.
            count = 0.
    if best is not None:
        best['projected_count'] = count
    return best


def projection_blocks(rows, counts):
    """Each enabled channel has its own distribution and explicit unknown sink."""
    blocks, weights, target, matrices = [], [], [], []
    for kind in ['INDIVIDUAL', 'PARTIAL_JOINT']:
        indices = [i for i, r in enumerate(rows) if r['kind'] == kind and r['channel_weight'] > 0]
        if not indices:
            continue
        raw = counts[indices]
        denominator = raw.sum(axis=0)
        matrix = np.divide(raw, denominator, out=np.zeros_like(raw), where=denominator > 0)
        matrix = np.vstack([matrix, (denominator == 0).astype(float)])
        cw = rows[indices[0]]['channel_weight']
        local_target = [rows[i]['channel_mass'] for i in indices] + [0.]
        matrices.append(matrix)
        target.extend(local_target)
        weights.extend([cw * (.5 + .5 * rows[i]['support_weight']) for i in indices] + [cw])
        blocks.append({'kind': kind, 'indices': indices, 'channel_weight': cw, 'length': len(indices) + 1})
    return np.vstack(matrices), np.array(target), np.array(weights), blocks


def curate(rows, matches, index, config):
    ids = sorted({m['play_id'] for r, values in zip(rows, matches) if r['channel_weight'] > 0
                  for m in values if m['accepted'] and m['projected_count'] > 0}
                 | set(map(str, config.locked_play_ids)))
    empty = {'plays': [], 'coverage_mass': 0., 'unmatched_mass': 1., 'selected_book_coverage': 0.,
             'positive_weight_support_coverage': 0.}
    if not ids:
        return {**empty, 'status': 'NO_SUPPORTED_CANDIDATES'}
    score, counts = np.zeros((len(rows), len(ids))), np.zeros((len(rows), len(ids)))
    lookup = {p: j for j, p in enumerate(ids)}
    for i, values in enumerate(matches):
        for m in values:
            if m['play_id'] in lookup and m['accepted'] and m['projected_count'] > 0:
                j = lookup[m['play_id']]
                score[i, j], counts[i, j] = m['score'], m['projected_count']
    enabled = np.array([r['channel_weight'] > 0 for r in rows])
    if any(counts[enabled, lookup[str(p)]].sum() == 0 for p in config.locked_play_ids):
        return {**empty, 'status': 'LOCKED_PLAY_HAS_NO_ENABLED_SUPPORTED_FEATURES'}
    mass = np.array([r['mass'] for r in rows])
    support = np.array([r['support_weight'] for r in rows])
    selection_weights = mass * (.5 + .5 * support)
    selected = list(dict.fromkeys(lookup[str(p)] for p in config.locked_play_ids))
    sets = {index.plays[ids[j]]['set_id'] for j in selected}
    if len(sets) > config.max_sets:
        raise ValueError('Locked plays exceed set budget')
    sizes = defaultdict(int)
    for pid in ids:
        sizes[index.plays[pid]['set_id']] += 1
    needed = math.ceil(1 / config.max_play_weight)

    def capacity(chosen_sets):
        available = sum(sizes[s] for s in chosen_sets)
        extras = sorted((n for s, n in sizes.items() if s not in chosen_sets), reverse=True)
        available += sum(extras[:config.max_sets - len(chosen_sets)])
        return min(config.max_plays, available)

    if capacity(sets) < needed:
        return {**empty, 'status': 'WEIGHT_CAP_INFEASIBLE_WITH_SUPPORTED_CANDIDATES',
                'proof': {'attainable_play_capacity': capacity(sets), 'required_plays': needed}}
    covered = score[:, selected].max(axis=1) if selected else np.zeros(len(rows))
    while len(selected) < min(config.max_plays, len(ids)):
        choices = []
        for j, pid in enumerate(ids):
            if j in selected:
                continue
            next_sets = sets | {index.plays[pid]['set_id']}
            if len(next_sets) > config.max_sets or capacity(next_sets) < needed:
                continue
            gain = float(selection_weights @ np.maximum(score[:, j] - covered, 0))
            gain /= 1.05 if len(next_sets) > len(sets) else 1.
            choices.append((gain, pid, j))
        if not choices:
            break
        gain, pid, j = sorted(choices, key=lambda x: (-x[0], x[1]))[0]
        if gain <= 1e-12 and len(selected) >= needed:
            break
        selected.append(j)
        sets.add(index.plays[pid]['set_id'])
        covered = np.maximum(covered, score[:, j])
    if len(selected) < needed:
        return {**empty, 'status': 'SEARCH_EXHAUSTED_WITHOUT_FEASIBLE_BOOK'}
    locked = set(map(str, config.locked_play_ids))
    pruned = []
    while True:
        matrix, target, objective_weights, blocks = projection_blocks(rows, counts[:, selected])

        def loss(w):
            error = matrix @ w - target
            return float(np.dot(objective_weights * error, error) + config.ridge * np.dot(w, w))

        def jac(w):
            return 2 * matrix.T @ (objective_weights * (matrix @ w - target)) + 2 * config.ridge * w

        fit = minimize(loss, np.full(len(selected), 1 / len(selected)), jac=jac, method='SLSQP',
                       bounds=[(0, config.max_play_weight)] * len(selected),
                       constraints=[{'type': 'eq', 'fun': lambda w: w.sum() - 1,
                                     'jac': lambda w: np.ones(len(w))}],
                       options={'ftol': 1e-11, 'maxiter': 400})
        if not fit.success:
            raise RuntimeError('Mixture optimization failed: ' + fit.message)
        removable = [j for j, w in zip(selected, fit.x)
                     if w <= config.positive_weight_threshold and ids[j] not in locked]
        if not config.prune_zero_weight or not removable or len(selected) - len(removable) < needed:
            break
        pruned.extend(ids[j] for j in removable)
        selected = [j for j in selected if j not in removable]
    fitted = matrix @ fit.x
    residuals, channel_diagnostics, tv, offset = [], {}, 0., 0
    for block in blocks:
        indices, cw = block['indices'], block['channel_weight']
        local = fitted[offset:offset + block['length']]
        local_target = target[offset:offset + block['length']]
        channel_tv = float(np.abs(local - local_target).sum() / 2)
        tv += cw * channel_tv
        channel_diagnostics[block['kind']] = {'weight': cw, 'total_variation': channel_tv,
                                              'projected_unknown_mass': float(local[-1])}
        for i, f in zip(indices, local[:-1]):
            residuals.append({'requirement_id': rows[i]['requirement_id'],
                              'observed_mass': rows[i]['mass'], 'projected_mass': float(cw * f),
                              'residual': float(cw * f - rows[i]['mass'])})
        offset += block['length']
    available = score[:, selected].max(axis=1) > 0
    positive = [j for j, w in zip(selected, fit.x) if w > config.positive_weight_threshold]
    effective = score[:, positive].max(axis=1) > 0 if positive else np.zeros(len(rows), dtype=bool)
    chosen = []
    for j, w in zip(selected, fit.x):
        p = index.plays[ids[j]]
        chosen.append({'play_id': p['id'], 'name': p['name'], 'formation': p['formation'],
                       'set': p['set_name'], 'set_id': p['set_id'], 'personnel': p['personnel'],
                       'proposed_weight': float(w), 'source_url': p['source_url'],
                       'route_evidence': p['route_evidence'], 'derived_distribution': p['derived_distribution'],
                       'backfield': p['backfield'], 'backfield_evidence': p['backfield_evidence'],
                       'assignments': [{**r, 'canonical_route': route(r.get('routeName'))[0],
                                        'evidence': 'PUBLISHER_ANNOTATION_NOT_NFL_VERIFICATION'} for r in p['routes']],
                       'protection_rules': None, 'read_progression': None,
                       'diagram_version_mismatch': p.get('diagram_version_mismatch', False),
                       'supported_requirement_ids': [rows[i]['requirement_id'] for i in np.flatnonzero(score[:, j] > 0)]})
    selected_coverage, effective_coverage = float(mass[available].sum()), float(mass[effective].sum())
    return {'status': 'CURATED', 'plays': sorted(chosen, key=lambda p: (-p['proposed_weight'], p['play_id'])),
            'set_count': len({p['set_id'] for p in chosen}), 'coverage_mass': selected_coverage,
            'selected_book_coverage': selected_coverage, 'positive_weight_support_coverage': effective_coverage,
            'positive_weight_threshold': config.positive_weight_threshold,
            'unmatched_mass': 1 - selected_coverage, 'projected_total_variation': tv,
            'channel_diagnostics': channel_diagnostics, 'fit_objective': float(fit.fun),
            'optimizer_iterations': int(fit.nit), 'feature_residuals': residuals,
            'pruned_zero_weight_play_ids': sorted(pruned),
            'weight_semantics': 'ASSUMED_PROXY_MIX_NOT_IDENTIFIED_NFL_CALL_FREQUENCY',
            'objective_semantics': 'SEPARATE_CHANNEL_RECONSTRUCTION_NOT_EPA_OR_WIN_VALUE'}


def match(payload, index, config=Config()):
    config.validate()
    start = time.perf_counter()
    rows, ignored = requirements(payload, config)
    locked = set(map(str, config.locked_play_ids))
    if locked - set(index.plays):
        raise ValueError('Unknown locked play IDs')
    for pid in locked:
        p = index.plays[pid]
        if config.allowed_personnel and p['personnel'] not in config.allowed_personnel:
            raise ValueError('Locked play violates personnel filter')
        if not p['pure_pass'] and not (config.include_hybrid and p['hybrid']):
            raise ValueError('Locked play is not an allowed passing candidate')
    matches, evaluations = [], 0
    for req in rows:
        values = []
        for p in index.candidates(req, config):
            evaluations += 1
            detail = compatibility(req, p, config.minimum_score)
            if detail:
                values.append({'play_id': p['id'], 'play_name': p['name'], 'source_url': p['source_url'],
                               **detail, 'accepted': detail['score'] >= config.minimum_score})
        values.sort(key=lambda m: (-m['score'], m['play_id']))
        matches.append(values)
    if not any(r['channel_weight'] > 0 for r in rows):
        book = {'status': 'NO_ELIGIBLE_OBSERVATIONS', 'plays': [], 'coverage_mass': 0.,
                'selected_book_coverage': 0., 'positive_weight_support_coverage': 0., 'unmatched_mass': 1.}
    else:
        book = curate(rows, matches, index, config)
    selected = {p['play_id'] for p in book['plays']}
    positive = {p['play_id'] for p in book['plays'] if p['proposed_weight'] > config.positive_weight_threshold}
    for name, ids in [('route_coverage_mass', selected), ('positive_weight_route_coverage', positive)]:
        book[name] = sum(r['route_mass'] for r, values in zip(rows, matches) if r['kind'] == 'INDIVIDUAL'
                         and any(v['play_id'] in ids and v['accepted'] and v['projected_count'] > 0 for v in values))
    included = sum(r['routes'] for r in rows if r['kind'] == 'INDIVIDUAL')
    excluded = sum(r.get('routes', 0) for r in ignored)
    fraction = included / (included + excluded) if included + excluded else 0.
    book['overall_input_coverage_mass'] = book['route_coverage_mass'] * fraction
    for r, values in zip(rows, matches):
        r['ranked_candidates'] = values[:config.top_k]
        r['accepted_candidate_count'] = sum(v['accepted'] for v in values)
        r['projection_candidate_count']=sum(v['accepted'] and v['projected_count']>0 for v in values)
        r['status'] = 'SUPPORTED_COMPATIBILITY' if r['projection_candidate_count'] else 'ABSTAIN'
        r['top_score_margin'] = values[0]['score'] - values[1]['score'] if len(values) > 1 else None
        r['abstention_reason'] = None if r['projection_candidate_count'] else 'FORMATION_TRANSLATION_GAP' if r['formation'].startswith('UNSUPPORTED:') else 'CATALOG_OR_ROUTE_EVIDENCE_GAP'
    return {'schema_version': VERSION, 'team': payload['team'], 'season': payload['season'],
            'status': book['status'], 'catalog_sha256': index.fingerprint, 'config': asdict(config),
            'source_snapshot_id': payload.get('source_snapshot_id'), 'source_adapter_version': payload.get('adapter_version'),
            'source_capabilities': payload.get('capabilities', ['LEGACY_INPUT']), 'coverage': payload.get('coverage', []),
            'included_route_fraction': fraction, 'ignored_route_fraction': 1 - fraction,
            'ignored_joint_play_count': sum(r.get('plays', 0) for r in ignored),
            'included_joint_play_count':sum(r['plays'] for r in rows if r['kind']=='PARTIAL_JOINT'),
            'score_semantics': 'HEURISTIC_COMPATIBILITY_NOT_CALIBRATED_PROBABILITY',
            'requirements': rows, 'ignored': ignored, 'playbook': book,
            'runtime': {'seconds': time.perf_counter() - start, 'candidate_evaluations': evaluations,
                        'dense_evaluation_count': len(rows) * len(index.plays)},
            'limitations': ['Supplied prospect cohort is not the full offense',
                           'Source WR role is not verified outside alignment; catalog geometry is a proxy',
                           'Empty backfield does not identify its parent formation family',
                           'Partial joint observations are not verified full NFL concepts',
                           'Unknown progression, protection, adjustments and diagram-version warnings remain explicit',
                           'Count support weights are not calibrated uncertainty',
                           'Outcomes are excluded; weights do not predict EPA or win value',
                           'Exports support our model and manual assembly, not native Madden binary import']}
