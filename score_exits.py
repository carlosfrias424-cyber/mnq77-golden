#!/usr/bin/env python3
"""Paper only. No orders. Does not change the live bot.

Hermes entry rule. No rail lock.
Last two weeks. 10:00-16:00 Chicago. Hold close within 15.
Next bar lifts. One position. Same rail can fire again after the trade is done.
Skip ONH, ONL, EMA, OPEN.
A stop that trades is minus the stop. A target that trades is plus the target.
A 16:00 flatten is the last price, capped at the stop and the target.
If stop and target both print on the same bar, the stop counts.
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
STOPS = (18, 20, 22, 24, 25, 26, 28, 30, 35)
TPS = (30, 35, 40, 45, 50, 60)
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


def session_over(dt):
    return dt.hour * 60 + dt.minute >= 16 * 60


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
            elif session_over(close_dt):
                pts = max(-stop, min(tp, pts_close))
                out.append((opened, side, name, rail, entry, pts, "eod"))
                pos = None
            continue
        if hold is None or not in_session(close_dt):
            continue
        hit = pick(hold, b, active)
        if hit is None:
            continue
        _dist, side, name, rail = hit
        pos = (side, b["c"], name, rail, dt)
    if pos:
        side, entry, name, rail, opened = pos
        last = bars[keys[-1]]
        pts = (last["c"] - entry) if side == "Buy" else (entry - last["c"])
        pts = max(-stop, min(tp, pts))
        out.append((opened, side, name, rail, entry, pts, "open"))
    return out, ambiguous, keys


def extra_past_stop(keys, bars, opened, side, entry, stop, tp):
    """Points past the stop before the target, this trade alone. None if the target never prints."""
    seen_stop = False
    mae = 0.0
    for dt in keys:
        if dt < opened:
            continue
        b = bars[dt]
        if side == "Buy":
            mae = max(mae, entry - b["l"])
            hit_stop = b["l"] <= entry - stop
            hit_tp = b["h"] >= entry + tp
        else:
            mae = max(mae, b["h"] - entry)
            hit_stop = b["h"] >= entry + stop
            hit_tp = b["l"] <= entry - tp
        if hit_stop and hit_tp and not seen_stop:
            return None
        if seen_stop and hit_tp:
            return round(mae - stop, 2)
        if hit_stop:
            seen_stop = True
        if session_over(dt + timedelta(minutes=1)):
            break
    return None


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
    start = "2026-09-16T14:00:00Z"
    end = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("WINDOW last two weeks. HERMES RULE. NO RAIL LOCK. ONH ONL skipped. BOT NOT CHANGED.", flush=True)
    bars = pull_bars(key, start, end)
    board = []
    base_rows = None
    base_keys = None
    for stop in STOPS:
        for tp in TPS:
            rows, amb, keys = run(alerts, bars, float(stop), float(tp))
            closed, net, wr, pf = stats(rows)
            w = sum(1 for r in closed if r[5] > 0)
            l = sum(1 for r in closed if r[5] < 0)
            if stop == 20 and tp == 40:
                base_rows, base_keys = rows, keys
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
    print(f"BEST {best[1]}/{best[2]} {best[0]:+.1f} pts  ${best[0] * 10:+.0f}", flush=True)
    print(f"CURRENT 20/40 {base[0]:+.1f} pts  ${base[0] * 10:+.0f}", flush=True)
    print("FEW POINTS  20-stop losers that later reached the 40 target the same day. This trade alone.", flush=True)
    extras = []
    stops_n = 0
    for opened, side, name, rail, entry, _pts, how in base_rows:
        if how != "stop":
            continue
        stops_n += 1
        extra = extra_past_stop(base_keys, bars, opened, side, entry, 20.0, 40.0)
        if extra is None:
            continue
        extras.append((extra, opened, side, name, rail, entry))
    print(f"STOPS {stops_n}  LATER_TARGET {len(extras)}", flush=True)
    for n in (2, 3, 5, 8, 10):
        k = sum(1 for e, *_ in extras if e <= n)
        print(f"extra<={n} {k}", flush=True)
    for extra, opened, side, name, rail, entry in sorted(extras):
        print(
            f"{opened:%m-%d %H:%M} {side:4} {name}@{rail:.2f} @{entry:.2f} extra {extra:.1f}",
            flush=True,
        )
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
