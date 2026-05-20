# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

The active application code lives in `europe_leagues/`. The repository root also contains agent/persona docs, Feishu docs artifacts, and `.trae/skills/*` skill definitions.

## Environment and common commands

### Python environment

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-openclaw.txt
python3 -m playwright install chromium
```

Optional, if you need the packaged browser/openclaw setup:

```bash
bash scripts/setup_openclaw_env.sh
```

### CLI health checks

Most development happens through the formal CLI in `europe_leagues/`:

```bash
cd europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
python3 prediction_system.py list-leagues --json
```

### Core workflow commands

```bash
cd europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-05-11 --json
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
python3 prediction_system.py predict-schedule --league premier_league --date 2026-05-11 --days 1 --json
python3 prediction_system.py predict-schedule --league premier_league --date 2026-05-11 --days 1 --no-write --json
python3 prediction_system.py save-result --match-id premier_league_20260511_曼联_切尔西 --home-score 2 --away-score 1 --json
python3 prediction_system.py auto-sync-results --json
python3 prediction_system.py result-sync-daemon --json
python3 prediction_system.py accuracy --refresh --json
python3 prediction_system.py harness-list --json
python3 prediction_system.py harness-run --pipeline match_prediction --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
```

### Tests

There is no discovered repo-wide `make test`, `make lint`, or dedicated lint config in this repository. Verification is primarily `unittest`-based.

Run tests from `europe_leagues/` so local imports like `from app import cli` work:

```bash
cd europe_leagues
python3 -m unittest test_cli_persistence
python3 -m unittest test_prediction_persistence
python3 -m unittest test_result_sync
python3 -m unittest test_rag_store
python3 -m unittest test_result_manager
```

Run a single test module/class/test:

```bash
cd europe_leagues
python3 -m unittest test_result_sync.ResultSyncTest
python3 -m unittest test_result_sync.ResultSyncTest.test_register_prediction_result_sync_prefers_canonical_teams_match_id
```

Useful regression targets when touching key areas:

- CLI routing / JSON envelopes: `test_cli_persistence`
- prediction persistence / batch side effects / world cup paths: `test_prediction_persistence`
- result sync registry and SoT refresh: `test_result_sync`
- RAG index behavior: `test_rag_store`
- result archive / accuracy behavior: `test_result_manager`

### Optional Node dependency

A minimal `package.json` exists only for `feishu-cli` tooling. Install it only if you are working on Feishu import/export helpers:

```bash
npm install
```

## Big-picture architecture

### 1. The real CLI entrypoint is `europe_leagues/app/cli.py`

`europe_leagues/prediction_system.py` is a compatibility wrapper only. Future changes to command behavior should usually happen in `app/cli.py`, not in `prediction_system.py`.

Important implication: external tools may discover `prediction_system.py`, but the actual command set, JSON output envelope, runtime profile injection, and command routing live in `app/cli.py`.

### 2. Prediction flow is layered: CLI -> Domain shell -> Orchestrator -> domain services

The main runtime path is:

- `app/cli.py`: argument parsing, JSON output, command routing
- `domain/predictor.py`: stable facade exposing `DomainPredictor`
- `enhanced_prediction_workflow.py`: main orchestration in `EnhancedPredictor`
- `domain/*`: extracted services for live refresh, inference, postprocess, persistence, RAG, reporting, writeback, etc.

`EnhancedPredictor` is still the main orchestration hub. It wires together the domain services and owns the end-to-end prediction workflow.

### 3. `PredictionPersistenceService` owns prediction side effects

If a change affects what happens after a prediction is produced, start in `europe_leagues/domain/persistence.py`.

This service is the owner for prediction persistence side effects, including:

- teams markdown writeback coordination
- `MEMORY.md` rolling-memory updates
- runtime archive updates
- memory sample sync
- RAG index sync
- result sync registration

`result_manager.py` remains the lower-level archive/result/accuracy foundation, but prediction-side orchestration should not be reimplemented ad hoc in CLI handlers or in `EnhancedPredictor`.

### 4. Batch schedule prediction has different persistence semantics than single-match prediction

This is easy to break.

For `predict-schedule` / `generate_prediction_report`:

- each match is predicted with per-match persistence disabled
- writeback/persistence/accuracy refresh happen at the batch level
- `--no-write` must suppress both teams writeback and batch persistence side effects

If you change schedule generation, verify against `test_prediction_persistence.py` and `test_cli_persistence.py`.

### 5. Storage is split between SoT markdown and runtime JSON

There are two persistent data planes:

#### League or competition SoT files

- Five major leagues: `europe_leagues/<league>/teams_2025-26.md`
- World Cup: `europe_leagues/world_cup/teams_2026.md`
- World Cup roster files: `europe_leagues/world_cup/players/*.json`

These markdown/json files are the human-readable source of truth for league-backed competitions.

#### Runtime/archive/index files

Under `europe_leagues/.okooo-scraper/`:

- `runtime/*.json`: prediction archive, result sync registry, accuracy stats, RAG data, etc.
- `snapshots/`: odds and page snapshots
- `schedules/`: collected schedule data

Do not hardcode these paths. Use `runtime/paths.py` and `get_default_paths()`.

### 6. Competition behavior depends on whether the competition is league-backed SoT or runtime-only

Current code distinguishes between:

- league-backed SoT competitions: the five major leagues and now `world_cup`
- runtime-only competitions: European cups and other cup-style competitions that persist to `MEMORY.md` plus runtime archive/index files

This affects:

- where predictions are written
- how `teams_match_id` is formed
- how result sync resolves canonical match IDs
- whether teams markdown is the primary record

When changing persistence or result sync, confirm whether the competition is expected to be SoT-backed or runtime-only.

### 7. Result sync is its own subsystem

`europe_leagues/runtime/result_sync.py` manages:

- result sync registry storage in `.okooo-scraper/runtime/result_sync_registry.json`
- canonical vs external match ID handling
- schedule refresh from teams markdown rows
- due-result polling and automatic save-result execution

A key subtlety: result sync may prefer canonical `teams_match_id` values over external match IDs, and it refreshes schedule metadata from the SoT markdown before syncing. Preserve that behavior when editing result backfill logic.

### 8. RAG is part of the formal prediction chain, not a side experiment

RAG-related logic spans multiple layers:

- `runtime/rag_store.py`: builds/updates cases and index files
- `domain/rag.py`: retrieval service used during prediction
- `domain/postprocess.py`: formats retrieved memory into prediction output
- `domain/persistence.py`: syncs rolling memory and index artifacts after writes/results

If you change memory/archive behavior, consider its downstream effect on RAG cases and registry files.

### 9. Runtime profile and personas are wired into command behavior

The runtime profile included in CLI JSON output is derived from:

- `agent_runtime_registry.py`
- `agents/*.md`
- command-to-role mapping in `app/cli.py`

If you add or rename formal commands, update the command classification and runtime profile plumbing together.

### 10. The repository still contains many legacy/support scripts

There are many historical helper scripts in `europe_leagues/` and the repository root. The formal path is the CLI plus domain/runtime/storage layers described above.

Prefer extending the formal path unless the task is explicitly about one-off maintenance, backfill, or migration scripts.

## High-value file map

When you need to understand or modify a behavior, start here:

- command routing: `europe_leagues/app/cli.py`
- compatibility entrypoint: `europe_leagues/prediction_system.py`
- stable prediction facade: `europe_leagues/domain/predictor.py`
- main orchestration: `europe_leagues/enhanced_prediction_workflow.py`
- prediction side effects: `europe_leagues/domain/persistence.py`
- teams markdown writeback: `europe_leagues/domain/writeback.py`
- live refresh / snapshot reuse: `europe_leagues/domain/live.py`
- inference pipeline: `europe_leagues/domain/inference.py`
- postprocessing / output shaping: `europe_leagues/domain/postprocess.py`
- result/archive/accuracy foundation: `europe_leagues/result_manager.py`
- result sync registry and due sync: `europe_leagues/runtime/result_sync.py`
- path resolution: `europe_leagues/runtime/paths.py`
- stable markdown storage API: `europe_leagues/storage/teams_md.py`

## Repository-specific guidance

### Skills

The repository contains `.trae/skills/*/SKILL.md` files. The documented convention is:

- `SKILL.md` describes capability, entrypoints, execution steps, and output expectations
- installation/update/sync/version-management logic should live in CLI, hooks, startup scripts, or harness code, not inside `SKILL.md`

### README/doc precedence

The root `README.md`, `agent.md`, and `docs/architecture/europe_leagues_architecture.md` describe the intended architecture well, but current code is the source of truth when they diverge. In particular, recent code includes `world_cup` as a league-backed competition in `LEAGUE_CONFIG` and related persistence/result-sync paths.
