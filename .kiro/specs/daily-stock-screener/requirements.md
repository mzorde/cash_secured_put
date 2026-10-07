# Requirements Document

## Introduction

The Daily Stock Screener is an on-demand and scheduled analysis system that scans a universe of U.S. equities (the union of S&P 500 and NASDAQ-100 constituents) and identifies stocks suitable for a cash-secured put selling strategy, acting in the role of an experienced financial adviser. A stock qualifies only when it satisfies four independent filters: a net uptrend over approximately the past 1.5 years, a valuation-and-earnings health check, a cash-secured put premium threshold, and an earnings-consistency check. The system fetches market data from Yahoo Finance, computes option deltas locally via the Black-Scholes model when option Greeks are unavailable, and produces a machine-readable CSV plus a human-readable summary report. The system runs on demand through a command-line interface and also runs on a hosted GitHub Actions scheduled workflow once per trading day ahead of U.S. market open; because the workflow executes on hosted CI runners, the scheduled run proceeds even when the user's laptop is powered off, committing each day's report into a version-controlled reports/ directory in the repository and uploading the same report files as a per-run CI artifact.

Market data access differs by run mode. On the hosted CI scheduled run, the Screener obtains market data directly through the yfinance library (the same Yahoo Finance data source), because hosted CI runners cannot launch the user's local process. The local stockflow MCP server remains available only for on-demand runs inside the user's IDE. The screening logic and filter criteria are identical regardless of the data transport.

The initial build does not integrate the Schwab API and does not include any email or alerting layer.

## Glossary

- **Screener**: The overall system that scans the stock universe, applies all filters, and produces reports.
- **Universe_Builder**: The component that fetches, unions, and deduplicates S&P 500 and NASDAQ-100 constituents into the candidate ticker set.
- **Data_Provider**: The component that retrieves price history, fundamentals, earnings history, and options chains from Yahoo Finance; on hosted CI scheduled runs it reads directly through the yfinance library, and on on-demand IDE runs it may read through the local stockflow MCP server.
- **Trend_Filter**: The component that evaluates net price appreciation over the lookback window.
- **Valuation_Filter**: The component that evaluates earnings positivity, the price-to-earnings ratio, and the cash-coverage fallback.
- **Options_Filter**: The component that evaluates cash-secured put premiums at a target delta and expiry.
- **Earnings_Filter**: The component that evaluates earnings-beat consistency and quarterly cadence.
- **Delta_Calculator**: The component that computes option delta locally using the Black-Scholes model when provided Greeks are unavailable.
- **Report_Generator**: The component that produces the CSV output and the human-readable summary.
- **Scheduled_Workflow**: The hosted GitHub Actions workflow, triggered on a cron schedule and on manual dispatch, that runs the Screener on CI runners independent of the user's local machine, commits the resulting report files into the repository's reports/ directory, and uploads them as a CI artifact.
- **CI_Runner**: A GitHub Actions hosted compute environment that executes the Scheduled_Workflow independently of the user's local machine.
- **CI_Artifact**: The set of report files uploaded and retained against a single Scheduled_Workflow run.
- **Reports_Directory**: The version-controlled reports/ directory in the repository into which the Scheduled_Workflow commits each day's dated report files.
- **CLI**: The command-line interface through which a user triggers an on-demand screening run.
- **Candidate**: A ticker in the deduplicated universe that is evaluated against all filters.
- **Qualifying_Ticker**: A Candidate that passes all four filters (Trend, Valuation, Options, Earnings).
- **Premium_Ranking**: The ordering of all Qualifying_Tickers by Premium_Yield in descending order, with ties broken by ticker symbol in ascending alphabetical order, so that the ordering is deterministic.
- **Report_Limit**: The maximum number of Qualifying_Tickers included in the Reported_Set, fixed at 20.
- **Reported_Set**: The final, ordered set of Qualifying_Tickers included in the output, formed by taking the first Report_Limit entries of the Premium_Ranking, or all Qualifying_Tickers when fewer than Report_Limit qualify.
- **Lookback_Window**: The historical price window of approximately 378 trading days (about 1.5 years) used by the Trend_Filter.
- **Uptrend**: A condition in which the current price is at least 15% higher than the price at the start of the Lookback_Window; the intermediate path need not be monotonic.
- **PE_Ratio**: The trailing price-to-earnings ratio of a Candidate.
- **Cash_Coverage_Metric**: Total cash and cash equivalents divided by total market capitalization, used as the fallback test when PE_Ratio is at or above 100.
- **Cash_Secured_Put**: A short put option position fully collateralized by cash equal to the strike price times the contract multiplier.
- **Target_Delta**: An absolute put delta of 0.30 (30%).
- **Target_Expiry**: The listed option expiration date nearest to 30 calendar days from the run date.
- **Premium_Yield**: The option premium (bid or last price) divided by the strike price, expressed as a decimal fraction.
- **Earnings_Beat**: A reported quarter whose actual earnings per share meets or exceeds the consensus estimate.
- **Earnings_Miss**: A reported quarter whose actual earnings per share is below the consensus estimate.
- **Trading_Day**: A day on which U.S. equity markets are open for regular trading.
- **Report_Archive**: The durable, dated store of previously generated reports, consisting of the version-controlled Reports_Directory in the repository (which retains report history through version control) together with the per-run CI_Artifacts.

## Requirements

### Requirement 1: Universe Construction

**User Story:** As an investor, I want the system to automatically assemble the candidate stock universe from S&P 500 and NASDAQ-100 constituents, so that I screen a current and comprehensive set of large-cap stocks without maintaining the list manually.

#### Acceptance Criteria

1. WHEN a screening run starts, THE Universe_Builder SHALL fetch the current S&P 500 constituent list and the current NASDAQ-100 constituent list from public sources.
2. WHEN both constituent lists are retrieved, THE Universe_Builder SHALL produce the Candidate set as the union of the two lists with duplicate tickers removed.
3. IF a constituent list cannot be retrieved, THEN THE Universe_Builder SHALL record a retrieval error and halt the run with a non-zero exit status.
4. WHEN the Candidate set is produced, THE Universe_Builder SHALL record the total count of unique Candidates in the run output.

### Requirement 2: Trend Filter

**User Story:** As an investor, I want to include only stocks in a net uptrend over the past 1.5 years, so that I sell puts on names with established upward price momentum.

#### Acceptance Criteria

1. WHEN evaluating a Candidate, THE Trend_Filter SHALL retrieve daily closing prices covering a Lookback_Window of approximately 378 Trading_Days.
2. WHEN the Lookback_Window prices are available, THE Trend_Filter SHALL compute net price appreciation as the current closing price divided by the closing price at the start of the Lookback_Window minus one.
3. WHERE net price appreciation is at least 15%, THE Trend_Filter SHALL mark the Candidate as passing the Uptrend condition.
4. IF fewer than 378 Trading_Days of closing prices are available for a Candidate, THEN THE Trend_Filter SHALL exclude the Candidate and record insufficient price history as the exclusion reason.

### Requirement 3: Valuation and Earnings Health Filter

**User Story:** As an investor, I want to include only companies with positive earnings and a reasonable valuation, or sufficient cash reserves when valuation is high, so that I avoid overvalued or unprofitable companies.

#### Acceptance Criteria

1. WHEN evaluating a Candidate, THE Valuation_Filter SHALL retrieve the Candidate's trailing earnings, PE_Ratio, total cash and cash equivalents, and total market capitalization.
2. IF a Candidate's trailing earnings are not positive, THEN THE Valuation_Filter SHALL exclude the Candidate and record non-positive earnings as the exclusion reason.
3. WHERE a Candidate's PE_Ratio is below 100, THE Valuation_Filter SHALL mark the Candidate as passing the valuation condition.
4. WHILE a Candidate's PE_Ratio is at or above 100, THE Valuation_Filter SHALL compute the Cash_Coverage_Metric as total cash and cash equivalents divided by total market capitalization.
5. WHERE a Candidate's PE_Ratio is at or above 100 and the Cash_Coverage_Metric is at least 1.0, THE Valuation_Filter SHALL mark the Candidate as passing the valuation condition.
6. IF a Candidate's PE_Ratio is at or above 100 and the Cash_Coverage_Metric is below 1.0, THEN THE Valuation_Filter SHALL exclude the Candidate and record insufficient cash coverage as the exclusion reason.

### Requirement 4: Cash-Secured Put Options Filter

**User Story:** As an investor, I want to include only stocks offering at least a 2.5% premium on a 30-day cash-secured put near 30% delta, so that the income from selling puts meets my yield target.

#### Acceptance Criteria

1. WHEN evaluating a Candidate, THE Options_Filter SHALL retrieve the Candidate's put option chain from the Data_Provider for the Target_Expiry nearest to 30 calendar days from the run date.
2. WHERE the retrieved option chain does not include a put delta value, THE Delta_Calculator SHALL compute the put delta for each strike using the Black-Scholes model with implied volatility, or with historical volatility when implied volatility is unavailable.
3. WHEN put deltas are available for the Target_Expiry, THE Options_Filter SHALL select the put strike whose absolute delta is closest to the Target_Delta of 0.30.
4. WHEN the Target_Delta put strike is selected, THE Options_Filter SHALL compute the Premium_Yield as the put option premium divided by the strike price.
5. WHERE the Premium_Yield is at least 0.025, THE Options_Filter SHALL mark the Candidate as passing the options condition and record the selected strike, expiry, premium, delta, and Premium_Yield.
6. IF no put option chain is available for a Target_Expiry within 7 calendar days of the 30-day target, THEN THE Options_Filter SHALL exclude the Candidate and record missing options data as the exclusion reason.

### Requirement 5: Earnings Consistency Filter

**User Story:** As an investor, I want to include only companies that consistently beat earnings and report every quarter, so that I avoid names with deteriorating or erratic earnings performance.

#### Acceptance Criteria

1. WHEN evaluating a Candidate, THE Earnings_Filter SHALL retrieve the Candidate's reported quarterly earnings history including actual earnings per share and consensus estimate per quarter.
2. IF a Candidate has fewer than 4 quarters of reported earnings history, THEN THE Earnings_Filter SHALL exclude the Candidate and record insufficient earnings history as the exclusion reason.
3. WHEN 4 or more quarters of earnings history are available, THE Earnings_Filter SHALL classify each reported quarter as an Earnings_Beat or an Earnings_Miss by comparing actual earnings per share against the consensus estimate.
4. IF a Candidate's earnings history contains 3 or more consecutive Earnings_Misses, THEN THE Earnings_Filter SHALL exclude the Candidate and record excessive consecutive misses as the exclusion reason.
5. IF a Candidate's reported earnings history skips a quarter in the expected quarterly cadence, THEN THE Earnings_Filter SHALL exclude the Candidate and record a broken quarterly cadence as the exclusion reason.
6. WHERE a Candidate has at least 4 quarters of history, no 3 consecutive Earnings_Misses, and an unbroken quarterly cadence, THE Earnings_Filter SHALL mark the Candidate as passing the earnings condition.

### Requirement 6: Qualification Aggregation

**User Story:** As an investor, I want a stock to be flagged only when it passes every filter, so that the final list contains only names meeting all of my criteria.

#### Acceptance Criteria

1. WHERE a Candidate passes the Trend_Filter, the Valuation_Filter, the Options_Filter, and the Earnings_Filter, THE Screener SHALL designate the Candidate as a Qualifying_Ticker.
2. IF a Candidate fails any one filter, THEN THE Screener SHALL exclude the Candidate from the Qualifying_Ticker set and retain the first recorded exclusion reason.
3. WHEN a Candidate is excluded, THE Screener SHALL continue evaluating the remaining Candidates.

### Requirement 7: Ranking and Limiting

**User Story:** As an investor, I want qualifying stocks ranked by put premium yield and trimmed to the strongest 20, so that I see the highest-income cash-secured put opportunities first and am not overwhelmed by a long list.

#### Acceptance Criteria

1. WHEN all Qualifying_Tickers have been determined, THE Screener SHALL order the Qualifying_Tickers by Premium_Yield in descending order to form the Premium_Ranking.
2. WHERE two or more Qualifying_Tickers have an equal Premium_Yield, THE Screener SHALL order those Qualifying_Tickers by ticker symbol in ascending alphabetical order.
3. WHEN the Premium_Ranking is formed, THE Screener SHALL produce the Reported_Set as the first Report_Limit of 20 Qualifying_Tickers in the Premium_Ranking.
4. WHERE the Qualifying_Ticker count is below the Report_Limit of 20, THE Screener SHALL include all Qualifying_Tickers in the Reported_Set in Premium_Ranking order.

### Requirement 8: Report Generation

**User Story:** As an investor, I want each run to produce a CSV of the top-ranked qualifying tickers and a readable summary, so that I can act on the highest-income opportunities first and review the reasoning.

#### Acceptance Criteria

1. WHEN a screening run completes, THE Report_Generator SHALL write a CSV file listing each Qualifying_Ticker in the Reported_Set, in Premium_Ranking order, with its net price appreciation, PE_Ratio, selected put strike, Target_Expiry, premium, delta, and Premium_Yield.
2. WHEN a screening run completes, THE Report_Generator SHALL write a human-readable summary that states the total Candidate count, the total Qualifying_Ticker count, the Reported_Set count, and a per-ticker rationale for each Qualifying_Ticker in the Reported_Set in Premium_Ranking order.
3. WHERE the total Qualifying_Ticker count exceeds the Reported_Set count, THE Report_Generator SHALL state in the summary that the Reported_Set is capped at the Report_Limit of 20 and report how many Qualifying_Tickers were omitted.
4. WHEN a screening run completes with zero Qualifying_Tickers, THE Report_Generator SHALL write both report files with empty result sets and a summary stating that no stocks qualified.
5. WHEN report files are written, THE Report_Generator SHALL name each file with the run date.

### Requirement 9: On-Demand CLI Execution

**User Story:** As an investor, I want to trigger a screening run from the command line, so that I can run the screener whenever I choose.

#### Acceptance Criteria

1. WHEN the CLI is invoked by a user, THE Screener SHALL start a screening run and produce the report files for the current run date.
2. WHILE a screening run is in progress, THE CLI SHALL report run progress to the user.
3. WHEN a screening run completes successfully, THE CLI SHALL report the output file paths and exit with a zero exit status.
4. IF a screening run halts due to a data retrieval error, THEN THE CLI SHALL report the error and exit with a non-zero exit status.

### Requirement 10: Hosted Scheduled Execution via GitHub Actions

**User Story:** As an investor, I want the screener to run automatically on a hosted CI schedule each trading day ahead of market open and keep past reports under version control, so that I have fresh results even when my laptop is powered off and can review the committed history.

#### Acceptance Criteria

1. THE Scheduled_Workflow SHALL be triggered on a GitHub Actions cron schedule once per Trading_Day and SHALL execute the Screener on a CI_Runner independent of the user's local machine, so that the run proceeds while the user's local machine is powered off.
2. THE Scheduled_Workflow SHALL set the cron schedule with margin ahead of U.S. equity market open, and the requirement SHALL NOT assume exact-to-the-minute trigger timing, because CI cron scheduling can drift.
3. WHEN the Scheduled_Workflow run obtains market data, THE Data_Provider SHALL read directly through the yfinance library on the CI_Runner rather than through the local stockflow MCP server.
4. IF the scheduled run date is not a Trading_Day, THEN THE Screener SHALL detect the non-trading day and exit without producing a new dated report, even though the cron schedule fired.
5. WHEN a scheduled screening run completes successfully, THE Scheduled_Workflow SHALL commit the dated CSV and summary report files into the Reports_Directory of the repository under the run date.
6. WHEN a scheduled screening run completes successfully, THE Scheduled_Workflow SHALL upload the same dated CSV and summary report files as a CI_Artifact for the run.
7. IF the scheduled screening run fails, THEN THE Scheduled_Workflow SHALL surface a failing run status and SHALL preserve previously committed reports by not committing a partial or empty report over them.
8. WHERE a user triggers the Scheduled_Workflow manually through the GitHub Actions workflow_dispatch trigger, THE Scheduled_Workflow SHALL execute the same screening run as the scheduled trigger, in addition to the on-demand local CLI execution described in Requirement 9.

## Assumptions

- The repository is hosted on GitHub with GitHub Actions enabled.
- The Scheduled_Workflow is granted contents-write permission so that it can commit report files into the Reports_Directory.
