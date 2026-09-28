#!/usr/bin/env python3
"""Score one Chicago day with the Hermes card and compare it to the live submits.

No orders. Hold close must be within 15. Entry can be farther.
Stop 20, target 40. One position. Flatten on the 15:59 bar if neither traded.
A clock exit is capped at +40 and -20. The rail goes quiet when the trade ends,
until a later bar closes 20 points away.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
LOG = ROOT / "logs/seven.jsonl"
STOP, TP = 20.0, 40.0
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}


def envload():
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        if not raw.strip() or raw.startswith("#") or "=" not in raw:
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
    if not POI.exists():
        return out
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


def pull_bars(key, day):
    start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    end = datetime(day.year, day.month, day.day, 16, 5, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    print("PULL", day.isoformat(), flush=True)
    data = db.Historical(key).timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="trades",
        start=start.strftime("%Y-%m-%dT%H:%M:%S"),
        end=end.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    bars = {}
    n = 0
    for rec in data:
        n += 1
        if n % 500000 == 0:
            print("TRADES", n, flush=True)
        ts = rec.ts_event / 1e9
        dt = datetime.fromtimestamp(ts, TZ).replace(second=0, microsecond=0)
        if dt.date() != day:
            continue
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


def score(alerts, bars, day):
    keys = [k for k in sorted(bars) if k.date() == day and 4 * 60 <= k.hour * 60 + k.minute < 16 * 60]
    ai = 0
    active = {}
    quiet = {}
    pos = None
    out = []
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
        if pos:
            side, entry, stop_px, tp_px, meta = pos
            if side == "Buy":
                hit_stop = b["l"] <= stop_px
                hit_tp = b["h"] >= tp_px
            else:
                hit_stop = b["h"] >= stop_px
                hit_tp = b["l"] <= tp_px
            if hit_stop or hit_tp:
                pts, how = (-STOP, "stop") if hit_stop else (TP, "tp")
                out.append((meta, pts, how))
                quiet[meta["name"]] = meta["rail"]
                pos = None
            elif dt.hour == 15 and dt.minute >= 59:
                pts = (b["c"] - entry) if side == "Buy" else (entry - b["c"])
                pts = min(TP, max(-STOP, pts))
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
            dist = abs(hold["c"] - rail)
            if dist > 15:
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
        if side == "Buy":
            stop_px, tp_px = entry - STOP, entry + TP
        else:
            stop_px, tp_px = entry + STOP, entry - TP
        meta = {"t": dt, "side": side, "name": name, "rail": rail, "entry": entry, "dist": dist}
        pos = (side, entry, stop_px, tp_px, meta)
    if pos:
        meta = pos[4]
        out.append((meta, 0.0, "open"))
    return out


def live_fills(day):
    rows = []
    if not LOG.exists():
        return rows
    for ln in LOG.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("event") != "struct40_submit" or not o.get("submit"):
            continue
        ts = o.get("ts")
        if not ts:
            continue
        dt = datetime.fromtimestamp(ts / 1000, TZ)
        if dt.date() != day:
            continue
        poi = str(o.get("poi") or "")
        name, _, rail = poi.partition("@")
        try:
            rail_px = float(rail)
        except ValueError:
            rail_px = None
        try:
            mid = float(o.get("mid"))
        except (TypeError, ValueError):
            mid = None
        rows.append({
            "t": dt.replace(second=0, microsecond=0),
            "side": o.get("side"),
            "name": name,
            "rail": rail_px,
            "entry": mid,
        })
    return rows


def key_of(t, side, name, rail):
    px = None if rail is None else round(float(rail), 2)
    return (t.strftime("%H:%M"), side, name, px)


def show(title, rows):
    wins = [p for _, p, how in rows if p > 0 and how != "open"]
    losses = [p for _, p, how in rows if p < 0]
    net = sum(p for _, p, how in rows if how != "open")
    print(title, flush=True)
    print(f"TRADES {len(rows)}  W {len(wins)} L {len(losses)}  {net:+.1f} pts  ${net * 10:+.0f}", flush=True)
    for meta, pts, how in rows:
        print(
            f"{meta['t']:%H:%M} {meta['side']:4} {meta['name']}@{meta['rail']:.2f} "
            f"@{meta['entry']:.2f} {pts:+.1f} {how} dist {meta['dist']:.2f}",
            flush=True,
        )


def main():
    envload()
    day = datetime.now(TZ).date()
    if len(sys.argv) > 1:
        day = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), "DAY", day.isoformat(), flush=True)
    bars = pull_bars(key, day)
    hermes = score(alerts, bars, day)
    live = live_fills(day)
    show("HERMES", hermes)
    print("LIVE", flush=True)
    print(f"FILLS {len(live)}", flush=True)
    for r in live:
        rail = "" if r["rail"] is None else f"{r['rail']:.2f}"
        ent = "" if r["entry"] is None else f"{r['entry']:.2f}"
        print(f"{r['t']:%H:%M} {r['side']} {r['name']}@{rail} @{ent}", flush=True)
    hkeys = [key_of(m["t"], m["side"], m["name"], m["rail"]) for m, _, _ in hermes]
    lkeys = [key_of(r["t"], r["side"], r["name"], r["rail"]) for r in live]
    print("COMPARE", flush=True)
    if hkeys == lkeys:
        print("IDENTICAL", len(hkeys), flush=True)
        return
    print("NOT_IDENTICAL", flush=True)
    hs, ls = set(hkeys), set(lkeys)
    for k in hkeys:
        if k not in ls:
            print("HERMES_ONLY", *k, flush=True)
    for k in lkeys:
        if k not in hs:
            print("LIVE_ONLY", *k, flush=True)


if __name__ == "__main__":
    main()
