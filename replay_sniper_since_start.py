#!/usr/bin/env python3
"""Backtest CURRENT box rules. Does NOT change live_77.py.

sniper_live_15 on Databento ticks + last webhook:
  wick tags 10, last print holds, last print within 15, 5m tape with us.
  No C2. No volume. Dual unused.
  Book paper: SL20 TP40 BE off. One position. One try per forming 1m.
Session 04:00–11:30 CT M–F. Holiday 2026-09-07 skipped.
Days: first tv_poi through last (since 7/7 started).
"""
from __future__ import annotations

import json, os, sys
from collections import deque, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
TZ = ZoneInfo("America/Chicago")
WATCH, ARM, SL, TP = 10.0, 15.0, 20.0, 40.0
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
HOLIDAYS = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}


def envload():
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        if not raw.strip() or raw.startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def bar_open(ts, minutes):
    dt = datetime.fromtimestamp(ts, TZ)
    m = (dt.minute // minutes) * minutes
    return dt.replace(minute=m, second=0, microsecond=0).timestamp()


def px_of(rec):
    raw = getattr(rec, "pretty_price", None)
    if raw is not None:
        try:
            return float(raw)
        except Exception:
            pass
    raw = getattr(rec, "price", None)
    if raw is None:
        return None
    try:
        x = float(raw)
        return x / 1e9 if abs(x) > 1e7 else x
    except Exception:
        return None


def sz_of(rec):
    for k in ("size", "quantity", "qty"):
        v = getattr(rec, k, None)
        if v is not None:
            try:
                return max(0.0, float(v))
            except Exception:
                return 0.0
    return 0.0


def side_delta(rec, sz):
    s = str(getattr(rec, "side", "") or "").upper()
    # Databento: B = buy aggressor, A = sell aggressor. N is not counted.
    if s in ("B", "BUY", "BID"):
        return sz
    if s in ("A", "SELL", "ASK"):
        return -sz
    return 0.0


def rec_ts(rec):
    for k in ("ts_event", "ts_recv", "timestamp"):
        v = getattr(rec, k, None)
        if v is None:
            continue
        try:
            x = int(v)
            if x > 1e16:
                return x / 1e9
            if x > 1e12:
                return x / 1e6
            return float(x)
        except Exception:
            continue
    return None


@dataclass
class Candle:
    t0: float
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0
    n: int = 0
    delta: float = 0.0


def sr_kind(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def bounce_from_name(kind):
    if kind in SUPPORT:
        return True
    if kind in RESIST:
        return False
    return None


def extreme_dist(px, lo, hi):
    if lo <= px <= hi:
        return 0.0
    if px > hi:
        return px - hi
    return lo - px


def load_alerts():
    out = []
    if not POI.exists():
        print("MISSING tv_poi.jsonl", flush=True)
        return out
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
        except Exception:
            continue
        if px <= 0:
            continue
        recv = o.get("recv_ts") or o.get("ts") or 0
        try:
            recv = float(recv)
            if recv > 1e12:
                recv /= 1000.0
        except Exception:
            continue
        if recv <= 0:
            continue
        out.append((recv, kind, px))
    out.sort()
    if out:
        a = datetime.fromtimestamp(out[0][0], TZ)
        b = datetime.fromtimestamp(out[-1][0], TZ)
        print("webhooks", len(out), a, "->", b, flush=True)
    else:
        print("webhooks 0", flush=True)
    return out


def last_rail(alerts, t):
    best = None
    for recv, kind, px in alerts:
        if recv > t:
            break
        best = (kind, px, recv)
    return best


def tape_5m(closed_5, m5, bounce):
    last = closed_5[-1] if closed_5 else None
    prev = closed_5[-2] if len(closed_5) >= 2 else None
    d_last = last.delta if last else None
    d_prev = prev.delta if prev else None
    d_live = m5.delta if m5 else None
    met = dict(d_last=d_last, d_prev=d_prev, d_live=d_live)
    if d_last is None:
        return False, {**met, "why": "need_closed_5m"}
    cur = d_live if d_live is not None else d_last
    slope = None if d_prev is None else (d_last - d_prev)
    met["slope"] = slope
    if bounce:
        ok = cur > 0 and (slope is None or slope >= 0)
    else:
        ok = cur < 0 and (slope is None or slope <= 0)
    if not ok:
        met["why"] = "tape_against_5m"
    return ok, met


def sess_ok(ts):
    dt = datetime.fromtimestamp(ts, TZ)
    if dt.weekday() >= 5:
        return False
    if dt.date() in HOLIDAYS:
        return False
    mins = dt.hour * 60 + dt.minute
    return (4 * 60) <= mins < (11 * 60 + 30)


def session_days(alerts):
    if not alerts:
        return []
    a = datetime.fromtimestamp(alerts[0][0], TZ).date()
    b = datetime.fromtimestamp(alerts[-1][0], TZ).date()
    out = []
    d = a
    while d <= b:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


def ingest(data):
    rows = []
    for rec in data:
        px = px_of(rec)
        ts = rec_ts(rec)
        if px is None or ts is None:
            continue
        sz = sz_of(rec)
        rows.append((ts, px, sz, side_delta(rec, sz)))
    return rows


def utc_window(day):
    start = datetime(day.year, day.month, day.day, 4, 0, tzinfo=TZ) - timedelta(minutes=10)
    end = datetime(day.year, day.month, day.day, 11, 30, tzinfo=TZ) + timedelta(minutes=30)
    return start.astimezone(datetime.now().astimezone().tzinfo and TZ).strftime("%Y-%m-%dT%H:%M:%S"), \
           None


def fetch_day(client, day, symbol, stype):
    start = datetime(day.year, day.month, day.day, 3, 50, tzinfo=TZ)
    end = datetime(day.year, day.month, day.day, 12, 0, tzinfo=TZ)
    a = start.astimezone(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    # Databento wants UTC-ish ISO; convert to UTC
    a = start.astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    b = end.astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    print("databento", day, symbol, a, "->", b, flush=True)
    try:
        data = client.timeseries.get_range(
            dataset="GLBX.MDP3", schema="trades",
            symbols=[symbol], stype_in=stype, start=a, end=b,
        )
        return ingest(data)
    except Exception as e:
        print(" ", symbol, e, flush=True)
        return None


def fetch_trades(key, days):
    import databento as db
    client = db.Historical(key=key)
    symbol = os.environ.get("MNQ_DB_SYMBOL") or "MNQZ6"
    stype = os.environ.get("MNQ_DB_STYPE") or "raw_symbol"
    rows = []
    for day in days:
        part = fetch_day(client, day, symbol, stype)
        if not part:
            part = fetch_day(client, day, "MNQ.c.0", "continuous")
        if part:
            print("  prints", len(part), flush=True)
            rows.extend(part)
        else:
            print("  NO PRINTS", day, flush=True)
    rows.sort()
    print("prints total", len(rows), flush=True)
    return rows


def path_from(prints, i0, side, entry):
    mae = mfe = 0.0
    for ts, px, *_ in prints[i0:]:
        if side == "Buy":
            mae = max(mae, entry - px)
            mfe = max(mfe, px - entry)
            if px <= entry - SL:
                return "SL", -SL, mae, mfe, ts
            if px >= entry + TP:
                return "TP", TP, mae, mfe, ts
        else:
            mae = max(mae, px - entry)
            mfe = max(mfe, entry - px)
            if px >= entry + SL:
                return "SL", -SL, mae, mfe, ts
            if px <= entry - TP:
                return "TP", TP, mae, mfe, ts
        if ts > prints[i0][0] + 6 * 3600:
            break
    return "OPEN", None, mae, mfe, None


def main():
    print("VERSION sniper_live_15_since_start  last-print  WATCH10 ARM15 tape5m 20/40")
    print("live_77.py NOT touched. Dual unused.\n", flush=True)
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ.get("DATABENTO_KEY") or ""
    if not key:
        sys.exit("DATABENTO_API_KEY missing in .env")
    alerts = load_alerts()
    if not alerts:
        sys.exit("no webhooks")
    days = session_days(alerts)
    print("session days", [d.isoformat() for d in days], flush=True)
    prints = fetch_trades(key, days)
    if not prints:
        sys.exit("no databento prints")

    m1 = m5 = None
    closed_5 = deque(maxlen=20)
    spent = {}
    in_until = None
    last_key = ""
    bounce = None
    tried_t0 = None
    skips = defaultdict(int)
    by_day = defaultdict(list)
    ai = 0

    def roll(cur, minutes, ts, px, vol, dlt):
        t0 = bar_open(ts, minutes)
        just = None
        if cur is None or cur.t0 != t0:
            just = cur
            cur = Candle(t0, px, px, px, px, 0.0, 0, 0.0)
        cur.h = max(cur.h, px)
        cur.l = min(cur.l, px)
        cur.c = px
        cur.v += vol
        cur.n += 1
        cur.delta += dlt
        return cur, just

    n = len(prints)
    for i, (ts, px, sz, dlt) in enumerate(prints):
        m1, just1 = roll(m1, 1, ts, px, sz, dlt)
        m5, just5 = roll(m5, 5, ts, px, sz, dlt)
        if just5 is not None:
            closed_5.append(just5)
        if m1 is None:
            continue
        if not sess_ok(ts):
            continue
        day = datetime.fromtimestamp(ts, TZ).date()

        while ai + 1 < len(alerts) and alerts[ai + 1][0] <= ts:
            ai += 1
        rail = last_rail(alerts[: ai + 1], ts) if alerts else None

        if in_until and ts < in_until:
            continue
        if in_until and ts >= in_until:
            in_until = None
        for k, rpx in list(spent.items()):
            if abs(px - rpx) >= 20:
                spent.pop(k, None)

        if rail is None:
            skips["no_webhook"] += 1
            last_key = ""
            bounce = None
            continue
        kind, rpx, _recv = rail
        key = f"{kind}@{rpx:.2f}"
        if key != last_key:
            last_key = key
            bounce = bounce_from_name(kind)
            tried_t0 = None

        bar_lo, bar_hi = m1.l, m1.h
        if extreme_dist(rpx, bar_lo, bar_hi) > WATCH:
            skips["no_rail_in_watch"] += 1
            continue

        loc = True if px > rpx else (False if px < rpx else None)
        if bounce is None:
            if loc is None:
                skips["at_rail"] += 1
                continue
            bounce = loc

        tagged = (abs(bar_lo - rpx) <= WATCH) if bounce else (abs(bar_hi - rpx) <= WATCH)
        near = abs(px - rpx) <= ARM
        hold = True if loc is None else ((px >= rpx) if bounce else (px <= rpx))
        if not tagged:
            skips["idle_no_hit"] += 1
            continue
        if loc is not None and loc != bounce:
            skips["through"] += 1
            continue
        if not hold:
            skips["gave_rail"] += 1
            continue
        if not near:
            skips["chase"] += 1
            continue
        lean, met = tape_5m(closed_5, m5, bounce)
        if not lean:
            skips[met.get("why") or "tape_against"] += 1
            continue
        if key in spent:
            skips["rail_spent"] += 1
            continue
        if tried_t0 == m1.t0:
            skips["already_tried_this_1m"] += 1
            continue

        tried_t0 = m1.t0
        side = "Buy" if bounce else "Sell"
        hit, pnl, mae, mfe, hit_t = path_from(prints, i, side, px)
        if hit in ("SL", "TP") and hit_t is not None:
            in_until = hit_t
        else:
            in_until = ts + 6 * 3600
        spent[key] = rpx
        rec = dict(
            day=day, t=datetime.fromtimestamp(ts, TZ), side=side,
            entry=px, poi=key, hit=hit, pnl=pnl, mae=mae, mfe=mfe,
            hit_t=datetime.fromtimestamp(hit_t, TZ) if hit_t else None,
        )
        by_day[day].append(rec)

    tot_w = tot_l = tot_pnl = tot_n = 0
    for day in sorted(by_day):
        trades = by_day[day]
        print(f"\n==== {day.strftime('%a %Y-%m-%d')} ====", flush=True)
        print("when      side  entry     poi                    hit   pnl    MAE   MFE", flush=True)
        w = l = pnl = 0
        for tr in trades:
            ptxt = f"{tr['pnl']:+6.1f}" if tr["pnl"] is not None else "   n/a"
            print(
                f"{tr['t']:%H:%M:%S}  {tr['side']:4} {tr['entry']:8.2f}  {tr['poi']:<20}  "
                f"{tr['hit']:4} {ptxt}  {tr['mae']:5.1f} {tr['mfe']:5.1f}",
                flush=True,
            )
            if tr["hit"] == "TP":
                w += 1
            elif tr["hit"] == "SL":
                l += 1
            pnl += tr["pnl"] or 0
        n = w + l
        print(
            f"taken {len(trades)}  W {w}  L {l}  WR {100*w/n if n else 0:.0f}%  PnL {pnl:+.0f}",
            flush=True,
        )
        tot_w += w
        tot_l += l
        tot_pnl += pnl
        tot_n += len(trades)

    n = tot_w + tot_l
    print("\n==== ALL DAYS (sniper_live_15, last print, 20/40 pts/lot) ====", flush=True)
    print(f"taken {tot_n}  W {tot_w}  L {tot_l}  WR {100*tot_w/n if n else 0:.0f}%  PnL {tot_pnl:+.0f}")
    if tot_l:
        print(f"PF {(tot_w * 40.0) / (tot_l * 20.0):.2f}")
    elif tot_w:
        print("PF inf")
    print("skips", dict(skips), flush=True)
    print("DONE. live_77.py not touched.", flush=True)


if __name__ == "__main__":
    main()
