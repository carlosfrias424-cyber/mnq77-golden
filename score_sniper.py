#!/usr/bin/env python3
"""Score only. No orders.

The process that is running. Not the later files.

10:00-16:00 CT. Stop 20, target 40, 5 MNQ.
Hold bar trades the rail, closes within 15, on the hold side.
Sellers larger on a long. Buyers larger on a short.
The next minute lifts. That close is the fill, even if it is more than 15 away.
No ATR filter. One position. Flatten at 16:00.
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


def in_session(dt):
    if dt.weekday() >= 5:
        return False
    m = dt.hour * 60 + dt.minute
    return 10 * 60 <= m < 16 * 60


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
        if m < 8 * 60 or m >= 16 * 60:
            continue
        key_dt = dt.replace(second=0, microsecond=0)
        px = px_of(rec)
        sz = float(getattr(rec, "size", 0) or 0)
        s = str(getattr(rec, "side", "") or "").upper()
        dlt = sz if s in ("B", "BUY", "BID") else (-sz if s in ("A", "SELL", "ASK") else 0.0)
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
        b["prints"].append((ts, px, dlt))
    print("TRADES", n, "BARS", len(bars), flush=True)
    return bars


def atr14(keys, bars, i):
    if i < 14:
        return None
    use = keys[i - 14:i + 1]
    for j in range(1, 15):
        if (use[j] - use[j - 1]).total_seconds() != 60:
            return None
    trs = []
    for j in range(1, 15):
        prev_c = bars[use[j - 1]]["c"]
        h, l = bars[use[j]]["h"], bars[use[j]]["l"]
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / 14.0


def arm(hold, active):
    best = None
    for name, rail in active.items():
        if not (hold["l"] <= rail <= hold["h"]):
            continue
        dist = abs(hold["c"] - rail)
        if dist > NEAR:
            continue
        if hold["c"] > rail and hold["sell"] > hold["buy"]:
            side = "Buy"
        elif hold["c"] < rail and hold["buy"] > hold["sell"]:
            side = "Sell"
        else:
            continue
        if best is None or dist < best[0]:
            best = (dist, side, name, rail)
    return best


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


def eod_of(dt):
    return dt.replace(hour=16, minute=0, second=0, microsecond=0).timestamp()


def score(alerts, bars, mode):
    keys = sorted(bars)
    ai = 0
    active = {}
    pos = None
    skip_hold = None
    out = []
    armed = fired = 0
    for i in range(1, len(keys)):
        dt = keys[i]
        hold_dt = keys[i - 1]
        b = bars[dt]
        asof = dt.timestamp()
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        if pos is not None:
            side, entry, stop_px, tp_px, meta = pos
            done = None
            for ts, px, _d in b["prints"]:
                if ts >= eod_of(dt):
                    pts = (px - entry) if side == "Buy" else (entry - px)
                    done = (pts, "eod", px)
                    break
                hit = crossed(side, px, stop_px, tp_px)
                if hit:
                    done = (hit[0], hit[1], px)
                    break
            if done:
                pts, how, _px = done
                out.append((meta, pts, how))
                pos = None
                skip_hold = dt
            continue
        if (dt - hold_dt).total_seconds() != 60:
            continue
        if skip_hold is not None and hold_dt == skip_hold:
            continue
        if not in_session(dt):
            continue
        hold = bars[hold_dt]
        hit = arm(hold, active)
        if hit is None:
            continue
        atr = None
        if mode != "now":
            atr = atr14(keys, bars, i - 1)
            if atr is None or atr >= ATR_MAX:
                continue
        _dist, side, name, rail = hit
        armed += 1
        if mode in ("close", "live", "now"):
            if side == "Buy":
                ok = b["buy"] > b["sell"] and b["c"] > hold["c"] and b["c"] > rail
            else:
                ok = b["sell"] > b["buy"] and b["c"] < hold["c"] and b["c"] < rail
            if not ok or (mode == "close" and abs(b["c"] - rail) > NEAR):
                continue
            entry = b["c"]
            fill_ts = b["prints"][-1][0] if b["prints"] else asof
        else:
            buy = sell = 0.0
            entry = None
            fill_i = None
            for i, (ts, px, dlt) in enumerate(b["prints"]):
                if dlt > 0:
                    buy += dlt
                elif dlt < 0:
                    sell += -dlt
                if abs(px - rail) > NEAR:
                    continue
                if side == "Buy":
                    ok = buy > sell and px > hold["c"] and px > rail
                else:
                    ok = sell > buy and px < hold["c"] and px < rail
                if ok:
                    entry = px
                    fill_ts = ts
                    fill_i = i
                    break
            if entry is None:
                continue
        fired += 1
        stop_px = entry - STOP if side == "Buy" else entry + STOP
        tp_px = entry + TP if side == "Buy" else entry - TP
        meta = {
            "t": datetime.fromtimestamp(fill_ts, TZ),
            "side": side, "name": name, "rail": rail,
            "entry": entry, "dist": abs(entry - rail), "atr": atr,
        }
        if mode == "sniper":
            done = None
            for ts, px, _d in b["prints"][fill_i + 1:]:
                if ts >= eod_of(dt):
                    pts = (px - entry) if side == "Buy" else (entry - px)
                    done = (pts, "eod")
                    break
                hit_x = crossed(side, px, stop_px, tp_px)
                if hit_x:
                    done = hit_x
                    break
            if done:
                out.append((meta, done[0], done[1]))
                skip_hold = dt
                continue
        pos = (side, entry, stop_px, tp_px, meta)
    if pos is not None:
        side, entry, _s, _t, meta = pos
        last = bars[keys[-1]]["c"]
        pts = (last - entry) if side == "Buy" else (entry - last)
        out.append((meta, pts, "eod"))
    return out, armed, fired


def show(title, rows):
    wins = [p for _, p, _ in rows if p > 0]
    losses = [p for _, p, _ in rows if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    net = gp - gl
    n = len(rows)
    wr = (len(wins) / n) if n else 0
    pf = (gp / gl) if gl else 0
    aw = (gp / len(wins)) if wins else 0
    al = (gl / len(losses)) if losses else 0
    rr = (aw / al) if al else 0
    print(title, flush=True)
    print(
        f"TRADES {n}  W {len(wins)} L {len(losses)}  "
        f"WR {wr:.1%}  PF {pf:.2f}  RR {rr:.2f}  "
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
    print("WINDOW 10:00-16:00 CT  close any distance  stop 20  target 40  no atr", flush=True)
    bars = pull(key)
    rows, armed, fired = score(alerts, bars, "now")
    print(f"ARMED {armed}  FIRED {fired}", flush=True)
    show("NOW  the process that is running. Through the last print.", rows)


if __name__ == "__main__":
    main()
