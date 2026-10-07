# Design Document

## Overview

The Daily Stock Screener evaluates the union of S&P 500 and NASDAQ-100 constituents against four independent filters — Trend, Valuation, Options, and Earnings — ranks the stocks that pass all four by cash-secured put premium yield, trims the ranking to the strongest 20, and emits a dated CSV plus a human-readable summary for that reported set. It runs on demand through a CLI and automatically each trading day before market open via a hosted GitHub Actions workflow that commits each run's reports into a version-controlled `reports/` directory and uploads them as a CI artifact.

The architecture separates concerns into a thin orchestrator (`Screener`), a universe assembler (`Universe_Builder`), a single market-data gateway (`Data_Provider`), four stateless filters, a `Delta_Calculator` implementing Black-Scholes, a `Report_Generator`, a `CLI`, and a hosted `Scheduled_Workflow`. Each filter is a pure decision over data supplied by the `Data_Provider`, which keeps the business logic independently testable and insulated from the quirks of the external data source.

The `Data_Provider` is defined as an interface with two interchangeable implementations behind the same contract. On the hosted CI scheduled run, a **yfinance-backed** implementation reads Yahoo Finance data directly through the `yfinance` library, because hosted CI runners cannot launch the user's local process. For on-demand IDE runs an optional **stockflow-MCP-backed** implementation (over the `stockflow` MCP server configured in `.kiro/settings/mcp.json`) may be used instead. Both implementations are read-only and source the same Yahoo Finance data; the screening logic and filter criteria are identical regardless of transport. `Universe_Builder` uses neither provider: it fetches constituent lists directly from public S&P 500 / NASDAQ-100 sources.

Code examples below use Structured Pseudocode because the workspace has no established language. The pseudocode fixes the contracts (names, inputs, outputs, decisions) without binding the implementation to a specific runtime.

### Design Goals

- **Isolation of external data.** All network access is confined to `Universe_Builder` (public constituent sources) and `Data_Provider` (yfinance on CI, or the stockflow MCP server for on-demand IDE runs). Filters never touch the network or any provider directly, so they can be exercised with generated inputs.
- **Pure, total filter functions.** Each filter maps supplied data to a `FilterResult` (pass/exclude + reason + recorded metrics) with no hidden state and no exceptions for ordinary "excluded" outcomes.
- **Fail-closed on structural failures, fail-soft on per-ticker gaps.** A universe fetch failure halts the whole run (non-zero exit). A missing data point for a single candidate excludes only that candidate and the run continues.
- **Deterministic reporting.** Given the same evaluation results and run date, the CSV and summary are byte-reproducible, which makes round-trip testing meaningful.

## Architecture

### Component Diagram

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │  Scheduled_Workflow  (hosted GitHub Actions, on CI_Runner)            │
   │  cron + workflow_dispatch → checkout → setup runtime → run CLI        │
   │                           → commit reports/ → upload CI_Artifact      │
   └───────────────────────────────────┬───────────────────────────────────┘
                                        │ invokes (laptop-off capable)
                                        ▼
                                   ┌───────────┐
                                   │    CLI    │   (also invoked on demand)
                                   └─────┬─────┘
                                         │  run(runDate)
                                         ▼
                                   ┌──────────────┐
                                   │   Screener   │  (orchestrator)
                                   └──────┬───────┘   self-skips non-trading days
             ┌───────────────────────────┼───────────────────────────────┐
             ▼                            ▼                               ▼
    ┌─────────────────┐          ┌─────────────────┐            ┌──────────────────┐
    │ Universe_Builder│          │  Data_Provider  │            │ Report_Generator │
    └─────────────────┘          └────────┬────────┘            └────────┬─────────┘
      (S&P500 ∪ NDX100,          (interface; one impl)                   │
       public sources)                    ▼                       writes dated CSV +
                            ┌──────────────────────────┐          summary into reports/
                            │ yfinance library (on CI)  │  OR      (committed) + artifact
                            │ stockflow MCP (IDE runs)  │  read-only, Yahoo-sourced
                            └──────────────────────────┘
             ┌──────────────┬───────────────┬────────────────┬───────────────┐
             ▼              ▼               ▼                 ▼               ▼
      ┌───────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
      │Trend_Filter│ │Valuation_... │ │Options_Filter│ │Earnings_...  │ │ Delta_Calculator │
      └───────────┘ └──────────────┘ └──────┬───────┘ └──────────────┘ └────────▲─────────┘
                                            └── delta via Black-Scholes (PRIMARY on CI; ──┘
                                                yfinance chains omit delta)
```

### Data Flow (one screening run)

1. **Entry.** The `CLI` is the single entry point. It is invoked on demand by a user, or by the hosted `Scheduled_Workflow` on a CI_Runner (cron or `workflow_dispatch`). The CLI calls `Screener.run(runDate)`.
2. **Trading-day gate.** `Screener` first checks whether `runDate` is a `Trading_Day`. If it is not, the run self-skips: it produces no new dated report and exits without error, even though the cron schedule fired.
3. **Universe.** `Screener` asks `Universe_Builder` for the deduplicated candidate set. If either constituent list cannot be retrieved, the builder raises a retrieval failure; `Screener` halts the run and the entry point exits non-zero.
4. **Per-candidate evaluation.** For each `Candidate`, `Screener` evaluates the four filters in a fixed order — Trend → Valuation → Options → Earnings — short-circuiting on the first exclusion. Each filter pulls what it needs from `Data_Provider`, which maps the request onto its backing implementation (yfinance on CI, or the stockflow MCP server for on-demand IDE runs).
   - A per-ticker data gap or provider error — an `Empty`/`None` payload, a provider-level failure, or a tool error — becomes a `FilterResult` of `EXCLUDED` with the appropriate reason; it never aborts the run.
   - The `Options_Filter` uses a provider-supplied delta when one is present; yfinance option chains generally do **not** include a delta, so on CI the `Delta_Calculator` Black-Scholes fallback is the primary source of delta.
5. **Aggregation.** A candidate passing all four filters becomes a `Qualifying_Ticker`; otherwise it is excluded with the first recorded reason. The loop continues over all remaining candidates.
6. **Ranking and limiting.** Once all candidates are evaluated, `Screener` orders every `Qualifying_Ticker` by `Premium_Yield` descending (ties broken by ticker symbol ascending) to form the `Premium_Ranking`, then takes the first `REPORT_LIMIT` (20) entries as the `Reported_Set` — all of them when fewer than 20 qualify.
7. **Reporting.** `Report_Generator` writes the dated CSV and the summary over the `Reported_Set` (including the empty-result case).
8. **Commit / archive / exit.** On a successful run the `Scheduled_Workflow` commits the dated report files into the version-controlled `Reports_Directory` (`reports/`) and uploads them as a `CI_Artifact`; together these form the `Report_Archive`. The `CLI` prints the output paths and exits zero. On a halting error the CLI exits non-zero, which surfaces on CI as a failing workflow that commits nothing over prior reports.

### Evaluation Order and Short-Circuiting

Filters run in a fixed, cheap-to-expensive order so that the common early rejections (trend, valuation) run before the more expensive options-chain retrieval and delta computation. Because the first exclusion reason is the one retained (Requirement 6.2), this order is part of the contract, not an optimization detail.

## Components and Interfaces

### Screener (Orchestrator)

Owns the run lifecycle and the per-candidate evaluation loop. Holds no market data of its own.

```
component Screener:
    constant REPORT_LIMIT = 20

    dependencies: universeBuilder, dataProvider, filters[4] (ordered),
                  reportGenerator, tradingCalendar

    function run(runDate) -> RunResult:
        if not tradingCalendar.isTradingDay(runDate):       # Req 10.4: self-skip
            return RunResult(runDate, skipped=true)          # no new dated report; exit zero
        candidates = universeBuilder.buildUniverse()        # may raise RetrievalError -> halt
        evaluations = []
        for candidate in candidates:
            evaluations.append(evaluateCandidate(candidate, runDate))
        qualifying = [e for e in evaluations if e.qualified]
        reportedSet = rankAndLimit(qualifying)              # Premium_Ranking, trimmed to top 20
        reportGenerator.write(runDate, candidates.count, qualifying.count, reportedSet)
        return RunResult(runDate, candidates.count, qualifying.count, reportedSet)

    function rankAndLimit(qualifying) -> list<Evaluation>:
        # Premium_Ranking: Premium_Yield descending, ties broken by ticker ascending.
        ranked = sort(qualifying, by = (desc(e.metrics.premiumYield), asc(e.ticker)))
        return ranked[0 : REPORT_LIMIT]                     # all of them when count < REPORT_LIMIT

    function evaluateCandidate(candidate, runDate) -> Evaluation:
        recordedMetrics = {}
        for filter in filters:                               # Trend, Valuation, Options, Earnings
            result = filter.evaluate(candidate, runDate, dataProvider)
            recordedMetrics.merge(result.metrics)
            if result.status == EXCLUDED:
                return Evaluation(candidate, qualified=false,
                                  reason=result.reason, metrics=recordedMetrics)
        return Evaluation(candidate, qualified=true, reason=null, metrics=recordedMetrics)
```

`evaluateCandidate` guarantees Requirement 6: AND-aggregation (6.1), first-reason retention via short-circuit (6.2), and loop continuation because each candidate is evaluated independently and provider errors surface as `EXCLUDED` results rather than thrown exceptions (6.3).

### Universe_Builder

```
component Universe_Builder:
    function buildUniverse() -> list<Ticker>:
        sp500 = fetchConstituents(SP500_SOURCE)             # raise RetrievalError on failure
        ndx100 = fetchConstituents(NDX100_SOURCE)            # raise RetrievalError on failure
        union = deduplicate(normalize(sp500) + normalize(ndx100))
        recordCount(union.length)
        return union
```

- `normalize` upper-cases and trims tickers so case/whitespace variants dedupe correctly.
- `fetchConstituents` is the only network boundary here; a failure propagates as `RetrievalError` to halt the run (Requirement 1.3).
- The deduplicated set and its count satisfy Requirements 1.2 and 1.4.

### Data_Provider

An **interface** — the single read-only gateway over Yahoo Finance — with two interchangeable implementations behind the same contract. Every method returns typed, validated results and distinguishes "no data" (an empty/`None` payload the filter treats as an exclusion) from "retrieval failed" (a `RetrievalError` for structural failures only used by `Universe_Builder`; per-ticker fetches return empty rather than raising).

```
interface Data_Provider:
    getDailyCloses(ticker, lookbackDays) -> PriceSeries | Empty
    getFundamentals(ticker) -> Fundamentals | Empty
    getOptionChain(ticker, expiry) -> OptionChain | Empty
    getListedExpiries(ticker) -> list<Date> | Empty
    getEarningsHistory(ticker) -> list<EarningsQuarter> | Empty
    getRiskFreeRate() -> Rate
```

#### Implementations

- **Yfinance_Provider (used on CI).** The implementation the `Scheduled_Workflow` runs on a CI_Runner, reading Yahoo Finance directly through the `yfinance` library. Methods map onto yfinance as follows:
  - `getDailyCloses` → a history download covering roughly the last 2 years of daily closes (enough to satisfy the 378-trading-day Lookback_Window).
  - `getFundamentals` → ticker `info` / financials (trailing earnings, PE_Ratio, total cash, market cap).
  - `getEarningsHistory` → the ticker earnings / earnings-dates history (actual vs. estimate per quarter).
  - `getListedExpiries` → the ticker `options` expiry list; `getOptionChain` → the `option_chain(expiry)` puts table.
- **Stockflow_Provider (optional, on-demand IDE runs).** Behind the same interface, an implementation over the local `stockflow` MCP server configured in `.kiro/settings/mcp.json` (read-only tools, Yahoo-sourced). Available only inside the user's IDE, never on hosted CI, because the CI_Runner cannot launch the user's local process.

Delta is handled identically for both: yfinance option chains generally omit a per-strike delta, so the `Delta_Calculator` Black-Scholes fallback is the **primary** delta source on CI. A provider-supplied delta is used only when one is actually present.

#### Known limitations

- Data is Yahoo-sourced, so it may be delayed, incomplete, or rate-limited; a per-ticker gap maps to the `Empty` → exclude-candidate fail-soft path.
- No dividend data is incorporated.
- The Black-Scholes delta uses the European no-dividend form and does not model American early exercise.

Design notes:
- **Caching / rate limiting.** A per-run in-memory cache keyed by `(ticker, method, args)` avoids duplicate calls across filters. A simple throttle guards against provider rate limits.
- **Retry with backoff.** Transient failures are retried a small, bounded number of times before being treated as `Empty` (per-ticker) or `RetrievalError` (universe lists).
- **Volatility inputs.** `getOptionChain` returns per-strike implied volatility when present; `getDailyCloses` doubles as the source for historical volatility fallback.

### Trend_Filter

```
component Trend_Filter:
    constant LOOKBACK_DAYS = 378
    constant UPTREND_THRESHOLD = 0.15

    function evaluate(candidate, runDate, dataProvider) -> FilterResult:
        series = dataProvider.getDailyCloses(candidate, LOOKBACK_DAYS)
        if series is Empty or series.length < LOOKBACK_DAYS:
            return excluded("insufficient price history")
        appreciation = series.last / series.first - 1
        if appreciation >= UPTREND_THRESHOLD:
            return passed(metrics={ netAppreciation: appreciation })
        return excluded("below uptrend threshold", metrics={ netAppreciation: appreciation })
```

Covers Requirements 2.1–2.4. `series.first` is the close at the start of the lookback window; `series.last` is the current close.

### Valuation_Filter

```
component Valuation_Filter:
    constant PE_CEILING = 100
    constant CASH_COVERAGE_MIN = 1.0

    function evaluate(candidate, runDate, dataProvider) -> FilterResult:
        f = dataProvider.getFundamentals(candidate)
        if f is Empty:
            return excluded("missing fundamentals")
        if f.trailingEarnings <= 0:
            return excluded("non-positive earnings")
        metrics = { peRatio: f.peRatio }
        if f.peRatio < PE_CEILING:
            return passed(metrics)
        cashCoverage = f.totalCash / f.marketCap
        metrics.cashCoverage = cashCoverage
        if cashCoverage >= CASH_COVERAGE_MIN:
            return passed(metrics)
        return excluded("insufficient cash coverage", metrics)
```

Covers Requirements 3.1–3.6. The combined decision: given positive earnings, pass iff `PE < 100` OR `cash/marketCap >= 1.0`.

### Options_Filter

```
component Options_Filter:
    constant TARGET_DELTA = 0.30
    constant TARGET_DAYS = 30
    constant EXPIRY_TOLERANCE_DAYS = 7
    constant MIN_PREMIUM_YIELD = 0.025

    function evaluate(candidate, runDate, dataProvider) -> FilterResult:
        expiries = dataProvider.getListedExpiries(candidate)
        target = selectNearestExpiry(expiries, runDate, TARGET_DAYS)
        if target is None or abs(daysBetween(runDate, target) - TARGET_DAYS) > EXPIRY_TOLERANCE_DAYS:
            return excluded("missing options data")

        chain = dataProvider.getOptionChain(candidate, target)
        if chain is Empty or chain.puts.isEmpty():
            return excluded("missing options data")

        puts = ensureDeltas(chain.puts, candidate, runDate, target, dataProvider)
        selected = argmin(puts, by = |p| abs(abs(p.delta) - TARGET_DELTA))
        premiumYield = selected.premium / selected.strike      # premium = bid or last
        metrics = { strike: selected.strike, expiry: target, premium: selected.premium,
                    delta: selected.delta, premiumYield: premiumYield }
        if premiumYield >= MIN_PREMIUM_YIELD:
            return passed(metrics)
        return excluded("premium yield below threshold", metrics)

    function ensureDeltas(puts, candidate, runDate, expiry, dataProvider) -> list<Put>:
        if all puts have a delta:
            return puts
        S = dataProvider.getDailyCloses(candidate, 1).last
        r = dataProvider.getRiskFreeRate()
        T = daysBetween(runDate, expiry) / 365
        for p in puts where p.delta is missing:
            sigma = p.impliedVol if present
                    else historicalVolatility(dataProvider.getDailyCloses(candidate, VOL_WINDOW))
            p.delta = DeltaCalculator.putDelta(S, p.strike, T, r, sigma)
        return puts
```

Covers Requirements 4.1–4.6. The nearest-expiry rule, closest-to-target-delta selection, premium-yield computation, threshold decision, and the within-7-days tolerance for exclusion are all explicit above.

### Delta_Calculator (Black-Scholes)

The put delta under Black-Scholes (no dividend yield) is `N(d1) - 1`, where `N` is the standard normal CDF.

```
component Delta_Calculator:
    # S = underlying price, K = strike, T = years to expiry,
    # r = risk-free rate, sigma = volatility (annualized, > 0)
    function putDelta(S, K, T, r, sigma) -> Delta:
        require S > 0 and K > 0 and T > 0 and sigma > 0
        d1 = (ln(S / K) + (r + sigma^2 / 2) * T) / (sigma * sqrt(T))
        return normalCdf(d1) - 1          # in (-1, 0)

    function callDelta(S, K, T, r, sigma) -> Delta:
        d1 = (ln(S / K) + (r + sigma^2 / 2) * T) / (sigma * sqrt(T))
        return normalCdf(d1)              # in (0, 1); callDelta - putDelta == 1
```

Design notes:
- **Volatility source priority.** Use the chain's per-strike implied volatility when present; otherwise fall back to annualized historical volatility computed from daily close log-returns over a fixed window (`VOL_WINDOW`). This satisfies Requirement 4.2.
- **No-dividend assumption.** The initial build omits dividend yield `q`. The formula generalizes to `e^{-qT}(N(d1) - 1)` if dividends are added later; the interface is unchanged.
- **Degenerate inputs.** `T <= 0`, `sigma <= 0`, or non-positive prices are rejected before computation; the caller treats a strike it cannot price as unavailable.
- `normalCdf` uses a standard numerical approximation (e.g., an Abramowitz-Stegun erf-based formula) accurate to ~1e-7.

### Earnings_Filter

```
component Earnings_Filter:
    constant MIN_QUARTERS = 4
    constant MAX_CONSECUTIVE_MISSES = 3

    function evaluate(candidate, runDate, dataProvider) -> FilterResult:
        history = dataProvider.getEarningsHistory(candidate)   # chronological
        if history is Empty or history.length < MIN_QUARTERS:
            return excluded("insufficient earnings history")
        classifications = [ (q.actualEps >= q.estimateEps ? BEAT : MISS) for q in history ]
        if maxConsecutiveRun(classifications, MISS) >= MAX_CONSECUTIVE_MISSES:
            return excluded("excessive consecutive misses")
        if hasQuarterGap(history.map(q -> q.reportPeriod)):
            return excluded("broken quarterly cadence")
        return passed(metrics={ quartersAnalyzed: history.length })
```

Covers Requirements 5.1–5.6. `hasQuarterGap` compares consecutive reporting periods and flags any skipped quarter in the expected cadence.

### Report_Generator

```
component Report_Generator:
    # reportedSet is already the Premium_Ranking trimmed to REPORT_LIMIT (ranked order).
    function write(runDate, candidateCount, qualifyingCount, reportedSet):
        csvPath = reportName(runDate, "csv")
        summaryPath = reportName(runDate, "txt")
        writeCsv(csvPath, reportedSet)         # header + one row per Reported_Set ticker, ranked
        writeSummary(summaryPath, candidateCount, qualifyingCount, reportedSet)   # handles empty set
        return (csvPath, summaryPath)

    function reportName(runDate, ext) -> Path:
        return REPORTS_DIR / ("screener-" + format(runDate, "YYYY-MM-DD") + "." + ext)
```

The CSV (Requirement 8.1) lists only the `Reported_Set` in `Premium_Ranking` order, with columns `ticker, netAppreciation, peRatio, strike, expiry, premium, delta, premiumYield`. The summary (8.2) states the total Candidate count, the total Qualifying_Ticker count, the Reported_Set count, and one rationale per Reported_Set ticker in ranked order. When the total Qualifying_Ticker count exceeds the Reported_Set count (8.3), the summary notes that the set is capped at `REPORT_LIMIT` (20) and reports how many qualifiers were omitted (`qualifyingCount - reportedSet.count`). The zero-qualifier case (8.4) writes both files with empty result sets and a "no stocks qualified" summary. Filenames embed the run date (8.5). `REPORTS_DIR` is the version-controlled `reports/` directory (`Reports_Directory`) the `Scheduled_Workflow` commits.

### Scheduled_Workflow

The scheduler is a **hosted GitHub Actions workflow**, not an in-process component. It runs on hosted `CI_Runners` independent of the user's machine (so the scheduled run proceeds with the laptop powered off), triggered on a cron schedule and on manual `workflow_dispatch`. It wraps the `CLI` (Requirement 9) as the entry point: it checks out the repo, sets up the runtime, runs the Screener CLI, then — only on a successful run — commits the dated report files into the `Reports_Directory` and uploads them as a `CI_Artifact`.

Operational notes:
- **Cron drift.** The cron schedule is set with margin ahead of U.S. market open; CI cron timing can drift, so there is no exact-to-the-minute guarantee (Req 10.2).
- **Non-trading-day self-skip.** The trading-day gate lives in `Screener.run` (see above): on a non-trading day the CLI exits zero without producing a new dated report, so there is nothing to commit even though the cron fired (Req 10.4).
- **Failure safety.** If the run fails (e.g. a universe-list `RetrievalError` → non-zero CLI exit), the workflow surfaces a failing status and the commit step does not run, so a partial or empty report is never committed over prior reports (Req 10.7).

Illustrative workflow YAML sketch (a design artifact, not final CI config):

```yaml
name: daily-stock-screener
on:
  schedule:
    - cron: "30 12 * * 1-5"   # ~08:30 ET weekdays, with margin before the 09:30 open
  workflow_dispatch: {}        # manual trigger runs the same screening (Req 10.8)
permissions:
  contents: write              # allows committing reports into the repo (Req 10.5)
jobs:
  screen:
    runs-on: ubuntu-latest     # hosted CI_Runner, laptop-off capable (Req 10.1)
    steps:
      - uses: actions/checkout@v4
      - name: Set up runtime
        run: <install the Screener runtime and dependencies, incl. yfinance>
      - name: Run screener CLI
        run: <invoke the Screener CLI>   # self-skips non-trading days (Req 10.4);
                                         # non-zero exit on halting error fails the job (Req 10.7)
      - name: Commit dated reports        # runs only on success; no-op when nothing was produced
        run: |
          git add reports/
          git commit -m "reports: ${{ '{{' }} run date {{ '}}' }}" || echo "nothing to commit"
          git push
      - name: Upload report artifact      # Req 10.6
        uses: actions/upload-artifact@v4
        with:
          name: screener-reports
          path: reports/
```

The workflow commits into the `Reports_Directory` on success (Req 10.5), uploads the same files as a `CI_Artifact` (Req 10.6), and `workflow_dispatch` runs the identical screening path as the scheduled trigger (Req 10.8). On CI the `Data_Provider` resolves to the yfinance-backed implementation (Req 10.3).

### CLI

```
component CLI:
    function main(args) -> ExitCode:
        runDate = today()
        try:
            onProgress = (msg) -> print(msg)         # progress reporting (8.2)
            result = screener.run(runDate, onProgress)
            print("CSV: " + result.csvPath)
            print("Summary: " + result.summaryPath)
            return 0
        catch RetrievalError as e:
            printError(e)
            return 1
```

Covers Requirements 9.1–9.4. The CLI is the single entry point the `Scheduled_Workflow` invokes on CI as well as the on-demand local run.

### On-Demand Agent (IDE)

An optional convenience front-end for interactive use inside Kiro. A Kiro custom agent definition stored at `.kiro/agents/stock-screener-adviser.md` gives the user a conversational way to run screening and analysis without invoking the CLI by hand. The agent persona is framed as an experienced, cautious financial adviser oriented around cash-secured put screening.

- **Wiring.** The agent is connected to the local `stockflow` MCP server configured in `.kiro/settings/mcp.json`, using its read-only tools — `get_stock_data_v2`, `get_historical_data_v2`, and `get_options_chain_v2` — as its data access. It applies the SAME four screening criteria (Trend, Valuation, Options, Earnings), the SAME premium-yield-descending top-20 ranking, and the SAME thresholds as the automated `Screener`. It is a thin interactive front-end: it does not reimplement or replace the `Screener` logic or the `CLI`.
- **Scope.** The agent is for ad-hoc, interactive exploration of individual tickers or small sets. The authoritative daily run remains the GitHub Actions `Scheduled_Workflow` (yfinance-backed), and the full-universe batch run remains the `CLI`/`Screener`. The agent runs only inside Kiro on the user's laptop, so it is available only when the laptop is on.
- **Boundaries.** The agent does not write the committed files under `reports/` — producing the dated CSV and summary remains the `Screener`/`Report_Generator`'s job. It writes reports only if the user explicitly asks it to run the CLI.
- **Guardrail.** The agent must honor the financial-content guardrail: it provides screening and analysis as a tool, not personalized investment advice.

This front-end adds no new requirements-level acceptance criteria and no new correctness properties; it is documented here purely as an optional, interactive alternative to the CLI.

## Data Models

```
type Ticker = string                                  # normalized upper-case symbol

type PriceSeries:
    closes: list<float>                               # chronological; last = current close
    length: int

type Fundamentals:
    trailingEarnings: float
    peRatio: float
    totalCash: float
    marketCap: float

type OptionChain:
    expiry: Date
    puts: list<Put>

type Put:
    strike: float
    bid: float
    last: float
    premium: float                                    # bid when > 0 else last
    delta: float | null                               # null when provider omits Greeks
    impliedVol: float | null

type EarningsQuarter:
    reportPeriod: QuarterId                           # e.g. 2024-Q1
    actualEps: float
    estimateEps: float

type FilterStatus = PASSED | EXCLUDED

type FilterResult:
    status: FilterStatus
    reason: string | null                             # set when EXCLUDED
    metrics: map<string, any>                         # merged into the evaluation record

type Evaluation:
    ticker: Ticker
    qualified: bool
    reason: string | null                             # first exclusion reason, if excluded
    metrics: map<string, any>                         # netAppreciation, peRatio, strike, ...

type RunResult:
    runDate: Date
    skipped: bool                                     # true on a non-trading-day self-skip
    candidateCount: int
    qualifyingCount: int                              # total Qualifying_Tickers (may exceed REPORT_LIMIT)
    reportedSet: list<Evaluation>                     # Premium_Ranking trimmed to REPORT_LIMIT, in ranked order
    reportPaths: (csvPath, summaryPath)
```

## Error Handling

The system distinguishes two error classes:

**Structural failures (halt the run).** Only the inability to retrieve a constituent list in `Universe_Builder` qualifies. These raise `RetrievalError`, which propagates to the entry point: the `CLI` prints the error and exits non-zero (9.4, 1.3). On CI that non-zero exit surfaces as a failing `Scheduled_Workflow`, whose commit step does not run, so previously committed reports in the `Reports_Directory` are preserved (10.7).

**Per-candidate data gaps (exclude one candidate).** Missing price history, fundamentals, option chain, or earnings history for a single ticker produces an `EXCLUDED` `FilterResult` with a specific reason. The run continues over the remaining candidates (6.3). This fail-soft behavior is why `Data_Provider` returns `Empty` for per-ticker gaps instead of raising.

**Transient provider errors.** `Data_Provider` retries a bounded number of times with backoff. If retries are exhausted, a universe-list fetch becomes a `RetrievalError` (halt), while a per-ticker fetch becomes `Empty` (exclude that candidate).

**Degenerate numeric inputs.** `Delta_Calculator` rejects non-positive `S`, `K`, `T`, or `sigma`. A strike that cannot be priced is treated as unavailable rather than producing a spurious delta.

**Reason precedence.** Because filters run in a fixed order and the loop short-circuits on the first exclusion, the retained reason is deterministic (6.2). The fixed order is Trend → Valuation → Options → Earnings.

## Testing Strategy

A dual approach: **property-based tests** for the pure filter/calculator/report logic (minimum 100 iterations per property, each referencing its design property), and **example / integration tests** for I/O boundaries and specific scenarios.

- **Property tests** target the stateless, input-varying logic: set union, numeric computations, decision thresholds, sequence analysis, Black-Scholes delta, selection, aggregation, and report round-trips. Generators cover edge cases (empty sets, boundary values at the exact thresholds, whitespace/case ticker variants, non-ASCII, large series).
- **Integration tests** target the `Data_Provider` implementations (yfinance-backed and the optional stockflow-MCP-backed) and the `Universe_Builder` boundary using mocked provider responses, the trading-day self-skip via `Screener.run` with a controlled clock/calendar, and the end-to-end CLI run.
- **Example / edge-case tests** target specific error paths (universe fetch failure, retrieval error exit codes), the non-trading-day self-skip, the zero-qualifier report case, the capped-and-omitted summary case, progress reporting, and preservation of prior committed reports after a failed scheduled run.
- **Property test configuration.** Each property test runs at least 100 randomized iterations and is tagged `Feature: daily-stock-screener, Property {number}: {property_text}`.

Mocks are used for all external calls so property tests of the options logic remain cheap enough to run many iterations.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Universe is the deduplicated union

For any two lists of tickers (with arbitrary overlaps, case, and whitespace variants), the Candidate set produced by the Universe_Builder contains exactly the distinct normalized tickers present in either input, with no duplicates, and the recorded Candidate count equals that set's size.

**Validates: Requirements 1.2, 1.4**

### Property 2: Net price appreciation formula

For any price series with a positive starting close, the Trend_Filter's computed net price appreciation equals `currentClose / startClose - 1`.

**Validates: Requirements 2.2**

### Property 3: Uptrend threshold decision

For any price series of sufficient length, the Trend_Filter marks the Candidate as passing the Uptrend condition if and only if `currentClose / startClose - 1 >= 0.15`.

**Validates: Requirements 2.3**

### Property 4: Insufficient price history excludes

For any price series, the Trend_Filter excludes the Candidate with the insufficient-price-history reason if and only if the series has fewer than 378 trading days of closes.

**Validates: Requirements 2.1, 2.4**

### Property 5: Non-positive earnings excludes

For any fundamentals whose trailing earnings are not positive, the Valuation_Filter excludes the Candidate with the non-positive-earnings reason.

**Validates: Requirements 3.2**

### Property 6: Valuation decision

For any fundamentals with positive trailing earnings, the Valuation_Filter marks the Candidate as passing the valuation condition if and only if `PE_Ratio < 100` OR `totalCash / marketCap >= 1.0`; otherwise it excludes the Candidate with the insufficient-cash-coverage reason.

**Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.6**

### Property 7: Nearest target expiry selection

For any set of listed expiry dates and run date, the expiry selected by the Options_Filter minimizes the absolute difference between its days-from-run-date and 30.

**Validates: Requirements 4.1**

### Property 8: Black-Scholes put delta is well-formed

For any valid Black-Scholes inputs (`S > 0`, `K > 0`, `T > 0`, `sigma > 0`), the computed put delta lies strictly between -1 and 0, decreases monotonically as the strike increases (holding other inputs fixed), and satisfies `callDelta - putDelta == 1`.

**Validates: Requirements 4.2**

### Property 9: Target-delta strike selection

For any set of puts with deltas for the target expiry, the strike selected by the Options_Filter minimizes the absolute difference between its absolute delta and the Target_Delta of 0.30.

**Validates: Requirements 4.3**

### Property 10: Premium yield and threshold decision

For any selected put with a positive strike, the computed Premium_Yield equals `premium / strike`, and the Options_Filter marks the Candidate as passing the options condition — recording strike, expiry, premium, delta, and Premium_Yield — if and only if the Premium_Yield is at least 0.025.

**Validates: Requirements 4.4, 4.5**

### Property 11: Missing options data excludes

For any set of listed expiries, the Options_Filter excludes the Candidate with the missing-options-data reason if and only if no listed expiry falls within 7 calendar days of the 30-day target.

**Validates: Requirements 4.6**

### Property 12: Insufficient earnings history excludes

For any earnings history, the Earnings_Filter excludes the Candidate with the insufficient-earnings-history reason if and only if the history has fewer than 4 reported quarters.

**Validates: Requirements 5.2**

### Property 13: Earnings beat/miss classification

For any reported quarter, the Earnings_Filter classifies it as an Earnings_Beat if and only if actual earnings per share is greater than or equal to the consensus estimate, and as an Earnings_Miss otherwise.

**Validates: Requirements 5.1, 5.3**

### Property 14: Consecutive-miss exclusion

For any earnings history of at least 4 quarters with an unbroken cadence, the Earnings_Filter excludes the Candidate with the excessive-consecutive-misses reason if and only if its beat/miss sequence contains a run of 3 or more consecutive Earnings_Misses.

**Validates: Requirements 5.4**

### Property 15: Broken-cadence exclusion

For any sequence of reported quarterly periods, the Earnings_Filter excludes the Candidate with the broken-quarterly-cadence reason if and only if the sequence skips a quarter in the expected quarterly cadence.

**Validates: Requirements 5.5**

### Property 16: Earnings pass condition

For any earnings history, the Earnings_Filter marks the Candidate as passing the earnings condition if and only if the history has at least 4 quarters, contains no run of 3 or more consecutive Earnings_Misses, and has an unbroken quarterly cadence.

**Validates: Requirements 5.6**

### Property 17: Qualification requires all filters

For any combination of the four filter outcomes, the Screener designates the Candidate a Qualifying_Ticker if and only if the Trend, Valuation, Options, and Earnings filters all pass.

**Validates: Requirements 6.1**

### Property 18: First exclusion reason is retained

For any Candidate that fails one or more filters, the Screener excludes it from the Qualifying_Ticker set and retains the exclusion reason of the first failing filter in the fixed evaluation order (Trend, Valuation, Options, Earnings).

**Validates: Requirements 6.2**

### Property 19: CSV round-trip preserves the ranked Reported_Set

For any Reported_Set (a Premium_Ranking trimmed to at most REPORT_LIMIT), writing the CSV and then parsing it back yields rows equal to the inputs in the same ranked order, with all required columns (ticker, net price appreciation, PE_Ratio, strike, expiry, premium, delta, Premium_Yield) present.

**Validates: Requirements 8.1**

### Property 20: Summary contains counts and per-ticker rationale for the Reported_Set

For any evaluation result set, the human-readable summary contains the total Candidate count, the total Qualifying_Ticker count, the Reported_Set count, and exactly one rationale entry for every Qualifying_Ticker in the Reported_Set.

**Validates: Requirements 8.2**

### Property 21: Report filenames embed the run date

For any run date, both generated report filenames contain that run date in the formatted date form.

**Validates: Requirements 8.5**

### Property 22: Non-trading-day runs produce no new dated report

For any run date, Screener.run produces a new dated report if and only if that date is a Trading_Day; on a non-trading day it self-skips, producing no new dated report and exiting without error, even when the cron schedule fired.

**Validates: Requirements 10.4**

### Property 23: Premium_Ranking is deterministic premium-descending with alphabetical tie-break

For any set of Qualifying_Tickers, the Premium_Ranking produced by the Screener orders them by Premium_Yield in non-increasing order, and any Qualifying_Tickers sharing an equal Premium_Yield appear in ascending alphabetical ticker-symbol order, so the resulting ordering is a deterministic function of the input set.

**Validates: Requirements 7.1, 7.2**

### Property 24: Reported_Set is the top-REPORT_LIMIT prefix of the ranking

For any set of Qualifying_Tickers, the Reported_Set equals the first REPORT_LIMIT (20) entries of the Premium_Ranking when the Qualifying_Ticker count exceeds REPORT_LIMIT, and equals the entire Premium_Ranking (all Qualifying_Tickers, in ranked order) when the count is at or below REPORT_LIMIT.

**Validates: Requirements 7.3, 7.4**
