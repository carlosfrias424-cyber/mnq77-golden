#!/usr/bin/env python3
"""Two books. Same tape. Every alert. No orders.

Book is 20 stop, 40 target, 10:00-16:00 Chicago, one position.
ATR is off on both. Delta size is not gated. No number was set.
A rail is the latest alert of that name. Every minute is checked.

CURRENT: side comes from the touch close. Bare H4 and H1 are allowed.
The fill may be farther than 15. A loss does not kill the rail.

PROPOSED: support is only bought. Resistance is only sold.
Bare H4 and H1 are skipped. The touch must trade the rail and close
back on the defended side, within 15. The next minute must lift away
and that close must still be within 15. After a stop, that rail is
dead for the day.
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db
import json

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
STOP, TP, NEAR = 20.0, 40.0, 15.0
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
TAPE_START = datetime(2026, 9, 14, 9, 0, tzinfo=TZ)


def envload():
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
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
    rows = []
    raw = Counter()
    for ln in POI.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        name = o.get("poi_name") or o.get("type") or ""
        kind = sr_kind(name)
        raw[(name or "").upper().strip() or "?"] += 1
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
        rows.append((recv, kind, px))
    rows.sort()
    return rows, raw


def parse_db_time(raw):
    if not isinstance(raw, str):
        return raw.astimezone(TZ)
    s = raw.strip().replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        cut = len(rest)
        for i, ch in enumerate(rest):
            if ch in "+-":
                cut = i
                break
        frac, tz = rest[:cut], rest[cut:]
        s = head + "." + (frac + "000000")[:6] + tz
    return datetime.fromisoformat(s).astimezone(TZ)


def tape_end(key):
    want = datetime.now(TZ) - timedelta(minutes=5)
    try:
        meta = db.Historical(key).metadata.get_dataset_range(dataset="GLBX.MDP3")
        raw = meta["end"] if isinstance(meta, dict) else meta.end
        avail = parse_db_time(raw) - timedelta(minutes=1)
        return min(want, avail)
    except Exception as e:
        print("RANGE", str(e)[:160], flush=True)
        return want - timedelta(minutes=25)


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
    print("PULL", start.isoformat(), end.isoformat(), flush=True)
    data = db.Historical(key).timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="trades",
        start=start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        end=end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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


def traded(bar, rail):
    return bar["l"] <= rail <= bar["h"]


def manage(pos, bar, dt):
    side, entry, meta = pos
    opened = meta["when"]
    after = dt.date() != opened.date() or dt.hour > 15 or (dt.hour == 15 and dt.minute >= 59)
    if side == "Buy":
        hit_stop = bar["l"] <= entry - STOP
        hit_tp = bar["h"] >= entry + TP
        last = bar["c"] - entry
    else:
        hit_stop = bar["h"] >= entry + STOP
        hit_tp = bar["l"] <= entry - TP
        last = entry - bar["c"]
    # A bar that opens at 16:00 or the next day is the flatten. Do not use its wick.
    if dt.date() != opened.date() or dt.hour >= 16:
        return (last, "eod"), True
    if hit_stop or hit_tp:
        # Same bar through both levels counts as the stop.
        return ((-STOP, "stop") if hit_stop else (TP, "tp")), True
    if after:
        return (last, "eod"), True
    return None, False


def consider(mode, name, rail, hold, lift):
    if not traded(hold, rail):
        return None, "no_touch"
    hc, lc = hold["c"], lift["c"]
    if mode == "proposed":
        if name in SUPPORT:
            side = "Buy"
        elif name in RESIST:
            side = "Sell"
        else:
            return None, "bare"
        if side == "Buy":
            if hc <= rail:
                return None, "closed_through"
            if hc > rail + NEAR:
                return None, "close_far"
            if not (hold["sell"] > hold["buy"]):
                return None, "hold_delta"
            if not (lift["buy"] > lift["sell"] and lc > hc and lc > rail):
                return None, "no_lift"
            if lc > rail + NEAR:
                return None, "fill_far"
        else:
            if hc >= rail:
                return None, "closed_through"
            if hc < rail - NEAR:
                return None, "close_far"
            if not (hold["buy"] > hold["sell"]):
                return None, "hold_delta"
            if not (lift["sell"] > lift["buy"] and lc < hc and lc < rail):
                return None, "no_lift"
            if lc < rail - NEAR:
                return None, "fill_far"
    else:
        if abs(hc - rail) > NEAR:
            return None, "hold_far"
        if hc > rail and (name in SUPPORT or name in BARE or name in RESIST):
            side = "Buy"
        elif hc < rail and (name in RESIST or name in BARE or name in SUPPORT):
            side = "Sell"
        else:
            return None, "no_side"
        if side == "Buy":
            if not (hold["sell"] > hold["buy"]):
                return None, "hold_delta"
            if not (lift["buy"] > lift["sell"] and lc > hc and lc > rail):
                return None, "no_lift"
        else:
            if not (hold["buy"] > hold["sell"]):
                return None, "hold_delta"
            if not (lift["sell"] > lift["buy"] and lc < hc and lc < rail):
                return None, "no_lift"
    return (side, abs(lc - rail)), None


def run(mode, alerts, bars):
    keys = sorted(bars)
    ai = 0
    active = {}
    pos = None
    dead = set()
    out = []
    why = Counter()
    for i in range(1, len(keys)):
        dt = keys[i]
        hold_dt = keys[i - 1]
        if dt.weekday() >= 5:
            continue
        b = bars[dt]
        if pos:
            done, flat = manage(pos, b, dt)
            if flat:
                pts, how = done
                meta = pos[2]
                out.append((dt, meta, pts, how))
                if mode == "proposed" and how == "stop":
                    dead.add((dt.date(), meta["name"], meta["rail"]))
                pos = None
            else:
                why["in_position"] += 1
            continue
        if (dt - hold_dt).total_seconds() != 60:
            continue
        if not (10 <= dt.hour < 16) or (dt.hour == 15 and dt.minute >= 59):
            continue
        asof = dt.timestamp() + 60
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        hold = bars[hold_dt]
        best = None
        for name, rail in active.items():
            if mode == "proposed" and (dt.date(), name, rail) in dead:
                why["rail_dead"] += 1
                continue
            hit, reason = consider(mode, name, rail, hold, b)
            if hit is None:
                if reason != "no_touch":
                    why[reason] += 1
                continue
            side, dist = hit
            if best is None or dist < best[0]:
                best = (dist, side, name, rail, hold, b)
        if best is None:
            continue
        dist, side, name, rail, hold, lift = best
        pos = (side, lift["c"], {
            "name": name, "rail": rail, "side": side, "entry": lift["c"],
            "dist": dist, "hold_d": hold["buy"] - hold["sell"],
            "lift_d": lift["buy"] - lift["sell"], "when": dt,
        })
    if pos:
        dt = keys[-1]
        b = bars[dt]
        side, entry, meta = pos
        pts = (b["c"] - entry) if side == "Buy" else (entry - b["c"])
        out.append((dt, meta, pts, "open"))
    return out, why


def show(title, out, why):
    print(title, flush=True)
    w = l = 0
    net = gross_w = gross_l = 0.0
    weeks = {}
    for dt, meta, pts, how in out:
        if how == "open":
            tag = "open"
        elif pts > 0:
            w += 1
            gross_w += pts
            tag = how
        elif pts < 0:
            l += 1
            gross_l += -pts
            tag = how
        else:
            tag = how
        net += 0 if how == "open" else pts
        if how != "open":
            wk = (dt.date() - timedelta(days=dt.weekday())).isoformat()
            weeks[wk] = weeks.get(wk, 0.0) + pts
        print(
            f"{dt.strftime('%m-%d %H:%M')} {meta['side']} {meta['name']}@{meta['rail']:.2f}"
            f" @{meta['entry']:.2f} {pts:+.1f} {tag} dist {meta['dist']:.2f}"
            f" hold_d {meta['hold_d']:.0f} lift_d {meta['lift_d']:.0f}"
        )
    pf = (gross_w / gross_l) if gross_l else float("inf")
    n = w + l
    wr = (100.0 * w / n) if n else 0.0
    print(f"{title} {n} W {w} L {l} WR {wr:.1f}% PF {pf:.2f} {net:+.1f} pts ${net * 10:+.0f}")
    for wk, pts in weeks.items():
        print(f"  WEEK {wk} {pts:+.1f}")
    print("GATES", dict(why))
    print(flush=True)


def main():
    envload()
    alerts, raw = load_alerts()
    kind_n = Counter(k for _, k, _ in alerts)
    bare = sum(kind_n[k] for k in BARE)
    sided = sum(kind_n[k] for k in list(SUPPORT) + list(RESIST))
    print("ALERTS", len(alerts), "SIDED", sided, "BARE", bare)
    print("KINDS", dict(kind_n))
    print("RAW", raw.most_common(12))
    if alerts:
        a0 = datetime.fromtimestamp(alerts[0][0], TZ)
        a1 = datetime.fromtimestamp(alerts[-1][0], TZ)
        print("ALERT_SPAN", a0.isoformat(), a1.isoformat())
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    end = tape_end(key)
    bars = pull_bars(key, TAPE_START, end)
    show("CURRENT", *run("current", alerts, bars))
    show("PROPOSED", *run("proposed", alerts, bars))


if __name__ == "__main__":
    main()
