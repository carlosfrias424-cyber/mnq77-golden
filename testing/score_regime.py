#!/usr/bin/env python3
"""Paper only. No orders. Does not change the live bot.

Same 74 fades. Adds the points that were not in the file:
  atr14    average 1-minute true range of the 14 bars ending at the entry bar
  last30   high-low of the 30 minutes ending at the entry bar
  since10  high-low from 10:00 Chicago to the entry bar
  hold_rg  high-low of the hold bar, the bar that wicked the rail
  vol_x    entry-bar volume divided by the median volume of the prior 20 bars
  vx       front VIX future, if the pull works

Median and top-quarter splits are descriptive. They are not a tuned rule.
Each cut is printed alone and on top of o20 under 8.
"""
from __future__ import annotations

import os
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
TSV = "https://raw.githubusercontent.com/carlosfrias424-cyber/mnq77-golden/main/testing/data/fade_74.tsv"


def envload():
    for raw in (ROOT / ".env").read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def px_of(raw):
    x = float(raw)
    if abs(x) > 1e7:
        x /= 1e9
    return x


def load_rows():
    text = urllib.request.urlopen(TSV, timeout=30).read().decode()
    lines = text.splitlines()
    head = lines[0].split("\t")
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        rec = dict(zip(head, line.split("\t")))
        rec["pts"] = float(rec["pts"])
        rec["o20"] = float(rec["o20"])
        dt = datetime.strptime(f"2026-{rec['day']} {rec['hm']}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        rec["dt"] = dt
        rows.append(rec)
    return rows


def pull(key, symbol, stype):
    ends = (
        "2026-10-01T19:00:00Z",
        "2026-10-01T18:00:00Z",
        "2026-10-01T16:45:00Z",
        "2026-09-30T21:00:00Z",
    )
    start = "2026-09-12T00:00:00Z"
    client = db.Historical(key)
    last = None
    for end in ends:
        print("PULL", symbol, end, flush=True)
        try:
            data = client.timeseries.get_range(
                dataset="GLBX.MDP3",
                symbols=symbol,
                stype_in=stype,
                schema="ohlcv-1m",
                start=start,
                end=end,
            )
        except Exception as e:
            last = e
            print("RETRY", symbol, type(e).__name__, str(e)[:180], flush=True)
            continue
        bars = {}
        n = 0
        for rec in data:
            n += 1
            ts = rec.ts_event / 1e9
            dt = datetime.fromtimestamp(ts, TZ).replace(second=0, microsecond=0)
            bars[dt] = (
                px_of(rec.open),
                px_of(rec.high),
                px_of(rec.low),
                px_of(rec.close),
                int(rec.volume),
            )
        print("BARS", symbol, n, flush=True)
        return bars
    print("NO_BARS", symbol, type(last).__name__ if last else "", str(last)[:180] if last else "")
    return None


def week(day):
    if day <= "09-18":
        return "09-14"
    if day <= "09-25":
        return "09-21"
    return "09-28"


def show(title, group):
    if not group:
        print(f"{title:28} n 0")
        return
    wins = sum(1 for r in group if r["pts"] > 0)
    losses = sum(1 for r in group if r["pts"] < 0)
    net = sum(r["pts"] for r in group)
    wnets = []
    for w in ("09-14", "09-21", "09-28"):
        wnets.append(f"{w} {sum(r['pts'] for r in group if week(r['day']) == w):+.0f}")
    print(f"{title:28} n {len(group):3} W {wins:2} L {losses:2} {net:+8.1f}  {'  '.join(wnets)}")


def attach(rows, bars, vx):
    times = sorted(bars)
    for r in rows:
        dt = r["dt"]
        prior = [t for t in times if t <= dt]
        r["atr14"] = None
        r["last30"] = None
        r["since10"] = None
        r["hold_rg"] = None
        r["vol_x"] = None
        r["vx"] = None
        if len(prior) >= 15:
            trs = []
            for t in prior[-14:]:
                i = times.index(t)
                o, h, l, c, _v = bars[t]
                prev_c = bars[times[i - 1]][3] if i else c
                trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
            r["atr14"] = sum(trs) / len(trs)
        window = [t for t in prior if t > dt - timedelta(minutes=30)]
        if window:
            r["last30"] = max(bars[t][1] for t in window) - min(bars[t][2] for t in window)
        open_ = dt.replace(hour=10, minute=0, second=0, microsecond=0)
        sess = [t for t in prior if t >= open_]
        if sess:
            r["since10"] = max(bars[t][1] for t in sess) - min(bars[t][2] for t in sess)
        hold_t = dt - timedelta(minutes=1)
        if hold_t in bars:
            r["hold_rg"] = bars[hold_t][1] - bars[hold_t][2]
        hist = [t for t in prior if t < dt][-20:]
        if dt in bars and len(hist) >= 10:
            base = median(bars[t][4] for t in hist)
            if base > 0:
                r["vol_x"] = bars[dt][4] / base
        if vx:
            vt = [t for t in vx if t <= dt]
            if vt:
                r["vx"] = vx[vt[-1]][3]


def cut(rows, key, pred, label):
    have = [r for r in rows if r.get(key) is not None]
    picked = [r for r in have if pred(r)]
    print(f"\n{label}")
    show("alone", picked)
    show("and o20<8", [r for r in picked if r["o20"] < 8])


def main():
    envload()
    key = os.environ["DATABENTO_API_KEY"]
    rows = load_rows()
    bars = pull(key, "MNQZ6", "raw_symbol")
    if not bars:
        raise SystemExit("no mnq bars")
    vx = pull(key, "VX.n.0", "continuous")
    attach(rows, bars, vx)
    base = [r for r in rows if r["o20"] < 8]
    print("\nBASE")
    show("all 74", rows)
    show("o20 under 8", base)

    def med(key):
        xs = sorted(r[key] for r in rows if r.get(key) is not None)
        if not xs:
            return None
        return xs[len(xs) // 2]

    def q75(key):
        xs = sorted(r[key] for r in rows if r.get(key) is not None)
        if not xs:
            return None
        return xs[int(len(xs) * 0.75)]

    print("\nSCALES")
    for key in ("atr14", "last30", "since10", "hold_rg", "vol_x", "vx"):
        xs = [r[key] for r in rows if r.get(key) is not None]
        if not xs:
            print(f"{key:10} none")
            continue
        xs.sort()
        print(f"{key:10} n {len(xs):3} lo {xs[0]:.2f} med {xs[len(xs)//2]:.2f} hi {xs[-1]:.2f}")

    specs = []
    for key in ("atr14", "last30", "since10", "hold_rg", "vol_x", "vx"):
        m = med(key)
        h = q75(key)
        if m is None:
            continue
        specs.append((key, lambda r, k=key, m=m: r[k] < m, f"{key} below median {m:.2f}"))
        specs.append((key, lambda r, k=key, m=m: r[k] >= m, f"{key} at or above median {m:.2f}"))
        specs.append((key, lambda r, k=key, h=h: r[k] >= h, f"{key} top quarter {h:.2f}"))
    specs.append(("vol_x", lambda r: r.get("vol_x") is not None and r["vol_x"] >= 2, "vol_x at least 2x"))
    specs.append(("vol_x", lambda r: r.get("vol_x") is not None and r["vol_x"] < 2, "vol_x under 2x"))

    for key, pred, label in specs:
        cut(rows, key, pred, label)


if __name__ == "__main__":
    main()
