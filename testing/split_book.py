#!/usr/bin/env python3
"""Reprint the ledger cuts from testing/data/fade_74.tsv. No orders. No market data."""
from pathlib import Path

P = Path(__file__).resolve().parent / "data" / "fade_74.tsv"


def load():
    lines = P.read_text().splitlines()
    head = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        rec = dict(zip(head, line.split("\t")))
        rec["pts"] = float(rec["pts"])
        rec["o20"] = float(rec["o20"])
        rec["f20"] = int(rec["f20"])
        rec["push"] = int(rec["push"])
        rows.append(rec)
    return rows


def week(day):
    if day <= "09-18":
        return "09-14"
    if day <= "09-25":
        return "09-21"
    return "09-28"


def show(title, group):
    wins = sum(1 for r in group if r["pts"] > 0)
    losses = sum(1 for r in group if r["pts"] < 0)
    net = sum(r["pts"] for r in group)
    print(f"{title:22} n {len(group):3} W {wins:2} L {losses:2} {net:+8.1f}")


def main():
    rows = load()
    show("ALL", rows)
    print("O20")
    show("under 8", [r for r in rows if r["o20"] < 8])
    show("8 or more", [r for r in rows if r["o20"] >= 8])
    print("WEEK under 8")
    for w in ("09-14", "09-21", "09-28"):
        show(w, [r for r in rows if week(r["day"]) == w and r["o20"] < 8])
    print("RAIL")
    for name in sorted(set(r["rail"] for r in rows)):
        show(name, [r for r in rows if r["rail"] == name])


if __name__ == "__main__":
    main()
