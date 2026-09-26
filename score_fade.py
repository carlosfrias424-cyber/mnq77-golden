#!/usr/bin/env python3
"""Paper score only. No orders.

Long: 1m bar trades a rail, closes above it, sell size > buy size.
Next 1m bar: buy size > sell size, close higher, still above the rail.
That close is the entry. If the next bar does not lift, the setup is dead.
Short is the mirror.
Book: stop 20, target 40. One trade at a time.
Same rail stays quiet until price is 20 pts away.
Tape: Databento B = buy aggressor, A = sell aggressor.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
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


def session(dt):
    if dt.weekday() >= 5:
        return False
    m = dt.hour * 60 + dt.minute
    return 4 * 60 <= m < 16 * 60


def pull_bars(key):
    print("PULL", flush=True)
    data = db.Historical(key).timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="trades",
        start="2026-09-14T09:00:00Z",
        end="2026-09-25T21:00:00Z",
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


def score(alerts, bars, tp):
    keys = [k for k in sorted(bars) if session(k)]
    ai = 0
    active = {}
    quiet = {}
    pos = None
    out = []
    for i in range(1, len(keys)):
        dt = keys[i]
        hold_dt = keys[i - 1]
        if (dt - hold_dt).total_seconds() != 60:
            hold = None
        else:
            hold = bars[hold_dt]
        b = bars[dt]
        t = dt.timestamp()
        asof = (hold_dt.timestamp() + 60) if hold else t
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        if pos:
            side, entry, stop_px, tp_px, meta = pos
            if side == "Buy":
                hit_stop = b["l"] <= stop_px
                hit_tp = b["h"] >= tp_px
            else:
                hit_stop = b["h"] >= stop_px
                hit_tp = b["l"] <= tp_px
            if hit_stop or hit_tp:
                pts, how = (-STOP, "stop") if hit_stop else (tp, "tp")
                out.append((meta, pts, how))
                quiet[meta["name"]] = meta["rail"]
                pos = None
            elif dt.hour == 15 and dt.minute >= 59:
                pts = (b["c"] - entry) if side == "Buy" else (entry - b["c"])
                out.append((meta, pts, "eod"))
                quiet[meta["name"]] = meta["rail"]
                pos = None
            continue
        for name, px in list(quiet.items()):
            if abs(b["c"] - px) >= 20:
                del quiet[name]
        if hold is None or not (10 <= dt.hour < 16):
            continue
        best = None
        for name, rail in active.items():
            if name in quiet or not (hold["l"] <= rail <= hold["h"]):
                continue
            if name in SUPPORT or (name in BARE and hold["c"] > rail):
                side = "Buy"
            elif name in RESIST or (name in BARE and hold["c"] < rail):
                side = "Sell"
            else:
                continue
            if side == "Buy":
                if not (hold["c"] > rail and hold["sell"] > hold["buy"]):
                    continue
                if not (b["buy"] > b["sell"] and b["c"] > hold["c"] and b["c"] > rail):
                    continue
            else:
                if not (hold["c"] < rail and hold["buy"] > hold["sell"]):
                    continue
                if not (b["sell"] > b["buy"] and b["c"] < hold["c"] and b["c"] < rail):
                    continue
            dist = abs(b["c"] - rail)
            if dist > 15:
                continue
            if best is None or dist < best[0]:
                best = (dist, side, name, rail, hold)
        if best is None:
            continue
        dist, side, name, rail, hold = best
        entry = b["c"]
        if side == "Buy":
            stop_px, tp_px = entry - STOP, entry + tp
        else:
            stop_px, tp_px = entry + STOP, entry - tp
        meta = {"t": dt, "side": side, "name": name, "rail": rail, "entry": entry, "dist": dist}
        pos = (side, entry, stop_px, tp_px, meta)
    return out


def show(title, rows, tp):
    wins = [p for _, p, _ in rows if p > 0]
    losses = [p for _, p, _ in rows if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    net = gp - gl
    wr = (len(wins) / len(rows)) if rows else 0
    pf = (gp / gl) if gl else 0
    aw = (gp / len(wins)) if wins else 0
    al = (gl / len(losses)) if losses else 0
    rr = (aw / al) if al else 0
    print(title, flush=True)
    print(
        f"TRADES {len(rows)}  W {len(wins)} L {len(losses)}  "
        f"WR {wr:.1%}  PF {pf:.2f}  RR {rr:.2f}  book {tp/STOP:.2f}R  "
        f"PNL {net:+.1f} pts  ${net * 10:+.0f}",
        flush=True,
    )
    for meta, pts, how in rows:
        print(
            f"{meta['t']:%m-%d %H:%M} {meta['side']:4} {meta['name']}@{meta['rail']:.2f} "
            f"@{meta['entry']:.2f} {pts:+.1f} {how} dist {meta['dist']:.2f}",
            flush=True,
        )


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("WINDOW 10:00-16:00 CT  dist<=15  stop 20", flush=True)
    bars = pull_bars(key)
    show("2.0R  target 40", score(alerts, bars, 40.0), 40.0)
    show("1.5R  target 30", score(alerts, bars, 30.0), 30.0)


if __name__ == "__main__":
    main()
