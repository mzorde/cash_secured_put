#!/usr/bin/env python3
"""Ad-hoc cash-secured-put screener preview.

Usage:
    uv run python screen.py AVGO AMD NVDA CRDO       # tickers as arguments
    uv run python screen.py -f sp500_list            # tickers from a file
    uv run python screen.py -f sp500_list NFLX       # file + extra tickers

A ticker file has one symbol per line; blank lines and lines starting with '#'
are ignored.

Runs the Daily Stock Screener's four filters against each ticker using the same
Yahoo data (via yfinance) that the stockflow MCP server wraps, then prints a
per-ticker pass/exclude breakdown and a premium-yield-ranked list of qualifiers.

This is an interactive preview tool, not the authoritative screener. It does not
write the committed reports/ files.
"""
import sys
import os
import csv
import math
import datetime as dt

import yfinance as yf

REPORTS_DIR_DEFAULT = "reports"
CSV_COLUMNS = ["ticker", "netAppreciation", "peRatio", "strike",
               "expiry", "premium", "delta", "premiumYield"]

RISK_FREE = 0.04
UPTREND_MIN = 0.15
PE_CEILING = 100.0
CASH_COVERAGE_MIN = 1.0
TARGET_DELTA = 0.30
TARGET_DAYS = 30
EXPIRY_TOLERANCE_DAYS = 7
MIN_PREMIUM_YIELD = 0.025
MIN_QUARTERS = 4
MAX_CONSECUTIVE_MISSES = 3
LOOKBACK_MIN_CLOSES = 378


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def put_delta(S, K, T, r, sig):
    if min(S, K, T, sig) <= 0:
        return None
    d1 = (math.log(S / K) + (r + sig * sig / 2) * T) / (sig * math.sqrt(T))
    return norm_cdf(d1) - 1


def screen(sym: str) -> dict:
    """Return a result dict: status PASS/EXCLUDE plus metrics and first-fail reason."""
    t = yf.Ticker(sym)
    r = {"ticker": sym, "status": "EXCLUDE", "reason": None, "metrics": {}}
    m = r["metrics"]

    # 1. Trend
    closes = t.history(period="2y", interval="1d")["Close"].dropna().tolist()
    m["n_closes"] = len(closes)
    if len(closes) < LOOKBACK_MIN_CLOSES:
        r["reason"] = f"trend: insufficient price history ({len(closes)} closes)"
        return r
    appr = closes[-1] / closes[0] - 1
    m["appreciation"] = appr
    if appr < UPTREND_MIN:
        r["reason"] = f"trend: net appreciation {appr*100:.1f}% < 15%"
        return r

    # 2. Valuation & earnings health
    info = t.info
    eps = info.get("trailingEps")
    pe = info.get("trailingPE")
    cash = info.get("totalCash")
    mc = info.get("marketCap")
    m["eps"], m["pe"] = eps, pe
    if not eps or eps <= 0:
        r["reason"] = f"valuation: non-positive earnings (eps={eps})"
        return r
    cov = (cash / mc) if (cash and mc) else 0.0
    m["cash_coverage"] = cov
    if pe is None:
        r["reason"] = "valuation: PE unavailable"
        return r
    if not (pe < PE_CEILING or cov >= CASH_COVERAGE_MIN):
        r["reason"] = f"valuation: PE {pe:.1f} >= 100 and cash/mktcap {cov:.3f} < 1.0"
        return r

    # 3. Cash-secured put
    exps = t.options
    if not exps:
        r["reason"] = "options: no listed expiries"
        return r
    today = dt.date.today()
    target = min(exps, key=lambda e: abs((dt.date.fromisoformat(e) - today).days - TARGET_DAYS))
    days = (dt.date.fromisoformat(target) - today).days
    if abs(days - TARGET_DAYS) > EXPIRY_TOLERANCE_DAYS:
        r["reason"] = f"options: nearest expiry {target} is {days}d out (>7 from 30)"
        return r
    S, T = closes[-1], days / 365
    rows = []
    for _, row in t.option_chain(target).puts.iterrows():
        K = float(row["strike"])
        bid = float(row.get("bid") or 0)
        last = float(row.get("lastPrice") or 0)
        prem = bid if bid > 0 else last
        iv = float(row.get("impliedVolatility") or 0)
        sig = iv if iv > 0 else 0.4
        d = put_delta(S, K, T, RISK_FREE, sig)
        if d is not None and prem > 0:
            rows.append((K, prem, d, sig))
    if not rows:
        r["reason"] = "options: no priceable puts"
        return r
    K, prem, d, sig = min(rows, key=lambda x: abs(abs(x[2]) - TARGET_DELTA))
    py = prem / K
    m.update({"spot": S, "strike": K, "premium": prem, "delta": d, "iv": sig,
              "expiry": target, "days_out": days, "premium_yield": py})
    if py < MIN_PREMIUM_YIELD:
        r["reason"] = f"options: premium yield {py*100:.2f}% < 2.5%"
        return r

    # 4. Earnings consistency
    try:
        ed = t.get_earnings_dates(limit=12).dropna(
            subset=["Reported EPS", "EPS Estimate"]).sort_index()
    except Exception as e:
        r["reason"] = f"earnings: history error ({e!r})"
        return r
    m["quarters"] = len(ed)
    if len(ed) < MIN_QUARTERS:
        r["reason"] = f"earnings: < 4 quarters ({len(ed)})"
        return r
    run = mx = 0
    for _, row in ed.iterrows():
        miss = row["Reported EPS"] < row["EPS Estimate"]
        run = run + 1 if miss else 0
        mx = max(mx, run)
    m["max_consecutive_misses"] = mx
    if mx >= MAX_CONSECUTIVE_MISSES:
        r["reason"] = f"earnings: {mx} consecutive misses"
        return r

    r["status"] = "QUALIFY"
    return r


def load_tickers_from_file(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s.upper())
    return out


def parse_args(argv):
    """Collect tickers and options. Returns (tickers, outdir, suffix)."""
    tickers, outdir, suffix, i = [], REPORTS_DIR_DEFAULT, "", 1
    while i < len(argv):
        a = argv[i]
        if a in ("-f", "--file"):
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} requires a file path")
            tickers.extend(load_tickers_from_file(argv[i + 1]))
            i += 2
        elif a in ("-o", "--outdir"):
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} requires a directory path")
            outdir = argv[i + 1]
            i += 2
        elif a in ("-s", "--suffix"):
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} requires a suffix")
            suffix = argv[i + 1]
            i += 2
        else:
            if a.strip():
                tickers.append(a.strip().upper())
            i += 1
    # dedupe, preserve order
    seen, out = set(), []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out, outdir, suffix


def write_reports(outdir, run_date, candidate_count, qualifiers, suffix=""):
    """Write dated CSV + summary into outdir. Returns (csv_path, summary_path)."""
    os.makedirs(outdir, exist_ok=True)
    stamp = run_date.strftime("%Y-%m-%d")
    tag = f"-{suffix}" if suffix else ""
    csv_path = os.path.join(outdir, f"screener-{stamp}{tag}.csv")
    summary_path = os.path.join(outdir, f"screener-{stamp}{tag}.txt")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in qualifiers:
            m = r["metrics"]
            w.writerow([
                r["ticker"],
                f"{m['appreciation']:.4f}",
                f"{m['pe']:.2f}" if m.get("pe") is not None else "",
                m["strike"],
                m["expiry"],
                f"{m['premium']:.2f}",
                f"{m['delta']:.4f}",
                f"{m['premium_yield']:.4f}",
            ])

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"Daily Stock Screener - run {stamp}\n")
        f.write(f"Candidates scanned: {candidate_count}\n")
        f.write(f"Qualifying tickers: {len(qualifiers)}\n")
        reported = qualifiers[:20]
        if len(qualifiers) > 20:
            f.write(f"Reported (capped at 20): {len(reported)}  "
                    f"(omitted {len(qualifiers) - 20})\n")
        f.write("\nRanked by premium yield (descending):\n")
        if not reported:
            f.write("  No stocks qualified.\n")
        for i, r in enumerate(reported, 1):
            m = r["metrics"]
            f.write(f"  {i:2d}. {r['ticker']:6s} yield={m['premium_yield']*100:5.2f}%  "
                    f"strike={m['strike']}  prem={m['premium']:.2f}  "
                    f"delta={m['delta']:.3f}  exp={m['expiry']}  "
                    f"appr={m['appreciation']*100:.0f}%  pe={m['pe']:.1f}\n")
    return csv_path, summary_path


def main(argv):
    tickers, outdir, suffix = parse_args(argv)
    if not tickers:
        print("usage: uv run python screen.py [-f FILE] [-o OUTDIR] [-s SUFFIX] [TICKER ...]")
        return 2
    print("DATA_AS_OF", dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    results = []
    for sym in tickers:
        try:
            results.append(screen(sym))
        except Exception as e:
            results.append({"ticker": sym, "status": "ERROR", "reason": repr(e), "metrics": {}})

    for r in results:
        m = r["metrics"]
        print(f"\n===== {r['ticker']} : {r['status']} =====")
        if "appreciation" in m:
            print(f"  trend     appr={m['appreciation']*100:6.1f}%  ({m['n_closes']} closes)")
        if "pe" in m and m["pe"] is not None:
            print(f"  valuation pe={m['pe']:.1f}  cash/mktcap={m.get('cash_coverage',0):.3f}")
        if "premium_yield" in m:
            print(f"  options   strike={m['strike']}  prem={m['premium']:.2f}  "
                  f"delta={m['delta']:.3f}  exp={m['expiry']}({m['days_out']}d)  "
                  f"yield={m['premium_yield']*100:.2f}%")
        if "max_consecutive_misses" in m:
            print(f"  earnings  quarters={m['quarters']}  max_consec_misses={m['max_consecutive_misses']}")
        if r["status"] != "QUALIFY" and r["reason"]:
            print(f"  -> first fail: {r['reason']}")

    quals = [r for r in results if r["status"] == "QUALIFY"]
    quals.sort(key=lambda r: (-r["metrics"]["premium_yield"], r["ticker"]))
    print("\n===== QUALIFIERS (ranked by premium yield desc, top 20) =====")
    if not quals:
        print("  (none)")
    for i, r in enumerate(quals[:20], 1):
        m = r["metrics"]
        print(f"  {i:2d}. {r['ticker']:6s} yield={m['premium_yield']*100:5.2f}%  "
              f"strike={m['strike']}  prem={m['premium']:.2f}  exp={m['expiry']}")

    csv_path, summary_path = write_reports(outdir, dt.date.today(), len(results), quals, suffix)
    print(f"\nSaved CSV:     {csv_path}")
    print(f"Saved summary: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
