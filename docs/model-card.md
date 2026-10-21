# Model and simulator card

## Intended use

WorldFork explores resource-allocation tradeoffs in a synthetic supply network. It is intended for local engineering demonstrations, reproducible experiments, and inspection of a prediction-to-decision pipeline.

It is not validated for real logistics, public policy, emergencies, autonomous control, or decisions about people. Generated supply units and cost units have no established real-world conversion.

## Components

| Component | Role | Boundary |
| --- | --- | --- |
| Ridge regression | Predict synthetic demand from generated history | Learns properties of the generator, not a real population |
| Baseline predictor | Context for forecast error | A comparison point, not a production benchmark |
| Policy search | Select allocation parameters for a chosen objective | Bounded search; no global optimality proof |
| Monte Carlo simulator | Compare policy outcomes across sampled futures | Captures only uncertainty represented in its generator |
| Optional language model | Translate a prompt into scenario fields | Untrusted schema-constrained input; no planning authority |

## Data and forecast assumptions

The history generator creates 120 days of hourly observations for six districts. The first 90 days are used for training and the final 30 days are held out. The first 24 hours supply history for lagged features and the previous-day baseline, leaving 12,816 training district-hours and 4,320 test district-hours. Standardization uses training rows only.

The ridge model predicts demand divided by each district's base demand using 20 encoded features: first and second daily sine/cosine harmonics, weather, announced events, weekend status, previously observed demand, district indicators, and event-by-district interactions. Ridge regularization is fixed at 4.0 in the code; no automated hyperparameter tuning is implemented. The baseline predicts the previous day's observed demand at the same hour.

The included fixed synthetic history produced a test MAE of **3.3137 resource units per district-hour**, compared with **6.2380** for the baseline, and a pooled test **R² of 0.8868** in the verified runtime. These measure fit to the synthetic generator. They are not real-world performance claims or independent model benchmarks.

At each simulated decision point, the forecast uses the previous hour's realized demand and the current hour's known covariates. Dispatch happens before the current hour's demand is observed. Weather, announced events, and current hub capacity are treated as known inputs; uncertainty in observing or forecasting those signals is not modeled.

There are three supply hubs, six districts, and a sparse set of permitted routes. Supply is divisible. Allocation is prepositioned by hour, and delivery is capped at realized demand. Excess dispatched supply is wasted; unmet demand does not carry forward as a backlog. Hub and route capacities constrain dispatch.

## Interpretation

The API records the model's training and test counts, feature names, data source, split, mean absolute error, baseline error, and R². Model assessment and policy evaluation answer different questions: lower forecast error alone does not establish better allocation outcomes.

Service metrics are percentages from 0 to 100. Timeline service is measured for each hour, rather than accumulated across the horizon. The reported lower-tail service and time-series ranges describe the sampled simulator outcomes, not uncertainty about a true real-world effect.

| Output | Definition |
| --- | --- |
| Service rate | Total delivered units divided by total requested units across all evaluation worlds and the 12-hour horizon |
| P10 service | Empirical 10th percentile of the individual worlds' horizon service rates |
| District service | Each district's delivered units divided by its requested units, pooled across worlds and hours |
| Equity gap | Largest minus smallest pooled district service rate, in percentage points |
| Timeline | Each hour's pooled service rate, with 10th–90th percentiles of that hour's world-level rates |
| Allocation edge | Mean dispatched units on that route over the full 12-hour horizon |
| Waste | Mean dispatched but undelivered units per evaluation world over the horizon |
| Cost | Mean total synthetic credits per evaluation world over the horizon |
| Unit cost | Total credits divided by total delivered units across evaluation worlds |

Cost is measured in synthetic credits: dispatched units multiplied by each route's cost, plus reserve procurement at 2.4 credits per reserved unit per hour over the 12-hour horizon. Dispatch cost includes supply that is subsequently wasted. These credits are not dollars or estimates of economic benefit. District service gaps summarize disparity within this modeled network; they do not establish fairness across people or protected groups.

Policies are evaluated on shared held-out futures. Search and evaluation use separate random streams. Sensitivity results come from rerunning the selected policy with a changed scenario input; they are simulator responses and not causal estimates from observed data.

The reported paired 95% interval uses 400 bootstrap resamples of evaluation worlds, preserving each world's baseline and searched-policy pairing. It estimates sampling variation in their pooled service-rate difference under this simulator. It does not include uncertainty about the generator, model family, objective choices, or real-world calibration.

The recommended policy is selected on search futures. It remains the recommendation even when an alternative performs better in the held-out evaluation. This preserves the separation between selection and evaluation and makes unfavorable results visible.

## Search objective

Search evaluates four reference policies and 108 grid candidates on 64 search worlds. Parameters control forecast hedging, route-cost preference, historical service deficits, route scarcity, and demand weighting. Dispatch uses ten constrained redistribution rounds per hour. It is a heuristic family, not an exact network-flow solver or global optimality certificate.

The score is 100 times a weighted sum of service fraction, P10 service fraction, one minus the district service gap as a fraction, and cost efficiency. Cost efficiency is `1 - min(unit_cost / 4, 1)`. The reference of four synthetic credits per delivered unit is a modeling choice.

| Objective | Service | P10 service | One minus gap | Cost efficiency |
| --- | ---: | ---: | ---: | ---: |
| Balanced | 0.42 | 0.18 | 0.24 | 0.16 |
| Service | 0.80 | 0.20 | 0.00 | 0.00 |
| Efficiency | 0.00 | 0.00 | 0.00 | 1.00 |
| Equity | 0.00 | 0.00 | 1.00 | 0.00 |

A score is a preference-weighted utility, not a probability of success.

The focused objectives deliberately isolate their stated criterion. Service also weights its lower-tail outcome; efficiency and equity do not silently include other preferences. They can sacrifice delivery coverage. Use the balanced objective when all four outcomes matter. Different objectives can still select the same policy when the candidate set offers no relevant tradeoff.

## Reproducibility

A scenario records its seed, number of worlds, inputs, and chosen objective. Exported runs include model metadata, search details, policy parameters, outputs, and checks. Re-run with the same code, dependencies, and numerical runtime when comparing results. Wall-clock timestamps, run identifiers, and measured execution times are not deterministic evidence.

## Limitations

- Synthetic historical data and future simulations share modeling assumptions. Held-out evaluation does not test domain shift to a real network.
- A fixed 12-hour horizon omits longer-term adaptation, seasonal change, infrastructure investment, and feedback from human behavior.
- Capacity and routing constraints are simplified. They are not a digital twin validated against field observations.
- Objective weights express explicit preferences over service, cost, and disparity. They do not discover a universally best policy.
- Finite simulation and search budgets leave sampling error and unexplored policies.
- Optional language-model configuration does not imply that live provider calls have been tested. See [validation.md](validation.md).
