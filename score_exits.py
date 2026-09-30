#!/usr/bin/env python3
"""Paper only. No orders. Does not change the live bot.

Same entries as fade_hold15_10_16_on.
10:00-16:00 CT. Hold close within 15. Next bar lifts. One position.
No quiet lock. ONH and ONL included. EMA and OPEN skipped.
Side comes from the hold close, same as live pick().
This will not match the old score_fade card. That card used a quiet lock
and it measured the 15 on the lift close.

Grid is the exit only. If stop and target both print on the same bar, the stop counts.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
STOPS = (12, 15, 18, 20, 22, 25, 30)
TPS = (20, 24, 30, 35, 40, 45, 50, 60)
SKIP = ("EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT", "ONL"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE", "ONH"}
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
    import json
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


def pull_bars(key, start, end):
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


def in_session(dt):
    if dt.weekday() >= 5:
        return False
    m = dt.hour * 60 + dt.minute
    return 10 * 60 <= m < 16 * 60


def pick(hold, lift, active):
    best = None
    for name, rail in active.items():
        if not (hold["l"] <= rail <= hold["h"]):
            continue
        dist = abs(hold["c"] - rail)
        if dist > 15:
            continue
        if hold["c"] > rail and hold["sell"] > hold["buy"]:
            if not (lift["buy"] > lift["sell"] and lift["c"] > hold["c"] and lift["c"] > rail):
                continue
            side = "Buy"
        elif hold["c"] < rail and hold["buy"] > hold["sell"]:
            if not (lift["sell"] > lift["buy"] and lift["c"] < hold["c"] and lift["c"] < rail):
                continue
            side = "Sell"
        else:
            continue
        if best is None or dist < best[0]:
            best = (dist, side, name, rail)
    return best


def run(alerts, bars, stop, tp):
    keys = [k for k in sorted(bars) if k.weekday() < 5 and 9 * 60 <= k.hour * 60 + k.minute < 16 * 60]
    ai = 0
    active = {}
    pos = None
    out = []
    ambiguous = 0
    for i in range(1, len(keys)):
        dt = keys[i]
        hold_dt = keys[i - 1]
        hold = bars[hold_dt] if (dt - hold_dt).total_seconds() == 60 else None
        b = bars[dt]
        asof = (hold_dt.timestamp() + 60) if hold else dt.timestamp()
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        close_dt = dt + timedelta(minutes=1)
        if pos:
            side, entry, name, rail, opened = pos
            if side == "Buy":
                hit_stop = b["l"] <= entry - stop
                hit_tp = b["h"] >= entry + tp
                pts_close = b["c"] - entry
            else:
                hit_stop = b["h"] >= entry + stop
                hit_tp = b["l"] <= entry - tp
                pts_close = entry - b["c"]
            if hit_stop and hit_tp:
                ambiguous += 1
            if hit_stop or hit_tp:
                pts, how = (-stop, "stop") if hit_stop else (tp, "tp")
                out.append((opened, side, name, rail, entry, pts, how))
                pos = None
            elif close_dt.hour * 60 + close_dt.minute >= 16 * 60:
                out.append((opened, side, name, rail, entry, pts_close, "eod"))
                pos = None
            continue
        if hold is None or not in_session(close_dt):
            continue
        hit = pick(hold, b, active)
        if hit is None:
            continue
        dist, side, name, rail = hit
        pos = (side, b["c"], name, rail, dt)
    if pos:
        side, entry, name, rail, opened = pos
        last = bars[keys[-1]]
        pts = (last["c"] - entry) if side == "Buy" else (entry - last["c"])
        out.append((opened, side, name, rail, entry, pts, "open"))
    return out, ambiguous


def stats(rows):
    closed = [r for r in rows if r[6] != "open"]
    wins = [r[5] for r in closed if r[5] > 0]
    losses = [r[5] for r in closed if r[5] < 0]
    gp, gl = sum(wins), abs(sum(losses))
    net = sum(r[5] for r in closed)
    wr = (len(wins) / len(closed)) if closed else 0
    pf = (gp / gl) if gl else 0
    return closed, net, wr, pf


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    start = "2026-09-14T14:00:00Z"
    end = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("ENTRIES live rule. GRID exits only. BOT NOT CHANGED.", flush=True)
    bars = pull_bars(key, start, end)
    board = []
    for stop in STOPS:
        for tp in TPS:
            rows, amb = run(alerts, bars, float(stop), float(tp))
            closed, net, wr, pf = stats(rows)
            w = sum(1 for r in closed if r[5] > 0)
            l = sum(1 for r in closed if r[5] < 0)
            board.append((net, stop, tp, len(closed), w, l, wr, pf, amb, rows))
    board.sort(key=lambda r: r[0], reverse=True)
    print(f"{'stop':>4} {'tp':>4} {'n':>4} {'W':>3} {'L':>3} {'WR':>6} {'PF':>5} {'pts':>8} {'$':>8}  note", flush=True)
    for net, stop, tp, n, w, l, wr, pf, amb, _rows in board:
        mark = "CURRENT" if stop == 20 and tp == 40 else ""
        if amb:
            mark = (mark + " amb " + str(amb)).strip()
        print(
            f"{stop:4} {tp:4} {n:4} {w:3} {l:3} {wr:6.1%} {pf:5.2f} {net:+8.1f} {net * 10:+8.0f}  {mark}",
            flush=True,
        )
    best = board[0]
    base = next(r for r in board if r[1] == 20 and r[2] == 40)
    print(f"BEST {best[1]}/{best[2]} {best[0]:+.1f} pts", flush=True)
    print(f"CURRENT 20/40 {base[0]:+.1f} pts", flush=True)
    for title, row in (("BEST", best), ("CURRENT", base)):
        print(title, flush=True)
        for opened, side, name, rail, entry, pts, how in row[9]:
            if how == "open":
                continue
            print(
                f"{opened:%m-%d %H:%M} {side:4} {name}@{rail:.2f} @{entry:.2f} {pts:+.1f} {how}",
                flush=True,
            )


if __name__ == "__main__":
    main()
