#!/usr/bin/env python3
"""Today 3-way replay. Dual unused. No orders.

1) shelf6  — 1m CLOSE within 6 + 5m tape (what box should have been)
2) shelf15 — same close-fire, but close within 15 pts (not 6)
3) sniper  — wick tags within 10, close still within 15, 15s-push / 5s-fade

Test 2 is 15 POINTS on the shelf close, not 15 seconds.
Test 3 tape uses decision.jsonl delta_5s as a 15s/5s proxy.
"""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
SL, TP, QTY = 20.0, 40.0, 5
TAIL = 80_000_000
WATCH, NEAR = 10.0, 15.0


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
    if not path.exists():
        print("MISSING", name, flush=True)
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > TAIL:
            f.seek(size - TAIL)
            f.readline()
        raw = f.read().decode("utf-8", "replace")
    out = []
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if dt and t0 <= dt <= t1:
            out.append((dt, o))
    print(name, "kept", len(out), "file", size, flush=True)
    return out


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


def tape_flip(bounce, dt, d5s):
    """15s still push, last 5s fade. Uses delta_5s samples."""
    win15 = [(t, d) for t, d in d5s if dt - timedelta(seconds=15) <= t <= dt]
    win5 = [(t, d) for t, d in d5s if dt - timedelta(seconds=5) <= t <= dt]
    if not win5 or not win15:
        return None, dict(why="no_delta_window", n15=len(win15), n5=len(win5))
    last5 = win5[-1][1]
    # push = the 15s samples before the last 5s
    early = [(t, d) for t, d in win15 if t < dt - timedelta(seconds=5)] or win15[: max(1, len(win15) // 2)]
    push = sum(d for _, d in early) / len(early)
    if bounce:
        ok = last5 > 0 and push <= 0
    else:
        ok = last5 < 0 and push >= 0
    return ok, dict(last5=last5, push=round(push, 1), n15=len(win15), n5=len(win5))


def run_book(name, bars, ticks, arm, sniper, use_flip, d5s):
    spent = {}
    in_until = None
    trades, skips = [], []
    for dt, o in bars:
        c1 = o.get("c1") or {}
        try:
            c = float(c1.get("c")); h = float(c1.get("h")); l = float(c1.get("l"))
            px = float(o.get("px") or str(o.get("poi")).split("@")[-1])
        except Exception:
            continue
        poi = o.get("poi")
        if in_until and dt < in_until:
            skips.append((dt, poi, "in_trade", c))
            continue
        if in_until and dt >= in_until:
            in_until = None
        for k, spx in list(spent.items()):
            if abs(c - spx) >= 20:
                spent.pop(k, None)

        bounce = o.get("side_locked")
        if bounce is None:
            bounce = o.get("sr") == "support" or o.get("tag") == "low"
        side = o.get("side") if o.get("side") in ("Buy", "Sell") else ("Buy" if bounce else "Sell")
        hold = (c >= px) if bounce else (c <= px)
        tagged = (abs(l - px) <= WATCH) if bounce else (abs(h - px) <= WATCH)
        shelf = abs(c - px) <= arm
        lean5 = o.get("tape_lean")

        if sniper:
            if not tagged:
                skips.append((dt, poi, "idle_no_hit", c)); continue
            if not hold:
                skips.append((dt, poi, "gave_rail", c)); continue
            if not shelf:
                skips.append((dt, poi, "chase", c)); continue
            if use_flip:
                ok, met = tape_flip(bounce, dt, d5s)
                if ok is None:
                    skips.append((dt, poi, "tape_unknown", c)); continue
                if not ok:
                    skips.append((dt, poi, "tape_no_flip", c)); continue
            elif lean5 is not True:
                skips.append((dt, poi, "tape_against", c)); continue
        else:
            if o.get("reason") == "through_close" or hold is False:
                skips.append((dt, poi, "through_or_gave", c)); continue
            if not shelf:
                skips.append((dt, poi, "off_shelf", c)); continue
            if lean5 is not True:
                skips.append((dt, poi, "tape_against", c)); continue

        if poi in spent:
            skips.append((dt, poi, "rail_spent", c)); continue

        after = [(x, m) for x, m in ticks if x >= dt]
        hit, hit_t, mae, mfe = path_hit(side, c, after)
        pts = TP if hit == "TP40" else (-SL if hit == "SL20" else 0.0)
        trades.append(dict(t=dt, side=side, entry=c, poi=poi, hit=hit, hit_t=hit_t,
                           mae=mae, mfe=mfe, pts=pts, live_skip=o.get("skip"),
                           live_reason=o.get("reason")))
        spent[poi] = px
        in_until = hit_t
    return trades, skips


def show(title, trades, skips):
    print("\n========", title, "========", flush=True)
    pnl = 0.0
    for tr in trades:
        dol = tr["pts"] * 2 * QTY
        pnl += dol
        ht = tr["hit_t"].strftime("%H:%M:%S") if tr["hit_t"] else "open"
        print("%s %s %.2f %s %s %s MAE=%.1f MFE=%.1f %+.0fpt $%+.0f live=%s/%s" % (
            tr["t"].strftime("%H:%M:%S"), tr["side"], tr["entry"], tr["poi"],
            tr["hit"], ht, tr["mae"], tr["mfe"], tr["pts"], dol,
            tr["live_skip"], tr["live_reason"]), flush=True)
    print("n=%d  TP=%d SL=%d OPEN=%d  $%+.0f  (5 MNQ x $2)" % (
        len(trades),
        sum(1 for t in trades if t["hit"] == "TP40"),
        sum(1 for t in trades if t["hit"] == "SL20"),
        sum(1 for t in trades if t["hit"] == "OPEN"),
        pnl), flush=True)
    print("skips", dict(Counter(w for _, _, w, _ in skips)), flush=True)


def main():
    day = datetime.now(CDT).date()
    t0 = datetime(day.year, day.month, day.day, 4, 0, tzinfo=CDT)
    t1 = datetime(day.year, day.month, day.day, 11, 30, tzinfo=CDT)
    print("window", t0, "->", t1, flush=True)

    seven = load_tail("seven.jsonl", t0, t1)
    dec = load_tail("decision.jsonl", t0, t1)
    starts = [o for _, o in seven if o.get("event") == "seven_start"]
    print("seven_start", starts[-1] if starts else "NONE — wrong/missing bot", flush=True)

    ticks, d5s = [], []
    for dt, o in dec:
        m = o.get("mid")
        if m is not None:
            try:
                ticks.append((dt, float(m)))
            except Exception:
                pass
        d = o.get("delta_5s")
        if d is not None:
            try:
                d5s.append((dt, float(d)))
            except Exception:
                pass
    ticks.sort(); d5s.sort()
    print("ticks", len(ticks), "delta_5s", len(d5s), flush=True)

    by_bar = {}
    bug = Counter()
    live_go = []
    for dt, o in seven:
        ev = o.get("event")
        r = o.get("reason") or ""
        if r in ("visit_dead", "same_sweep_spent", "rail_spent", "already_tried_this_1m",
                 "sit_muted", "lock_ghost"):
            bug[r] += 1
        if ev in ("paper_fire", "struct40_submit", "struct40_fail") or o.get("submit") or r == "fire":
            live_go.append((dt, o))
        c1 = o.get("c1") or {}
        if c1.get("t0") is None or not o.get("poi"):
            continue
        by_bar[c1["t0"]] = (dt, o)
    bars = [by_bar[k] for k in sorted(by_bar)]
    print("closed 1m scored", len(bars), "bug_reasons", dict(bug), flush=True)
    print("\n--- box LIVE go/skip ---", flush=True)
    if not live_go:
        print("NONE", flush=True)
    for dt, o in live_go:
        print("%s %s skip=%s poi=%s mid=%s hold=%s shelf=%s lean=%s dist=%s" % (
            dt.strftime("%H:%M:%S"), o.get("event"), o.get("skip"), o.get("poi"),
            o.get("mid"), o.get("hold"), o.get("on_shelf"), o.get("tape_lean"),
            o.get("dist")), flush=True)

    a, sa = run_book("shelf6", bars, ticks, 6.0, False, False, d5s)
    b, sb = run_book("shelf15", bars, ticks, 15.0, False, False, d5s)
    c, sc = run_book("sniper", bars, ticks, NEAR, True, True, d5s)
    show("TEST 1  shelf6  (close<=6, 5m tape)", a, sa)
    show("TEST 2  shelf15 (close<=15, 5m tape)", b, sb)
    show("TEST 3  sniper15 + 15s/5s flip", c, sc)

    print("\n--- TEST1 vs LIVE: would-fire but box skipped (bug/lock) ---", flush=True)
    live_skip = {(o.get("c1") or {}).get("t0"): o.get("skip") for _, o in live_go}
    n = 0
    for tr in a:
        t0b = None
        for dt, o in bars:
            if dt == tr["t"]:
                t0b = (o.get("c1") or {}).get("t0")
                live_r = o.get("reason"); live_s = o.get("skip")
                break
        else:
            continue
        if live_s or (live_r not in ("fire", None) and not any(
                o.get("event") in ("paper_fire", "struct40_submit") for _, o in live_go if o.get("poi") == tr["poi"])):
            print("%s %s %s live_skip=%s live_reason=%s — shelf6 WOULD FIRE" % (
                tr["t"].strftime("%H:%M:%S"), tr["side"], tr["poi"], live_s, live_r), flush=True)
            n += 1
    if n == 0:
        print("(none, or live already fired the same bars)", flush=True)


if __name__ == "__main__":
    main()
