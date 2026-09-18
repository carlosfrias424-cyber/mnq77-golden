#!/usr/bin/env python3
"""Replay today with CORRECT 7/7. Tails last 80MB of decision.jsonl so it does not hang."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict, Counter

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
WATCH, ARM, SL, TP, QTY = 10.0, 6.0, 20.0, 40.0, 5
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
TAIL = 80_000_000


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


def load_tail(name, t0, t1):
    path = ROOT / name
    out = []
    if not path.exists():
        print("MISSING", name, flush=True)
        return out
    size = path.stat().st_size
    print("read", name, "bytes", size, flush=True)
    with path.open("rb") as f:
        if size > TAIL:
            f.seek(size - TAIL)
            f.readline()
        raw = f.read().decode("utf-8", "replace")
    n = 0
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if not dt or dt < t0 or dt > t1:
            continue
        out.append((dt, o))
        n += 1
    print("  kept", n, flush=True)
    return out


def kind_of(o):
    u = str(o.get("poi_name") or o.get("type") or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def bounce_of(kind, first_over):
    if kind in SUPPORT:
        return True
    if kind in RESIST:
        return False
    return first_over


def dist_bar(px, lo, hi):
    if lo <= px <= hi:
        return 0.0
    if px > hi:
        return px - hi
    return lo - px


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


def main():
    day = datetime.now(CDT).date()
    t0 = datetime(day.year, day.month, day.day, 4, 0, tzinfo=CDT)
    t1 = datetime.now(CDT)
    print("window", t0.strftime("%H:%M"), "->", t1.strftime("%H:%M %Z"), flush=True)

    dec = load_tail("decision.jsonl", t0, t1)
    ticks = []
    for dt, o in dec:
        mid = o.get("mid")
        if mid is None:
            continue
        try:
            mid = float(mid)
        except Exception:
            continue
        d5 = o.get("delta_5s")
        try:
            d5 = float(d5) if d5 is not None else None
        except Exception:
            d5 = None
        ticks.append((dt, mid, d5, bool(o.get("d_long")), bool(o.get("d_short"))))
    ticks.sort()
    print("ticks", len(ticks), flush=True)
    if len(ticks) < 30:
        print("not enough ticks in tail — need a bigger TAIL", flush=True)
        return

    pois = []
    for dt, o in load_tail("tv_poi.jsonl", t0 - timedelta(hours=6), t1):
        k = kind_of(o)
        if k is None:
            continue
        try:
            px = round(float(o.get("price") or 0), 2)
        except Exception:
            continue
        if px <= 0:
            continue
        pois.append((dt, k, px))
    pois.sort()
    print("poi", len(pois), flush=True)

    bars = defaultdict(lambda: dict(o=None, h=None, l=None, c=None, d5=None, dl=0, ds=0))
    for dt, mid, d5, dl, ds in ticks:
        t0b = dt.replace(second=0, microsecond=0)
        b = bars[t0b]
        if b["o"] is None:
            b["o"] = mid
        b["h"] = mid if b["h"] is None else max(b["h"], mid)
        b["l"] = mid if b["l"] is None else min(b["l"], mid)
        b["c"] = mid
        if d5 is not None:
            b["d5"] = d5
        b["dl"] += int(dl)
        b["ds"] += int(ds)
    times = sorted(bars)
    print("1m bars", len(times), flush=True)

    def last_poi(dt):
        best = None
        for p in pois:
            if p[0] <= dt:
                best = p
            else:
                break
        return best

    side_lock = None
    spent = {}
    in_until = None
    trades = []
    skips = []

    for t0b in times:
        b = bars[t0b]
        closed_at = t0b + timedelta(minutes=1)
        if in_until and closed_at < in_until:
            continue
        if in_until and closed_at >= in_until:
            in_until = None
        lp = last_poi(closed_at)
        if lp is None:
            continue
        _, kind, px = lp
        key = "%s@%.2f" % (kind, px)
        lo, hi, c, o = b["l"], b["h"], b["c"], b["o"]
        for k, spx in list(spent.items()):
            if abs(c - spx) >= 20:
                spent.pop(k, None)
        if dist_bar(px, lo, hi) > WATCH:
            if side_lock and side_lock[0] == key:
                side_lock = None
            continue
        loc = True if c > px else (False if c < px else (True if o > px else (False if o < px else None)))
        if loc is None:
            continue
        if side_lock is None or side_lock[0] != key:
            side_lock = (key, bounce_of(kind, loc))
        bounce = side_lock[1]
        if loc != bounce:
            skips.append((closed_at, key, "through_close", c))
            continue
        tagged = (abs(lo - px) <= WATCH) if bounce else (abs(hi - px) <= WATCH)
        if not tagged:
            continue
        hold = (c >= px) if bounce else (c <= px)
        shelf = abs(c - px) <= ARM
        d5 = b["d5"]
        if bounce:
            lean = (d5 is not None and d5 > 0) or (b["dl"] > b["ds"])
        else:
            lean = (d5 is not None and d5 < 0) or (b["ds"] > b["dl"])
        if not hold:
            skips.append((closed_at, key, "body_gave_rail", c))
            continue
        if not shelf:
            skips.append((closed_at, key, "off_shelf", c))
            continue
        if not lean:
            skips.append((closed_at, key, "tape_against", c))
            continue
        if key in spent:
            skips.append((closed_at, key, "rail_spent", c))
            continue
        side = "Buy" if bounce else "Sell"
        after = [(dt, mid) for dt, mid, *_ in ticks if dt >= closed_at]
        hit, hit_t, mae, mfe = path_hit(side, float(c), after)
        pts = TP if hit == "TP40" else (-SL if hit == "SL20" else 0.0)
        trades.append(dict(t=closed_at, side=side, entry=float(c), poi=key,
                           hit=hit, hit_t=hit_t, mae=mae, mfe=mfe, pts=pts, d5=d5))
        spent[key] = px
        in_until = hit_t

    print("\n=== CORRECT 7/7 replay ===", flush=True)
    pnl = 0.0
    for tr in trades:
        dol = tr["pts"] * 2 * QTY
        pnl += dol
        ht = tr["hit_t"].strftime("%H:%M:%S") if tr["hit_t"] else "open"
        print("%s %s %.2f %s hit=%s %s MAE=%.1f MFE=%.1f pts=%+.0f $%+.0f d5=%s" % (
            tr["t"].strftime("%H:%M"), tr["side"], tr["entry"], tr["poi"],
            tr["hit"], ht, tr["mae"], tr["mfe"], tr["pts"], dol, tr["d5"]), flush=True)
    print("trades", len(trades), "$pnl", round(pnl), "(5 MNQ x $2/pt)", flush=True)
    wr = sum(1 for t in trades if t["hit"] == "TP40")
    ls = sum(1 for t in trades if t["hit"] == "SL20")
    print("TP", wr, "SL", ls, "open", len(trades) - wr - ls, flush=True)
    print("\n=== skip counts ===", dict(Counter(w for _, _, w, _ in skips)), flush=True)
    shown = Counter()
    for t, k, w, px in skips:
        if shown[w] >= 8:
            continue
        shown[w] += 1
        print("  %s %-16s %s c=%.2f" % (t.strftime("%H:%M"), w, k, px), flush=True)

    print("\n=== box actually did ===", flush=True)
    for dt, o in load_tail("seven.jsonl", t0, t1):
        ev = o.get("event")
        if ev in ("paper_fire", "struct40_submit", "struct40_fail") or o.get("submit"):
            print("%s %s skip=%s poi=%s mid=%s hold=%s shelf=%s lean=%s" % (
                dt.strftime("%H:%M:%S"), ev, o.get("skip"), o.get("poi"),
                o.get("mid"), o.get("hold"), o.get("on_shelf"), o.get("tape_lean")), flush=True)


if __name__ == "__main__":
    main()
