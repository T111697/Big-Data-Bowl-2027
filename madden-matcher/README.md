# Observed NFL tendencies → custom Madden playbook proxy

This stage consumes the corrected `1.1.0` exports from PR #1, preserves `WR` and `SLOT_WR`, and produces modeling-tool JSON plus a CSV for manual Madden assembly. It does not need film. It does not reconstruct actual NFL calls, identify true call frequencies, estimate causal player effects, or predict EPA/wins.

## Quick start

```bash
pip install -r nfl-team-index/requirements.txt -r madden-matcher/requirements.txt

# Public catalog, bounded concurrent acquisition with cached page hashes.
python madden-matcher/acquire.py --details 160 --workers 4

# Optional but recommended: retain partial same-play receiver combinations.
# Reads the existing TeamStore without changing its tables or schema.
python madden-matcher/build_inputs.py --snapshot nfl-team-index/snapshot-v2

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python madden-matcher/cli.py \
  --inputs madden-matcher/data/inputs --teams ALL --out madden-matcher/outputs/run

python madden-matcher/validate.py --outputs madden-matcher/outputs/run
python -m unittest discover -s madden-matcher -p 'test_*.py'
```

The existing `nfl-team-index/exports-v2` inputs also work without partial-combination enrichment. No Kaggle token is needed for this stage once the local index exists. Source data and real outputs stay outside git. To run without source datasets or network access:

```bash
python madden-matcher/cli.py --catalog madden-matcher/examples/catalog.json \
  --inputs madden-matcher/examples/inputs --out /tmp/madden-example
```

The example is synthetic software data, not real Madden or NFL evidence.

## Catalog boundaries

The acquisition adapter reads publicly rendered JSON from Madden Tools pages labeled Madden 27. It takes the formation sets listed by the Bears book, then the union of plays on those public set pages. Those pages contain plays from other books, so **the resulting pool is not a verified Bears playbook**. It is a bounded candidate pool for a custom playbook.

The initial snapshot has 1,364 plays in 44 sets, including 919 pure passing candidates. A deterministic round-robin sample across sets fetched 160 passing detail pages; 159 contained route annotations. The other plays have tags only. This limits the book's breadth and introduces catalog sampling bias. Increase `--details` or supply a validated compatible catalog to expand it. Zero acquisition failures were recorded for this snapshot; future failures are recorded rather than concealed.

Only play identities, formation/personnel metadata, tags and route annotation facts are retained. Editorial analysis and claimed read progressions are excluded. Public annotations labeled `game-verified` or `owner-confirmed` remain publisher claims, not independent verification. Some diagram asset paths reference Madden 26; these are flagged, and image geometry is not imported. Final play availability and assignments must be checked in the user's Madden version/platform. No authentication, subscription content or private playbook is required.

## Matching logic

1. Aggregate observed route cells across down/motion into formation family × receiver distribution × source role × route. Preserve original contexts and sparse flags for audit. These are conditional counts within the supplied prospect cohort, not full-offense tendencies.
2. Normalize aliases with explicit strength reductions. `Curl` can support `HITCH`; `Comeback` receives weaker support. Option/composite routes and protection labels remain unknown unless explicitly modeled. Formation families must agree. Known receiver-distribution contradictions reject a candidate.
3. Derive outside/slot and distribution proxies from publisher coordinates. Depth-chart WR3 is never automatically labeled slot. Proxies cannot establish on/off-line legality, hash/boundary alignment, actual NFL responsibilities or motion compatibility.
4. Score individual matches using route strength, role strength, formation and distribution evidence. The score is a transparent heuristic, **not a calibrated probability**. An annotation score is `0.85 × (0.55 route + 0.20 role + 0.15 formation + 0.10 distribution)`. Unknown distribution contributes 0.35; imperfect role/alias support is reduced. Tag-only evidence is downweighted by 0.55 instead of 0.85 and has unknown role.
5. For partial same-play observations, solve an injective receiver-slot assignment. Each observed receiver gets a distinct catalog slot. Maximize the worst pair compatibility, then total compatibility. A combination passes only if every assigned pair clears the threshold. Unobserved receivers and adjustments stay unknown; `complete_concept_verified` is always false.
6. Rank candidate explanations deterministically. Default threshold 0.65 makes tag-only and family-only evidence insufficient for acceptance. Missing evidence produces abstention. `top_k` controls reported rankings, not the optimizer's candidate pool.

At the default threshold, provable score upper bounds prune family-only candidates (maximum 0.61625) and tag-only candidates (maximum 0.495). Formation, distribution, position and pass/run filters further reduce work. Lower custom thresholds automatically restore broader candidate pools. Validation compares indexed and exhaustive **accepted** candidates; rejected low-score rows need not appear in rankings.

Down, motion, target outcomes and defensive context are preserved upstream, but not asserted as catalog compatibility. Their Madden-side labels have not been independently established. Intended protection, QB reads, option decisions and timing remain null/unknown. Target EPA, target rate and positive outcomes never influence selection or weights, preventing outcome-based selection from masquerading as concept identification.

## Curation and weights

A greedy facility-coverage pass selects a compact set of accepted candidates, subject to play and formation-set budgets. Observed feature mass and `n/(n+20)` reliability control coverage importance. A small penalty discourages opening unnecessary sets. Locked/excluded plays and personnel restrictions are enforced. This is an approximation, not a globally optimal discrete book search.

A second SLSQP optimization fits nonnegative play-mixture weights that sum to one, with a per-play cap and ridge regularization. It minimizes weighted squared error between observed and projected compatible route features. Defaults are 24 plays, 8 sets, a 0.35 per-play weight cap, and ridge `1e-5`.

With enriched inputs, individual features receive 80% of the target feature mass and partial-combination features 20%. Catalog compatible-route multiplicities and joint-presence features are normalized within each selected play to construct the projection matrix. This is a **chosen reconstruction representation**, not an identified generative model of NFL play calls. A single route can contribute to alternative individual features; joint constraints are where distinct slots are enforced. Untargeted/unobserved catalog routes and omitted NFL receivers are not silently counted as observed.

Interpret weights as an assumed proxy mix for simulations, not a recommended live play-call rate. A high compatibility or low residual says nothing by itself about football value. Multiple books can explain the same partial observations. Budget/cap infeasibility and unsupported locks produce explicit errors/statuses rather than fabricated weights.

Customize all model parameters through `--config madden-matcher/examples/config.json`. Config-file fields take precedence over individual flags. Common flags include `--max-plays`, `--max-sets`, `--personnel 11,12`, repeated `--lock-id` and `--exclude-id`, and `--teams CHI,GB`. Set `joint_weight` to zero for an individual-only sensitivity run.

## Outputs and imports

| Output | Use |
| --- | --- |
| `TEAM-YEAR-custom-playbook.json` | Modeling-tool import: selected plays, proposed weights, personnel, route assignments, evidence, ranked requirements, residuals and unknowns |
| `TEAM-YEAR-custom-playbook.csv` | Human assembly worksheet with formation/set/play names and source links |
| `batch-summary.json` | Team-season status, route and weighted-feature coverage, residuals, cache/runtime |
| Validation report | Constraint checks, accepted-candidate parity, cache reuse, measured runtime |

`route_coverage_mass` measures supported individual route mass within supported canonical input routes. `overall_input_coverage_mass` also accounts for unsupported source route categories. `coverage_mass` includes the configurable joint feature channel and must not be described as full-offense coverage. `projected_total_variation` is a distance between normalized weighted feature vectors, not prediction accuracy.

The JSON is the contract for our own model. **No native Madden binary import/export is implemented.** The CSV supports manual custom-playbook assembly. The modeling UI should expose source scope, abstentions, coverage and unknown assignments alongside every proxy book.

## Reuse, optimization and validation

Compile the catalog once per batch. Inverted indexes prune formations/routes before scoring; selected-play matrices use vectorized NumPy operations and an analytic optimizer gradient. Output caching hashes input bytes, semantic catalog contents, configuration and the algorithm version. Acquisition timestamps do not invalidate unchanged catalog semantics. Changes to routes or other play metadata do. Bump `VERSION` when changing scoring/curation behavior. Warm runs reuse complete per-team outputs and regenerate assembly sheets.

The offline tests cover route unknowns, role/formation conflicts, injective assignments, constraints and infeasibility, deterministic ordering, outcome exclusion, index parity and cache invalidation. GitHub CI runs these alongside PR #1 tests and a synthetic CLI smoke test. `validate.py` audits every real output and benchmarks a Chicago profile against exhaustive accepted-candidate matching. It does not establish NFL concept accuracy: that needs independent labels/film later.

## Deferred extension seams

A future verified catalog or film adapter can provide exact alignment, full concept labels, motion, protection and QB progression without changing TeamStore. Keep publisher proxies and verified labels as separate evidence channels. Roster/player assignment, opponent-conditioned performance, injury risk and weather simulation should consume these explicit unknowns rather than assume they are established by this stage.
