#!/usr/bin/env python3
"""Paper only. No orders. Does not change the live bot.

Window is the 961 card: Sep 14-25, 2026. 10:00-16:00 Chicago.
Stop 20, target 40. One position. No rail lock on the fade card.
Skip ONH, ONL, EMA, OPEN.

SANITY is the old Monday card. It should print 33 trades and +436.5.
FADE is the 961 rule: the 15 points is on the hold close, not the entry.
A break is a 1-minute close through the rail. A retest is the next touch.
FADE_AND_BRT keeps a fade only when that touch is the retest.
FADE_NOT_BRT keeps a fade only when it is not.
BRT is the retest alone. No absorption tape. The next bar has to continue.
"""
from __future__ import annotations

import json
import os
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
            o = json.loads(ln)
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


def pull_bars(key):
    start, end = "2026-09-14T09:00:00Z", "2026-09-25T21:00:00Z"
    print(f"PULL {start} {end}", flush=True)
    data = db.Historical(key).timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="trades",
        start=start,
        end=end,
    )
    bars = {}
    n = 0
    for rec in data:
        n += 1
        if n % 500000 == 0:
            print("TRADES", n, flush=True)
        ts = rec.ts_event / 1e9
        dt = datetime.fromtimestamp(ts, TZ).replace(second=0, microsecond=0)
        px = px_of(rec)
        sz = float(getattr(rec, "size", 0) or 0)
        s = str(getattr(rec, "side", "") or "").upper()
        b = bars.get(dt)
        if b is None:
            b = bars[dt] = {"o": px, "h": px, "l": px, "c": px, "buy": 0.0, "sell": 0.0}
        b["h"] = max(b["h"], px)
        b["l"] = min(b["l"], px)
        b["c"] = px
        if s in ("B", "BUY", "BID"):
            b["buy"] += sz
        elif s in ("A", "SELL", "ASK"):
            b["sell"] += sz
    print("TRADES", n, "BARS", len(bars), flush=True)
    return bars


def mark_breaks(up, dn, active, prev_c, prev_dt, dt, close):
    if prev_dt is not None and dt.date() != prev_dt.date():
        up.clear()
        dn.clear()
        return
    if prev_c is None or prev_dt is None or (dt - prev_dt).total_seconds() != 60:
        return
    for name, rail in active.items():
        if prev_c <= rail and close > rail:
            up[name] = dt
            dn.pop(name, None)
        elif prev_c >= rail and close < rail:
            dn[name] = dt
            up.pop(name, None)
        elif close < rail:
            up.pop(name, None)
        elif close > rail:
            dn.pop(name, None)


def is_retest(side, name, hold_dt, up, dn):
    stamp = up.get(name) if side == "Buy" else dn.get(name)
    return stamp is not None and stamp < hold_dt


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


def brt_ok(side, hold, lift, rail):
    if side == "Buy":
        return hold["c"] > rail and lift["c"] > hold["c"] and lift["c"] > rail
    return hold["c"] < rail and lift["c"] < hold["c"] and lift["c"] < rail


def choose_side(mode, name, hold, rail):
    if mode == "monday" or mode == "fade_sr":
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


def run(alerts, bars, mode):
    keys = [k for k in sorted(bars) if k.weekday() < 5 and 4 * 60 <= k.hour * 60 + k.minute < 16 * 60]
    ai = 0
    active = {}
    quiet = {}
    up, dn = {}, {}
    prev_c = None
    prev_dt = None
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
            if active.get(kind) != px:
                up.pop(kind, None)
                dn.pop(kind, None)
            active[kind] = px
            ai += 1
        if pos:
            side, entry, name, rail, opened = pos
            if side == "Buy":
                hit_stop = b["l"] <= entry - STOP
                hit_tp = b["h"] >= entry + TP
                pts_close = b["c"] - entry
            else:
                hit_stop = b["h"] >= entry + STOP
                hit_tp = b["l"] <= entry - TP
                pts_close = entry - b["c"]
            done = False
            if hit_stop or hit_tp:
                pts, how = (-STOP, "stop") if hit_stop else (TP, "tp")
                done = True
            elif (mode == "monday" and dt.hour == 15 and dt.minute >= 59) or (
                mode != "monday" and (dt + timedelta(minutes=1)).hour * 60 + (dt + timedelta(minutes=1)).minute >= 16 * 60
            ):
                pts = pts_close
                if mode != "monday":
                    pts = max(-STOP, min(TP, pts))
                how = "eod"
                done = True
            if done:
                out.append((opened, side, name, rail, entry, pts, how))
                if mode == "monday":
                    quiet[name] = rail
                pos = None
            mark_breaks(up, dn, active, prev_c, prev_dt, dt, b["c"])
            prev_c, prev_dt = b["c"], dt
            continue
        if mode == "monday":
            for name, px in list(quiet.items()):
                if abs(b["c"] - px) >= 20:
                    del quiet[name]
        can = hold is not None and 10 <= dt.hour < 16
        if can:
            best = None
            for name, rail in active.items():
                if mode == "monday" and name in quiet:
                    continue
                if not (hold["l"] <= rail <= hold["h"]):
                    continue
                side = choose_side(mode, name, hold, rail)
                if side is None:
                    continue
                retest = is_retest(side, name, hold_dt, up, dn)
                if mode == "brt":
                    if not retest or not brt_ok(side, hold, b, rail):
                        continue
                else:
                    if not fade_ok(side, hold, b, rail):
                        continue
                    if mode == "fade_brt" and not retest:
                        continue
                    if mode == "fade_plain" and retest:
                        continue
                dist_px = hold["c"] if mode != "monday" else b["c"]
                dist = abs(dist_px - rail)
                if dist > 15:
                    continue
                if best is None or dist < best[0]:
                    best = (dist, side, name, rail, retest)
            if best is not None:
                _dist, side, name, rail, _retest = best
                pos = (side, b["c"], name, rail, dt)
        mark_breaks(up, dn, active, prev_c, prev_dt, dt, b["c"])
        prev_c, prev_dt = b["c"], dt
    return out


def show(title, rows):
    closed = [r for r in rows if r[6] != "open"]
    wins = [r[5] for r in closed if r[5] > 0]
    losses = [r[5] for r in closed if r[5] < 0]
    gp, gl = sum(wins), abs(sum(losses))
    net = sum(r[5] for r in closed)
    wr = (len(wins) / len(closed)) if closed else 0
    pf = (gp / gl) if gl else 0
    eq = peak = 0.0
    dd = 0.0
    for r in closed:
        eq += r[5]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    print(
        f"{title}  TRADES {len(closed)}  W {len(wins)} L {len(losses)}  "
        f"WR {wr:.1%}  PF {pf:.2f}  PNL {net:+.1f} pts  ${net * 10:+.0f}  DD {-dd:.1f}",
        flush=True,
    )
    return net, len(closed), len(wins)


def trades(title, rows):
    print(title, flush=True)
    for opened, side, name, rail, entry, pts, how in rows:
        print(
            f"{opened:%m-%d %H:%M} {side:4} {name}@{rail:.2f} @{entry:.2f} {pts:+.1f} {how}",
            flush=True,
        )


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("WINDOW 2026-09-14 to 2026-09-25. BOT NOT CHANGED.", flush=True)
    bars = pull_bars(key)
    books = {}
    for mode, title in (
        ("monday", "SANITY"),
        ("fade", "FADE"),
        ("fade_sr", "FADE_SR"),
        ("fade_brt", "FADE_AND_BRT"),
        ("fade_plain", "FADE_NOT_BRT"),
        ("brt", "BRT"),
    ):
        books[mode] = run(alerts, bars, mode)
        net, n, w = show(title, books[mode])
        if mode == "monday":
            print("SANITY_OK" if n == 33 and abs(net - 436.5) < 1 else "SANITY_FAIL", flush=True)
        if mode == "fade":
            print("REPRINT_961" if n == 43 and w == 31 and abs(net - 961) < 5 else "REPRINT_NO", flush=True)
        if mode == "fade_sr":
            print("REPRINT_961_SR" if n == 43 and w == 31 and abs(net - 961) < 5 else "REPRINT_NO_SR", flush=True)
    for mode, title in (
        ("fade", "FADE"),
        ("fade_brt", "FADE_AND_BRT"),
        ("fade_plain", "FADE_NOT_BRT"),
        ("brt", "BRT"),
    ):
        trades(title, books[mode])


if __name__ == "__main__":
    main()
