#!/usr/bin/env python3
"""Paper only. No orders.

From Wed 10:00 CT through the last tape Databento has.
An eye tag: prior close on the fade side, this bar trades the rail,
this bar closes back on that side.
The live bot also needs the close within 15, opposing delta on that
bar, a lift on the next bar, the cash session, and no open trade.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
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


def parse_end(raw):
    text = str(raw).strip().replace("Z", "+00:00")
    if "." in text:
        head, tail = text.split(".", 1)
        if "+" in tail:
            text = head + "+" + tail.split("+", 1)[1]
        else:
            text = head + "+00:00"
    avail = datetime.fromisoformat(text)
    if avail.tzinfo is None:
        avail = avail.replace(tzinfo=timezone.utc)
    return avail.astimezone(timezone.utc)


def tape_end(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        rng = client.metadata.get_dataset_range(dataset="GLBX.MDP3")
        raw = rng["end"] if isinstance(rng, dict) else getattr(rng, "end", None)
        if raw is not None:
            end = min(now, parse_end(raw))
            return end.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as e:
        print("RANGE", type(e).__name__, str(e)[:180], flush=True)
    return (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_trades(client, start, end):
    return client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="trades",
        start=start,
        end=end,
    )


def pull_bars(key):
    client = db.Historical(key)
    start = "2026-09-30T15:00:00Z"
    end = tape_end(client)
    print(f"PULL {start} {end}", flush=True)
    try:
        data = get_trades(client, start, end)
    except Exception as e:
        m = re.search(r"available up to '([^']+)'", str(e))
        if not m:
            raise
        end = (parse_end(m.group(1)) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"RETRY {end}", flush=True)
        data = get_trades(client, start, end)
    bars = {}
    n = 0
    for rec in data:
        n += 1
        if n % 500000 == 0:
            print("TRADES", n, flush=True)
        ts = rec.ts_event / 1e9
        dt = datetime.fromtimestamp(ts, TZ).replace(second=0, microsecond=0)
        if dt.weekday() >= 5:
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


def delta(b):
    return b["buy"] - b["sell"]


def in_session(dt):
    m = dt.hour * 60 + dt.minute
    return 10 * 60 <= m < 16 * 60


def bucket(dt):
    m = dt.hour * 60 + dt.minute
    if dt.hour < 2 or m >= 17 * 60:
        return "asia"
    if in_session(dt):
        return "rth"
    return "off"


def lift_ok(side, hold, lift):
    if lift is None:
        return False
    d = delta(lift)
    if side == "Buy":
        return d > 0 and lift["c"] > hold["c"] and lift["c"] > 0
    return d < 0 and lift["c"] < hold["c"]


def outcome(keys, bars, i, side, entry, kind):
    if side == "Buy":
        stop, tp = entry - STOP, entry + TP
    else:
        stop, tp = entry + STOP, entry - TP
    last = entry
    for j in range(i, len(keys)):
        dt = keys[j]
        b = bars[dt]
        last = b["c"]
        if kind == "asia" and dt.hour == 1 and dt.minute >= 59:
            pts = (last - entry) if side == "Buy" else (entry - last)
            return pts, "asia_end"
        if kind != "asia" and dt.hour == 15 and dt.minute >= 59:
            pts = (last - entry) if side == "Buy" else (entry - last)
            return pts, "eod"
        if side == "Buy":
            if b["l"] <= stop:
                return -STOP, "stop"
            if b["h"] >= tp:
                return TP, "tp"
        else:
            if b["h"] >= stop:
                return -STOP, "stop"
            if b["l"] <= tp:
                return TP, "tp"
    pts = (last - entry) if side == "Buy" else (entry - last)
    return pts, "open"


def score(alerts, bars):
    keys = sorted(bars)
    ai = 0
    active = {}
    pos = None
    bot = []
    missed = []
    seen = set()
    for i in range(1, len(keys)):
        dt = keys[i]
        prev_dt = keys[i - 1]
        gap = (dt - prev_dt).total_seconds() != 60
        hold = None if gap else bars[prev_dt]
        b = bars[dt]
        asof = (prev_dt.timestamp() + 60) if hold else dt.timestamp()
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        if pos:
            side, entry, _name, _rail, _t = pos
            hit = None
            if side == "Buy":
                if b["l"] <= entry - STOP:
                    hit = (-STOP, "stop")
                elif b["h"] >= entry + TP:
                    hit = (TP, "tp")
            else:
                if b["h"] >= entry + STOP:
                    hit = (-STOP, "stop")
                elif b["l"] <= entry - TP:
                    hit = (TP, "tp")
            if hit is None and dt.hour == 15 and dt.minute >= 59:
                pts = (b["c"] - entry) if side == "Buy" else (entry - b["c"])
                hit = (pts, "eod")
            if hit:
                bot[-1] = bot[-1] + (hit[0], hit[1])
                pos = None
        if hold is None:
            continue
        lift = b
        fired = None
        if pos is None and in_session(datetime.fromtimestamp(dt.timestamp() + 60, TZ)):
            best = None
            for name, rail in active.items():
                if not (hold["l"] <= rail <= hold["h"]):
                    continue
                dist = abs(hold["c"] - rail)
                if dist > NEAR:
                    continue
                d = delta(hold)
                if hold["c"] > rail and d < 0:
                    if not (delta(lift) > 0 and lift["c"] > hold["c"] and lift["c"] > rail):
                        continue
                    side = "Buy"
                elif hold["c"] < rail and d > 0:
                    if not (delta(lift) < 0 and lift["c"] < hold["c"] and lift["c"] < rail):
                        continue
                    side = "Sell"
                else:
                    continue
                if best is None or dist < best[0]:
                    best = (dist, side, name, rail)
            if best:
                dist, side, name, rail = best
                pos = (side, lift["c"], name, rail, dt)
                fired = (name, rail)
                bot.append([dt, side, name, rail, lift["c"], dist, delta(hold), delta(lift)])
        if i < 2:
            continue
        prior = bars[keys[i - 2]] if (prev_dt - keys[i - 2]).total_seconds() == 60 else None
        if prior is None:
            continue
        for name, rail in active.items():
            if not (hold["l"] <= rail <= hold["h"]):
                continue
            if prior["l"] <= rail <= prior["h"]:
                continue
            if hold["c"] > rail and prior["c"] > rail:
                side = "Buy"
            elif hold["c"] < rail and prior["c"] < rail:
                side = "Sell"
            else:
                continue
            key = (prev_dt, name, side)
            if key in seen:
                continue
            seen.add(key)
            dist = abs(hold["c"] - rail)
            d = delta(hold)
            gates = []
            if not in_session(datetime.fromtimestamp(dt.timestamp() + 60, TZ)):
                gates.append("session")
            if dist > NEAR:
                gates.append("dist")
            if side == "Buy" and not d < 0:
                gates.append("hold_delta")
            if side == "Sell" and not d > 0:
                gates.append("hold_delta")
            if side == "Buy":
                ok = delta(lift) > 0 and lift["c"] > hold["c"] and lift["c"] > rail
            else:
                ok = delta(lift) < 0 and lift["c"] < hold["c"] and lift["c"] < rail
            if not ok:
                gates.append("no_lift")
            if not gates:
                if fired != (name, rail):
                    gates.append("busy")
                else:
                    continue
            kind = bucket(prev_dt)
            pts, how = outcome(keys, bars, i, side, hold["c"], kind)
            if how not in ("tp", "open"):
                continue
            missed.append((prev_dt, side, name, rail, hold["c"], dist, d, delta(lift), gates, pts, how, hold["buy"], hold["sell"], lift["buy"], lift["sell"]))
    return bot, missed


def show(bot, missed):
    done = [r for r in bot if len(r) > 8]
    open_bot = [r for r in bot if len(r) == 8]
    wins = [r[-2] for r in done if r[-2] > 0]
    losses = [r[-2] for r in done if r[-2] < 0]
    net = sum(wins) - abs(sum(losses))
    print(
        f"BOT {len(done)}  W {len(wins)} L {len(losses)}  {net:+.1f} pts  open {len(open_bot)}",
        flush=True,
    )
    for r in bot:
        if len(r) > 8:
            dt, side, name, rail, entry, dist, hd, ld, pts, how = r
        else:
            dt, side, name, rail, entry, dist, hd, ld = r
            pts, how = 0.0, "open"
        print(
            f"BOT {dt:%m-%d %H:%M} {side:4} {name}@{rail:.2f} @{entry:.2f} {pts:+.1f} {how} dist {dist:.2f} hold_d {hd:.0f} lift_d {ld:.0f}",
            flush=True,
        )
    from collections import Counter
    c = Counter("+".join(g) for *_, g, pts, how, _hb, _hs, _lb, _ls in missed)
    print(f"MISSED_PAID {len(missed)}", flush=True)
    for k, n in c.most_common():
        print(f"GATE {k}  n {n}", flush=True)
    for row in missed:
        dt, side, name, rail, entry, dist, hd, ld, gates, pts, how, hb, hs, lb, ls = row
        print(
            f"MISS {dt:%m-%d %H:%M} {side:4} {name}@{rail:.2f} @{entry:.2f} {pts:+.1f} {how} dist {dist:.2f} gate {'+'.join(gates)} hold_d {hd:.0f} ({hb:.0f}/{hs:.0f}) lift_d {ld:.0f} ({lb:.0f}/{ls:.0f})",
            flush=True,
        )


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("WINDOW wed 10:00 CT through now. Eye tag vs the live four gates.", flush=True)
    bars = pull_bars(key)
    bot, missed = score(alerts, bars)
    show(bot, missed)


if __name__ == "__main__":
    main()
