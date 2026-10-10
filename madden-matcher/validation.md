# Stage 2 validation snapshot

Run against the corrected PR #1 index and the public catalog acquired on October 10, 2026. This is structural and reconstruction validation. There are no independent NFL full-concept labels, so concept accuracy, expected EPA and win improvement have not been measured.

| Check | Result |
| --- | --- |
| Public catalog | 1,364 plays, 44 sets, 919 pure passing candidates |
| Detailed route evidence | 160 pages requested, 159 plays with annotations |
| Acquisition | 205 pages fetched, zero recorded failures |
| NFL team-season profiles | 96 across all 32 teams and 2023–2025 |
| Partial role-route combinations | 6,989 aggregated cells before canonical filtering |
| Output statuses | 86 curated; 10 abstained for no eligible observations |
| Cold batch | 3.38 seconds locally, catalog compilation before the timer |
| Cached batch | 0.32 seconds locally, all 96 cache hits |
| Chicago 2025 accepted-candidate parity | Indexed and exhaustive matching identical |
| Chicago 2025 scored comparisons | 727 indexed vs 211,370 exhaustive pure-pass comparisons |
| Chicago 2025 benchmark | 0.036s indexed vs 0.106s exhaustive; approximately 3× locally |
| Constraints | All curated weights finite, nonnegative, sum to 1 and satisfy cap; play/set budgets satisfied |
| Receiver slot integrity | Every reported joint assignment uses distinct catalog slots |
| Deferred responsibilities | Protection and QB progression remain null |

Timings depend on hardware and catalog breadth. The 99.66% comparison reduction is for the Chicago benchmark, not a universal performance guarantee. Reconstruction diagnostics may be poor even when software constraints pass; no residual threshold is disguised as an accuracy test.

## Chicago examples

| Season | Selected plays | Individual route compatibility coverage | Weighted feature coverage | Projected feature TV distance |
| --- | ---: | ---: | ---: | ---: |
| 2023 | 15 | 83.5% | 83.5% | 0.300 |
| 2024 | 20 | 77.3% | 81.9% | 0.420 |
| 2025 | 24 | 75.7% | 65.7% | 0.434 |

Coverage is conditional on the supplied observed cohort and bounded catalog. The joint channel is partial; it is not a verified complete-concept channel. These numbers do not represent full-offense coverage or ground-truth Madden matching accuracy.

## Reproduce

Use `build_inputs.py`, `cli.py` and `validate.py` as described in the README. The validation JSON contains every team-season record and benchmark counters. The test suite uses synthetic fixtures and requires no restricted datasets, network or credentials. GitHub CI also runs the merged layer's tests and a synthetic CLI export smoke test.
