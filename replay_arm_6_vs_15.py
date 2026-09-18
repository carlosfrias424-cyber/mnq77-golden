#!/usr/bin/env python3
"""Same Databento prints + last webhook. Score ARM=6 and ARM=15.
Proves 15 is a wider net: every 6-pt fire is a 15 candidate unless a 15-only
fill is already occupying the book. Dual unused. No mid-cleaning.
Caches prints at /tmp/mnq_z6_prints.pkl so we do not re-download.
"""
from __future__ import annotations
import pickle, sys
from pathlib import Path

# reuse helpers from the exact replay sitting next to this file when copied,
# else load the already-run /tmp module after we exec the exact script's defs.
CACHE = Path("/tmp/mnq_z6_prints.pkl")
SRC = Path("/tmp/replay_exact_77.py")

def main():
    ns = {}
    exec(SRC.read_text(), ns)
    envload = ns["envload"]
    load_alerts = ns["load_alerts"]
    fetch_trades = ns["fetch_trades"]
    bar_open = ns["bar_open"]
    Candle = ns["Candle"]
    sess_ok = ns["sess_ok"]
    last_rail = ns["last_rail"]
    bounce_from_name = ns["bounce_from_name"]
    extreme_dist = ns["extreme_dist"]
    tape_5m = ns["tape_5m"]
    path_hit = ns["path_hit"]
    DAYS = ns["DAYS"]
    WATCH = ns["WATCH"]
    SL = ns["SL"]
    TP = ns["TP"]
    QTY = ns["QTY"]
    datetime = ns["datetime"]
    TZ = ns["TZ"]
    deque = ns["deque"]
    defaultdict = ns["defaultdict"]

    envload()
    alerts = load_alerts()
    if CACHE.exists():
        prints = pickle.loads(CACHE.read_bytes())
        print("cache", CACHE, "prints", len(prints), flush=True)
    else:
        import os
        key = os.environ.get("DATABENTO_API_KEY") or os.environ.get("DATABENTO_KEY") or ""
        if not key:
            sys.exit("DATABENTO_API_KEY missing")
        prints = fetch_trades(key)
        CACHE.write_bytes(pickle.dumps(prints, protocol=4))
        print("wrote", CACHE, flush=True)

    m1 = m5 = None
    closed_1, closed_5 = deque(), deque()
    closed_events = []

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

    def run(arm):
        by_day = {d: [] for d in DAYS}
        spent, in_until, side_locked, last_key = {}, None, {}, ""
        skips = defaultdict(int)
        idx, nprints = 0, len(prints)
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
                if key not in side_locked:
                    side_locked[key] = bounce_from_name(kind)
            if extreme_dist(rpx, candle.l, candle.h) > WATCH:
                skips["no_rail_in_watch"] += 1
                continue
            loc = True if candle.c > rpx else (False if candle.c < rpx else None)
            bounce = side_locked.get(key)
            if bounce is None:
                named = bounce_from_name(kind)
                bounce = named if named is not None else loc
                if bounce is None:
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
            dist = abs(candle.c - rpx)
            if dist > arm:
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
            by_day[day].append(dict(
                t=datetime.fromtimestamp(close_ts, TZ), side=side, entry=candle.c,
                poi=key, hit=hitp, dist=dist, also6=dist <= 6.0,
                hit_t=datetime.fromtimestamp(ht, TZ) if ht else None,
                mae=mae, mfe=mfe, pts=pts,
            ))
            spent[key] = rpx
            in_until = ht
        return by_day, skips

    for arm in (6.0, 15.0):
        by_day, skips = run(arm)
        tot_n = tot_tp = tot_sl = tot_pnl = tot6 = 0
        print("\n################ ARM", arm, "################", flush=True)
        for day in DAYS:
            trades = by_day[day]
            pnl = 0.0
            print("--------", day.strftime("%a"), "ARM", arm, "--------", flush=True)
            for tr in trades:
                dol = tr["pts"] * 2 * QTY
                pnl += dol
                tag = "<=6" if tr["also6"] else "7-15"
                print("%s %s %.2f %s dist=%.1f %s %s %+.0fpt $%+.0f" % (
                    tr["t"].strftime("%H:%M"), tr["side"], tr["entry"], tr["poi"],
                    tr["dist"], tag, tr["hit"], tr["pts"], dol), flush=True)
            n6 = sum(1 for t in trades if t["also6"])
            print("n=%d (of which dist<=6: %d) TP=%d SL=%d $%+.0f" % (
                len(trades), n6,
                sum(1 for t in trades if t["hit"] == "TP40"),
                sum(1 for t in trades if t["hit"] == "SL20"),
                pnl), flush=True)
            tot_n += len(trades)
            tot_tp += sum(1 for t in trades if t["hit"] == "TP40")
            tot_sl += sum(1 for t in trades if t["hit"] == "SL20")
            tot_pnl += pnl
            tot6 += n6
        print("ARM", arm, "TOTAL n=%d dist<=6=%d TP=%d SL=%d $%+.0f" % (
            tot_n, tot6, tot_tp, tot_sl, tot_pnl), flush=True)
        print("skips", dict(skips), flush=True)


if __name__ == "__main__":
    main()
