# Validation

This record distinguishes implemented checks from results actually observed during verification.

## Current verification status

Verification uses Windows, Python 3.12.14, and NumPy 2.3.5.

| Check | Observed result |
| --- | --- |
| Complete Python suite | **49 tests passed** in 5.097 seconds, including 19 engine tests and 30 HTTP/provider tests |
| Forecast fit and chronological evaluation | 12,816 training district-hours; 4,320 held-out district-hours; MAE 3.3137 versus 6.2380 for the previous-day baseline; pooled R² 0.8868 |
| Browser JavaScript syntax | `node --check web/app.js` passed |
| Browser workbench checks | Verified in the desktop in-app browser: scenario execution, forecast/evidence views, archive restoration, fractional inputs, safe text rendering, and interpretation/run exclusion |
| Local launcher | `Start-WorldFork.ps1 -Port 8787` started the app successfully |
| Live OpenAI integration | Not exercised; provider tests use bounded local stubs |
| Public deployment | Not performed |

The engine tests cover chronological evaluation, strict scenario bounds, fixed-seed reproducibility, separate random streams, training-only policy selection, common futures for perturbations, no access to current demand at dispatch, and resource/cost reconciliation under extreme scenarios. Model quality assertions apply only to the fixed synthetic benchmark.

The HTTP tests cover successful persistence/export, host and origin rejection, missing or malformed write tokens, request bounds, duplicate JSON keys, traversal attempts, static-file containment, compute contention, request limits, retention, and sanitized failures. Provider tests cover the fixed endpoint, structured-output request, absent credentials, invalid output, refusal, response limits, redirection rejection, and sanitized transport failures.

Selected source review covered input validation, static-file paths, browser text insertion, SQLite access, the provider credential boundary, random-stream separation, forecast feature timing, and metric definitions. Regression cases cover malformed tokens, database-connection cleanup, oversized integers, invalid Unicode names and prompts, and malformed provider output. All 49 tests passed after these fixes and the final objective-weight changes.

Browser verification used the real local engine. A separate, visibly labeled stub interpreter tested language-interface behavior without contacting OpenAI: demand `1.17`, disruption `0.43`, and reserve capacity `13.25` survived translation, execution, persistence, and archive restoration. A name containing literal `<b>` markup remained text. The Run button was disabled while interpretation was pending. The main application correctly disabled interpretation without credentials. Preset execution, policy selection, and keyboard activation of a district node were exercised. A browser-created run was independently retrieved through the JSON download endpoint with the expected attachment header and all seven constraint checks passing. No console warnings or errors were observed in the inspected browser flow. The layout was inspected at desktop/panel widths, including a measured 1112-pixel viewport with no horizontal page overflow; phone and tablet device testing was not performed.

The isolated fixture is reproducible with `python -m tests.browser_fixture` at port 8790. It uses its own ignored test database and a fixed local response, not a live model. Stop it after testing.

Run the Python suite from the source directory:

```powershell
python -m unittest discover -s tests -v
```

The provider tests use local stubs. They verify request construction and failure handling, without proving that a particular account, model, or live API response is available.

## Verification scope

The relevant checks are:

- Forecast evaluation on data excluded from fitting and comparison with a stated baseline.
- Reproducible numerical results for a fixed scenario and seed.
- Separate policy-search and policy-evaluation random streams.
- Resource nonnegativity, no delivery beyond demand, and hub/route capacity limits.
- Validation of malformed fields, non-finite values, unsupported objectives, and oversized requests.
- Request-origin and local-token enforcement for writes.
- Loopback host handling, static-file containment, and controlled error responses.
- Bounded compute, request rate, stored history, and provider-response size.
- Persistence and JSON export of completed experiments.
- Browser interaction, chart rendering, accessibility basics, and narrow-screen layout.

## Explicitly unverified

- Accuracy on real demand data or transfer to a real distribution network.
- Operational value, business outcomes, savings, or real-world service improvements.
- Live OpenAI requests without a configured credential and an observed successful call.
- Production hosting, multi-user access control, external penetration testing, and long-running load behavior.

Security verification is scoped to selected local application paths. Passing those checks does not establish that the application is free of vulnerabilities.
