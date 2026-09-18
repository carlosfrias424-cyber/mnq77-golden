#!/usr/bin/env python3
"""Exact 7/7 replay. Dual unused. No orders. No mid-cleaning.

Same as live_77_shelf15.py + mnq_vol.py:
  Databento GLBX trades → 1m/5m OHLC + 5m delta
  rail = last TV webhook as of that 1m close
  fire that closed 1m if: wick tags 10, hold, close within 15, 5m tape with us
  skip/off_shelf do NOT mute; spent only after a fill, clear at 20 pts
Book paper: 5 MNQ, SL 20, TP 40, first print after the close.
Days: Wed 9/16, Thu 9/17, Fri 9/18  04:00–11:30 America/Chicago.
"""
from __future__ import annotations
import json, os, sys
from collections import deque, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
TZ = ZoneInfo("America/Chicago")
WATCH, ARM, SL, TP, QTY = 10.0, 15.0, 20.0, 40.0, 5
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
DAYS = [datetime(2026, 9, d, tzinfo=TZ).date() for d in (16, 17, 18)]


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
    if s in ("A", "B"):
        return sz if s == "A" else -sz
    if s in ("BUY", "BID"):
        return sz
    if s in ("SELL", "ASK"):
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
    print("webhooks", len(out), flush=True)
    return out


def last_rail(alerts, t):
    best = None
    for recv, kind, px in alerts:
        if recv > t:
            break
        best = (kind, px)
    return best


def tape_5m(closed_5, m5, bounce):
    last = closed_5[-1] if closed_5 else None
    prev = closed_5[-2] if len(closed_5) >= 2 else None
    d_last = last.delta if last else None
    d_prev = prev.delta if prev else None
    d_live = m5.delta if m5 else None
    met = dict(d_last=d_last, d_prev=d_prev, d_live=d_live, src="databento_5m")
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
    if dt.date() not in DAYS:
        return False
    mins = dt.hour * 60 + dt.minute
    return (4 * 60) <= mins < (11 * 60 + 30)


def path_hit(side, entry, prints):
    mae = mfe = 0.0
    for ts, px in prints:
        if side == "Buy":
            mae = max(mae, entry - px)
            mfe = max(mfe, px - entry)
            if px <= entry - SL:
                return "SL20", ts, mae, mfe
            if px >= entry + TP:
                return "TP40", ts, mae, mfe
        else:
            mae = max(mae, px - entry)
            mfe = max(mfe, entry - px)
            if px >= entry + SL:
                return "SL20", ts, mae, mfe
            if px <= entry - TP:
                return "TP40", ts, mae, mfe
    return "OPEN", None, mae, mfe


def fetch_trades(key, start_iso, end_iso):
    import databento as db
    client = db.Historical(key=key)
    symbol = os.environ.get("MNQ_DB_SYMBOL") or "MNQZ6"
    stype = os.environ.get("MNQ_DB_STYPE") or "raw_symbol"
    print("databento", symbol, stype, start_iso, "->", end_iso, flush=True)
    try:
        data = client.timeseries.get(
            dataset="GLBX.MDP3", schema="trades",
            symbols=[symbol], stype_in=stype,
            start=start_iso, end=end_iso,
        )
    except Exception as e:
        print("MNQZ6 failed", e, "trying MNQ.c.0", flush=True)
        data = client.timeseries.get(
            dataset="GLBX.MDP3", schema="trades",
            symbols=["MNQ.c.0"], stype_in="continuous",
            start=start_iso, end=end_iso,
        )
    rows = []
    for rec in data:
        px = px_of(rec)
        ts = rec_ts(rec)
        if px is None or ts is None:
            continue
        sz = sz_of(rec)
        rows.append((ts, px, sz, side_delta(rec, sz)))
    rows.sort()
    print("prints", len(rows), flush=True)
    return rows


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ.get("DATABENTO_KEY") or ""
    if not key:
        sys.exit("DATABENTO_API_KEY missing in .env")
    alerts = load_alerts()
    # 03:50 CDT warmup so a 5m bar exists before 04:00
    start = datetime(2026, 9, 16, 3, 50, tzinfo=TZ).astimezone(timezone.utc)
    end = datetime(2026, 9, 18, 12, 0, tzinfo=TZ).astimezone(timezone.utc)
    prints = fetch_trades(key, start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S"))
    if not prints:
        sys.exit("no databento prints")

    m1 = m5 = None
    closed_1, closed_5 = deque(), deque()
    closed_events = []  # (candle, m5_snap, closed_5_snap)

    def roll(minutes, ts, px, vol, dlt):
        nonlocal m1, m5
        t0 = bar_open(ts, minutes)
        cur = m1 if minutes == 1 else m5
        closed = closed_1 if minutes == 1 else closed_5
        just = None
        if cur is None or cur.t0 != t0:
            if cur is not None:
                closed.append(cur)
                just = cur
            cur = Candle(t0, px, px, px, px, 0.0, 0, 0.0)
            if minutes == 1:
                m1 = cur
            else:
                m5 = cur
        cur.h = max(cur.h, px)
        cur.l = min(cur.l, px)
        cur.c = px
        cur.v += vol
        cur.n += 1
        cur.delta += dlt
        return just

    for ts, px, sz, dlt in prints:
        just1 = roll(1, ts, px, sz, dlt)
        roll(5, ts, px, sz, dlt)
        if just1 is not None:
            closed_events.append((just1, m5, list(closed_5)))

    print("closed 1m", len(closed_events), flush=True)

    by_day = {d: [] for d in DAYS}
    spent = {}
    in_until = None
    side_locked = {}
    last_key = ""
    skips = defaultdict(int)
    idx = 0
    nprints = len(prints)

    for candle, m5s, c5 in closed_events:
        close_ts = candle.t0 + 60.0
        if not sess_ok(candle.t0):
            continue
        day = datetime.fromtimestamp(candle.t0, TZ).date()
        while idx < nprints and prints[idx][0] < close_ts:
            idx += 1
        after = prints[idx:]

        if in_until and close_ts < in_until:
            skips["in_trade"] += 1
            continue
        if in_until and close_ts >= in_until:
            in_until = None
        for k, rpx in list(spent.items()):
            if abs(candle.c - rpx) >= 20:
                spent.pop(k, None)

        rail = last_rail(alerts, close_ts)
        if rail is None:
            skips["no_webhook"] += 1
            continue
        kind, rpx = rail
        key = f"{kind}@{rpx:.2f}"
        if key != last_key:
            last_key = key
            side_locked.pop(key, None) if False else None
            # new rail: keep other locks, reset this key's lock only if brand new visit
            if key not in side_locked:
                side_locked[key] = bounce_from_name(kind)

        if extreme_dist(rpx, candle.l, candle.h) > WATCH:
            skips["no_rail_in_watch"] += 1
            continue

        loc = True if candle.c > rpx else (False if candle.c < rpx else None)
        bounce = side_locked.get(key)
        if bounce is None:
            named = bounce_from_name(kind)
            if named is not None:
                bounce = named
            elif loc is not None:
                bounce = loc
            else:
                skips["at_rail"] += 1
                continue
            side_locked[key] = bounce

        if loc is None:
            skips["at_rail"] += 1
            continue
        if loc != bounce:
            skips["through_close"] += 1
            continue
        hit = (abs(candle.l - rpx) <= WATCH) if bounce else (abs(candle.h - rpx) <= WATCH)
        if not hit:
            skips["idle_no_hit"] += 1
            continue
        hold = (candle.c >= rpx) if bounce else (candle.c <= rpx)
        if not hold:
            skips["body_gave_rail"] += 1
            continue
        if abs(candle.c - rpx) > ARM:
            skips["off_shelf"] += 1
            continue
        lean, met = tape_5m(c5, m5s, bounce)
        if not lean:
            skips[met.get("why") or "tape_against"] += 1
            continue
        if key in spent:
            skips["rail_spent"] += 1
            continue

        side = "Buy" if bounce else "Sell"
        path = [(t, p) for t, p, *_ in after]
        hitp, ht, mae, mfe = path_hit(side, candle.c, path)
        pts = TP if hitp == "TP40" else (-SL if hitp == "SL20" else 0.0)
        rec = dict(day=day, t=datetime.fromtimestamp(close_ts, TZ), side=side,
                   entry=candle.c, poi=key, hit=hitp, hit_t=datetime.fromtimestamp(ht, TZ) if ht else None,
                   mae=mae, mfe=mfe, pts=pts, tape=met)
        by_day[day].append(rec)
        spent[key] = rpx
        in_until = ht

    tot_n = tot_tp = tot_sl = tot_pnl = 0
    for day in DAYS:
        trades = by_day[day]
        print("\n========", day.strftime("%a %Y-%m-%d"), "EXACT 7/7 ========" , flush=True)
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
        tot_n += len(trades)
        tot_tp += sum(1 for t in trades if t["hit"] == "TP40")
        tot_sl += sum(1 for t in trades if t["hit"] == "SL20")
        tot_pnl += pnl
    print("\nskips", dict(skips), flush=True)
    print("======== 3-DAY TOTAL (Databento + last webhook, live rules) ========" , flush=True)
    print("n=%d TP=%d SL=%d $%+.0f  (5 MNQ x $2, SL20/TP40)" % (tot_n, tot_tp, tot_sl, tot_pnl), flush=True)


if __name__ == "__main__":
    main()
