#!/usr/bin/env python3
"""Fetch Nasdaq-100 constituents (QQQ ETF holdings) and save them to a file.

Usage:
    uv run python fetch_nasdaq100.py                 # writes nasdaq100_list
    uv run python fetch_nasdaq100.py out.txt         # custom output path

QQQ tracks the Nasdaq-100, so its equity holdings ARE the index constituents.
Source: the stockanalysis.com QQQ holdings page, which embeds the full holdings
list in the page as `holdings:[{no:..,n:"..",s:"$TICKER",..}, ...]`.
Tickers are normalized to Yahoo Finance form (share-class '.' -> '-').
"""
import sys
import re
import urllib.request

HOLDINGS_URL = "https://stockanalysis.com/etf/qqq/holdings/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_qqq_holdings():
    req = urllib.request.Request(HOLDINGS_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")

    # Isolate the holdings:[ ... ] array embedded in the page.
    start = html.find("holdings:[")
    if start < 0:
        raise RuntimeError("Could not locate the holdings array in the page.")
    arr = html[start + len("holdings:") :]
    depth, end = 0, None
    for i, ch in enumerate(arr):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise RuntimeError("Malformed holdings array in the page.")
    block = arr[:end]

    # Each entry has s:"$TICKER" (the symbol field). Pull them in order.
    symbols = re.findall(r'\bs:"\$?([A-Za-z0-9.\-]+)"', block)
    seen, out = set(), []
    for s in symbols:
        t = s.strip().upper().replace(".", "-")
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    if not out:
        raise RuntimeError("No tickers parsed from the holdings array.")
    return out


def main(argv):
    out_path = argv[1] if len(argv) > 1 else "nasdaq100_list"
    tickers = fetch_qqq_holdings()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(tickers) + "\n")
    print(f"Wrote {len(tickers)} QQQ (Nasdaq-100) tickers to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
