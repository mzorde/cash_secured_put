#!/usr/bin/env python3
"""Fetch current Nasdaq-100 constituents (QQQ ETF holdings) and save them.

Usage:
    uv run python fetch_nasdaq100.py                 # writes nasdaq100_list
    uv run python fetch_nasdaq100.py out.txt         # custom output path

QQQ tracks the Nasdaq-100, so its equity holdings ARE the index constituents.
Source: the public Wikipedia "List of NASDAQ-100 companies" article, which
carries a table listing every constituent and its ticker. This is the same
reliable pandas.read_html approach used by fetch_sp500.py.

Tickers are normalized to Yahoo Finance form (share-class '.' -> '-').
"""
import sys
import io
import urllib.request

import pandas as pd

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"


def _normalize(symbols):
    """Upper-case, normalize share-class dots, dedupe while preserving order."""
    seen, out = set(), []
    for s in symbols:
        t = str(s).strip().upper().replace(".", "-")
        # Guard against NaN/blank cells and footnote markers.
        if not t or t == "NAN" or not all(c.isalnum() or c == "-" for c in t):
            continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def fetch_nasdaq100():
    req = urllib.request.Request(WIKI_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8")

    tables = pd.read_html(io.StringIO(html))

    # Find the constituents table: it has a Ticker/Symbol column AND enough rows
    # to be the component list (~100), not the small "related indices" tables.
    for tbl in tables:
        cols = {str(c).strip().lower(): c for c in tbl.columns}
        sym_key = next(
            (cols[k] for k in ("ticker", "symbol", "ticker symbol") if k in cols),
            None,
        )
        if sym_key is not None and len(tbl) >= 90:
            tickers = _normalize(tbl[sym_key].tolist())
            if len(tickers) >= 90:
                return tickers

    raise RuntimeError(
        "Could not find the Nasdaq-100 constituents table on the page. "
        "The Wikipedia layout may have changed."
    )


def main(argv):
    out_path = argv[1] if len(argv) > 1 else "nasdaq100_list"
    tickers = fetch_nasdaq100()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(tickers) + "\n")
    print(f"Wrote {len(tickers)} Nasdaq-100 (QQQ) tickers to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
