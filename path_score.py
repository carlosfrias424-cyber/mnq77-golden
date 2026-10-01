#!/usr/bin/env python3
"""Paper only. No orders. Does not change the live bot.

Same 74 fade fills. Replay the minute path after the entry.

Answers two things:
  how many 20-point stops later reached the 40-point target, and how far past 20 they went
  whether the red days are different in volume and range
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
TP = 40.0
STOPS = (20.0, 23.0, 25.0, 30.0)
RAW = """
09-14 10:41 Buy
09-14 10:51 Buy
09-14 13:01 Sell
09-15 10:13 Sell
09-15 10:24 Sell
09-15 11:29 Sell
09-15 12:28 Sell
09-15 13:01 Buy
09-15 15:10 Buy
09-15 15:36 Sell
09-16 10:38 Buy
09-16 10:51 Sell
09-16 11:07 Sell
09-16 13:01 Sell
09-16 13:06 Sell
09-16 13:20 Sell
09-16 13:34 Sell
09-16 13:56 Sell
09-16 14:05 Sell
09-16 14:34 Buy
09-16 14:43 Buy
09-16 14:55 Sell
09-16 15:07 Sell
09-17 10:39 Buy
09-17 13:38 Sell
09-17 14:53 Buy
09-17 15:32 Buy
09-18 11:15 Sell
09-18 14:14 Buy
09-18 14:51 Sell
09-18 15:02 Buy
09-21 10:09 Buy
09-21 10:51 Sell
09-21 13:13 Buy
09-22 13:40 Buy
09-23 10:52 Buy
09-23 12:20 Sell
09-24 10:56 Sell
09-24 12:37 Sell
09-24 13:44 Buy
09-24 14:12 Buy
09-24 15:07 Buy
09-24 15:58 Buy
09-25 10:00 Sell
09-25 11:35 Buy
09-25 11:52 Buy
09-25 13:28 Buy
09-28 11:41 Sell
09-28 12:12 Sell
09-28 12:33 Sell
09-28 13:40 Buy
09-28 14:20 Buy
09-29 10:07 Sell
09-29 10:22 Sell
09-29 10:43 Sell
09-29 10:49 Buy
09-29 11:02 Sell
09-29 12:36 Sell
09-29 13:01 Sell
09-29 13:32 Buy
09-29 13:51 Buy
09-29 14:24 Sell
09-30 10:13 Buy
09-30 10:45 Buy
09-30 10:54 Sell
09-30 11:33 Buy
09-30 13:27 Sell
09-30 14:56 Buy
09-30 15:08 Buy
09-30 15:21 Buy
10-01 10:19 Buy
10-01 10:43 Sell
10-01 11:09 Sell
10-01 11:41 Sell
"""


def envload():
    for raw in (ROOT / ".env").read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def fills():
    out = []
    for line in RAW.splitlines():
        line = line.strip()
        if not line:
            continue
        day, hm, side = line.split()
        dt = datetime.strptime(f"2026-{day} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        out.append({"day": day, "hm": hm, "side": side, "dt": dt})
    return out


def px_of(raw):
    x = float(raw)
    if abs(x) > 1e7:
        x /= 1e9
    return x


def pull_ohlcv(key):
    ends = (
        "2026-10-01T21:00:00Z",
        "2026-10-01T16:45:00Z",
        "2026-10-01T09:40:00Z",
        "2026-09-30T21:00:00Z",
    )
    start = "2026-09-14T12:00:00Z"
    client = db.Historical(key)
    last = None
    for end in ends:
        print("PULL", end, flush=True)
        try:
            data = client.timeseries.get_range(
                dataset="GLBX.MDP3",
                symbols="MNQZ6",
                stype_in="raw_symbol",
                schema="ohlcv-1m",
                start=start,
                end=end,
            )
        except Exception as e:
            last = e
            print("RETRY", type(e).__name__, str(e)[:160], flush=True)
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
        print("BARS", n, "END", end, flush=True)
        return bars
    raise SystemExit(f"no bars {type(last).__name__} {last}")


def week_of(day):
    if day <= "09-18":
        return "09-14"
    if day <= "09-25":
        return "09-21"
    return "09-28"


def walk(bars, keys, row):
    entry_bar = bars.get(row["dt"])
    if entry_bar is None:
        return None
    entry = entry_bar[3]
    side = row["side"]
    start_i = keys.index(row["dt"]) + 1
    mae = 0.0
    mfe = 0.0
    first_stop = None
    first_tp = None
    exits = {}
    for stop in STOPS:
        exits[stop] = None
    for dt in keys[start_i:]:
        if dt.date() != row["dt"].date():
            break
        o, h, l, c, _v = bars[dt]
        if side == "Buy":
            adv = entry - l
            fav = h - entry
            close_pts = c - entry
        else:
            adv = h - entry
            fav = entry - l
            close_pts = entry - c
        mae = max(mae, adv)
        mfe = max(mfe, fav)
        if adv >= 20 and first_stop is None:
            first_stop = dt
        if fav >= TP and first_tp is None:
            first_tp = dt
        end = dt + timedelta(minutes=1)
        end_m = end.hour * 60 + end.minute
        for stop in STOPS:
            if exits[stop] is not None:
                continue
            if adv >= stop or fav >= TP:
                exits[stop] = (-stop, "stop") if adv >= stop else (TP, "tp")
            elif end_m >= 16 * 60:
                exits[stop] = (max(-stop, min(TP, close_pts)), "eod")
        if end_m >= 16 * 60 or all(v is not None for v in exits.values()):
            if end_m >= 16 * 60:
                break
    for stop in STOPS:
        if exits[stop] is None:
            exits[stop] = (0.0, "open")
    return {
        "entry": entry,
        "mae": mae,
        "mfe": mfe,
        "exits": exits,
        "first_stop": first_stop,
        "first_tp": first_tp,
        "bar_vol": entry_bar[4],
    }


def day_stats(bars):
    out = {}
    by = {}
    for dt, (o, h, l, c, v) in bars.items():
        by.setdefault(dt.date(), []).append((dt, h, l, v))
    for day, rows in by.items():
        rth = [r for r in rows if 10 * 60 <= r[0].hour * 60 + r[0].minute < 16 * 60]
        morn = [r for r in rows if 8 * 60 + 30 <= r[0].hour * 60 + r[0].minute < 10 * 60]
        if not rth:
            continue
        out[day.strftime("%m-%d")] = {
            "vol": sum(r[3] for r in rth),
            "rng": max(r[1] for r in rth) - min(r[2] for r in rth),
            "morn": (max(r[1] for r in morn) - min(r[2] for r in morn)) if morn else None,
        }
    return out


def num(v, p=1):
    if v is None:
        return "na"
    return f"{v:.{p}f}"


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    rows = fills()
    print("FILLS", len(rows), flush=True)
    if len(rows) != 74:
        raise SystemExit(f"expected 74 got {len(rows)}")
    print("BOT NOT CHANGED", flush=True)
    bars = pull_ohlcv(key)
    keys = sorted(bars)
    keyset = {k: i for i, k in enumerate(keys)}
    played = []
    missing = []
    for row in rows:
        if row["dt"] not in keyset:
            missing.append(row)
            print("MISSING", row["day"], row["hm"], flush=True)
            continue
        # index() is fine, 20k bars
        got = walk(bars, keys, row)
        if got is None:
            missing.append(row)
            print("MISSING", row["day"], row["hm"], flush=True)
            continue
        row.update(got)
        played.append(row)
        if len(played) <= 3:
            print(f"SAMPLE {row['day']} {row['hm']} {row['side']} entry {got['entry']:.2f}", flush=True)
    print("PLAYED", len(played), "MISSING", len(missing), flush=True)
    print("STOLEN  stop tagged, then the 40 target traded later", flush=True)
    stolen = []
    same = []
    for row in played:
        fs, ft = row["first_stop"], row["first_tp"]
        if fs is None or ft is None:
            continue
        if fs == ft:
            same.append(row)
            continue
        if ft > fs and row["mae"] >= 20:
            stolen.append(row)
            past = row["mae"] - 20
            print(
                f"{row['day']} {row['hm']} {row['side']:4} mae {row['mae']:.2f} "
                f"past {past:.2f} mfe {row['mfe']:.2f}",
                flush=True,
            )
    print("STOLEN", len(stolen), "SAME_BAR", len(same), flush=True)
    buckets = ((0, 3), (3, 5), (5, 10), (10, 1000))
    for lo, hi in buckets:
        grp = [r for r in stolen if lo < (r["mae"] - 20) <= hi or (lo == 0 and r["mae"] - 20 <= hi)]
        if lo == 0:
            grp = [r for r in stolen if (r["mae"] - 20) <= 3]
        elif lo == 3:
            grp = [r for r in stolen if 3 < (r["mae"] - 20) <= 5]
        elif lo == 5:
            grp = [r for r in stolen if 5 < (r["mae"] - 20) <= 10]
        else:
            grp = [r for r in stolen if (r["mae"] - 20) > 10]
        print(f"PAST {lo:g}-{hi:g} {len(grp)}", flush=True)
    print("STOPS target stays 40. Same bar, stop is counted first.", flush=True)
    weeks = ["09-14", "09-21", "09-28"]
    hdr = "WEEK".ljust(8) + "".join(f"{int(s):>10}" for s in STOPS)
    print(hdr, flush=True)
    for wk in weeks:
        grp = [r for r in played if week_of(r["day"]) == wk]
        cells = []
        for stop in STOPS:
            net = sum(r["exits"][stop][0] for r in grp)
            w = sum(1 for r in grp if r["exits"][stop][0] > 0)
            l = sum(1 for r in grp if r["exits"][stop][0] < 0)
            cells.append(f"{net:+.0f}/{w}W")
        print(f"{wk:8}" + "".join(f"{c:>10}" for c in cells), flush=True)
    all_cells = []
    for stop in STOPS:
        net = sum(r["exits"][stop][0] for r in played)
        w = sum(1 for r in played if r["exits"][stop][0] > 0)
        all_cells.append(f"{net:+.0f}/{w}W")
    print(f"{'ALL':8}" + "".join(f"{c:>10}" for c in all_cells), flush=True)
    stats = day_stats(bars)
    print("DAYS vol is 10:00-16:00 contracts. morn is 08:30-10:00 range. rng is 10:00-16:00.", flush=True)
    by = {}
    for row in played:
        by.setdefault(row["day"], []).append(row)
    for day in sorted(by):
        grp = by[day]
        net = sum(r["exits"][20.0][0] for r in grp)
        w = sum(1 for r in grp if r["exits"][20.0][0] > 0)
        st = stats.get(day, {})
        morn = st.get("morn")
        print(
            f"{day} n {len(grp)} W{w} {net:+.1f} vol {st.get('vol', 'na')} "
            f"rng {num(st.get('rng'))} morn {num(morn)}",
            flush=True,
        )
    wins = [r for r in played if r["exits"][20.0][0] > 0]
    losses = [r for r in played if r["exits"][20.0][0] < 0]

    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        if not xs:
            return None
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    print(
        f"ENTRY_VOL wins {num(med([r['bar_vol'] for r in wins]), 0)} "
        f"losses {num(med([r['bar_vol'] for r in losses]), 0)}",
        flush=True,
    )
    print(
        f"MAE wins {num(med([r['mae'] for r in wins]))} "
        f"losses {num(med([r['mae'] for r in losses]))}",
        flush=True,
    )


if __name__ == "__main__":
    main()
