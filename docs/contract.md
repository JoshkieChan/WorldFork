# WorldFork API contract

All metrics are computed on a synthetic network. Percent metrics use 0–100.

## Engine module: worldfork/engine.py

Exports `bootstrap() -> dict`, `validate_scenario(data) -> dict` (raises ValueError), `run_experiment(scenario) -> dict`.

Scenario keys: `name` (1–80 chars), `demand_multiplier` (0.6–2.5), `disruption` (0–0.85; west hub capacity loss), `reserve_units` (0–200; extra capacity per hour), `objective` (`balanced`, `service`, `efficiency`, `equity`), `seed` (1–2147483647 integer), `worlds` (32–512 integer). Missing keys use defaults; unknown keys are rejected. Fixed 12-hour horizon.

Bootstrap returns:
- `defaults`: normalized scenario
- `presets`: array of `{id, name, description, scenario}`
- `network`: `{nodes: [{id, name, type: 'hub'|'district', x, y, capacity?, base_demand?}], edges: [{source,target,cost}]}`. Positions on a 1000 by 600 canvas.
- `model`: `{name, train_samples, test_samples, mae, baseline_mae, r2, features: [string], data_source, split}`
- `version`: string

Experiment returns:
- `id`, `created_at`, `scenario`, `model`, `network`
- `recommended_policy`: policy id, `summary`: honest generated short recommendation text
- `policies`: array of `{id, name, description, service_rate, p10_service, cost, equity_gap, score, district_service: [{id,name,service_rate,delivered,demand}], timeline: [{hour,service_rate,low,high,demand,delivered}], allocations: [{source,target,units}], distribution: [{bin,count}], parameters: object}`. Include policy ids `baseline` and `optimized`, plus 2+ meaningful alternatives. All aggregates from held-out simulation worlds.
- `evidence`: array of `{id, role, title, detail, status: 'pass'|'info'|'warning', metrics: object}`; these are actual computational stages, not claims of LLM agents.
- `checks`: array of `{name,passed,detail}`; actual conservation/nonnegativity/capacity validation.
- `search`: `{candidates,training_worlds,evaluation_worlds,seed,elapsed_ms,method}`; disjoint search/evaluation seed streams.
- `sensitivity`: array of `{label,change,service_rate,delta}`; measured reruns of chosen policy with perturbations.
- `limitations`: array of strings

## HTTP API

- GET `/api/bootstrap`: engine bootstrap plus `capabilities: {language_model: boolean, model: string|null}` and `csrf_token` (process-local request token).
- GET `/api/health`: `{status:'ok',version}`
- POST `/api/runs`: scenario object, returns experiment (201). Writes require `X-WorldFork-Token` from bootstrap and exact same-origin if Origin supplied. Engine validation errors return 422 `{error: string}`.
- GET `/api/runs`: `{runs: [{id,created_at,name,recommended_policy,service_rate}]}`
- GET `/api/runs/<id>`: experiment
- GET `/api/runs/<id>/export`: JSON download
- POST `/api/interpret`: `{prompt: string}` -> `{scenario, interpretation: string, provider: string}`. Explicit optional configured OpenAI structured output only; returns 503 if absent. Never claim local keyword rules are LLMs.

Server binds loopback only by default. Static files from web/ only. SQLite persists run evidence under ignored `.worldfork/`. Bound request size, rate, concurrency, and storage. No shell execution, user supplied outbound URL, arbitrary file access, or model tools.
