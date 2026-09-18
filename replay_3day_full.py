#!/usr/bin/env python3
"""Score Wed/Thu/Fri from ALL TV alerts + cleaned decision mids.
Dual unused. No orders. Does NOT use seven.jsonl (that was the cherry pick).

Each closed 1m in 04:00–11:30 CT:
  last alerts received by that minute
  wick within 10 of an official rail → candidate
  closest tagged rail wins that bar
  hold + close within 15 + 5m delta lean
  one position; spent until 20 pts away
Ticks: drop any mid that jumps >40 pts in <3s (ghost prints).
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
SL, TP, QTY, ARM, WATCH = 20.0, 40.0, 5, 15.0, 10.0
DAYS = [
    datetime(2026, 9, 16, tzinfo=CDT).date(),
    datetime(2026, 9, 17, tzinfo=CDT).date(),
    datetime(2026, 9, 18, tzinfo=CDT).date(),
]
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}


def dt_of(o):
    t = o.get("ts") or o.get("recv_ts")
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(CDT)
        except Exception:
            return None
    try:
        t = int(float(t))
    except Exception:
        return None
    if t < 1e12:
        t *= 1000
    if t < 1e11:
        return None
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).astimezone(CDT)


def kind_of(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def bounce_of(kind):
    if kind in SUPPORT:
        return True
    if kind in RESIST:
        return False
    return None


def load_jsonl(name, t0, t1, tail=None):
    path = ROOT / name
    if not path.exists():
        print("MISSING", name, flush=True)
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if tail and size > tail:
            f.seek(size - tail)
            f.readline()
        raw = f.read().decode("utf-8", "replace")
    out, first, last = [], None, None
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if not dt:
            continue
        if first is None:
            first = dt
        last = dt
        if t0 <= dt <= t1:
            out.append((dt, o))
    print(name, "size", size, "span", first, "->", last, "kept", len(out), flush=True)
    return out


def clean_ticks(rows):
    ticks = []
    ghost = 0
    prev = None
    for dt, o in rows:
        m = o.get("mid")
        if m is None:
            continue
        try:
            m = float(m)
        except Exception:
            continue
        if not (20000 < m < 40000):
            ghost += 1
            continue
        if prev is not None:
            dt0, m0 = prev
            sec = (dt - dt0).total_seconds()
            if sec >= 0 and sec < 3 and abs(m - m0) > 40:
                ghost += 1
                continue
        ticks.append((dt, m))
        prev = (dt, m)
    print("ticks", len(ticks), "ghost_dropped", ghost, flush=True)
    return ticks


def bars_1m(ticks):
    buckets = defaultdict(list)
    for dt, m in ticks:
        t0 = dt.replace(second=0, microsecond=0)
        buckets[t0].append(m)
    bars = []
    for t0 in sorted(buckets):
        xs = buckets[t0]
        bars.append(dict(t0=t0, o=xs[0], h=max(xs), l=min(xs), c=xs[-1]))
    return bars


def tape_5m(ticks):
    """Last mid-change over the closed 5m vs the 5m before. Lean = sign of last 5m move."""
    b5 = defaultdict(list)
    for dt, m in ticks:
        t0 = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
        b5[t0].append((dt, m))
    keys = sorted(b5)
    lean_at = {}
    for i, t0 in enumerate(keys):
        xs = b5[t0]
        if i == 0 or len(xs) < 2:
            continue
        prev = b5[keys[i - 1]]
        move = xs[-1][1] - prev[-1][1]
        lean_at[t0] = move  # >0 long, <0 short
    return lean_at


def rails_asof(alerts, when):
    best = {}
    for dt, name, px, kind in alerts:
        if dt > when:
            break
        best[(kind, px)] = (dt, name, px, kind)
    return list(best.values())


def path_hit(side, entry, after):
    mae = mfe = 0.0
    for dt, mid in after:
        if side == "Buy":
            mae = max(mae, entry - mid)
            mfe = max(mfe, mid - entry)
            if mid <= entry - SL:
                return "SL20", dt, mae, mfe
            if mid >= entry + TP:
                return "TP40", dt, mae, mfe
        else:
            mae = max(mae, mid - entry)
            mfe = max(mfe, entry - mid)
            if mid >= entry + SL:
                return "SL20", dt, mae, mfe
            if mid <= entry - TP:
                return "TP40", dt, mae, mfe
    return "OPEN", None, mae, mfe


def pick_rail(bar, rails):
    tagged = []
    for dt, name, px, kind in rails:
        dhi = abs(bar["h"] - px)
        dlo = abs(bar["l"] - px)
        inside = bar["l"] <= px <= bar["h"]
        dist = 0.0 if inside else min(dhi, dlo)
        if dist > WATCH:
            continue
        tagged.append((dist, abs(bar["c"] - px), dt, name, px, kind))
    if not tagged:
        return None
    tagged.sort(key=lambda x: (x[0], x[1], -x[2].timestamp()))
    return tagged[0]


def run_day(day, alerts, ticks, lean5):
    t0 = datetime(day.year, day.month, day.day, 4, 0, tzinfo=CDT)
    t1 = datetime(day.year, day.month, day.day, 11, 30, tzinfo=CDT)
    day_ticks = [(dt, m) for dt, m in ticks if t0 <= dt <= t1]
    bars = [b for b in bars_1m(day_ticks) if t0 <= b["t0"] < t1]
    day_alerts = [(dt, n, px, k) for dt, n, px, k in alerts if dt.date() <= day]
    day_alerts.sort()

    spent = {}
    in_until = None
    lock_side = {}
    trades, skips = [], defaultdict(int)
    for bar in bars:
        close_t = bar["t0"] + timedelta(minutes=1)
        if in_until and close_t < in_until:
            skips["in_trade"] += 1
            continue
        if in_until and close_t >= in_until:
            in_until = None
        c = bar["c"]
        for k, px in list(spent.items()):
            if abs(c - px) >= 20:
                spent.pop(k, None)

        rails = rails_asof(day_alerts, close_t)
        hit = pick_rail(bar, rails)
        if hit is None:
            skips["no_tag"] += 1
            continue
        _, _, adt, name, px, kind = hit
        key = f"{kind}@{px:.2f}"
        bounce = bounce_of(kind)
        if bounce is None:
            bounce = lock_side.get(key)
            if bounce is None:
                if c > px:
                    bounce = True
                elif c < px:
                    bounce = False
                else:
                    skips["at_rail"] += 1
                    continue
                lock_side[key] = bounce
        loc = c > px if c != px else None
        if loc is None:
            skips["at_rail"] += 1
            continue
        if loc != bounce:
            skips["through_close"] += 1
            continue
        hold = (c >= px) if bounce else (c <= px)
        if not hold:
            skips["body_gave"] += 1
            continue
        if abs(c - px) > ARM:
            skips["off_shelf"] += 1
            continue
        t5 = bar["t0"].replace(minute=(bar["t0"].minute // 5) * 5, second=0, microsecond=0)
        move = lean5.get(t5)
        if move is None:
            skips["tape_unknown"] += 1
            continue
        lean = (move > 0) if bounce else (move < 0)
        if not lean:
            skips["tape_against"] += 1
            continue
        if key in spent:
            skips["rail_spent"] += 1
            continue
        side = "Buy" if bounce else "Sell"
        after = [(dt, m) for dt, m in day_ticks if dt >= close_t]
        hitp, ht, mae, mfe = path_hit(side, c, after)
        pts = TP if hitp == "TP40" else (-SL if hitp == "SL20" else 0.0)
        trades.append(dict(t=close_t, side=side, entry=c, poi=key, hit=hitp,
                           hit_t=ht, mae=mae, mfe=mfe, pts=pts))
        spent[key] = px
        in_until = ht
    return bars, trades, dict(skips)


def main():
    t0 = datetime(2026, 9, 16, 0, 0, tzinfo=CDT)
    t1 = datetime(2026, 9, 18, 13, 0, tzinfo=CDT)
    print("FULL 3-day from alerts+mids  (not seven.jsonl)", flush=True)
    print("ARM=15  Dual unused  no orders", flush=True)
    poi = load_jsonl("tv_poi.jsonl", t0, t1, tail=None)
    dec = load_jsonl("decision.jsonl", t0, t1, tail=400_000_000)
    alerts = []
    for dt, o in poi:
        kind = kind_of(o.get("poi_name") or o.get("type") or "")
        if kind is None:
            continue
        try:
            px = round(float(o.get("price") or 0), 2)
        except Exception:
            continue
        if px <= 0:
            continue
        alerts.append((dt, kind, px, kind))
    alerts.sort()
    print("official alerts", len(alerts), "unique", len({(k, p) for _, _, p, k in alerts}), flush=True)
    ticks = clean_ticks(dec)
    lean5 = tape_5m(ticks)

    tot = dict(n=0, tp=0, sl=0, pnl=0.0)
    for day in DAYS:
        bars, trades, skips = run_day(day, alerts, ticks, lean5)
        print("\n========", day.strftime("%a %Y-%m-%d"), "========", flush=True)
        print("1m bars", len(bars), "alerts_used_upto_day", sum(1 for d, *_ in alerts if d.date() <= day), flush=True)
        pnl = 0.0
        for tr in trades:
            dol = tr["pts"] * 2 * QTY
            pnl += dol
            ht = tr["hit_t"].strftime("%H:%M:%S") if tr["hit_t"] else "open"
            print("%s %s %.2f %s %s %s MAE=%.1f MFE=%.1f %+.0fpt $%+.0f" % (
                tr["t"].strftime("%H:%M:%S"), tr["side"], tr["entry"], tr["poi"],
                tr["hit"], ht, tr["mae"], tr["mfe"], tr["pts"], dol), flush=True)
        print("n=%d TP=%d SL=%d OPEN=%d $%+.0f" % (
            len(trades),
            sum(1 for t in trades if t["hit"] == "TP40"),
            sum(1 for t in trades if t["hit"] == "SL20"),
            sum(1 for t in trades if t["hit"] == "OPEN"),
            pnl), flush=True)
        print("skips", skips, flush=True)
        tot["n"] += len(trades)
        tot["tp"] += sum(1 for t in trades if t["hit"] == "TP40")
        tot["sl"] += sum(1 for t in trades if t["hit"] == "SL20")
        tot["pnl"] += pnl
    print("\n======== 3-DAY TOTAL (all rails, cleaned mids) ========", flush=True)
    print("n=%(n)d TP=%(tp)d SL=%(sl)d $%(pnl)+.0f  (5 MNQ x $2)" % tot, flush=True)


if __name__ == "__main__":
    main()
