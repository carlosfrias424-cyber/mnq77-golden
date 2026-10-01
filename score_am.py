#!/usr/bin/env python3
"""Paper only. No orders.

6:00-8:30 CT add-on. Same fade as the live bot.
Hold bar trades the rail and closes within 15, on the hold side.
Sellers larger than buyers on a long. Buyers larger than sellers on a short.
The next 1-minute bar lifts off the rail. That close is the entry.
ATR of the last 14 minutes must be under 15.
Stop 20, target 40. Flatten at 8:30. One position.
This book does not block the 10:00 book. Sep 14 through Oct 1.
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
STOP, TP, NEAR, ATR_MAX = 20.0, 40.0, 15.0, 15.0
SKIP = ("EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT", "ONL"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE", "ONH"}
BARE = {"H4", "H1"}
START = datetime(2026, 9, 14, tzinfo=TZ)
END = datetime(2026, 10, 1, tzinfo=TZ)


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


def px_of(rec):
    raw = getattr(rec, "pretty_price", None)
    if raw is not None:
        try:
            return float(raw)
        except Exception:
            pass
    x = float(rec.price)
    return x / 1e9 if abs(x) > 1e7 else x


def side_delta(rec, sz):
    s = str(getattr(rec, "side", "") or "").upper()
    if s in ("B", "BUY", "BID"):
        return sz
    if s in ("A", "SELL", "ASK"):
        return -sz
    return 0.0


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


def days():
    d = START
    while d <= END:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def pull_morning(key, day):
    open_ = day.replace(hour=5, minute=30, second=0, microsecond=0)
    shut = day.replace(hour=8, minute=31, second=0, microsecond=0)
    data = db.Historical(key).timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="trades",
        start=open_.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        end=shut.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    bars = {}
    n = 0
    for rec in data:
        n += 1
        ts = rec.ts_event / 1e9
        dt = datetime.fromtimestamp(ts, TZ).replace(second=0, microsecond=0)
        if dt.date() != day.date():
            continue
        px = px_of(rec)
        sz = float(getattr(rec, "size", 0) or 0)
        b = bars.get(dt)
        if b is None:
            b = bars[dt] = {"h": px, "l": px, "c": px, "delta": 0.0}
        b["h"] = max(b["h"], px)
        b["l"] = min(b["l"], px)
        b["c"] = px
        b["delta"] += side_delta(rec, sz)
    return bars, n


def atr14(times, bars, i):
    if i < 14:
        return None
    if any(times[j] <= times[j - 1] for j in range(i - 13, i + 1)):
        return None
    trs = []
    for j in range(i - 13, i + 1):
        prev = bars[times[j - 1]]["c"]
        b = bars[times[j]]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev)))
    return sum(trs) / 14.0


def pick(hold, lift, active):
    best = None
    for name, rail in active.items():
        if not (hold["l"] <= rail <= hold["h"]):
            continue
        dist = abs(hold["c"] - rail)
        if dist > NEAR:
            continue
        if hold["c"] > rail and hold["delta"] < 0:
            if not (lift["delta"] > 0 and lift["c"] > hold["c"] and lift["c"] > rail):
                continue
            side = "Buy"
        elif hold["c"] < rail and hold["delta"] > 0:
            if not (lift["delta"] < 0 and lift["c"] < hold["c"] and lift["c"] < rail):
                continue
            side = "Sell"
        else:
            continue
        if best is None or dist < best[0]:
            best = (dist, side, name, rail)
    return best


def week(day):
    if day <= "09-18":
        return "09-14"
    if day <= "09-25":
        return "09-21"
    return "09-28"


def show(title, rows):
    wins = sum(1 for r in rows if r["pts"] > 0)
    losses = sum(1 for r in rows if r["pts"] < 0)
    net = sum(r["pts"] for r in rows)
    bits = "  ".join(
        f"{w} {sum(r['pts'] for r in rows if week(r['day']) == w):+.0f}"
        for w in ("09-14", "09-21", "09-28")
    )
    print(f"{title}  n {len(rows)}  W {wins}  L {losses}  {net:+.1f} pts  {bits}", flush=True)


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("WINDOW 06:00-08:30 CT  ATR<15  stop 20  target 40  flat 08:30", flush=True)
    gated = []
    raw = []
    ai = 0
    active = {}
    for day in days():
        bars, n = pull_morning(key, day)
        print(f"DAY {day:%m-%d} TRADES {n} BARS {len(bars)}", flush=True)
        times = sorted(bars)
        pos = None
        for i in range(1, len(times)):
            dt = times[i]
            hold_dt = times[i - 1]
            lift = bars[dt]
            close = dt + timedelta(minutes=1)
            cm = close.hour * 60 + close.minute
            if pos is not None:
                side, entry, name, rail, atr, dist = pos
                if side == "Buy":
                    hit_stop = lift["l"] <= entry - STOP
                    hit_tp = lift["h"] >= entry + TP
                else:
                    hit_stop = lift["h"] >= entry + STOP
                    hit_tp = lift["l"] <= entry - TP
                done = None
                if hit_stop or hit_tp:
                    pts, how = (-STOP, "stop") if hit_stop else (TP, "tp")
                    done = (pts, how)
                elif cm >= 8 * 60 + 30:
                    mark = (lift["c"] - entry) if side == "Buy" else (entry - lift["c"])
                    done = (max(-STOP, min(TP, mark)), "flat")
                if done:
                    pts, how = done
                    rec = {
                        "day": f"{dt:%m-%d}", "hm": f"{pos_time:%H:%M}", "side": side,
                        "name": name, "rail": rail, "entry": entry, "pts": pts,
                        "how": how, "atr": atr, "dist": dist,
                    }
                    raw.append(rec)
                    if atr is not None and atr < ATR_MAX:
                        gated.append(rec)
                    pos = None
                continue
            if (dt - hold_dt) != timedelta(minutes=1):
                continue
            if not (6 * 60 <= cm < 8 * 60 + 30):
                continue
            hold = bars[hold_dt]
            asof = (hold_dt + timedelta(minutes=1)).timestamp()
            while ai < len(alerts) and alerts[ai][0] <= asof:
                _, kind, px = alerts[ai]
                active[kind] = px
                ai += 1
            hit = pick(hold, lift, active)
            if hit is None:
                continue
            dist, side, name, rail = hit
            atr = atr14(times, bars, i)
            if atr is None or atr >= ATR_MAX:
                continue
            pos = (side, lift["c"], name, rail, atr, dist)
            pos_time = close
        if pos is not None:
            side, entry, name, rail, atr, dist = pos
            last = bars[times[-1]]
            mark = (last["c"] - entry) if side == "Buy" else (entry - last["c"])
            rec = {
                "day": f"{times[-1]:%m-%d}", "hm": f"{pos_time:%H:%M}", "side": side,
                "name": name, "rail": rail, "entry": entry,
                "pts": max(-STOP, min(TP, mark)), "how": "flat",
                "atr": atr, "dist": dist,
            }
            raw.append(rec)
            if atr is not None and atr < ATR_MAX:
                gated.append(rec)
    print("GATE", flush=True)
    for r in gated:
        print(
            f"{r['day']} {r['hm']} {r['side']:4} {r['name']}@{r['rail']:.2f} "
            f"@{r['entry']:.2f} {r['pts']:+.1f} {r['how']} atr {r['atr']:.1f} dist {r['dist']:.2f}",
            flush=True,
        )
    show("ATR<15", gated)


if __name__ == "__main__":
    main()
