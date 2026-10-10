> Historical v1.1.0 correction. The current hardened run and defaults are documented in README.md.

# Context recovery audit

## Resolved from supplied data

The initial receiver filter incorrectly excluded SLOT_WR. Correcting it restores 15,436 regular-season slot route observations. The indexed receiver layer now contains 43,180 labeled routes (37,994 WR/SLOT_WR, plus TE/backs). Keep WR as SOURCE_WR and SLOT_WR as SOURCE_SLOT rather than claiming exact outside geometry from the generic WR designation. Source formation distribution remains distinct from individual role.

Actual passer identity, coverage family/type, formation distribution, down/distance, score, field position, motion and cushion are already available or joinable. The corrected indexed target rows have complete public passer joins; exact matchup leverage and throw quality are not provided by these joins.

## Partially recovered

Across all-team snap events, 4,651 receiver observations have a unique same-team observed center anchor. Of these, 2,422 are offense-right, 2,214 offense-left and 15 within a one-yard center band. These are position proxies using an observed center, not exact ball-relative alignment. The other 38,971 eligible snap observations remain unknown. No missing or zero player-ID snap records were present; football is not supplied as an identified snap entity in this audit. The anchor cannot resolve every alignment.

32,011 of 43,180 labeled receiver routes share a play with at least one observed opposing defensive prospect. Defender identity, lineup role and categorical coverage responsibilities can enrich the context. Responsibility labels such as MAN or DEEP_THIRD_LEFT are categories, not receiver ID references. Observed defenders are not necessarily the primary coverage defender and not the complete defense. Nearest among observed defenders must remain explicitly qualified.

## Still unresolved

Observed receiving combinations comprise 23,475 one-route plays, 7,548 two-route plays, 1,495 three-route plays and 31 four-route plays. Even four observed routes do not verify a full concept, intended progression, optional adjustments or blocking assignments. Missing teammates' paths cannot be reconstructed as facts. Learned trajectory templates can generate candidate sets with uncertainty, not recover hidden ground truth.

Precise ball-relative side, eligible receiver order, press/bracket leverage and actual coverage matchups need additional synchronized tracking or verified film. Complete playbooks, reads, protection rules, coaching intent, mental state and unobserved injury severity cannot be established from these files. Prioritize a film-labeled validation sample with observed multi-route plays, source-slot roles and representative coverage, then test matching accuracy and confidence calibration on held-out games. Historical full-player datasets can help validate methods but cannot fill in missing trajectories in a different game/season.

## Corrected pilot implication

Restoring slots expands the 2025 target-outcome test from three to twelve receivers. None of 34 eligible development trait associations survives BH correction. Explicit family-by-trait models do not improve adjusted target-EPA prediction over the body baseline; usage improvement remains uncertain. Earlier WR-only narrative counts are superseded by the corrected report and package.

## Recommended input contract

For each contextual field store value, evidence source, direct/proxy/model/manual status, confidence or reliability evidence, observation coverage and unresolved alternatives. Unknown values should remain unknown. A matcher should operate on feasible candidate concepts until independent evidence can identify the concept.
