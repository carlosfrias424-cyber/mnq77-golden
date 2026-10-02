#!/usr/bin/env python3
"""Score only. No orders. Does not change the bot.

The 4365 card. Nothing else.

10:00 through the 15:59 bar, Chicago. Stop 20, target 40.
The hold bar trades the rail.
A named low is a buy. A named high is a sell.
A bare H4 or H1 follows the hold close.
Sellers larger on the hold for a long, then the next minute's buyers are larger,
and that minute closes higher and still within 15 of the rail. Short is the flip.
That close is the fill. Farther than 15, no trade.
After the trade, that rail is dead until a close is 20 points away.
Skip ONH, ONL, EMA, OPEN. No ATR.
Still open on the 15:59 bar, flatten at that close.
Sep 14-25 has to print 33 trades and +436.5. Then the same rules through today.
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


def signed_size(rec):
    sz = int(getattr(rec, "size", 0) or 0)
    s = str(getattr(rec, "side", "") or "").upper()
    if s in ("B", "BUY", "BID"):
        return sz
    if s in ("A", "SELL", "ASK"):
        return -sz
    return 0


def pull(key):
    client = db.Historical(key)
    raw = str(client.metadata.get_dataset_range(dataset="GLBX.MDP3")["end"])
    end_dt = datetime.fromisoformat(raw.replace("Z", "+00:00")) - timedelta(minutes=1)
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    start = "2026-09-14T09:00:00Z"
    print(f"PULL {start} {end}", flush=True)
    data = client.timeseries.get_range(
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
        sg = signed_size(rec)
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
    print("TRADES", n, "BARS", len(bars), flush=True)
    return bars


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


def choose_side(name, hold, rail):
    if name in SUPPORT or (name in BARE and hold["c"] > rail):
        return "Buy"
    if name in RESIST or (name in BARE and hold["c"] < rail):
        return "Sell"
    return None


def run(alerts, bars):
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
            pts = how = None
            if hit_stop or hit_tp:
                pts, how = (-STOP, "stop") if hit_stop else (TP, "tp")
                done = True
            elif dt.hour == 15 and dt.minute >= 59:
                pts, how, done = pts_close, "eod", True
            if done:
                out.append({
                    "t": opened, "side": side, "name": name, "rail": rail,
                    "entry": entry, "pts": pts, "how": how,
                    "dist": abs(entry - rail),
                })
                quiet[name] = rail
                pos = None
            continue
        for name, px in list(quiet.items()):
            if abs(b["c"] - px) >= 20:
                del quiet[name]
        m = dt.hour * 60 + dt.minute
        if hold is None or not (10 * 60 <= m < 16 * 60):
            continue
        best = None
        for name, rail in active.items():
            if name in quiet:
                continue
            if not (hold["l"] <= rail <= hold["h"]):
                continue
            side = choose_side(name, hold, rail)
            if side is None or not fade_ok(side, hold, b, rail):
                continue
            dist = abs(b["c"] - rail)
            if dist > 15:
                continue
            if best is None or dist < best[0]:
                best = (dist, side, name, rail)
        if best is not None:
            _dist, side, name, rail = best
            pos = (side, b["c"], name, rail, dt)
    if pos is not None:
        side, entry, name, rail, opened = pos
        out.append({
            "t": opened, "side": side, "name": name, "rail": rail,
            "entry": entry, "pts": 0.0, "how": "open",
            "dist": abs(entry - rail),
        })
    return out


def show(title, rows):
    closed = [r for r in rows if r["how"] != "open"]
    wins = [r for r in closed if r["pts"] > 0]
    losses = [r for r in closed if r["pts"] < 0]
    net = sum(r["pts"] for r in closed)
    print(
        f"{title} TRADES {len(closed)} W {len(wins)} L {len(losses)} PNL {net:+.1f} pts  ${net * 10:+.0f}",
        flush=True,
    )
    for r in rows:
        print(
            f"{r['t']:%m-%d %H:%M} {r['side']:4} {r['name']}@{r['rail']:.2f} "
            f"@{r['entry']:.2f} {r['pts']:+.1f} {r['how']} dist {r['dist']:.2f}",
            flush=True,
        )


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("BOT NOT CHANGED", flush=True)
    print(
        "4365  entry within 15  rail dead until 20 away  "
        "stop 20  target 40  flat 15:59  no ONH no ONL",
        flush=True,
    )
    rows = run(alerts, pull(key))
    early = [r for r in rows if r["t"].date().isoformat() <= "2026-09-25"]
    early_net = sum(r["pts"] for r in early if r["how"] != "open")
    ok = len([r for r in early if r["how"] != "open"]) == 33 and abs(early_net - 436.5) < 1
    print("SANITY_OK" if ok else "SANITY_FAIL", len(early), f"{early_net:+.1f}", flush=True)
    show("SEP14-25", early)
    show("THROUGH_TODAY", rows)


if __name__ == "__main__":
    main()
