# Implementation Plan: Daily Stock Screener

## Overview

This plan builds the Daily Stock Screener incrementally from the inside out: shared data models first, then the pure stateless units (Delta_Calculator and the four filters) each validated against their design properties, then the data gateway and universe assembler at the network boundary, then the orchestrator that wires the filters together with ranking/limiting and the non-trading-day self-skip gate, then reporting over the ranked Reported_Set, and finally the CLI plus the hosted GitHub Actions scheduled workflow. Each step builds on the previous ones and ends by integrating its output into the growing system, so no code is left orphaned.

The design uses Structured Pseudocode, so all implementation tasks are expressed against the component contracts and data models in design.md. Property tests reference the 24 correctness properties defined in the design; they are marked optional (`*`) and run at least 100 iterations each, tagged `Feature: daily-stock-screener, Property {number}`.

## Tasks

- [ ] 1. Set up project structure, data models, and testing framework
  - Create the source directory layout: a module per component (universe, data_provider, filters, delta_calculator, report_generator, screener, cli, trading_calendar) plus a shared types/models module.
  - Define the core data types from the design: `Ticker`, `PriceSeries`, `Fundamentals`, `OptionChain`, `Put` (with `premium` = bid when > 0 else last), `EarningsQuarter`, `FilterStatus`, `FilterResult`, `Evaluation`, `RunResult` (including `skipped`, `candidateCount`, `qualifyingCount`, `reportedSet`).
  - Define the `Data_Provider` interface signatures and the `RetrievalError` type used for structural (halting) failures.
  - Add `passed(metrics)` and `excluded(reason, metrics)` constructors for `FilterResult`.
  - Set up the test framework and a property-based testing harness configured for a minimum of 100 iterations per property.
  - _Requirements: 1.2, 2.2, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1_

- [ ] 2. Implement the Delta_Calculator (Black-Scholes)
  - [ ] 2.1 Implement put/call delta and the normal CDF
    - Implement `normalCdf` using a standard erf-based numerical approximation (~1e-7 accuracy).
    - Implement `putDelta(S, K, T, r, sigma)` as `normalCdf(d1) - 1` and `callDelta(S, K, T, r, sigma)` as `normalCdf(d1)`, with `d1 = (ln(S/K) + (r + sigma^2/2)*T) / (sigma*sqrt(T))`.
    - Reject non-positive `S`, `K`, `T`, or `sigma` before computation so an unpriceable strike is treated as unavailable.
    - _Requirements: 4.2_

  - [ ]* 2.2 Write property test for Black-Scholes put delta
    - **Property 8: Black-Scholes put delta is well-formed**
    - Assert put delta lies strictly in (-1, 0), decreases monotonically as strike increases (other inputs fixed), and `callDelta - putDelta == 1`.
    - **Validates: Requirements 4.2**

- [ ] 3. Implement the Trend_Filter
  - [ ] 3.1 Implement net appreciation computation and uptrend decision
    - Implement `evaluate(candidate, runDate, dataProvider)`: fetch `getDailyCloses(candidate, 378)`, exclude with "insufficient price history" when `Empty` or `length < 378`, else compute `appreciation = last/first - 1` and pass iff `appreciation >= 0.15`, recording `netAppreciation`.
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [ ]* 3.2 Write property test for net appreciation formula
    - **Property 2: Net price appreciation formula** — equals `currentClose/startClose - 1` for positive starting close.
    - **Validates: Requirements 2.2**

  - [ ]* 3.3 Write property test for uptrend threshold decision
    - **Property 3: Uptrend threshold decision** — passes iff `currentClose/startClose - 1 >= 0.15` for series of sufficient length.
    - **Validates: Requirements 2.3**

  - [ ]* 3.4 Write property test for insufficient price history exclusion
    - **Property 4: Insufficient price history excludes** — excludes with the insufficient-history reason iff fewer than 378 closes.
    - **Validates: Requirements 2.1, 2.4**

- [ ] 4. Implement the Valuation_Filter
  - [ ] 4.1 Implement earnings, PE, and cash-coverage decision
    - Implement `evaluate`: exclude "missing fundamentals" when `Empty`; exclude "non-positive earnings" when `trailingEarnings <= 0`; else record `peRatio` and pass when `peRatio < 100`; otherwise compute `cashCoverage = totalCash/marketCap`, record it, pass when `>= 1.0`, else exclude "insufficient cash coverage".
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 4.2 Write property test for non-positive earnings exclusion
    - **Property 5: Non-positive earnings excludes** — excludes with the non-positive-earnings reason whenever trailing earnings are not positive.
    - **Validates: Requirements 3.2**

  - [ ]* 4.3 Write property test for the valuation decision
    - **Property 6: Valuation decision** — given positive earnings, passes iff `PE < 100` OR `totalCash/marketCap >= 1.0`, else excludes with insufficient-cash-coverage.
    - **Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.6**

- [ ] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Implement the Options_Filter and its delta fallback
  - [ ] 6.1 Implement expiry selection and the delta-fallback bridge
    - Implement `selectNearestExpiry(expiries, runDate, 30)` minimizing `|daysBetween(runDate, expiry) - 30|`.
    - Implement `ensureDeltas(puts, candidate, runDate, expiry, dataProvider)`: because yfinance option chains generally omit a per-strike delta, the Black-Scholes fallback is the primary delta path — when any put lacks a delta, fetch spot (`getDailyCloses(candidate, 1).last`), risk-free rate, and `T = daysBetween/365`, then fill missing deltas via `Delta_Calculator.putDelta` using per-strike implied vol when present else historical volatility from `getDailyCloses` over `VOL_WINDOW`.
    - _Requirements: 4.1, 4.2_

  - [ ] 6.2 Implement the options pass/exclude decision
    - Implement `evaluate`: exclude "missing options data" when no expiry is within 7 days of the 30-day target or the chain/puts are empty; else apply `ensureDeltas`, select the put minimizing `|abs(delta) - 0.30|`, compute `premiumYield = premium/strike`, record strike/expiry/premium/delta/premiumYield, and pass iff `premiumYield >= 0.025`.
    - _Requirements: 4.3, 4.4, 4.5, 4.6_

  - [ ]* 6.3 Write property test for nearest target expiry selection
    - **Property 7: Nearest target expiry selection** — selected expiry minimizes `|days-from-run-date - 30|`.
    - **Validates: Requirements 4.1**

  - [ ]* 6.4 Write property test for target-delta strike selection
    - **Property 9: Target-delta strike selection** — selected strike minimizes `|abs(delta) - 0.30|`.
    - **Validates: Requirements 4.3**

  - [ ]* 6.5 Write property test for premium yield and threshold decision
    - **Property 10: Premium yield and threshold decision** — `premiumYield == premium/strike`; passes (recording all five metrics) iff `premiumYield >= 0.025`.
    - **Validates: Requirements 4.4, 4.5**

  - [ ]* 6.6 Write property test for missing options data exclusion
    - **Property 11: Missing options data excludes** — excludes with the missing-options-data reason iff no listed expiry is within 7 days of the 30-day target.
    - **Validates: Requirements 4.6**

- [ ] 7. Implement the Earnings_Filter
  - [ ] 7.1 Implement classification, run-length, and cadence checks
    - Implement `evaluate`: exclude "insufficient earnings history" when `Empty` or `length < 4`; classify each quarter as BEAT iff `actualEps >= estimateEps` else MISS; exclude "excessive consecutive misses" when the max MISS run `>= 3`; exclude "broken quarterly cadence" via `hasQuarterGap` on reporting periods; else pass recording `quartersAnalyzed`.
    - Implement helpers `maxConsecutiveRun` and `hasQuarterGap` (compares consecutive `QuarterId` periods, flags any skipped quarter).
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 7.2 Write property test for insufficient earnings history exclusion
    - **Property 12: Insufficient earnings history excludes** — excludes with the insufficient-history reason iff fewer than 4 quarters.
    - **Validates: Requirements 5.2**

  - [ ]* 7.3 Write property test for beat/miss classification
    - **Property 13: Earnings beat/miss classification** — BEAT iff `actualEps >= estimateEps`, else MISS.
    - **Validates: Requirements 5.1, 5.3**

  - [ ]* 7.4 Write property test for consecutive-miss exclusion
    - **Property 14: Consecutive-miss exclusion** — for >= 4 quarters with unbroken cadence, excludes iff a run of >= 3 consecutive misses exists.
    - **Validates: Requirements 5.4**

  - [ ]* 7.5 Write property test for broken-cadence exclusion
    - **Property 15: Broken-cadence exclusion** — excludes with the broken-cadence reason iff the period sequence skips a quarter.
    - **Validates: Requirements 5.5**

  - [ ]* 7.6 Write property test for the earnings pass condition
    - **Property 16: Earnings pass condition** — passes iff >= 4 quarters, no run of >= 3 misses, and unbroken cadence.
    - **Validates: Requirements 5.6**

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Implement the Universe_Builder
  - [ ] 9.1 Implement constituent fetch, normalize, and deduplicate
    - Implement `buildUniverse`: fetch S&P 500 and NASDAQ-100 constituents (the only network boundary here), raise `RetrievalError` on either fetch failure, `normalize` (upper-case + trim) both lists, union with duplicates removed, and record the unique count.
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 9.2 Write property test for the deduplicated union
    - **Property 1: Universe is the deduplicated union** — the Candidate set equals the distinct normalized tickers across both inputs, with no duplicates, and the recorded count equals its size.
    - **Validates: Requirements 1.2, 1.4**

  - [ ]* 9.3 Write integration test for universe fetch failure
    - Using mocked sources, assert a failed constituent fetch raises `RetrievalError` (halting the run).
    - _Requirements: 1.1, 1.3_

- [ ] 10. Implement the Data_Provider gateway
  - [ ] 10.1 Define the Data_Provider interface and the yfinance-backed implementation (used on CI)
    - Define the `Data_Provider` interface: `getDailyCloses`, `getFundamentals`, `getOptionChain`, `getListedExpiries`, `getEarningsHistory`, `getRiskFreeRate`, returning typed validated results and distinguishing `Empty` (per-ticker gap) from `RetrievalError`.
    - Implement `Yfinance_Provider` behind that interface — the implementation the scheduled CI workflow runs — reading Yahoo Finance directly through the `yfinance` library: `getDailyCloses` via a ~2-year daily history download (enough for the 378-trading-day lookback); `getFundamentals` via ticker `info`/financials (trailing earnings, PE_Ratio, total cash, market cap); `getEarningsHistory` via the ticker earnings/earnings-dates history (actual vs. estimate per quarter); `getListedExpiries` via the ticker `options` expiry list and `getOptionChain` via `option_chain(expiry)` puts.
    - Map option-chain rows into `Put` with `premium` = bid when > 0 else last, and surface per-strike implied vol when present; note that yfinance chains omit delta, so delta is left `null` for the Delta_Calculator fallback (task 6.1) to fill.
    - _Requirements: 2.1, 3.1, 4.1, 5.1, 10.3_

  - [ ] 10.2 Add per-run caching, throttling, and bounded retry/backoff
    - Add an in-memory cache keyed by `(ticker, method, args)`, a simple throttle, and bounded retry with backoff; exhausted per-ticker retries return `Empty`.
    - _Requirements: 2.1, 3.1, 4.1, 5.1_

  - [ ]* 10.3 Write integration tests for the Data_Provider boundary
    - With mocked yfinance responses, assert typed parsing, `Empty` on per-ticker gaps, delta left `null` when the chain omits Greeks, cache hits avoid duplicate calls, and retries exhaust to `Empty`.
    - _Requirements: 2.1, 3.1, 4.1, 5.1, 10.3_

  - [ ]* 10.4 Implement the optional stockflow-MCP-backed Data_Provider (on-demand IDE runs)
    - Behind the same `Data_Provider` interface, implement `Stockflow_Provider` over the local `stockflow` MCP server (read-only, Yahoo-sourced) for on-demand IDE runs only; the scheduled/CI path does not need it, so this is lower priority and optional.
    - _Requirements: 4.1_

- [ ] 11. Implement the Screener orchestrator (self-skip, evaluation, ranking, limiting)
  - [ ] 11.1 Implement the non-trading-day gate and per-candidate evaluation loop
    - Implement `run(runDate)`: first call `tradingCalendar.isTradingDay(runDate)` and self-skip (return `RunResult(runDate, skipped=true)` with no new dated report, exit without error) when it is not a Trading_Day; otherwise build the universe (propagating `RetrievalError` to halt), evaluate each candidate, and continue to ranking.
    - Implement `evaluateCandidate`: run filters in the fixed order Trend → Valuation → Options → Earnings, merge recorded metrics, short-circuit on the first `EXCLUDED` result retaining its reason, and qualify only when all four pass; per-candidate provider gaps surface as `EXCLUDED`, so the loop always continues.
    - Implement `tradingCalendar` encapsulating the U.S. weekend/holiday calendar.
    - Wire the four filters, the Data_Provider, and the trading calendar into the Screener.
    - _Requirements: 6.1, 6.2, 6.3, 10.4_

  - [ ] 11.2 Implement rankAndLimit (Premium_Ranking and Reported_Set)
    - Implement `rankAndLimit(qualifying)`: order Qualifying_Tickers by `premiumYield` descending with ties broken by ticker symbol ascending (deterministic Premium_Ranking), then take the first `REPORT_LIMIT = 20` entries as the `Reported_Set` — all of them when fewer than 20 qualify.
    - Wire `rankAndLimit` into `run` so the `RunResult` carries `candidateCount`, total `qualifyingCount`, and the ranked `reportedSet`, and pass them to the Report_Generator.
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 11.3 Write property test for qualification requiring all filters
    - **Property 17: Qualification requires all filters** — qualifies iff all four filters pass.
    - **Validates: Requirements 6.1**

  - [ ]* 11.4 Write property test for first-exclusion-reason retention
    - **Property 18: First exclusion reason is retained** — retains the reason of the first failing filter in the fixed order.
    - **Validates: Requirements 6.2**

  - [ ]* 11.5 Write property test for the Premium_Ranking ordering
    - **Property 23: Premium_Ranking is deterministic premium-descending with alphabetical tie-break** — orders Qualifying_Tickers by Premium_Yield non-increasing, with equal yields ordered by ticker symbol ascending, as a deterministic function of the input set.
    - **Validates: Requirements 7.1, 7.2**

  - [ ]* 11.6 Write property test for the Reported_Set top-REPORT_LIMIT prefix
    - **Property 24: Reported_Set is the top-REPORT_LIMIT prefix of the ranking** — equals the first 20 entries of the Premium_Ranking when more than 20 qualify, and the entire ranking (all Qualifying_Tickers, in ranked order) when 20 or fewer qualify.
    - **Validates: Requirements 7.3, 7.4**

  - [ ]* 11.7 Write property test for the non-trading-day self-skip
    - **Property 22: Non-trading-day runs produce no new dated report** — with a controlled calendar, `Screener.run` produces a new dated report iff the run date is a Trading_Day; on a non-trading day it self-skips, produces no new dated report, and exits without error.
    - **Validates: Requirements 10.4**

- [ ] 12. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Implement the Report_Generator
  - [ ] 13.1 Implement dated CSV and summary writers over the Reported_Set
    - Implement `write(runDate, candidateCount, qualifyingCount, reportedSet)`: write the CSV with header + one row per `Reported_Set` ticker in Premium_Ranking (ranked) order, with columns `ticker, netAppreciation, peRatio, strike, expiry, premium, delta, premiumYield`.
    - Write the summary stating the total Candidate count, the total Qualifying_Ticker count, the Reported_Set count, and one rationale per Reported_Set ticker in ranked order; when `qualifyingCount` exceeds the Reported_Set count, state that the set is capped at `REPORT_LIMIT` (20) and report how many qualifiers were omitted (`qualifyingCount - reportedSet.count`); handle the zero-qualifier case with empty data and a "no stocks qualified" message.
    - Implement `reportName(runDate, ext)` writing into the `reports/` `Reports_Directory` and embedding the run date as `YYYY-MM-DD` in both filenames.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 13.2 Write property test for CSV round-trip
    - **Property 19: CSV round-trip preserves the ranked Reported_Set** — writing then parsing yields rows equal to the inputs in the same ranked order with all required columns present.
    - **Validates: Requirements 8.1**

  - [ ]* 13.3 Write property test for summary counts and rationale
    - **Property 20: Summary contains counts and per-ticker rationale for the Reported_Set** — summary contains the total Candidate count, the total Qualifying_Ticker count, the Reported_Set count, and exactly one rationale per Reported_Set ticker.
    - **Validates: Requirements 8.2**

  - [ ]* 13.4 Write property test for filename run-date embedding
    - **Property 21: Report filenames embed the run date** — both filenames contain the formatted run date.
    - **Validates: Requirements 8.5**

  - [ ]* 13.5 Write example test for the cap/omitted and zero-qualifier cases
    - Assert that when more than 20 qualify the summary reports the cap and the omitted count, and that the zero-qualifier case writes both files with empty result sets and a "no stocks qualified" summary.
    - _Requirements: 8.3, 8.4_

- [ ] 14. Implement the CLI entry point
  - [ ] 14.1 Wire the CLI to the Screener
    - Implement `main(args)`: set `runDate = today()`, run the Screener with a progress callback that prints progress, print the CSV and summary paths and return exit code 0 on success (including the non-trading-day self-skip, which exits 0 without new report files), and on `RetrievalError` print the error and return a non-zero exit code.
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ]* 14.2 Write integration tests for the CLI run
    - Assert successful runs print output paths and exit 0, progress is reported during the run, a non-trading-day self-skip exits 0 without producing new report files, and a retrieval error prints the error and exits non-zero.
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [ ] 15. Author the Scheduled_Workflow (GitHub Actions)
  - [ ] 15.1 Author `.github/workflows/daily-stock-screener.yml`
    - Author the workflow with triggers `schedule` (a weekday cron set with margin ahead of the ~09:30 ET US market open, acknowledging cron drift) and `workflow_dispatch` (manual trigger runs the identical screening path), and `permissions: contents: write`.
    - Add steps: checkout the repo; set up the runtime and install dependencies including `yfinance`; run the Screener CLI (which self-skips non-trading days and exits non-zero on a halting error); on success only, `git add reports/` and commit the dated report files into the `Reports_Directory` and push; upload the same dated report files as a `screener-reports` CI artifact.
    - Ensure the commit step runs only on a successful run so a partial/empty report is never committed over prior reports.
    - _Requirements: 10.1, 10.2, 10.3, 10.5, 10.6, 10.7, 10.8_

  - [ ]* 15.2 Validate the workflow file is well-formed
    - Add a test/check that parses `.github/workflows/daily-stock-screener.yml` and asserts it is valid YAML containing the `schedule` cron and `workflow_dispatch` triggers, `permissions: contents: write`, the yfinance install step, the CLI run step, the conditional commit-into-`reports/` step, and the artifact-upload step.
    - _Requirements: 10.2, 10.3, 10.5, 10.6, 10.7, 10.8_

  - [ ]* 15.3 Write a dry-run example test for the CLI the workflow invokes
    - With mocked providers and a controlled calendar, invoke the same CLI entry point the workflow runs and assert a non-trading-day dry run exits 0 with no new report files committed, acknowledging that full CI scheduling behavior (cron firing, laptop-off execution) is validated on GitHub rather than locally.
    - _Requirements: 10.1, 10.4_

- [ ] 16. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP; all 24 correctness properties are covered by these optional sub-tasks, each mapped to exactly one property-test sub-task.
- Each task references specific requirements for traceability, and each property sub-task names the design property it validates.
- Property tests run at least 100 iterations each and are tagged `Feature: daily-stock-screener, Property {number}`.
- Checkpoints ensure incremental validation at natural boundaries (pure units, data boundaries, orchestration with ranking, entry points).
- All network access is confined to Universe_Builder and the Data_Provider implementations; filters, ranking, and reporting are exercised with generated inputs and mocked providers.
- The scheduled/CI path uses the yfinance-backed Data_Provider; the stockflow-MCP-backed implementation (task 10.4) is optional and only for on-demand IDE runs.
- Full CI scheduling behavior (cron firing, laptop-off execution, commit/push, artifact retention) is validated on GitHub Actions rather than locally; local tests cover the CLI the workflow invokes and the well-formedness of the workflow file.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "7.1", "9.1", "10.1"] },
    { "id": 2, "tasks": ["2.2", "3.2", "3.3", "3.4", "4.2", "4.3", "7.2", "7.3", "7.4", "7.5", "7.6", "9.2", "9.3", "10.2", "10.4"] },
    { "id": 3, "tasks": ["6.1", "6.2", "10.3", "11.1"] },
    { "id": 4, "tasks": ["6.3", "6.4", "6.5", "6.6", "11.2", "11.3", "11.4", "11.7"] },
    { "id": 5, "tasks": ["11.5", "11.6", "13.1"] },
    { "id": 6, "tasks": ["13.2", "13.3", "13.4", "13.5", "14.1"] },
    { "id": 7, "tasks": ["14.2", "15.1"] },
    { "id": 8, "tasks": ["15.2", "15.3"] }
  ]
}
```
