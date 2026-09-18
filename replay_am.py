#!/usr/bin/env python3
"""Replay today's session with CORRECT 7/7 (last webhook, shelf 6, no sit-mute).
Uses decision.jsonl ticks + tv_poi.jsonl last webhook. Dual not used.
Book: 5 MNQ, SL 20, TP 40, BE off, one position.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
WATCH, ARM, SL, TP, QTY = 10.0, 6.0, 20.0, 40.0, 5
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
$

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


def load(p):
    out = []
    path = ROOT / p
    if not path.exists():
        print("MISSING", p)
        return out
    for ln in path.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if dt:
            out.append((dt, o))
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


def main():
    day = datetime.now(CDT).date()
    t0 = datetime(day.year, day.month, day.day, 4, 0, tzinfo=CDT)
    t1 = datetime(day.year, day.month, day.day, 11, 30, tzinfo=CDT)
    print("window", t0, "→", t1)

    ticks = []
    for dt, o in load("decision.jsonl"):
        if dt < t0 or dt > t1:
            continue
        if o.get("event") not in (None, "decision", "tick") and o.get("mid") is None:
            continue
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
    print("ticks", len(ticks))
    if len(ticks) < 50:
        print("not enough decision ticks — abort")
        return

    pois = []
    for dt, o in load("tv_poi.jsonl"):
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
    print("poi alerts", len(pois))

    bars = defaultdict(lambda: dict(o=None, h=None, l=None, c=None, d5=None, dl=0, ds=0, n=0))
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
        b["n"] += 1
    times = sorted(bars)
    print("1m bars", len(times))

    def last_poi(dt):
        best = None
        for p in pois:
            if p[0] <= dt:
                best = p
            else:
                break
        return best

    side_lock = None  # (rail_key, bounce)
    spent = {}
    pos = None  # dict
    trades = []
    skips = []

    def mfe_mae(side, entry, after):
        mae = mfe = 0.0
        hit = "OPEN"
        hit_t = None
        for dt, mid, *_ in after:
            if side == "Buy":
                mae = max(mae, entry - mid)
                mfe = max(mfe, mid - entry)
                if mid <= entry - SL:
                    hit, hit_t = "SL20", dt
                    break
                if mid >= entry + TP:
                    hit, hit_t = "TP40", dt
                    break
            else:
                mae = max(mae, mid - entry)
                mfe = max(mfe, entry - mid)
                if mid >= entry + SL:
                    hit, hit_t = "SL20", dt
                    break
                if mid <= entry - TP:
                    hit, hit_t = "TP40", dt
                    break
        return hit, hit_t, mae, mfe

    for i, t0b in enumerate(times[:-1]):
        b = bars[t0b]
        closed_at = t0b + timedelta(minutes=1)
        lp = last_poi(closed_at)
        if lp is None:
            continue
        _, kind, px = lp
        key = f"{kind}@{px:.2f}"

        # expire spent
        dead = [k for k, spx in spent.items() if abs(b["c"] - spx) >= 20]
        for k in dead:
            spent.pop(k, None)

        if pos is not None:
            continue  # one position; resolved below via path

        lo, hi, c, o = b["l"], b["h"], b["c"], b["o"]
        if min(abs(lo - px), abs(hi - px), 0 if lo <= px <= hi else 99) > WATCH and not (lo <= px <= hi):
            if side_lock and side_lock[0] == key:
                pass
            # leave watch → unlock that rail
            dist = 0.0 if lo <= px <= hi else (px - hi if px > hi else lo - px)
            if dist > WATCH:
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
        hit = (abs(lo - px) <= WATCH) if bounce else (abs(hi - px) <= WATCH)
        if not hit:
            continue
        hold = (c >= px) if bounce else (c <= px)
        shelf = abs(c - px) <= ARM
        d5 = b["d5"]
        if bounce:
            lean = (d5 is not None and d5 > 0) or (b["dl"] > b["ds"])
        else:
            lean = (d5 is not None and d5 < 0) or (b["ds"] > b["dl"])
        why = None
        if not hold:
            why = "body_gave_rail"
        elif not shelf:
            why = "off_shelf"
        elif not lean:
            why = "tape_against"
        if why:
            skips.append((closed_at, key, why, c))
            continue
        if key in spent:
            skips.append((closed_at, key, "rail_spent", c))
            continue

        side = "Buy" if bounce else "Sell"
        entry = c
        after = [(dt, mid, d5, dl, ds) for dt, mid, d5, dl, ds in ticks if dt >= closed_at]
        hit, hit_t, mae, mfe = mfe_mae(side, entry, after)
        pts = TP if hit == "TP40" else (-SL if hit == "SL20" else 0.0)
        trades.append(dict(
            t=closed_at, side=side, entry=entry, poi=key, hit=hit,
            hit_t=hit_t, mae=mae, mfe=mfe, pts=pts,
            hold=hold, shelf=shelf, lean=lean, c=c, h=hi, l=lo, d5=d5,
        ))
        spent[key] = px
        pos = trades[-1]
        # flatten pos when hit so next can fire
        if hit in ("TP40", "SL20"):
            pos = None

    print("\n=== CORRECT 7/7 fires (replay) ===")
    pnl = 0.0
    for tr in trades:
        dol = tr["pts"] * 2 * QTY
        pnl += dol
        ht = tr["hit_t"].strftime("%H:%M:%S") if tr["hit_t"] else ""
        print(
            f"{tr['t']:%H:%M} {tr['side']:4} {tr['entry']:.2f} {tr['poi']} "
            f"hit={tr['hit']:5} {ht} MAE={tr['mae']:.1f} MFE={tr['mfe']:.1f} "
            f"pts={tr['pts']:+.0f} $"{dol:+.0f} d5={tr['d5']}"
        )
    print(f"trades {len(trades)}  $ pnl {pnl:+.0f}  (5 MNQ, $2/pt)")
    wr = sum(1 for t in trades if t["hit"] == "TP40")
    ls = sum(1 for t in trades if t["hit"] == "SL20")
    print(f"TP {wr}  SL {ls}  open {len(trades)-wr-ls}")

    print("\n=== skips (first 20 of each why) ===")
    from collections import Counter
    c = Counter(w for _, _, w, _ in skips)
    print(dict(c))
    shown = Counter()
    for t, k, w, px in skips:
        if shown[w] >= 8:
            continue
        shown[w] += 1
        print(f"  {t:%H:%M} {w:16} {k} c={px:.2f}")

    print("\n=== actual box fires today ===")
    for dt, o in load("seven.jsonl"):
        if dt < t0 or dt > t1:
            continue
        ev = o.get("event")
        if ev in ("paper_fire", "struct40_submit", "struct40_fail") or o.get("submit"):
            print(
                f"{dt:%H:%M:%S} {ev} skip={o.get('skip')} side={o.get('side')} "
                f"poi={o.get('poi')} mid={o.get('mid')} hold={o.get('hold')} "
                f"shelf={o.get('on_shelf')} lean={o.get('tape_lean')}"
            )


if __name__ == "__main__":
    main()
