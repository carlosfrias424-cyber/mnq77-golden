#!/usr/bin/env python3
"""Score only. No orders. Does not change the bot.

The process that was started on the box. Not the 4365 card. Not the later files.

10:00-16:00 CT. Stop 20, target 40, 5 MNQ.
The hold bar trades the rail and closes within 15, on the hold side.
Sellers larger on a long. Buyers larger on a short.
The next minute lifts. That close is the fill, even if it is more than 15 away.
ONH and ONL count. EMA and OPEN do not.
No ATR. No 20-point rail lock. The next bar after an exit can fire.
Still open at 16:00, flatten. A trade still open when the tape ends is marked open.
Sep 14 through the last tape Databento has.
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
STOP, TP, NEAR = 20.0, 40.0, 15.0
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
    for attr in ("pretty_price", "price"):
        raw = getattr(rec, attr, None)
        if raw is None:
            continue
        try:
            return float(raw)
        except Exception:
            pass
    x = float(rec.price)
    return x / 1e9 if abs(x) > 1e7 else x


def signed(rec):
    sz = float(getattr(rec, "size", 0) or 0)
    s = str(getattr(rec, "side", "") or "").upper()
    if s in ("B", "BUY", "BID"):
        return sz
    if s in ("A", "SELL", "ASK"):
        return -sz
    return 0.0


def pull(key):
    client = db.Historical(key)
    raw = str(client.metadata.get_dataset_range(dataset="GLBX.MDP3")["end"])
    end_dt = datetime.fromisoformat(raw.replace("Z", "+00:00")) - timedelta(minutes=1)
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    print("PULL", end, flush=True)
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="trades",
        start="2026-09-14T13:00:00Z",
        end=end,
    )
    bars = {}
    n = 0
    for rec in data:
        n += 1
        if n % 500000 == 0:
            print("TRADES", n, flush=True)
        ts = rec.ts_event / 1e9
        dt = datetime.fromtimestamp(ts, TZ)
        if dt.weekday() >= 5:
            continue
        m = dt.hour * 60 + dt.minute
        if m < 10 * 60 or m >= 16 * 60:
            continue
        key_dt = dt.replace(second=0, microsecond=0)
        px = px_of(rec)
        dlt = signed(rec)
        b = bars.get(key_dt)
        if b is None:
            b = bars[key_dt] = {
                "o": px, "h": px, "l": px, "c": px,
                "buy": 0.0, "sell": 0.0, "prints": [],
            }
        b["h"] = max(b["h"], px)
        b["l"] = min(b["l"], px)
        b["c"] = px
        if dlt > 0:
            b["buy"] += dlt
        elif dlt < 0:
            b["sell"] += -dlt
        b["prints"].append((ts, px))
    print("TRADES", n, "BARS", len(bars), flush=True)
    return bars


def crossed(side, px, stop_px, tp_px):
    if side == "Buy":
        if px <= stop_px:
            return -STOP, "stop"
        if px >= tp_px:
            return TP, "tp"
    else:
        if px >= stop_px:
            return -STOP, "stop"
        if px <= tp_px:
            return TP, "tp"
    return None


def score(alerts, bars):
    keys = sorted(bars)
    ai = 0
    active = {}
    pos = None
    out = []
    for i in range(1, len(keys)):
        dt = keys[i]
        hold_dt = keys[i - 1]
        b = bars[dt]
        close_ts = (dt + timedelta(minutes=1)).timestamp()
        asof = hold_dt.timestamp() + 60
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        if pos is not None:
            side, entry, stop_px, tp_px, meta = pos
            done = None
            for ts, px in b["prints"]:
                if ts >= close_ts and dt.hour * 60 + dt.minute >= 15 * 60 + 59:
                    pts = (px - entry) if side == "Buy" else (entry - px)
                    done = (pts, "flat16")
                    break
                hit = crossed(side, px, stop_px, tp_px)
                if hit:
                    done = hit
                    break
            if done is None and dt.hour == 15 and dt.minute == 59:
                pts = (b["c"] - entry) if side == "Buy" else (entry - b["c"])
                done = (pts, "flat16")
            if done:
                out.append((meta, done[0], done[1]))
                pos = None
            continue
        if (dt - hold_dt).total_seconds() != 60:
            continue
        close_dt = dt + timedelta(minutes=1)
        m = close_dt.hour * 60 + close_dt.minute
        if not (10 * 60 <= m < 16 * 60):
            continue
        hold = bars[hold_dt]
        best = None
        for name, rail in active.items():
            if not (hold["l"] <= rail <= hold["h"]):
                continue
            dist = abs(hold["c"] - rail)
            if dist > NEAR:
                continue
            if hold["c"] > rail and hold["sell"] > hold["buy"]:
                if not (b["buy"] > b["sell"] and b["c"] > hold["c"] and b["c"] > rail):
                    continue
                side = "Buy"
            elif hold["c"] < rail and hold["buy"] > hold["sell"]:
                if not (b["sell"] > b["buy"] and b["c"] < hold["c"] and b["c"] < rail):
                    continue
                side = "Sell"
            else:
                continue
            if best is None or dist < best[0]:
                best = (dist, side, name, rail)
        if best is None:
            continue
        dist, side, name, rail = best
        entry = b["c"]
        stop_px = entry - STOP if side == "Buy" else entry + STOP
        tp_px = entry + TP if side == "Buy" else entry - TP
        meta = {
            "t": close_dt, "side": side, "name": name, "rail": rail,
            "entry": entry, "dist": abs(entry - rail),
        }
        pos = (side, entry, stop_px, tp_px, meta)
    if pos is not None:
        side, entry, _s, _t, meta = pos
        last = bars[keys[-1]]["c"]
        pts = (last - entry) if side == "Buy" else (entry - last)
        out.append((meta, pts, "open"))
    return out


def show(rows):
    closed = [(m, p, h) for m, p, h in rows if h != "open"]
    wins = [p for _, p, _ in closed if p > 0]
    losses = [p for _, p, _ in closed if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    net = gp - gl
    n = len(closed)
    wr = (len(wins) / n) if n else 0
    pf = (gp / gl) if gl else 0
    print(
        f"CLOSED {n}  W {len(wins)} L {len(losses)}  "
        f"WR {wr:.1%}  PF {pf:.2f}  "
        f"PNL {net:+.1f} pts  ${net * 10:+.0f}",
        flush=True,
    )
    for meta, pts, how in rows:
        print(
            f"{meta['t']:%m-%d %H:%M:%S} {meta['side']:4} {meta['name']}@{meta['rail']:.2f} "
            f"@{meta['entry']:.2f} {pts:+.1f} {how} dist {meta['dist']:.2f}",
            flush=True,
        )


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print(
        "BOX  10:00-16:00  hold within 15  fill any distance  "
        "stop 20  target 40  no atr  no rail lock  ONH ONL on",
        flush=True,
    )
    show(score(alerts, pull(key)))


if __name__ == "__main__":
    main()
