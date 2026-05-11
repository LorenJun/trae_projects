# [OPEN] Debug Session: snapshot-routing-chain

## Summary
- Symptom: some matches report `missing_real_market_line` even though local snapshot files exist.
- Symptom: some Premier League runs attach the wrong snapshot path or wrong match context.
- Expected: when a matching local snapshot exists, the prediction flow should hydrate the correct odds payload, preserve snapshot identity, and resolve a real over/under line.

## Hypotheses
1. Snapshot lookup relies too heavily on `match_id`, and when `match_id` is missing or stale, existing local snapshots are skipped.
2. Snapshot files are overwritten or cross-linked by wrong `match_id`, causing path-to-match mismatches in Premier League runs.
3. Snapshot payload is loaded successfully, but `extract_current_odds()` / merge logic drops the `大小球.final.line` information before inference.
4. `ensure_totals_if_needed()` runs with an empty or wrong `match_id`, so the auto-fetch path refreshes or inspects the wrong match context.

## Evidence Plan
- Instrument snapshot hydration, refresh, and totals auto-fetch with exact `match_id`, `snapshot_path`, and resolved totals line.
- Reproduce on matches that showed both symptoms:
  - `布莱顿 vs 狼队`
  - `曼城 vs 布伦特福德`
  - `沃尔夫斯堡 vs 拜仁慕尼黑`
- Compare pre-hydrate odds, post-hydrate odds, auto-fetch diagnostics, and final `resolve_over_under_line()` inputs.

## Status
- Open
- No business logic changed yet in this debugging session.

## Evidence
- Pre-fix runtime evidence from `.dbg/trae-debug-log-snapshot-routing-chain.ndjson`:
  - `曼城 vs 布伦特福德`
    - `hydrate existing snapshot missed`: requested `match_id=1300075` but no snapshot found by id.
    - Local snapshot actually existed under another id and another wrong filename:
      - payload match: `match_id=1296089`, `home_team=曼城`, `away_team=布伦特福德`
      - file path was wrongly stored as `premier_league/伯恩利vs阿斯顿维拉.json`
  - `布莱顿 vs 狼队`
    - local snapshot file existed: `premier_league/布莱顿vs狼队.json`
    - payload had `match_id=1296084`, `大小球.final.line=3.25`
    - but prediction flow reported `existing_snapshot_odds.reason=missing_match_id` and final `missing_real_market_line`
  - `沃尔夫斯堡 vs 拜仁慕尼黑`
    - `hydrate existing snapshot hit` on `bundesliga/沃尔夫斯堡vs拜仁慕尼黑.json`
    - payload content was actually `汉堡 vs 弗赖堡`, same `match_id=1300073`
    - this proves snapshot file contents can be overwritten by a wrong match-id while keeping an unrelated stable filename

## Hypothesis Review
1. Snapshot lookup relies too heavily on `match_id`, and when `match_id` is missing or stale, existing local snapshots are skipped.
   - Confirmed.
2. Snapshot files are overwritten or cross-linked by wrong `match_id`, causing path-to-match mismatches in Premier League runs.
   - Confirmed.
3. Snapshot payload is loaded successfully, but `extract_current_odds()` / merge logic drops the `大小球.final.line` information before inference.
   - Rejected as primary root cause. Once the correct payload is loaded, `resolve_over_under_line()` can read the line normally.
4. `ensure_totals_if_needed()` runs with an empty or wrong `match_id`, so the auto-fetch path refreshes or inspects the wrong match context.
   - Partially confirmed. Wrong `match_id` can poison refresh/fetch, but the bigger issue is missing fallback and missing payload validation.

## Minimal Fix
- `okooo_live_snapshot.py`
  - Added `snapshot_matches_request()` to validate payload team/date against the requested match.
  - Added `find_snapshot_by_teams()` and `find_snapshot_for_match()`:
    - first try `match_id`
    - reject mismatched payloads
    - fallback to `home_team + away_team + match_date`
  - Added canonical file normalization so a correct payload found in a wrong-named file is renamed back to the requested match filename.
  - Updated `refresh_snapshot()` to retry once without `--match-id` when the fetched payload does not match the requested teams/date.
- `domain/live.py`
  - `hydrate_existing_snapshot_odds()` now supports team/date fallback even when `match_id` is empty.
  - snapshot hydration now stores `snapshot_path` in merged odds.
- `domain/odds.py`
  - `auto_fetch_okooo_totals_if_needed()` now retries without `--match-id` when the fetched snapshot payload mismatches the requested match.

## Post-Fix Evidence
- `曼城 vs 布伦特福德`
  - Post-fix log: snapshot hydration now hits `premier_league/曼城vs布伦特福德.json`
  - payload remains `match_id=1296089`, teams correct, `totals_final_line=3.5`
  - final result resolves `line_source=snapshot_final`
- `布莱顿 vs 狼队`
  - Post-fix log: with empty input `match_id`, hydration now hits `premier_league/布莱顿vs狼队.json`
  - `resolve_over_under_line()` reads `3.25` from snapshot final
  - final result no longer reports `missing_real_market_line`
- `沃尔夫斯堡 vs 拜仁慕尼黑`
  - Post-fix refresh log shows `payload_home_team=沃尔夫斯堡`, `payload_away_team=拜仁慕尼黑`, `totals_final_line=4`
  - final result resolves `line_source=snapshot_final`
  - `okooo_totals_fetch` is skipped because the correct line is already available from snapshot

## Residual Risk
- If local snapshots already contain historically corrupted files for other matches, they are only repaired when read or refreshed again.
- Top-level prediction result may still preserve the externally passed stale `match_id`, while hydrated odds/realtime context uses the corrected snapshot `match_id`. This is acceptable for now but may be worth unifying later.
