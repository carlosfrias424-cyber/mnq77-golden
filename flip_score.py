#!/usr/bin/env python3
"""Paper only. No orders. Does not change the live bot.

BOOK is the live fade card. 10:00-16:00 CT. Stop 20, target 40.
The hold bar trades the rail and closes within 15 of it.
The next bar lifts. That close is the entry, even if it is farther than 15.
No ONH, ONL, EMA, OPEN. The rail is not locked after a trade.
Sep 14-25 should reprint 43 trades, 31 wins, +961.

SANITY is the older card. It should reprint 33 trades and +436.5.
The 15 points on that one is the entry, and the rail stays dead until price is 20 away.

OUT is the same fade rule from 04:00 to 10:00, its own book, flattened at 10:00.

For each trade the tip is the wick on the hold bar.
push is the aggressor size in the 20 seconds before that print.
f5 f10 f20 are the other side in the 5, 10, and 20 seconds after it.
Positive means buyers showed up on a long, or sellers on a short.
o5 o10 o20 are the points already gone by then. Positive is in the trade's favor.
"""
from __future__ import annotations

import bisect
import os
from array import array
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
STOP, TP = 20.0, 40.0
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}


def envload():
    for raw in (ROOT / ".env").read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def sr_kind(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def load_alerts():
    out = []
    for ln in POI.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = __import__("json").loads(ln)
        except Exception:
            continue
        kind = sr_kind(o.get("poi_name") or o.get("type") or "")
        if kind is None:
            continue
        try:
            px = round(float(o.get("price") or 0), 2)
            recv = float(o.get("recv_ts") or o.get("ts") or 0)
        except Exception:
            continue
        if px <= 0 or recv <= 0:
            continue
        if recv > 1e12:
            recv /= 1000.0
        out.append((recv, kind, px))
    out.sort()
    return out


def px_of(rec):
    raw = getattr(rec, "pretty_price", None)
    if raw is not None:
        try:
            return float(raw)
        except Exception:
            pass
    x = float(rec.price)
    return x / 1e9 if abs(x) > 1e7 else x


def signed_size(rec):
    sz = int(getattr(rec, "size", 0) or 0)
    s = str(getattr(rec, "side", "") or "").upper()
    if s in ("B", "BUY", "BID"):
        return sz
    if s in ("A", "SELL", "ASK"):
        return -sz
    return 0


def pull(key):
    ends = (
        "2026-10-01T16:45:00Z",
        "2026-10-01T16:30:00Z",
        "2026-09-25T21:00:00Z",
    )
    start = "2026-09-14T09:00:00Z"
    data = None
    used = None
    for end in ends:
        print(f"PULL {start} {end}", flush=True)
        try:
            data = db.Historical(key).timeseries.get_range(
                dataset="GLBX.MDP3",
                symbols="MNQZ6",
                stype_in="raw_symbol",
                schema="trades",
                start=start,
                end=end,
            )
            used = end
            break
        except Exception as e:
            print("RETRY", end, type(e).__name__, flush=True)
    if data is None:
        raise SystemExit("no data")
    ts_a, px_a, sg_a = array("d"), array("d"), array("i")
    bars = {}
    n = 0
    for rec in data:
        n += 1
        if n % 500000 == 0:
            print("TRADES", n, flush=True)
        ts = rec.ts_event / 1e9
        dt = datetime.fromtimestamp(ts, TZ).replace(second=0, microsecond=0)
        px = px_of(rec)
        sg = signed_size(rec)
        ts_a.append(ts)
        px_a.append(px)
        sg_a.append(sg)
        b = bars.get(dt)
        if b is None:
            b = bars[dt] = {"o": px, "h": px, "l": px, "c": px, "buy": 0.0, "sell": 0.0}
        b["h"] = max(b["h"], px)
        b["l"] = min(b["l"], px)
        b["c"] = px
        if sg > 0:
            b["buy"] += sg
        elif sg < 0:
            b["sell"] += -sg
    print("END", used, "TRADES", n, "BARS", len(bars), flush=True)
    return ts_a, px_a, sg_a, bars


def fade_ok(side, hold, lift, rail):
    if side == "Buy":
        return (
            hold["c"] > rail
            and hold["sell"] > hold["buy"]
            and lift["buy"] > lift["sell"]
            and lift["c"] > hold["c"]
            and lift["c"] > rail
        )
    return (
        hold["c"] < rail
        and hold["buy"] > hold["sell"]
        and lift["sell"] > lift["buy"]
        and lift["c"] < hold["c"]
        and lift["c"] < rail
    )


def choose_side(mode, name, hold, rail):
    if mode == "sanity":
        if name in SUPPORT or (name in BARE and hold["c"] > rail):
            return "Buy"
        if name in RESIST or (name in BARE and hold["c"] < rail):
            return "Sell"
        return None
    if hold["c"] > rail:
        return "Buy"
    if hold["c"] < rail:
        return "Sell"
    return None


def in_window(dt, mode):
    m = dt.hour * 60 + dt.minute
    if mode == "out":
        return 4 * 60 <= m < 10 * 60
    return 10 * 60 <= m < 16 * 60


def run(alerts, bars, mode):
    keys = [
        k for k in sorted(bars)
        if k.weekday() < 5 and 4 * 60 <= k.hour * 60 + k.minute < 16 * 60
    ]
    ai = 0
    active = {}
    quiet = {}
    pos = None
    out = []
    for i in range(1, len(keys)):
        dt = keys[i]
        hold_dt = keys[i - 1]
        gap = (dt - hold_dt).total_seconds() != 60
        hold = None if gap else bars[hold_dt]
        b = bars[dt]
        asof = (hold_dt.timestamp() + 60) if hold else dt.timestamp()
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        if pos:
            side, entry, name, rail, opened, hold_used = pos
            if side == "Buy":
                hit_stop = b["l"] <= entry - STOP
                hit_tp = b["h"] >= entry + TP
                pts_close = b["c"] - entry
            else:
                hit_stop = b["h"] >= entry + STOP
                hit_tp = b["l"] <= entry - TP
                pts_close = entry - b["c"]
            done = False
            pts = how = None
            bar_end = dt + timedelta(minutes=1)
            end_m = bar_end.hour * 60 + bar_end.minute
            if hit_stop or hit_tp:
                pts, how = (-STOP, "stop") if hit_stop else (TP, "tp")
                done = True
            elif mode == "sanity" and dt.hour == 15 and dt.minute >= 59:
                pts, how, done = pts_close, "eod", True
            elif mode == "book" and end_m >= 16 * 60:
                pts, how, done = max(-STOP, min(TP, pts_close)), "eod", True
            elif mode == "out" and end_m >= 10 * 60:
                pts, how, done = max(-STOP, min(TP, pts_close)), "eod", True
            if done:
                out.append({
                    "t": opened, "hold": hold_used, "side": side, "name": name,
                    "rail": rail, "entry": entry, "pts": pts, "how": how,
                })
                if mode == "sanity":
                    quiet[name] = rail
                pos = None
            continue
        if mode == "sanity":
            for name, px in list(quiet.items()):
                if abs(b["c"] - px) >= 20:
                    del quiet[name]
        if hold is None or not in_window(dt, mode):
            continue
        best = None
        for name, rail in active.items():
            if mode == "sanity" and name in quiet:
                continue
            if not (hold["l"] <= rail <= hold["h"]):
                continue
            side = choose_side(mode, name, hold, rail)
            if side is None or not fade_ok(side, hold, b, rail):
                continue
            dist_px = b["c"] if mode == "sanity" else hold["c"]
            dist = abs(dist_px - rail)
            if dist > 15:
                continue
            if best is None or dist < best[0]:
                best = (dist, side, name, rail)
        if best is not None:
            _dist, side, name, rail = best
            pos = (side, b["c"], name, rail, dt, hold_dt)
    return out


def dsum(ts, sg, a, b):
    i = bisect.bisect_left(ts, a)
    j = bisect.bisect_left(ts, b)
    return float(sum(sg[i:j]))


def px_at(ts, px, t):
    k = bisect.bisect_right(ts, t) - 1
    if k < 0:
        return None
    return px[k]


def favor(raw, side):
    return raw if side == "Buy" else -raw


def tape_of(ts, px, sg, row):
    hold = row["hold"]
    a = hold.timestamp()
    b = a + 60
    i = bisect.bisect_left(ts, a)
    j = bisect.bisect_left(ts, b)
    if i >= j:
        return None
    side = row["side"]
    if side == "Buy":
        tip_i = min(range(i, j), key=lambda k: (px[k], ts[k]))
    else:
        tip_i = min(range(i, j), key=lambda k: (-px[k], ts[k]))
    tip_ts = ts[tip_i]
    tip_px = px[tip_i]
    push = -favor(dsum(ts, sg, tip_ts - 20, tip_ts), side)
    out = {"tip": tip_ts, "tip_px": tip_px, "push": push}
    for sec in (5, 10, 20):
        out[f"f{sec}"] = favor(dsum(ts, sg, tip_ts, tip_ts + sec), side)
        later = px_at(ts, px, tip_ts + sec)
        if later is None:
            out[f"o{sec}"] = None
        elif side == "Buy":
            out[f"o{sec}"] = later - tip_px
        else:
            out[f"o{sec}"] = tip_px - later
    return out


def med(rows, key):
    xs = sorted(r[key] for r in rows if r.get(key) is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def show_row(r):
    tip = datetime.fromtimestamp(r["tip"], TZ)
    def n(v, p=0):
        return "na" if v is None else f"{v:+.{p}f}" if p else f"{v:.0f}"
    mark = "W" if r["pts"] > 0 else "L" if r["pts"] < 0 else "F"
    print(
        f"{r['t']:%m-%d %H:%M} {r['side']:4} {r['name']}@{r['rail']:.2f} {mark} {r['pts']:+.1f} {r['how']} "
        f"tip {tip:%H:%M:%S} {r['tip_px']:.2f} push {n(r['push'])} "
        f"f5 {n(r['f5'])} f10 {n(r['f10'])} f20 {n(r['f20'])} "
        f"o5 {n(r['o5'], 2)} o10 {n(r['o10'], 2)} o20 {n(r['o20'], 2)}",
        flush=True,
    )


def summarize(title, rows):
    wins = [r for r in rows if r["pts"] > 0]
    losses = [r for r in rows if r["pts"] < 0]
    net = sum(r["pts"] for r in rows)
    print(
        f"{title} TRADES {len(rows)} W {len(wins)} L {len(losses)} PNL {net:+.1f}",
        flush=True,
    )
    for label, group in (("WINS", wins), ("LOSS", losses)):
        if not group:
            continue
        def m(key, p=0):
            v = med(group, key)
            if v is None:
                return "na"
            return f"{v:+.{p}f}" if p else f"{v:.0f}"
        def rate(key):
            xs = [r[key] for r in group if r.get(key) is not None]
            if not xs:
                return "na"
            return f"{sum(1 for x in xs if x > 0)}/{len(xs)}"
        print(
            f"{title}_{label} n {len(group)} med push {m('push')} "
            f"f5 {m('f5')} f10 {m('f10')} f20 {m('f20')} "
            f"o5 {m('o5', 2)} o10 {m('o10', 2)} o20 {m('o20', 2)} "
            f"pos f5 {rate('f5')} f10 {rate('f10')} f20 {rate('f20')}",
            flush=True,
        )


def attach(rows, ts, px, sg):
    kept = []
    for r in rows:
        got = tape_of(ts, px, sg, r)
        if got is None:
            print("NO_TIP", r["t"], r["name"], flush=True)
            continue
        r.update(got)
        kept.append(r)
    return kept


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("BOT NOT CHANGED", flush=True)
    ts, px, sg, bars = pull(key)
    books = {mode: run(alerts, bars, mode) for mode in ("sanity", "book", "out")}
    card = [r for r in books["sanity"] if r["t"].date().isoformat() <= "2026-09-25"]
    net = sum(r["pts"] for r in card)
    print("SANITY_OK" if len(card) == 33 and abs(net - 436.5) < 1 else "SANITY_FAIL", len(card), f"{net:+.1f}", flush=True)
    card = [r for r in books["book"] if r["t"].date().isoformat() <= "2026-09-25"]
    wins = [r for r in card if r["pts"] > 0]
    net = sum(r["pts"] for r in card)
    print(
        "REPRINT_961" if len(card) == 43 and len(wins) == 31 and abs(net - 961) < 5 else "REPRINT_NO",
        len(card), len(wins), f"{net:+.1f}",
        flush=True,
    )
    for mode in ("book", "out"):
        books[mode] = attach(books[mode], ts, px, sg)
    book = books["book"]
    early = [r for r in book if r["t"].date().isoformat() <= "2026-09-25"]
    late = [r for r in book if r["t"].date().isoformat() > "2026-09-25"]
    print("BOOK", flush=True)
    for r in book:
        show_row(r)
    summarize("CARD", early)
    summarize("AFTER", late)
    print("OUT 04:00-10:00", flush=True)
    for r in books["out"]:
        show_row(r)
    summarize("OUT", books["out"])


if __name__ == "__main__":
    main()
