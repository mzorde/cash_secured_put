#!/usr/bin/env python3
"""Fetch current S&P 500 constituents and save them to sp500_list.

Usage:
    uv run python fetch_sp500.py            # writes sp500_list (one ticker per line)
    uv run python fetch_sp500.py out.txt    # custom output path

Source: the public Wikipedia "List of S&P 500 companies" table. Tickers are
normalized to Yahoo Finance form (e.g. BRK.B -> BRK-B).
"""
import sys
import io
import urllib.request

import pandas as pd

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500():
    req = urllib.request.Request(WIKI_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8")
    tables = pd.read_html(io.StringIO(html))
    # The first table whose columns include a Symbol column is the constituents list.
    for tbl in tables:
        cols = [str(c) for c in tbl.columns]
        if any(c.lower() == "symbol" for c in cols):
            sym_col = next(c for c in tbl.columns if str(c).lower() == "symbol")
            tickers = [str(s).strip().upper().replace(".", "-") for s in tbl[sym_col]]
            # dedupe, keep order
            seen, out = set(), []
            for s in tickers:
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
            return out
    raise RuntimeError("Could not find the S&P 500 constituents table on the page.")


def main(argv):
    out_path = argv[1] if len(argv) > 1 else "sp500_list"
    tickers = fetch_sp500()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(tickers) + "\n")
    print(f"Wrote {len(tickers)} S&P 500 tickers to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
