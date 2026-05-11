# [OPEN] Debug Session: uniform-prediction-bias

## Summary
- Symptom: predicted winner direction and scorelines look overly uniform across different matches.
- Expected: predictions should react to real over/under lines, odds movement, and match-specific inputs instead of converging to similar outputs.

## Hypotheses
1. Postprocess or fallback logic collapses varied model outputs into the same winner/score templates.
2. Real market inputs are fetched but not materially consumed by inference or weight adjustment.
3. RAG retrieval or review-learning context dominates the final decision and overwhelms match-specific signals.
4. Batch prediction or cache reuse leaks prior-match context into later matches.

## Evidence Plan
- Inspect runtime flow from `predict-schedule` / `predict-match` through inference, RAG, and postprocess.
- Instrument pre/post inference values, market inputs, and retrieved memory impact.
- Reproduce on a small set of matches with different OU / handicap profiles.

## Status
- Open
- No business logic changed yet in this debugging session.

## Evidence
- Reproduced on 3 matches with different market structures:
  - `premier_league` `曼城 vs 布伦特福德`
  - `serie_a` `拉齐奥 vs 国际米兰`
  - `ligue_1` `摩纳哥 vs 里尔`
- Runtime evidence from `.dbg/trae-debug-log-uniform-prediction-bias.ndjson`:
  - `domain/inference.py:986`
    - `曼城 vs 布伦特福德`: pre-live `home_win=0.4599 / draw=0.2815 / away_win=0.2654`
    - `live_outcome_adjustment` applied signals `低赔强侧(<=1.60) + 深让>=0.75 + 强让>=1.0`
    - draw boost delta became `home_win -0.1031 / draw +0.0981`
  - `domain/inference.py:1008`
    - `曼城 vs 布伦特福德`: `real_market_over_under_outcome_adjustment` applied `effect=under_to_draw`, final changed to `home_win=0.3647 / draw=0.4096 / away_win=0.2258`
    - `拉齐奥 vs 国际米兰`: same `effect=under_to_draw`, final `home_win=0.3871 / draw=0.3282 / away_win=0.2847`
    - `摩纳哥 vs 里尔`: same `effect=under_to_draw`, final `home_win=0.3827 / draw=0.3246 / away_win=0.2927`
  - `domain/postprocess.py:508`
    - `曼城 vs 布伦特福德`: raw top scores `1-0/2-0/1-1/0-0/2-1` -> reranked `1-1/0-0/2-2`
    - `拉齐奥 vs 国际米兰`: raw top scores `0-1/0-0/1-0/1-1/0-2` -> reranked `1-0/2-1/2-0`
    - `摩纳哥 vs 里尔`: raw top scores `0-1/1-1/1-0/0-0/1-2` -> reranked `1-0/2-1/2-0`
- Final result payloads also confirm RAG self-retrieval:
  - `/tmp/uniform-debug-match1.json`: `retrieved_memory.similar_cases[0].match_id = premier_league_20260510_曼城_布伦特福德`
  - `/tmp/uniform-debug-match2.json`: `retrieved_memory.similar_cases[0].match_id = serie_a_20260510_拉齐奥_国际米兰`
  - `/tmp/uniform-debug-match3.json`: `retrieved_memory.similar_cases[0].match_id = ligue_1_20260510_摩纳哥_里尔`
  - This is the same logical match as the current prediction result, so the retrieval layer is polluted by self-recall.

## Hypothesis Review
1. Postprocess or fallback logic collapses varied model outputs into the same winner/score templates.
   - Confirmed. Raw score candidates are diverse, but rerank repeatedly collapses them into `1-0/2-1/2-0` or `1-1/0-0/2-2`.
2. Real market inputs are fetched but not materially consumed by inference or weight adjustment.
   - Rejected. Market inputs are consumed, but the current adjustment direction is too aggressive and often pushes probability mass toward draw.
3. RAG retrieval or review-learning context dominates the final decision and overwhelms match-specific signals.
   - Partially confirmed. Not the only cause, but RAG does self-retrieve the same match and can reinforce existing prediction bias.
4. Batch prediction or cache reuse leaks prior-match context into later matches.
   - No evidence yet. The same issue reproduces in isolated single-match runs.

## Root Cause Snapshot
- The issue is not a single bug.
- Three mechanisms amplify each other:
  - `apply_live_outcome_adjustment()` gives unconditional draw boost for strong favorites / deep handicaps.
  - `apply_real_totals_outcome_adjustment()` maps under bias to draw too aggressively, even when side edge is still meaningful.
  - `rerank_top_scores()` can surface lower-ranked template scores and overwrite more diverse raw score candidates.
- Separately, RAG retrieval fails to exclude the current logical match when `match_id` uses a different namespace from archive/internal ids.

## Minimal Fix
- `domain/rag.py`
  - Added logical same-match filtering by `league_code + match_date + home_team + away_team`, with `match_id` exact match as an additional fast path.
  - Applied the filter to `similar_cases`, `market_cases`, and `upset_cases`.
- `enhanced_prediction_workflow.py`
  - Passed `match_date` into RAG retrieval.
  - Fixed debug payload to log `similar_cases` / `market_cases` counts instead of a non-existent `cases` field.
- `domain/inference.py`
  - Reduced `apply_live_outcome_adjustment()` so deep-favorite priors no longer create a large unconditional draw boost.
  - Reduced `apply_real_totals_outcome_adjustment()` with a side-gap / closeness gate so OU only nudges draw probability when the match is already competitive.
- `domain/postprocess.py`
  - Restricted rerank candidate pool to the top raw score candidates instead of pulling deeper low-probability templates.
  - When confidence is low, preserve more of the raw score diversity instead of forcing all scores to align with the top outcome label.

## Post-Fix Evidence
- Reproduced again on:
  - `曼城 vs 布伦特福德`
  - `拉齐奥 vs 国际米兰`
  - `摩纳哥 vs 里尔`
- `拉齐奥 vs 国际米兰`
  - Pre-fix `post_market_final_prob`: `0.3871 / 0.3282 / 0.2847`
  - Post-fix `post_market_final_prob`: `0.3994 / 0.3164 / 0.2842`
  - Pre-fix rerank: `1-0 / 2-1 / 2-0`
  - Post-fix rerank: `0-1 / 1-0 / 0-0`
- `摩纳哥 vs 里尔`
  - Pre-fix `post_market_final_prob`: `0.3827 / 0.3246 / 0.2927`
  - Post-fix `post_market_final_prob`: `0.3939 / 0.3049 / 0.3012`
  - Pre-fix rerank: `1-0 / 2-1 / 2-0`
  - Post-fix rerank: `0-1 / 1-1 / 1-0`
- RAG self-recall check
  - Post-fix `曼城 vs 布伦特福德` no longer retrieves `premier_league_20260510_曼城_布伦特福德` inside `retrieved_memory.similar_cases`
  - Post-fix `拉齐奥 vs 国际米兰` no longer retrieves `serie_a_20260510_拉齐奥_国际米兰`
  - Post-fix `摩纳哥 vs 里尔` no longer retrieves the current logical match

## Residual Risk
- `曼城 vs 布伦特福德` post-fix run did not load a valid real-market totals line and therefore did not exercise the same OU branch as the pre-fix run.
- There is likely a separate snapshot matching problem for some Premier League fixtures:
  - current run had no valid `大小球` real line
  - earlier payload pointed to an unrelated snapshot path (`伯恩利 vs 阿斯顿维拉`)
- This does not invalidate the uniform-prediction fix, but the snapshot mismatch should be debugged separately because it can still distort market-driven inference.
