# Observed NFL tendencies → Madden scheme proxy

The hardened matcher consumes corrected NFL mapping schemas 1.1.0 (legacy) or 1.2.0 (identified producer), and emits immutable, auditable modeling runs. It requires no film. Matches are scheme proxies, not verified NFL calls, scouting grades, causal player effects or predictions of EPA/wins.

## Run

```bash
pip install -r nfl-team-index/requirements.txt -r madden-matcher/requirements.txt
python nfl-team-index/pipeline.py --tracking-teams CHI
python nfl-team-index/enrich.py
python nfl-team-index/geometry.py
python nfl-team-index/context_audit.py
python nfl-team-index/validate_export.py
python madden-matcher/build_inputs.py

# Public pages only. TTL-aware revalidation; refresh forces a new fetch.
python madden-matcher/acquire.py --details 160 --cache-mode revalidate --ttl 86400

OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python madden-matcher/cli.py --teams ALL
python madden-matcher/validate.py --outputs madden-matcher/outputs \
  --report madden-matcher/validation.json
python -m unittest discover -s madden-matcher -p 'test_*.py'
```

Shared defaults in `research_contracts.py` are snapshot-v3, exports-v3 and inputs-v3. Directory names never determine schema version. Override `--out`, `--snapshot`, `--exports` and `--inputs` explicitly for alternate source roots/runs. The un-enriched exports-v3 also work as matcher inputs with their producer manifest. `--tracking-teams NONE` is a supported metadata-only path; geometry publishes an empty normal-schema table and NOT_MATERIALIZED status. No Kaggle token is needed after source collection.

Offline synthetic demonstration:

```bash
python madden-matcher/cli.py --catalog madden-matcher/examples/catalog.json \
  --inputs madden-matcher/examples/inputs --out /tmp/madden-example
python madden-matcher/validate.py --outputs /tmp/madden-example --report /tmp/example-validation.json
```

The example contains no real NFL or Madden evidence. Source data, credentials, snapshots and real outputs remain outside git.

## Evidence and formation axes

Public Madden Tools pages supply formation/personnel/tags and, where available, receiver route annotations. The initial catalog is the union of plays from 44 formation sets listed by the Bears book. It is not verified Bears-only play membership. It has 1,364 plays, 919 pure passing candidates and 159 plays with detailed annotations from 160 fetched detail pages. Hardening comparisons deliberately hold this original catalog constant to isolate the algorithm changes.

Source EMPTY means empty backfield, with its parent formation unknown. It matches publisher empty-set proxies across parent families, with reduced formation evidence. It is not blindly converted to Shotgun. Other known formation families must agree; known distribution contradictions reject candidates. Unsupported formations receive explicit translation-gap abstention reasons.

Source WR is treated as a generic receiver role, not verified outside alignment. SLOT_WR remains a source slot role compared with the catalog's coordinate-based slot proxy. Depth-chart WR3 never automatically means slot. Set-name and coordinate proxies do not establish NFL alignment, legal on/off-line status, motion, intended adjustments or full concepts. Publisher `game-verified` labels remain publisher claims; diagram paths referring to Madden 26 are flagged. Check play availability and assignments in the intended game version/platform.

## Scoring and accepted slot incidence

Individual score is `0.85 × (0.55 route + 0.20 role + 0.15 formation + 0.10 distribution)` for publisher annotation evidence. Route aliases reduce route strength; generic source WR uses 0.75 role strength independent of whether its catalog proxy is inside/outside. Empty-backfield translation uses 0.75 formation strength, preserving parent-family uncertainty. Unknown distribution contributes 0.35. Scores are heuristic compatibility, not calibrated probabilities.

A partial same-play combination uses an injective assignment: one distinct catalog receiver slot per observed receiver. Maximize the worst pair compatibility, then the sum. Unobserved receivers remain unknown, and complete_concept_verified stays false.

At the default threshold 0.65, family-only (upper bound 0.61625) and tag-only (upper bound 0.495) evidence cannot be accepted. Candidate indexing safely prunes them; lower thresholds restore the wider ranking pool. Only **distinct slots whose individual score meets the actual run threshold** contribute projected counts. Subthreshold family/role evidence cannot inflate multiplicity. Presence tags never establish executable slot counts, even when a custom threshold accepts their diagnostic compatibility score.

Down/motion/sparse contexts and exact independent player/game counts are retained when available. Support weight `n/(n+20)` is explicitly a count heuristic, not calibrated uncertainty. All target outcomes are excluded from scoring, selection and weights. Protection, QB progression and intended adjustments remain null. Missing/unsupported joint observations are reported with reasons and play counts instead of silently disappearing.

## Selection and separate-channel reconstruction

A greedy facility-coverage approximation chooses accepted candidates under play/set budgets, locks/exclusions and personnel filters. Before each move, an exact attainable-play capacity bound checks whether the remaining set budget can still meet the mixture cap. If the bound proves insufficient, an infeasibility status includes the proof; unresolved search failure is a distinct status. This capacity check proves count/set/cap feasibility, not global coverage optimality.

SLSQP fits nonnegative weights summing to 1 with a per-play cap and ridge. Individual and partial-joint features have **separate normalized projection blocks**, each with an explicit unknown sink for plays unsupported in that channel. Channel weights multiply loss terms, not inconsistent counts in one global denominator. Weight 0 disables a channel; weight 1 permits joint-only reconstruction. With no joint observations, the individual channel receives the full weight. Duplicate canonical joint cells are consolidated.

Defaults: 24 plays, 8 sets, max play weight 0.35, ridge 1e-5, joint loss weight 0.20. Zero/negligible-weight nonlocked plays are pruned and the remaining book refitted when the cap permits. Locks may remain at zero weight. This is a chosen partial-feature reconstruction model; weights are an assumed simulation proxy mixture, not identified NFL call frequencies or recommended live play-call rates. Different books can explain the same partial observations. Geometry, film and downstream value models are separate evidence stages.

All Config fields can be overridden with `--config madden-matcher/examples/config.json`; the file takes precedence over individual flags. Other flags include `--max-plays`, `--max-sets`, `--personnel 11,12`, repeated `--lock-id`/`--exclude-id`, and `--teams CHI,GB`. Missing directories, empty input sets, absent requested teams, duplicate profiles, invalid schemas and hash mismatches fail explicitly.

## Immutable runs and imports

Each content/configuration identity gets a run directory. Publication writes all files into staging, then renames the complete directory and atomically updates the root pointer. Previous runs remain available. A frozen copy of the catalog, every selected input, and its source-key lineage accompany the outputs. Cached runs verify every artifact hash before reuse. Catalog compilation is cached in process with a bounded cache. Changed source bytes, lineage, catalog semantics, configuration or algorithm version produce a new run ID.

| Artifact | Purpose |
| --- | --- |
| Root run-manifest.json | Pointer and hash of the exact published run |
| runs/ID/run-manifest.json | Frozen identity, config, requested profiles, source inputs and artifact hashes |
| runs/ID/TEAM-YEAR-custom-playbook.json | Modeling-tool import: weights, assignments, evidence, ranked requirements and diagnostics |
| Matching CSV | Manual Madden assembly worksheet |
| runs/ID/inputs and catalog.json | Reproducible frozen inputs, memberships and public catalog |
| Root batch-summary.json | Last invocation status/timing, including cache hits |

Use `cli.resolve_run(output_dir)` to resolve the current immutable run. Consumers must follow the manifest rather than glob an output directory. Validation reads that exact run and verifies its identity/configuration/artifacts; cache benchmarks run in a temporary directory with the recorded configuration. Validation never rewrites the inspected run. Reports must be saved outside the output directory.

These JSON files are for our modeling tool. No native Madden binary import is implemented; CSV supports manual assembly. A consuming UI must show source scope, unknown responsibilities, diagram warnings, abstentions and both coverage metrics.

## Diagnostics and lineage

- selected_book_coverage: weighted observed-feature support available somewhere in the selected book.
- positive_weight_support_coverage: support from plays above the recorded numerical threshold.
- route_coverage_mass: selected-book individual route support within canonical observed routes.
- positive_weight_route_coverage: corresponding support in the fitted positive-weight mix.
- overall_input_coverage_mass: selected individual support including unsupported source route categories in the denominator.
- projected_total_variation: channel-weighted feature distance, including explicit unknown sinks; not NFL concept accuracy. Version 1 distances are not comparable because the normalization changed.
- Stable requirement/source feature IDs, snapshot ID, adapter version and frozen memberships allow tracing selected requirements back to player/game/play keys. Source counts are cohort-conditional, never full-offense coverage.

## Catalog refresh and expansion

`--cache-mode revalidate` refetches pages older than TTL or with unknown fetch time. `refresh` refetches all requested pages. `snapshot` only uses existing hash-valid pages and fails on missing/corrupt pages. Source records distinguish fetched time from observed/run time; legacy pages retain unknown fetch time rather than receiving an invented date.

Detail-page selection now balances formation, set, personnel and route-tag metadata coverage with deterministic tie-breaking. Increasing `--details` can expand evidence; metadata coverage is not proof of representative route coverage. Paid analysis/private pages and editorial text are not imported. Acquisition failures remain explicit in the catalog envelope.

## Validation boundaries

CI runs the original tests, review counterexamples, failure/corruption tests, source-to-adapter-to-matcher integration for tracking/NONE, and a CLI smoke run. Real validation audits every requested profile and benchmarks indexed/exhaustive accepted candidates with the exact config. Source content fingerprints and staged SQLite writes protect provenance; stages are serialized and snapshot DB/state hash checks fail closed after interrupted publication.

No independent full-NFL-concept labels exist here. No matching accuracy, injury risk, causal player effect, expected EPA, draft value or season win improvement is established. Future uncertainty work should resample by player/game and distinguish cohort missingness from catalog and model uncertainty; current support counts expose these units without claiming that analysis has already been done.
