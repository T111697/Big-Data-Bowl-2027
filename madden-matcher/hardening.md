# Hardening review resolutions

This change set addresses the combined review of PRs #1 and #2 before player-fit modeling. It changes reconstruction mathematics and export/run interfaces; regenerate previous weights instead of treating the old mixtures as stable inputs.

| Review issue | Resolution | Regression coverage |
| --- | --- | --- |
| Inconsistent joint/individual normalization | Separate projection distributions, weighted losses, explicit unknown sinks; disabled channels removed | Joint weight 0, 0.2 and 1 all reconstruct identical compatible fixture at zero feature distance |
| EMPTY source/catalog mismatch | Empty-backfield axis with unknown parent, publisher empty-set proxy and reduced evidence | EMPTY matches empty catalog sets, does not match filled sets, never invents parent family |
| Validator overwrites custom results | Frozen run manifest; read-only inspection; temporary benchmarks with recorded config | Custom 3-play/1-set run and subset validation preserve every original output byte |
| False greedy infeasibility | Attainable capacity checks before moves; proof status separated from search failure | One-play A vs three-play B set counterexample now selects feasible B |
| Weak slot evidence inflates projected counts | Per-slot threshold acceptance before multiplicity | GO plus subthreshold POST projects one GO slot, not 1.5 |
| Selected vs fitted coverage conflation | Separate availability and positive-weight coverage; prune/refit negligible unlocked plays | Metrics independently reconcile with positive play memberships |
| Paths/missing/stale inputs | Shared defaults, explicit schemas/capabilities, source manifest, immutable exact-profile runs | Missing folders/teams fail; subset run ignores previous run's profiles |
| NONE tracking breaks geometry/checks | Empty normal-schema geometry, explicit absence capability and conditional checks | Full source pipeline runs with NONE and CHI tracking; missing promised shards fail |

## Additional improvements applied

- Unknown motion remains UNKNOWN; unknown target EPA remains missing rather than negative.
- Conflicting snap coordinates are rejected; identical duplicates are collapsed.
- Source and tracking artifact content hashes; identified snapshots; staged SQLite writes and failed-stage rollback.
- Bounded game-batch geometry with one shard decompression pass; batched trajectory retrieval and bounded frame cache.
- Stable source/requirement IDs, frozen player/game/play lineage, exact independent player/game counts and explicit count-support semantics.
- Generic source WR no longer receives verified-outside treatment.
- Versioned joint producer and count reconciliation; unsupported joint counts/reasons remain visible.
- TTL/refresh/snapshot catalog controls, known versus unknown fetch timestamps, source-bound acquisition failures and deterministic metadata-coverage detail selection.
- In-process catalog compilation cache; algorithm/config/input/lineage/implementation-bound immutable result reuse.
- Explicit errors replace production validation assertions; true integration and corruption checks run in CI.

## Interfaces and operational limits

NFL mapping schema is 1.2.0; the matcher retains explicit legacy 1.1.0 support. New inputs require a producer manifest, snapshot ID and capabilities. Current matcher schema/version is madden-matcher-2.0.0. Run manifests are madden-run-1.0. Directory names never select schemas. Consumers follow the root pointer to an immutable run, not a glob of flat output files.

Derived snapshot stages must be serialized. Database replacement plus hash-bound state fails closed after an interrupted handoff; it is not a multiwriter service. Validation requires the recorded matcher implementation to reproduce benchmarks. Source data/catalog/results remain outside git; examples/tests are synthetic. No native Madden binary import is provided.

The projection is still a chosen partial-feature representation, not an identified NFL play-call model. Discrete book selection remains a greedy approximation even with exact capacity checks. Count support is not calibrated uncertainty; game/player bootstrap analysis remains a subsequent robustness study. No film, verified full concept, protection, QB read, injury-risk, causal ability or win-value claim is added here.
