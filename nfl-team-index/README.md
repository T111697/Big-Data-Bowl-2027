# Team-indexed NFL research preparation

One canonical store prepares regular-season 2023–2025 team views while shared player/Combine dimensions remain global. The catalog is a supplied prospect cohort, not a complete offense or verified NFL playbook. Producer version team-index-2.0 emits mapping schema 1.2.0 with snapshot identity and explicit capabilities.

## Run

```bash
pip install -r nfl-team-index/requirements.txt
python nfl-team-index/pipeline.py --tracking-teams CHI
python nfl-team-index/enrich.py
python nfl-team-index/geometry.py --batch-games 1
python nfl-team-index/context_audit.py
python nfl-team-index/validate_export.py
```

Shared defaults are snapshot-v3 and exports-v3, declared in research_contracts.py. Directory names never imply schema versions. Use `--out` for alternate snapshots and `--exports` for their exports. Changed source recipes require a new snapshot directory. `--tracking-teams ALL` materializes tracking for every matched team; `NONE` prepares metadata with an explicit tracking-not-materialized capability. No API token is needed after the source files are collected.

Expected sources remain `restored/data/raw/{games,player_play,players,combine_results,combine_tracking,player_career_successes}.csv`, game_tracking CSVs, `nfl-success-study/outputs/{team_game,player_box_scores,injury_reports}.csv.gz`, prospect_id_bridge.csv, and public pbp/snap_counts files for 2023–25. Sources and generated snapshots stay outside git.

## Grains and semantics

| Table/view | Grain |
| --- | --- |
| games | eligible regular-season game |
| player_play | game/play/player; indexed team/season |
| receiver_play | offensive WR/SLOT_WR/TE/RB/FB observation |
| plays | observed offensive game/play |
| route_tendencies | team/season/source role/formation/distribution/motion/down/route |
| observed_signatures | partial receiver combination per observed play |
| receiver_context | receiver observation plus actual public passer/game context |
| route_geometry | observed receiver movement from snap through throw |
| receiver_defender_context | observed defensive prospects/categories, not actual primary defender |
| snap_alignment_context | source role and unique-center-relative side proxy |
| players/identity/combine/career | shared player dimensions |
| player_game/team_game/injuries/snaps | supplied game/week context |
| tracking_shards/tracking_keys | hashed compressed shards plus exact trajectory-to-shard lookup |

Source SLOT_WR is retained; source WR is not upgraded to verified outside geometry. Receiver alignment is a formation distribution, not an individual position. Motion is TRUE/FALSE/UNKNOWN. Unknown target EPA stays missing in positive-target outcomes. Target EPA is shared play value, not isolated receiver value. Route shares sum to one within an observed labeled context only. Career summaries and box scores are retrospective; injury-report absence does not establish health. Team membership means historical participation, not current roster membership. Historical team aliases are normalized and opaque public IDs preserved.

The route aggregation and joint adapter share eligible-route filtering and motion/source feature definitions. Stable source feature IDs and snapshot IDs support downstream player/game/play lineage. The adapter exports exact independent player/game counts without claiming calibrated uncertainty or full-offense representativeness.

## Memory, retrieval and duplicate handling

Core tracking ingestion streams 500,000-frame chunks. Geometry decompresses each shard once, spools eligible frames to temporary SQLite, and reads one configurable game batch at a time. It no longer concatenates all-team staged tracking in memory. No tracking produces a normal-schema empty geometry table and NOT_MATERIALIZED audit. Missing promised tracking shards fail validation.

`TeamStore.tracking_batch(keys)` retrieves all indexed contributing shards once per batch, filters requested keys and sorts trajectories. A bounded 64 MiB cache reuses suitably sized decompressed shards; large shards are read without caching. `tracking()` delegates to this API. Identical duplicate frames are collapsed; conflicting observations at the same player/play/time raise errors. Geometry and snap audits reject conflicting trajectories/snap keys instead of silently choosing source order. Unknown play direction cannot produce a verified side or trajectory.

Geometry has four interpolated displacement points and pre-throw path summaries. These describe execution, not intended routes, cut capacity or physiology. Without full eligibles/ball/film, alignment and defender identities remain proxies/unknowns.

## Provenance and publication

Source fingerprints use streamed SHA-256 content hashes, path, size, producer version and configuration. Each written tracking shard also has a content hash. The core manifest records the recipe; snapshot-state.json records the current database hash, snapshot identity, capabilities and completed stage recipes. TeamStore verifies this state before identified exports. Legacy snapshots are explicitly marked as legacy, not inferred from their directory names.

Enrichment, geometry and context writes run against SQLite backup copies. Only a completed stage replaces the live database, followed by atomic state/marker publication. Hash verification fails closed if a crash interrupts the DB/state handoff. Stages must run serially; this is not a multiwriter service. Do not bypass state verification to reuse an interrupted snapshot. Existing readers of an open SQLite connection retain their old database until they reconnect; new identified exports require matching state.

Exports publish files followed by a hash-bound input manifest; a reader rejects incomplete/mismatched generations. The Madden stage then freezes an exact input set into an immutable run. It follows manifests rather than globbing stale files. Fine-grained game updates and Parquet remain future physical-storage options, not prerequisites for this hardening.

## Validation

`validate_export.py` checks SQLite integrity, no receiver-context fan-out when enrichment exists, eligible route reconciliation, observed-context share sums, all promised shard files/hashes and trajectory reassembly when materialized. It exports the actual available team-season set rather than hard-coding a snapshot-folder naming convention. `--benchmark` optionally compares matching CHI 2023 keys against raw CSV reads, with warm filesystem caveats.

CI includes source-to-export-to-joint-adapter-to-matcher integration for tracking and NONE, unknown motion/EPA, conflicting snap duplicates, failed-stage rollback, content changes preserving size/mtime, corrupt snapshots and missing promised shards. The original source-role, alias and retrieval tests remain. No concept accuracy, causal player ability, injury risk or season-win benefit is established by these checks.
