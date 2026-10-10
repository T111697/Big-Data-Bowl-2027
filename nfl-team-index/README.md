# NFL team-indexed tendency pipeline

Reusable preparation layer for observed NFL tendencies and a separate Madden playbook matcher. One canonical store supports 32 team-season views without copying Combine/player records 32 times. This is a working research snapshot pipeline, not the finished roster optimizer or Madden importer.

## Run

Python 3.12; install `pip install -r nfl-team-index/requirements.txt` from the repository root. Supply the previously collected source files at the relative paths below; no API token is required. Source CSVs and generated data are excluded from Git.

```bash
python nfl-team-index/pipeline.py --out nfl-team-index/snapshot-v1 --tracking-teams CHI
python nfl-team-index/enrich.py --out nfl-team-index/snapshot-v1
python nfl-team-index/geometry.py --out nfl-team-index/snapshot-v1
python nfl-team-index/validate_export.py
python -m unittest discover -s nfl-team-index -p 'test_*.py'
```

Use `--tracking-teams ALL` with a **new output directory** to materialize matched tracking for all teams. `NONE` builds metadata/tendencies while retaining the source tracking audit. The included validation/export script defaults to `snapshot-v1` and is the reproducible pilot check. Use its `--out` and `--root` options when validating a different snapshot/source directory. The tabular layer already covers all teams regardless of the tracking selection.

Expected inputs: `restored/data/raw/{games,player_play,players,combine_results,combine_tracking,player_career_successes}.csv`, plus `game_tracking_2023.csv` through 2025; `nfl-success-study/outputs/{team_game,player_box_scores,injury_reports}.csv.gz` and `prospect_id_bridge.csv`; `nfl-success-study/raw/pbp_YEAR.csv.gz` and `snap_counts_YEAR.csv` for 2023–25. Public sources and original collection provenance are in the preceding data catalog. This pipeline does not fetch or distribute sources through GitHub.

## Grains and indexes

| Table | Grain / access pattern |
| --- | --- |
| games | game; regular season 2023–25 |
| plays | one observed offensive game/play |
| player_play | game/play/player, indexed by team/season/game |
| receiver_play | observed offensive WR/SLOT_WR/TE/RB/FB game/play/player |
| players / player_identity / combine_results / career_summary | shared player dimensions; team association through game participation |
| combine_tracking | event/player/time; indexed by player/class/event |
| team_game | public team/game context, including pregame opponent Elo |
| public_play | public game/play; offense and defense indexes; numeric game bridge |
| receiver_context | receiver play joined to actual passer and game context |
| player_game / injury_reports / snap_counts | team/season/game or week as supplied |
| route_tendencies | team/season/position/formation/distribution/motion/down/route |
| observed_signatures | partial observed route combination per play |
| route_geometry | observed receiver trajectory up to the throw |
| tracking_shards / tracking_keys | team/season compressed shards and exact play/player-to-shard index |

SQLite provides selective indexed queries without installing another database. Tracking stays in gzip shards because the frame volume is much larger than the modeling tables. Shard boundaries are storage boundaries, never analytical trajectory boundaries. `TeamStore.tracking()` retrieves every shard that contributes to a key and reassembles frames in time order. Columns can be projected before materializing a DataFrame.

```python
import sys
sys.path.insert(0, 'nfl-team-index')
from pipeline import TeamStore
store = TeamStore('nfl-team-index/snapshot-v1')
receivers = store.table('receiver_context', 'CHI', 2025,
                        columns=['nfl_id','route_ran','actual_passer_gsis_id'])
requirements = store.mapping_input('CHI', 2025)
```

Trade handling: membership means **observed participation for this team/season**, not current roster membership. Career summaries are post-career/collection aggregate labels and must not be used as pregame features. Historical aliases include LAR→LA, OAK→LV, SD→LAC and STL→LA; opaque public game IDs are preserved.

## Separation of algorithms

1. Source preparation produces indexed facts and quality audits.
2. Tendency extraction produces observed conditional distributions and geometry.
3. Madden matching consumes versioned JSON `route_requirements`; outputs `madden_matches` separately.
4. A later call-policy/roster scenario model sets feasible usage and evaluates alternatives.

Exports cover all 32 teams × three seasons. They explicitly identify cohort coverage, unresolved individual alignment and sparse cells. A downstream matcher should score personnel, formation, alignment, route geometry and observed joint compatibility, then report uncertainty. It must not turn partial signatures into verified concepts or route frequencies into identified play-call frequencies.

## Evidence and limits

The Kaggle sample is a prospect cohort. `receiver_alignment` is a distribution such as 2x2, not an individual slot/outside label. Individual role is preserved from the source: SLOT_WR→SOURCE_SLOT, WR→SOURCE_WR. WR is not upgraded to a verified outside geometric classification. Left/right remains incomplete because complete eligible alignment and ball position are not established here. Before producing route trees, use verified alignment data or full eligible/ball observations. Four interpolated trajectory points describe observed execution, not the full planned route or a measured cut capacity. Geometry without exactly one snap and throw event is excluded and audited; initial geometry only covers staged Chicago frames.

Target EPA is shared play value. Untargeted routes retain missing target outcomes rather than zero. Sparse flags use fewer than 20 routes or three games. Route shares sum to one within the **observed labeled context**, not the full team offense. Injury report absence does not mean health; snapshots lack some modification times. Public box scores are observed results, not pregame features.

Cache identity is version plus source path/size/mtime and configuration. Unchanged core/enrichment stages reuse snapshots. Changed inputs require a new output directory; fine-grained incremental updates and content-hash cache invalidation are future work. Enrichment runs separately without rescanning NFL tracking. Interrupted builds are not considered complete until their manifest exists; use a new directory after a failed build. Source scanning is bounded at 500,000 tracking frames. Geometry currently materializes the staged team frames; all-team geometry needs a per-team loop to bound memory. All-team tracking preparation is supported but not exercised in the Chicago pilot.

Production evolution: partitioned Parquet plus a SQL query engine can replace the physical store behind the same contracts, with content hashes, game-level invalidation and atomic artifact publishing. Prefer team/season/game partitions and sufficiently sized row groups, rather than thousands of tiny down/route/weather files. Maintain contextual filters as indexed columns and apply them at query time.

## Validation

Real-data checks cover SQLite integrity, all 32 canonical teams, absence of join fan-out, reconciliation of tendency counts, within-context share sums, reassembly across chunk boundaries, same-key raw-versus-indexed query benchmarks and all-team JSON export. Synthetic tests cover historical aliases, parameterized filters, column validation and trajectory reassembly. Build outputs contain detailed source/configuration and quality manifests; benchmark speed depends on query and cache state.

## Context recovery correction (v1.1.0)

The initial receiver filter omitted SLOT_WR. The corrected snapshot includes 15,436 additional slot-route records, for 43,180 total labeled receiver routes. Earlier mapping exports and the WR-only pilot must not be treated as representative of all WR alignments. The corrected pilot now includes WR and SLOT_WR and has 12 independent 2025 target-outcome test receivers, rather than three. No development association survives multiple-testing correction, and route-interaction Combine features do not improve target-outcome MSE over the body baseline in the corrected test.

Run `python nfl-team-index/context_audit.py --out nfl-team-index/snapshot-v2` after enrichment/geometry. It preserves opponent prospect responsibility categories and computes source-role plus observed-center-relative side proxies where a unique center snap exists. Category labels such as MAN are not receiver identity links. Neither observed nearest defenders nor center proxies constitute verified coverage assignments or exact ball-relative alignment. Context recovery scans source snap events across all teams, independently of the Chicago full-trajectory staging. Rebuild into snapshot-v2 and regenerate exports after this correction.
