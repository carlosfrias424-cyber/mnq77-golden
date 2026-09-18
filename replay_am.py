#!/usr/bin/env python3
"""Replay today from seven.jsonl (bars 7/7 actually saw). New rules, no sit-mute.
Book 5x20/40. Dual not used.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
ARM, SL, TP, QTY = 6.0, 20.0, 40.0, 5
TAIL = 40_000_000


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
    print("open", name, flush=True)
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
    print("  ", name, "kept", len(out), "file", size, flush=True)
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


def main():
    day = datetime.now(CDT).date()
    t0 = datetime(day.year, day.month, day.day, 4, 0, tzinfo=CDT)
    t1 = datetime.now(CDT)
    print("window", t0.strftime("%H:%M"), "->", t1.strftime("%H:%M"), flush=True)

    seven = load_tail("seven.jsonl", t0, t1)
    dec = load_tail("decision.jsonl", t0, t1)
    ticks = []
    for dt, o in dec:
        m = o.get("mid")
        if m is None:
            continue
        try:
            ticks.append((dt, float(m)))
        except Exception:
            pass
    ticks.sort()
    print("ticks", len(ticks), flush=True)

    # one row per closed 1m (c1.t0) that 7/7 actually scored
    by_bar = {}
    for dt, o in seven:
        c1 = o.get("c1") or {}
        tbar = c1.get("t0")
        if tbar is None:
            continue
        poi = o.get("poi")
        if not poi:
            continue
        by_bar[tbar] = (dt, o)
    bars = [by_bar[k] for k in sorted(by_bar)]
    print("scored 1m with c1", len(bars), flush=True)

    spent = {}
    in_until = None
    trades = []
    skips = []
    seen_lock = set()

    for dt, o in bars:
        c1 = o.get("c1") or {}
        try:
            c = float(c1.get("c"))
            px = float(o.get("px") or str(o.get("poi")).split("@")[-1])
        except Exception:
            continue
        poi = o.get("poi")
        if in_until and dt < in_until:
            continue
        if in_until and dt >= in_until:
            in_until = None
        for k, spx in list(spent.items()):
            if abs(c - spx) >= 20:
                spent.pop(k, None)

        hold = o.get("hold")
        shelf = o.get("on_shelf")
        if shelf is None:
            shelf = abs(c - px) <= ARM
        lean = o.get("tape_lean")
        bounce = o.get("side_locked")
        if bounce is None:
            bounce = o.get("sr") == "support" or o.get("tag") == "low"
        side = "Buy" if bounce else "Sell"
        if o.get("side") in ("Buy", "Sell"):
            side = o["side"]

        why = None
        if o.get("reason") == "through_close" or (hold is False and o.get("reason") == "through_close"):
            why = "through_close"
        elif hold is False:
            why = "body_gave_rail"
        elif shelf is False:
            why = "off_shelf"
        elif lean is False:
            why = "tape_against"
        elif hold is not True or lean is not True or shelf is not True:
            why = "not_complete"
        elif poi in spent:
            why = "rail_spent"
        if why:
            skips.append((dt, poi, why, c))
            continue

        entry = c
        after = [(x, m) for x, m in ticks if x >= dt]
        hit, hit_t, mae, mfe = path_hit(side, entry, after)
        pts = TP if hit == "TP40" else (-SL if hit == "SL20" else 0.0)
        trades.append(dict(t=dt, side=side, entry=entry, poi=poi, hit=hit,
                           hit_t=hit_t, mae=mae, mfe=mfe, pts=pts,
                           hold=hold, shelf=shelf, lean=lean, skip_live=o.get("skip")))
        spent[poi] = px
        in_until = hit_t
        seen_lock.add(o.get("skip"))

    print("\n=== CORRECT 7/7 (from live 1m scores, sit-mute OFF) ===", flush=True)
    pnl = 0.0
    for tr in trades:
        dol = tr["pts"] * 2 * QTY
        pnl += dol
        ht = tr["hit_t"].strftime("%H:%M:%S") if tr["hit_t"] else "open"
        print("%s %s %.2f %s hit=%s %s MAE=%.1f MFE=%.1f pts=%+.0f $%+.0f live_skip=%s" % (
            tr["t"].strftime("%H:%M:%S"), tr["side"], tr["entry"], tr["poi"],
            tr["hit"], ht, tr["mae"], tr["mfe"], tr["pts"], dol, tr["skip_live"]), flush=True)
    print("trades", len(trades), "$pnl", round(pnl), "(5 MNQ x $2/pt)", flush=True)
    print("TP", sum(1 for t in trades if t["hit"] == "TP40"),
          "SL", sum(1 for t in trades if t["hit"] == "SL20"),
          "open", sum(1 for t in trades if t["hit"] == "OPEN"), flush=True)

    print("\n=== skip counts ===", dict(Counter(w for _, _, w, _ in skips)), flush=True)
    shown = Counter()
    for t, k, w, c in skips:
        if shown[w] >= 6:
            continue
        shown[w] += 1
        print("  %s %-16s %s c=%.2f" % (t.strftime("%H:%M:%S"), w, k, c), flush=True)

    print("\n=== box live go/skip (including muted sits) ===", flush=True)
    for dt, o in seven:
        ev = o.get("event")
        if ev in ("paper_fire", "struct40_submit", "struct40_fail") or o.get("submit") or o.get("reason") == "fire":
            print("%s %s skip=%s poi=%s mid=%s hold=%s shelf=%s lean=%s" % (
                dt.strftime("%H:%M:%S"), ev, o.get("skip"), o.get("poi"),
                o.get("mid"), o.get("hold"), o.get("on_shelf"), o.get("tape_lean")), flush=True)


if __name__ == "__main__":
    main()
