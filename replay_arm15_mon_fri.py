#!/usr/bin/env python3
"""ARM 15 sit (last-alert, tag 10, close within 15, hold, tape if logged).
Days: Mon 9/14 then Fri 9/18. Sequential 20/40 on seven.jsonl mids.
Dual unused. Live not touched.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
WATCH, ARM, SL, TP, HOLE = 10.0, 15.0, 20.0, 40.0, 8.0
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
DAYS = [
    datetime(2026, 9, 14, tzinfo=CDT),
    datetime(2026, 9, 18, tzinfo=CDT),
]


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


def sr_kind(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def bounce_from_name(kind):
    u = (kind or "").upper()
    if u in SUPPORT:
        return True
    if u in RESIST:
        return False
    return None


def load_jsonl(name, t0, t1):
    p = ROOT / name
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
        if dt is None or dt < t0 or dt > t1:
            continue
        out.append((dt, o))
    out.sort(key=lambda x: x[0])
    return out


def last_alerts(poi):
    alerts = []
    for dt, o in poi:
        try:
            px = round(float(o.get("price") or 0), 2)
        except Exception:
            continue
        kind = sr_kind(str(o.get("poi_name") or o.get("type") or ""))
        if kind is None or px <= 0:
            continue
        alerts.append((dt, kind, px))
    return alerts


def alert_at(alerts, t):
    last = None
    for dt, kind, px in alerts:
        if dt <= t:
            last = (dt, kind, px)
        else:
            break
    return last


def bars_from_seven(seven):
    bars = {}
    mids = []
    for dt, o in seven:
        m = o.get("mid")
        if m is None:
            continue
        try:
            m = float(m)
        except Exception:
            continue
        mids.append((dt, m))
        key = dt.replace(second=0, microsecond=0)
        bar = o.get("bar")
        lo = hi = m
        if isinstance(bar, (list, tuple)) and len(bar) >= 2:
            try:
                lo, hi = float(bar[0]), float(bar[1])
            except Exception:
                lo = hi = m
        rec = bars.get(key)
        tape = o.get("tape_lean")
        if rec is None:
            bars[key] = dict(lo=lo, hi=hi, close=m, tape=tape)
        else:
            rec["lo"] = min(rec["lo"], lo, m)
            rec["hi"] = max(rec["hi"], hi, m)
            rec["close"] = m
            if tape is not None:
                rec["tape"] = tape
    return bars, mids


def path(side, entry, series, t0):
    seq = [(dt, m) for dt, m in series if dt >= t0]
    if not seq:
        return "NO_TAPE", None, 0.0, 0.0, None
    if abs(seq[0][1] - entry) > HOLE:
        return "HOLE", None, abs(seq[0][1] - entry), 0.0, None
    mae = mfe = 0.0
    for dt, mid in seq:
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
        if dt > t0 + timedelta(hours=6):
            break
    return "OPEN", None, mae, mfe, None


def run_day(day):
    t0 = day.replace(hour=4, minute=0, second=0, microsecond=0)
    t1 = day.replace(hour=11, minute=30, second=0, microsecond=0)
    label = day.strftime("%a %m/%d")
    print(f"\n==== {label} ARM 15  04:00-11:30 ====")
    seven = load_jsonl("seven.jsonl", t0, t1 + timedelta(hours=6))
    poi = load_jsonl("tv_poi.jsonl", t0 - timedelta(hours=2), t1)
    alerts = last_alerts(poi)
    bars, mids = bars_from_seven(seven)
    print(f"seven {len(seven)}  poi {len(poi)}  alerts {len(alerts)}  1m {len(bars)}")
    minutes = sorted(k for k in bars if t0 <= k < t1)
    taken = []
    skipped = holes = 0
    busy = None
    print("when      side  entry     rail                    hit   pnl    MAE   MFE")
    for minute in minutes:
        b = bars[minute]
        al = alert_at(alerts, minute)
        if al is None:
            continue
        _, kind, px = al
        bounce = bounce_from_name(kind)
        if bounce is None:
            bounce = b["close"] >= px
        lo, hi, close = b["lo"], b["hi"], b["close"]
        tagged = abs(lo - px) <= WATCH if bounce else abs(hi - px) <= WATCH
        near = abs(close - px) <= ARM
        hold = (close >= px) if bounce else (close <= px)
        tape = b.get("tape")
        if not (tagged and near and hold):
            continue
        if tape is False:
            continue
        side = "Buy" if bounce else "Sell"
        if busy is not None and minute < busy:
            skipped += 1
            continue
        hit, pnl, mae, mfe, hit_t = path(side, close, mids, minute)
        if hit == "HOLE":
            holes += 1
            print(f"{minute:%H:%M}     {side:4} {close:8.2f}  {kind}@{px:.2f}             HOLE  gap={mae:.1f}")
            continue
        if hit in ("SL", "TP") and hit_t is not None:
            busy = hit_t
        else:
            busy = minute + timedelta(hours=6)
        taken.append((hit, pnl))
        ptxt = f"{pnl:+6.1f}" if pnl is not None else "   n/a"
        print(f"{minute:%H:%M}     {side:4} {close:8.2f}  {kind}@{px:<8.2f}  {hit:4} {ptxt}  {mae:5.1f} {mfe:5.1f}")
    w = sum(1 for h, p in taken if h == "TP")
    l = sum(1 for h, p in taken if h == "SL")
    o = sum(1 for h, p in taken if h not in ("TP", "SL"))
    pnl = sum(p or 0 for _, p in taken)
    n = w + l
    print("--- card ---")
    print(f"taken {len(taken)}  skipped_open {skipped}  holes {holes}  still_open {o}")
    print(f"W {w}  L {l}  WR {100*w/n if n else 0:.0f}%  PnL {pnl:+.0f}")
    if l:
        print(f"PF {(w * 40.0) / (l * 20.0):.2f}")
    elif w:
        print("PF inf")
    return w, l, pnl, len(taken)


def main():
    print("VERSION arm15_mon_fri  9/14 then 9/18")
    print("ARM 15 last-alert. Dual off.\n")
    tot = []
    for d in DAYS:
        tot.append(run_day(d))
    w = sum(x[0] for x in tot)
    l = sum(x[1] for x in tot)
    pnl = sum(x[2] for x in tot)
    n = w + l
    print("\n==== BOTH ====")
    print(f"W {w}  L {l}  WR {100*w/n if n else 0:.0f}%  PnL {pnl:+.0f}")
    if l:
        print(f"PF {(w * 40.0) / (l * 20.0):.2f}")
    print("DONE one version.")


if __name__ == "__main__":
    main()
