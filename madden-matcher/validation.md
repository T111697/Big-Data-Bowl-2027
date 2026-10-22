# Hardened validation snapshot

Validated all 96 team-season profiles against a freshly rebuilt preparation snapshot, holding the original public Madden catalog constant. These are structural/reconstruction checks, not independently labeled NFL concept accuracy.

| Check | Result |
| --- | --- |
| Local tests | 40 pass: 3 original preparation tests and 37 matcher/integration/regression tests |
| Source profiles | 32 teams × 2023–2025 |
| Catalog held constant | 1,364 plays, 919 pure passing candidates, 159 annotated plays |
| Output status | 86 curated; 10 explicit no-observation abstentions |
| Previously blocked EMPTY observations with executable candidates | 4,353 of 4,911; availability, not selected-book/full-offense coverage |
| Negligible selected play weights | Zero in the regenerated default batch after pruning/refit |
| Cold isolated batch | 5.68 seconds locally, including catalog/input/run publication overhead |
| Warm isolated batch | 0.53 seconds locally, all 96 hash-verified cache hits |
| Chicago 2025 indexed/dense accepted candidates | Identical; 793 vs 211,370 comparisons (99.62% reduction) |
| Chicago benchmark timing | 0.034s indexed vs 0.182s dense locally |
| Geometry | 1,193 trajectories retained; one-game batches, peak 4,267 frames |
| Source unknown motion | 11 eligible observations remain unknown rather than no-motion |
| Custom/subset validation | Byte-preserving, no inspected run mutation |
| Frozen artifacts | Catalog, exact profile inputs, source-key lineage, config and checksums |

Comparison of selected-book individual route support:

| Chicago season | Previous | Hardened | Hardened selected plays |
| --- | ---: | ---: | ---: |
| 2023 | 83.5% | 83.5% | 11 |
| 2024 | 77.3% | 74.5% | 7 |
| 2025 | 75.7% | 78.9% | 19 |

Coverage is not uniformly higher. Scoring now preserves generic source-WR uncertainty, selection uses corrected counts, and fitting uses separate channels. Different selected books are expected. Version 1 and version 2 feature-distance/objective values are not directly comparable because their definitions changed. No improved football value or concept accuracy is inferred from these numbers.

The generated report records run ID e7e2a12dfec59abb235072397b364f6860bc4c5cb8bd97896dfbe2a013617338, manifest hash, all profile diagnostics and benchmark counters. Runtime is hardware/catalog dependent. The source snapshot and its manifest record complete content hashes and capabilities; source data and generated exports are distributed separately from code.
