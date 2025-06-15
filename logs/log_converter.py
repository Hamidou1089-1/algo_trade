#!/usr/bin/env python3
"""
compile_book.py  – fixed
Convert a snapshot log into an N-level order-book Parquet file.

Edit the three constants, run `python3 compile_book.py`.
"""

# ── EDIT THESE ────────────────────────────────────────────────────────────────
INPUT_LOG      = "market_data.log"
OUTPUT_PARQUET = "market_book_5lvl.parquet"
LEVELS         = 5
# ──────────────────────────────────────────────────────────────────────────────

import ast, numpy as np, pandas as pd

def flatten(ts: int, sym: str, book: dict, n: int) -> dict:
    bids = sorted(((int(p), int(q)) for p, q in book["bids"].items()),
                  reverse=True)
    asks = sorted(((int(p), int(q)) for p, q in book["asks"].items()))
    row  = {"time": ts, "asset": sym}

    for i in range(n):
        # asks (low → high)
        ap, aq = asks[i] if i < len(asks) else (np.nan, np.nan)
        row[f"ask_price{i+1}"]    = ap
        row[f"ask_quantity{i+1}"] = aq
        # bids (high → low)
        bp, bq = bids[i] if i < len(bids) else (np.nan, np.nan)
        row[f"bid_price{i+1}"]    = bp
        row[f"bid_quantity{i+1}"] = bq

    return row

def main():
    rows = []
    with open(INPUT_LOG) as fh:
        for line in fh:
            txt = line.lstrip()
            if not (txt.startswith("{'time'") or txt.startswith('{"time"')):
                continue
            snap = ast.literal_eval(txt)
            ts   = snap.pop("time")
            for sym, ob in snap.items():
                rows.append(flatten(ts, sym, ob, LEVELS))

    # ---------- build DataFrame (no dtype keyword!) --------------------------
    df = pd.DataFrame.from_records(rows)          # ← the only safe signature

    # optional: make integer columns nullable Int64 instead of float
    int_cols = [c for c in df.columns if c.endswith(("price1","price2","price3",
                                                     "price4","price5",
                                                     "quantity1","quantity2",
                                                     "quantity3","quantity4",
                                                     "quantity5"))]
    df[int_cols] = df[int_cols].astype("Int64")

    # ------------------------------------------------------------------------
    df.to_parquet(OUTPUT_PARQUET, index=False)    # needs pyarrow or fastparquet
    print(f"✓ wrote {len(df):,} rows → {OUTPUT_PARQUET}")

if __name__ == "__main__":
    main()
