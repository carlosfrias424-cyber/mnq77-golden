#!/usr/bin/env python3
"""Paper only. No orders.

Thursday morning, 04:00 CT through the last tape on hand.
Same fade as the live bot. Trades before 10:00 are tagged pre.
The live book is the 10:00 cut on its own, so a pre trade cannot
block a cash-session trade.
"""
from __future__ import annotations

import os
import re
from collections import Counter
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
DAY = "2026-10-01"


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
    start = "2026-10-01T09:00:00Z"
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


def closes_in_session(bar_start):
    close = bar_start + timedelta(minutes=1)
    m = close.hour * 60 + close.minute
    return 10 * 60 <= m < 16 * 60


def manage(pos, b):
    side, entry = pos[0], pos[1]
    if side == "Buy":
        if b["l"] <= entry - STOP:
            return -STOP, "stop"
        if b["h"] >= entry + TP:
            return TP, "tp"
    else:
        if b["h"] >= entry + STOP:
            return -STOP, "stop"
        if b["l"] <= entry - TP:
            return TP, "tp"
    return None


def eye_outcome(keys, bars, i, side, entry):
    if side == "Buy":
        stop, tp = entry - STOP, entry + TP
    else:
        stop, tp = entry + STOP, entry - TP
    last = entry
    for j in range(i, len(keys)):
        b = bars[keys[j]]
        last = b["c"]
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
    book = {"off": None, "live": None}
    rows = {"off": [], "live": []}
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
        for slot in ("off", "live"):
            pos = book[slot]
            if not pos:
                continue
            hit = manage(pos, b)
            if hit:
                rows[slot][-1].extend(hit)
                book[slot] = None
        if hold is None:
            continue
        best = None
        for name, rail in active.items():
            if not (hold["l"] <= rail <= hold["h"]):
                continue
            dist = abs(hold["c"] - rail)
            if dist > NEAR:
                continue
            d = delta(hold)
            if hold["c"] > rail and d < 0:
                if not (delta(b) > 0 and b["c"] > hold["c"] and b["c"] > rail):
                    continue
                side = "Buy"
            elif hold["c"] < rail and d > 0:
                if not (delta(b) < 0 and b["c"] < hold["c"] and b["c"] < rail):
                    continue
                side = "Sell"
            else:
                continue
            if best is None or dist < best[0]:
                best = (dist, side, name, rail, d, delta(b))
        if best:
            dist, side, name, rail, hd, ld = best
            rec = [dt, side, name, rail, b["c"], dist, hd, ld]
            if book["off"] is None:
                book["off"] = (side, b["c"], name, rail)
                rows["off"].append(rec)
            if book["live"] is None and closes_in_session(dt):
                book["live"] = (side, b["c"], name, rail)
                rows["live"].append(list(rec))
        if i < 2 or (prev_dt - keys[i - 2]).total_seconds() != 60:
            continue
        prior = bars[keys[i - 2]]
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
            if not closes_in_session(dt):
                gates.append("before_10")
            if dist > NEAR:
                gates.append("dist")
            if side == "Buy" and not d < 0:
                gates.append("hold_delta")
            if side == "Sell" and not d > 0:
                gates.append("hold_delta")
            if side == "Buy":
                ok = delta(b) > 0 and b["c"] > hold["c"] and b["c"] > rail
            else:
                ok = delta(b) < 0 and b["c"] < hold["c"] and b["c"] < rail
            if not ok:
                gates.append("no_lift")
            if not gates:
                continue
            pts, how = eye_outcome(keys, bars, i, side, hold["c"])
            if how not in ("tp", "open"):
                continue
            missed.append((prev_dt, side, name, rail, hold["c"], dist, gates, d, delta(b), pts, how))
    for slot in ("off", "live"):
        pos = book[slot]
        if not pos or not keys:
            continue
        last = bars[keys[-1]]
        side, entry = pos[0], pos[1]
        pts = (last["c"] - entry) if side == "Buy" else (entry - last["c"])
        rows[slot][-1].extend((pts, "open"))
    return rows, missed, active


def show_book(title, rows):
    done = [r for r in rows if r[-1] != "open"]
    opens = [r for r in rows if r[-1] == "open"]
    wins = [r[-2] for r in done if r[-2] > 0]
    losses = [r[-2] for r in done if r[-2] < 0]
    net = sum(r[-2] for r in done)
    print(
        f"{title} {len(done)}  W {len(wins)} L {len(losses)}  {net:+.1f} pts  open {len(opens)}",
        flush=True,
    )
    for r in rows:
        dt, side, name, rail, entry, dist, hd, ld, pts, how = r
        when = "pre" if (dt + timedelta(minutes=1)).hour < 10 else "rth"
        print(
            f"{title} {dt:%H:%M} {when} {side:4} {name}@{rail:.2f} @{entry:.2f} {pts:+.1f} {how} dist {dist:.2f} hold_d {hd:.0f} lift_d {ld:.0f}",
            flush=True,
        )


def show_alerts(alerts):
    start = datetime(2026, 10, 1, 4, 0, tzinfo=TZ).timestamp()
    n = 0
    for recv, kind, px in alerts:
        if recv < start:
            continue
        n += 1
        dt = datetime.fromtimestamp(recv, TZ)
        print(f"ALERT {dt:%H:%M:%S} {kind} {px:.2f}", flush=True)
    print(f"ALERTS_TODAY {n}", flush=True)


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("MORNING", DAY, "04:00 CT through now", flush=True)
    print("RULE touch, hold close within 15, opposing delta, next bar lifts. Stop 20 target 40.", flush=True)
    show_alerts(alerts)
    bars = pull_bars(key)
    rows, missed, _active = score(alerts, bars)
    print("CLOCK_OFF 04:00-now, one trade at a time", flush=True)
    show_book("OFF", rows["off"])
    print("LIVE 10:00 on, what the bot is allowed to take", flush=True)
    show_book("LIVE", rows["live"])
    c = Counter("+".join(r[6]) for r in missed)
    print(f"EYE_PAID {len(missed)}  chart bounce to +40 the rule did not take", flush=True)
    for k, n in c.most_common():
        print(f"GATE {k}  n {n}", flush=True)
    for dt, side, name, rail, entry, dist, gates, hd, ld, pts, how in missed:
        print(
            f"EYE {dt:%H:%M} {side:4} {name}@{rail:.2f} @{entry:.2f} {pts:+.1f} {how} dist {dist:.2f} gate {'+'.join(gates)} hold_d {hd:.0f} lift_d {ld:.0f}",
            flush=True,
        )


if __name__ == "__main__":
    main()
