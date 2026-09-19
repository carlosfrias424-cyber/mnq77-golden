#!/usr/bin/env python3
"""Wednesday 9/16. Unique paper_fire → 20/40 using seven.jsonl mids only.
If first mid is >8 pts from entry, tape is a hole — skip that sit.
Sequential one position. Dual unused. Live not touched.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
T0 = datetime(2026, 9, 16, 4, 0, tzinfo=CDT)
T1 = datetime(2026, 9, 16, 11, 30, tzinfo=CDT)
SL, TP, HOLE = 20.0, 40.0, 8.0


def dt_of(o):
    t = o.get("recv_ts") or o.get("ts")
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


def load_seven():
    p = ROOT / "seven.jsonl"
    if not p.exists():
        print("MISSING", p)
        return []
    out = []
    for ln in p.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if dt is None or dt < T0 or dt > T1 + timedelta(hours=6):
            continue
        out.append((dt, o))
    out.sort(key=lambda x: x[0])
    return out


def unique_fires(rows):
    seen = set()
    fires = []
    for dt, o in rows:
        if dt < T0 or dt >= T1:
            continue
        if o.get("event") != "paper_fire" and not o.get("submit"):
            continue
        side = o.get("side") or (o.get("snap") or {}).get("side")
        poi = o.get("poi")
        mid = o.get("mid")
        if side not in ("Buy", "Sell") or mid is None or not poi:
            continue
        key = (dt.strftime("%H:%M"), side, str(poi))
        if key in seen:
            continue
        seen.add(key)
        fires.append((dt, side, float(mid), str(poi)))
    return fires


def mids_after(rows, t0):
    out = []
    for dt, o in rows:
        if dt < t0:
            continue
        m = o.get("mid")
        if m is None:
            continue
        try:
            out.append((dt, float(m)))
        except Exception:
            continue
        if dt > t0 + timedelta(hours=6):
            break
    return out


def path(side, entry, series):
    if not series:
        return "NO_TAPE", None, 0.0, 0.0, None
    first = series[0][1]
    if abs(first - entry) > HOLE:
        return "HOLE", None, abs(first - entry), 0.0, None
    mae = mfe = 0.0
    for dt, mid in series:
        if side == "Buy":
            fav, adv = mid - entry, entry - mid
        else:
            fav, adv = entry - mid, mid - entry
        mfe = max(mfe, fav)
        mae = max(mae, adv)
        if adv >= SL:
            return "SL", -SL, mae, mfe, dt
        if fav >= TP:
            return "TP", TP, mae, mfe, dt
    return "OPEN", None, mae, mfe, None


def main():
    print("VERSION wed_seven_mids_20_40  9/16 04:00-11:30")
    print("Path on seven.jsonl mids. Hole if first mid >8 from entry.\n")
    rows = load_seven()
    fires = unique_fires(rows)
    print("unique paper GOs", len(fires), "seven rows", len(rows))
    if not fires:
        print("NO PAPER FIRES")
        return

    print("when           side  entry     poi                         hit   pnl    MAE   MFE")
    taken = []
    skipped = holes = 0
    busy_until = None
    for dt, side, entry, poi in fires:
        if busy_until is not None and dt < busy_until:
            skipped += 1
            print(f"{dt:%H:%M:%S}  {side:4} {entry:8.2f}  {poi:<26}  skip          open_position")
            continue
        series = mids_after(rows, dt)
        hit, pnl, mae, mfe, hit_t = path(side, entry, series)
        if hit == "HOLE":
            holes += 1
            print(f"{dt:%H:%M:%S}  {side:4} {entry:8.2f}  {poi:<26}  HOLE          first_mid_gap={mae:.1f}")
            continue
        if hit in ("SL", "TP") and hit_t is not None:
            busy_until = hit_t
        elif hit == "OPEN":
            busy_until = dt + timedelta(hours=6)
        taken.append((hit, pnl, mae, mfe))
        ptxt = f"{pnl:+6.1f}" if pnl is not None else "   n/a"
        print(
            f"{dt:%H:%M:%S}  {side:4} {entry:8.2f}  {poi:<26}  {hit:4} {ptxt}  "
            f"{mae:5.1f} {mfe:5.1f}"
        )

    w = sum(1 for h, p, *_ in taken if h == "TP")
    l = sum(1 for h, p, *_ in taken if h == "SL")
    o = sum(1 for h, p, *_ in taken if h == "OPEN")
    pnl = sum(p or 0 for _, p, *_ in taken)
    n = w + l
    print("\n--- card ---")
    print(f"unique GOs {len(fires)}  taken {len(taken)}  skipped_open {skipped}  holes {holes}  still_open {o}")
    print(f"W {w}  L {l}  WR {100*w/n if n else 0:.0f}%")
    if l:
        print(f"PF {(w * 40.0) / (l * 20.0):.2f}  RR 2:1  PnL {pnl:+.0f}")
    elif w:
        print(f"PF inf  RR 2:1  PnL {pnl:+.0f}")
    else:
        print(f"PnL {pnl:+.0f}")
    print("MAE on a real SL should be ~20, not 290. Dual off.")
    print("DONE one version.")


if __name__ == "__main__":
    main()
