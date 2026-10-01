#!/usr/bin/env python3
"""Paper only. No orders. Does not change the live bot.

The 74 trades from the fade book, 10:00-16:00, Sep 14 through Oct 1.
For each wick, pull the resting book (top 10 prices) from 10 seconds
before the tip to 5 seconds after. Not the whole history.

sit   size on the defending side within 1 point of the rail, just before the wick
left  that size on the first book update after the wick
back  the most that size came back to in the next 5 seconds
thin  the least it fell to in those 5 seconds
wick  defending size within 1 point of the tip itself, just before
slip  how far the best defending price moved against the trade in those 5 seconds

A long defends with the bid. A short defends with the offer.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
RAW = """
09-14 10:41 Buy  PDL@29468.25 L -20.0 stop tip 10:40:51 29462.50 push -27 f5 -56 f10 -81 f20 -308 o5 +9.75 o10 +10.50 o20 +9.75
09-14 10:51 Buy  PDL@29468.25 L -20.0 stop tip 10:50:48 29461.00 push 191 f5 65 f10 59 f20 67 o5 +4.50 o10 +5.25 o20 +5.00
09-14 13:01 Sell H1L@29588.25 W +40.0 tp tip 13:00:27 29602.25 push 297 f5 109 f10 129 f20 192 o5 +15.25 o10 +15.25 o20 +17.75
09-15 10:13 Sell PDH@29275.25 L -20.0 stop tip 10:12:53 29275.25 push 321 f5 14 f10 21 f20 29 o5 +5.75 o10 +5.75 o20 +3.75
09-15 10:24 Sell H4@29296.00 W +40.0 tp tip 10:23:56 29296.50 push 333 f5 24 f10 130 f20 132 o5 +4.75 o10 +7.00 o20 +6.50
09-15 11:29 Sell H4@29296.00 W +40.0 tp tip 11:28:39 29300.75 push 124 f5 -9 f10 81 f20 48 o5 +3.25 o10 +7.00 o20 +5.00
09-15 12:28 Sell H4@29255.50 L -20.0 stop tip 12:27:30 29255.50 push 6 f5 -76 f10 -77 f20 -92 o5 +2.00 o10 +2.50 o20 +2.25
09-15 13:01 Buy  H4@29255.50 L -20.0 stop tip 13:00:39 29255.00 push 395 f5 -30 f10 -30 f20 21 o5 +1.75 o10 +1.75 o20 +3.50
09-15 15:10 Buy  PDH@29275.25 L -20.0 stop tip 15:09:18 29272.00 push 60 f5 15 f10 -14 f20 -42 o5 +2.00 o10 +2.00 o20 +2.50
09-15 15:36 Sell PDH@29275.25 L -4.0 eod tip 15:35:21 29277.00 push 75 f5 -27 f10 -31 f20 6 o5 +0.75 o10 +0.50 o20 +2.25
09-16 10:38 Buy  H4@29484.25 W +40.0 tp tip 10:37:45 29483.25 push 457 f5 49 f10 145 f20 118 o5 +0.25 o10 +5.00 o20 +5.00
09-16 10:51 Sell H1@29527.50 W +40.0 tp tip 10:50:16 29532.00 push 136 f5 8 f10 -5 f20 -93 o5 +3.00 o10 +6.75 o20 +8.75
09-16 11:07 Sell H4@29484.25 W +40.0 tp tip 11:06:05 29486.00 push 145 f5 19 f10 79 f20 114 o5 +3.50 o10 +4.50 o20 +4.00
09-16 13:01 Sell PDL@29468.25 L -20.0 stop tip 13:00:07 29530.00 push 456 f5 -135 f10 -177 f20 -72 o5 +45.00 o10 +52.25 o20 +69.00
09-16 13:06 Sell H1@29527.50 L -20.0 stop tip 13:05:32 29533.00 push 233 f5 136 f10 186 f20 335 o5 +13.25 o10 +10.00 o20 +17.00
09-16 13:20 Sell PDL@29468.25 L -20.0 stop tip 13:19:52 29483.25 push 690 f5 -45 f10 -19 f20 401 o5 +17.75 o10 +15.75 o20 +34.00
09-16 13:34 Sell H4@29389.75 W +40.0 tp tip 13:33:33 29408.75 push 380 f5 184 f10 190 f20 138 o5 +34.50 o10 +29.75 o20 +27.00
09-16 13:56 Sell H4@29296.00 L -20.0 stop tip 13:55:33 29324.50 push 866 f5 195 f10 108 f20 181 o5 +16.25 o10 +9.75 o20 +16.00
09-16 14:05 Sell PDH@29275.25 W +40.0 tp tip 14:04:13 29303.50 push 334 f5 47 f10 130 f20 98 o5 +11.00 o10 +15.75 o20 +18.75
09-16 14:34 Buy  H1H@29160.25 L -20.0 stop tip 14:33:43 29149.50 push 376 f5 20 f10 145 f20 138 o5 +7.00 o10 +16.50 o20 +20.25
09-16 14:43 Buy  H1H@29160.25 W +40.0 tp tip 14:42:06 29151.00 push 664 f5 -182 f10 -223 f20 62 o5 +8.25 o10 +4.75 o20 +18.25
09-16 14:55 Sell H4@29242.00 L -20.0 stop tip 14:54:43 29248.50 push 297 f5 23 f10 77 f20 448 o5 +5.00 o10 +2.25 o20 +12.75
09-16 15:07 Sell PDH@29275.25 L -2.0 eod tip 15:06:47 29279.50 push 58 f5 51 f10 30 f20 102 o5 +3.25 o10 +5.50 o20 +5.50
09-17 10:39 Buy  H4@29714.25 W +40.0 tp tip 10:38:18 29710.75 push 135 f5 -67 f10 -37 f20 20 o5 +1.75 o10 +2.00 o20 +5.75
09-17 13:38 Sell H4@29714.25 L -20.0 stop tip 13:37:54 29715.75 push -86 f5 5 f10 38 f20 56 o5 +2.00 o10 +4.25 o20 +4.50
09-17 14:53 Buy  H1@29774.75 L -20.0 stop tip 14:52:34 29773.75 push 260 f5 87 f10 108 f20 -17 o5 +2.75 o10 +5.75 o20 +6.75
09-17 15:32 Buy  H4@29714.25 W +2.8 eod tip 15:31:00 29714.00 push -47 f5 9 f10 32 f20 11 o5 +1.50 o10 +3.25 o20 +2.50
09-18 11:15 Sell H4@29714.25 W +40.0 tp tip 11:14:35 29714.50 push 39 f5 -43 f10 -6 f20 -52 o5 +0.75 o10 +3.00 o20 +2.75
09-18 14:14 Buy  H4@29834.00 W +40.0 tp tip 14:13:55 29833.25 push 247 f5 -58 f10 -33 f20 105 o5 -0.50 o10 +1.25 o20 +0.25
09-18 14:51 Sell H1@29882.50 L -20.0 stop tip 14:50:11 29889.00 push 1248 f5 185 f10 323 f20 540 o5 +6.25 o10 +10.50 o20 +13.00
09-18 15:02 Buy  H4@29928.25 W +40.0 tp tip 15:01:25 29923.25 push 86 f5 34 f10 82 f20 43 o5 +4.75 o10 +7.50 o20 +3.75
09-21 10:09 Buy  H4@30567.50 W +40.0 tp tip 10:08:31 30567.00 push 151 f5 -23 f10 -64 f20 -14 o5 +2.50 o10 +3.75 o20 +2.25
09-21 10:51 Sell H4@30634.00 L -20.0 stop tip 10:50:49 30639.50 push 1914 f5 103 f10 344 f20 612 o5 +15.50 o10 +17.75 o20 +17.75
09-21 13:13 Buy  H4@30725.00 W +40.0 tp tip 13:12:52 30724.25 push 6 f5 -21 f10 -4 f20 61 o5 +0.75 o10 +1.75 o20 +2.75
09-22 13:40 Buy  H4@30967.50 W +40.0 tp tip 13:39:29 30966.00 push 93 f5 -161 f10 -110 f20 -89 o5 +1.75 o10 +4.00 o20 +5.00
09-23 10:52 Buy  H4@30725.00 L -20.0 stop tip 10:51:43 30723.00 push 128 f5 -34 f10 -65 f20 -23 o5 +7.00 o10 +7.25 o20 +7.25
09-23 12:20 Sell H4@30725.00 W +40.0 tp tip 12:19:52 30725.50 push 336 f5 -73 f10 132 f20 372 o5 +3.00 o10 +8.25 o20 +7.00
09-24 10:56 Sell H4@30567.50 W +40.0 tp tip 10:55:19 30570.00 push 205 f5 -7 f10 64 f20 67 o5 +4.25 o10 +5.00 o20 +3.50
09-24 12:37 Sell H4@30725.00 W +40.0 tp tip 12:36:36 30726.75 push 177 f5 21 f10 -0 f20 -82 o5 +7.25 o10 +4.50 o20 +5.50
09-24 13:44 Buy  H4@30725.00 W +40.0 tp tip 13:43:44 30724.50 push 9 f5 -23 f10 14 f20 133 o5 +2.75 o10 +5.50 o20 +11.25
09-24 14:12 Buy  H4@30725.00 W +40.0 tp tip 14:11:45 30723.25 push 43 f5 -96 f10 27 f20 63 o5 +5.75 o10 +8.75 o20 +7.00
09-24 15:07 Buy  H4@30725.00 L -20.0 stop tip 15:06:01 30723.75 push 48 f5 -5 f10 -5 f20 7 o5 +3.00 o10 +3.75 o20 +4.00
09-24 15:58 Buy  H4@30725.00 L -0.2 eod tip 15:57:31 30723.00 push 138 f5 -10 f10 -12 f20 -36 o5 +3.25 o10 +4.25 o20 +7.75
09-25 10:00 Sell H4@30827.50 L -20.0 stop tip 09:59:50 30829.50 push 471 f5 77 f10 90 f20 263 o5 +4.75 o10 +7.50 o20 +9.00
09-25 11:35 Buy  H4@30878.50 L -20.0 stop tip 11:34:02 30861.75 push 2383 f5 -543 f10 -453 f20 -439 o5 +31.00 o10 +35.00 o20 +24.50
09-25 11:52 Buy  H4@30878.50 L -20.0 stop tip 11:51:34 30877.50 push 63 f5 21 f10 52 f20 39 o5 +4.75 o10 +6.50 o20 +8.25
09-25 13:28 Buy  H4@30878.50 L -20.0 stop tip 13:27:53 30873.00 push 1123 f5 -16 f10 -17 f20 -22 o5 +6.50 o10 +6.00 o20 +1.50
09-28 11:41 Sell H4@30639.75 W +40.0 tp tip 11:40:17 30645.00 push 356 f5 -89 f10 18 f20 176 o5 +3.50 o10 +12.25 o20 +17.00
09-28 12:12 Sell PDL@30684.00 L -20.0 stop tip 12:11:55 30688.00 push 527 f5 269 f10 133 f20 327 o5 +9.75 o10 +1.00 o20 +27.50
09-28 12:33 Sell H4@30639.75 W +40.0 tp tip 12:32:17 30643.50 push 348 f5 -31 f10 -5 f20 35 o5 +2.75 o10 +4.25 o20 +5.75
09-28 13:40 Buy  H4@30582.50 W +40.0 tp tip 13:39:47 30578.75 push 495 f5 89 f10 139 f20 207 o5 +4.00 o10 +5.50 o20 +3.25
09-28 14:20 Buy  H4@30582.50 L -20.0 stop tip 14:19:43 30582.25 push 278 f5 -45 f10 -32 f20 -3 o5 +2.50 o10 +5.50 o20 +5.25
09-29 10:07 Sell H4@30639.75 L -20.0 stop tip 10:06:40 30641.50 push 304 f5 42 f10 66 f20 149 o5 +5.75 o10 +8.75 o20 +12.00
09-29 10:22 Sell H4@30582.50 W +40.0 tp tip 10:21:52 30586.50 push 229 f5 -59 f10 14 f20 -76 o5 +2.25 o10 +4.25 o20 +2.00
09-29 10:43 Sell H4@30582.50 L -20.0 stop tip 10:42:46 30592.00 push 465 f5 -59 f10 -36 f20 745 o5 +3.75 o10 +6.25 o20 +22.50
09-29 10:49 Buy  H4@30582.50 L -20.0 stop tip 10:48:18 30572.00 push 613 f5 -71 f10 5 f20 -6 o5 +8.25 o10 +14.75 o20 +15.75
09-29 11:02 Sell H4@30582.50 L -20.0 stop tip 11:01:27 30583.75 push 174 f5 -8 f10 -1 f20 -3 o5 +2.75 o10 +5.50 o20 +8.25
09-29 12:36 Sell H4@30582.50 W +40.0 tp tip 12:35:44 30584.75 push 240 f5 18 f10 97 f20 191 o5 +3.00 o10 +3.25 o20 +6.75
09-29 13:01 Sell H4@30582.50 L -20.0 stop tip 13:00:33 30587.25 push 1091 f5 -106 f10 -122 f20 -136 o5 +13.00 o10 +16.75 o20 +10.50
09-29 13:32 Buy  H4@30582.50 W +40.0 tp tip 13:31:43 30582.00 push 735 f5 -37 f10 55 f20 33 o5 +2.75 o10 +8.00 o20 +6.00
09-29 13:51 Buy  H4@30639.75 L -20.0 stop tip 13:50:40 30633.75 push 7 f5 -40 f10 18 f20 63 o5 +0.75 o10 +1.75 o20 +6.50
09-29 14:24 Sell H4@30639.75 L -20.0 stop tip 14:23:22 30643.00 push 281 f5 -68 f10 -98 f20 -82 o5 +3.00 o10 +2.75 o20 +2.75
09-30 10:13 Buy  H4@30827.50 L -20.0 stop tip 10:12:50 30813.75 push 1385 f5 55 f10 230 f20 365 o5 +12.25 o10 +19.50 o20 +26.50
09-30 10:45 Buy  H4@30827.50 W +40.0 tp tip 10:44:50 30825.50 push 785 f5 -149 f10 -180 f20 -82 o5 +4.25 o10 +7.25 o20 +9.00
09-30 10:54 Sell H4@30878.50 L -20.0 stop tip 10:53:55 30879.50 push 86 f5 2 f10 -11 f20 -28 o5 +3.75 o10 +3.75 o20 +2.00
09-30 11:33 Buy  H4@30827.50 L -20.0 stop tip 11:32:37 30826.00 push 193 f5 -39 f10 -54 f20 -113 o5 +5.00 o10 +4.25 o20 +3.00
09-30 13:27 Sell H4@30827.50 L -20.0 stop tip 13:26:51 30829.25 push 762 f5 -54 f10 -25 f20 10 o5 +2.25 o10 +1.75 o20 +1.50
09-30 14:56 Buy  H4@30725.00 L -20.0 stop tip 14:55:08 30700.25 push 1807 f5 235 f10 314 f20 466 o5 +6.25 o10 +6.00 o20 +10.50
09-30 15:08 Buy  H4@30682.25 W +40.0 tp tip 15:07:45 30665.00 push 496 f5 90 f10 82 f20 267 o5 +7.00 o10 +11.00 o20 +20.00
09-30 15:21 Buy  H4@30725.00 L -20.0 stop tip 15:20:30 30715.25 push 141 f5 -4 f10 -110 f20 -88 o5 +1.75 o10 +6.75 o20 +10.00
10-01 10:19 Buy  H4@30639.75 L -20.0 stop tip 10:18:57 30639.25 push 257 f5 -37 f10 165 f20 55 o5 -0.75 o10 +7.50 o20 +4.00
10-01 10:43 Sell H4@30582.50 L -20.0 stop tip 10:42:57 30584.00 push 158 f5 2 f10 -5 f20 73 o5 +5.00 o10 +5.00 o20 +12.75
10-01 11:09 Sell H4@30639.75 L -20.0 stop tip 11:08:37 30641.00 push 231 f5 13 f10 158 f20 208 o5 +6.25 o10 +12.50 o20 +13.75
10-01 11:41 Sell H4@30640.25 L -20.0 stop tip 11:40:14 30641.00 push 301 f5 87 f10 66 f20 21 o5 +8.50 o10 +6.50 o20 +6.00
"""
PAT = re.compile(
    r"(\d\d-\d\d) \d\d:\d\d (Buy|Sell)\s+\S+@([\d.]+) ([WL]) ([+-][\d.]+) \w+ "
    r"tip (\d\d:\d\d:\d\d) ([\d.]+)"
)


def envload():
    for raw in (ROOT / ".env").read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def trades():
    out = []
    for m in PAT.finditer(RAW):
        day, side, rail, wl, pts, tip, tip_px = m.groups()
        stamp = datetime.strptime(f"2026-{day} {tip}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        out.append({
            "day": day,
            "tip": tip,
            "ts": stamp.timestamp(),
            "side": side,
            "rail": float(rail),
            "wl": wl,
            "pts": float(pts),
            "tip_px": float(tip_px),
            "card": day <= "09-25",
        })
    return out


def px_of(raw):
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    if abs(x) > 1e7:
        x /= 1e9
    if x <= 0 or x > 1e6:
        return None
    return x


def book_of(rec):
    levels = getattr(rec, "levels", None)
    if levels is None:
        return None
    bids, asks = [], []
    for i in range(10):
        try:
            lvl = levels[i]
        except Exception:
            break
        bp = px_of(getattr(lvl, "bid_px", None))
        ap = px_of(getattr(lvl, "ask_px", None))
        try:
            bs = int(getattr(lvl, "bid_sz", 0) or 0)
            az = int(getattr(lvl, "ask_sz", 0) or 0)
        except (TypeError, ValueError):
            bs, az = 0, 0
        if bp is not None and bs > 0:
            bids.append((bp, bs))
        if ap is not None and az > 0:
            asks.append((ap, az))
    if not bids and not asks:
        return None
    return bids, asks


def band(levels, side, px, width=1.0):
    if side == "Buy":
        return sum(sz for p, sz in levels[0] if px - width <= p <= px + width)
    return sum(sz for p, sz in levels[1] if px - width <= p <= px + width)


def best_defend(levels, side):
    if side == "Buy":
        return max((p for p, sz in levels[0] if sz > 0), default=None)
    return min((p for p, sz in levels[1] if sz > 0), default=None)


def pull_one(client, row):
    tip = row["ts"]
    start = datetime.fromtimestamp(tip - 10, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    end = datetime.fromtimestamp(tip + 6, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="mbp-10",
        start=start,
        end=end,
    )
    before = after = None
    back = thin = None
    worst = None
    n = 0
    for rec in data:
        n += 1
        got = book_of(rec)
        if got is None:
            continue
        ts = rec.ts_event / 1e9
        if ts <= tip:
            before = got
            continue
        if ts > tip + 5:
            break
        if after is None:
            after = got
        sit = band(got, row["side"], row["rail"])
        back = sit if back is None else max(back, sit)
        thin = sit if thin is None else min(thin, sit)
        bd = best_defend(got, row["side"])
        if bd is not None:
            if worst is None:
                worst = bd
            elif row["side"] == "Buy":
                worst = min(worst, bd)
            else:
                worst = max(worst, bd)
    if before is None:
        return None, n
    sit = band(before, row["side"], row["rail"])
    wick = band(before, row["side"], row["tip_px"])
    left = None if after is None else band(after, row["side"], row["rail"])
    base = best_defend(before, row["side"])
    slip = None
    if base is not None and worst is not None:
        slip = (base - worst) if row["side"] == "Buy" else (worst - base)
        if slip < 0:
            slip = 0.0
    top = best_defend(before, row["side"])
    return {
        "sit": sit, "left": left, "back": back, "thin": thin,
        "wick": wick, "slip": slip, "top": top, "n": n,
    }, n


def med(rows, key):
    xs = sorted(r[key] for r in rows if r.get(key) is not None)
    if not xs:
        return None
    n = len(xs)
    mid = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    return mid


def num(v, p=0):
    if v is None:
        return "na"
    return f"{v:.{p}f}" if p else f"{v:.0f}"


def show(row):
    b = row.get("book")
    if not b:
        print(f"{row['day']} {row['tip']} {row['side']:4} {row['wl']} NO_BOOK", flush=True)
        return
    print(
        f"{row['day']} {row['tip']} {row['side']:4} {row['wl']} {row['pts']:+.1f} "
        f"sit {num(b['sit'])} left {num(b['left'])} back {num(b['back'])} "
        f"thin {num(b['thin'])} wick {num(b['wick'])} slip {num(b['slip'], 2)}",
        flush=True,
    )


def rate(rows, pred):
    xs = [r for r in rows if r.get("book")]
    if not xs:
        return "na"
    return f"{sum(1 for r in xs if pred(r['book']))}/{len(xs)}"


def summarize(title, rows):
    got = [r for r in rows if r.get("book")]
    wins = [r for r in got if r["pts"] > 0]
    losses = [r for r in got if r["pts"] < 0]
    print(f"{title} TRADES {len(got)} W {len(wins)} L {len(losses)}", flush=True)
    for label, grp in (("WINS", wins), ("LOSS", losses)):
        if not grp:
            continue
        books = [r["book"] for r in grp]
        print(
            f"{title}_{label} n {len(grp)} med sit {num(med(books, 'sit'))} "
            f"left {num(med(books, 'left'))} back {num(med(books, 'back'))} "
            f"thin {num(med(books, 'thin'))} wick {num(med(books, 'wick'))} "
            f"slip {num(med(books, 'slip'), 2)} "
            f"refilled {rate(grp, lambda b: b['back'] is not None and b['sit'] is not None and b['back'] > b['sit'])} "
            f"vanished {rate(grp, lambda b: b['thin'] == 0)} "
            f"held {rate(grp, lambda b: b['slip'] is not None and b['slip'] <= 2)}",
            flush=True,
        )


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    rows = trades()
    print("TRADES", len(rows), flush=True)
    if len(rows) != 74:
        raise SystemExit(f"expected 74 trades, got {len(rows)}")
    print("BOT NOT CHANGED", flush=True)
    print("BOOK mbp-10 around each wick only", flush=True)
    client = db.Historical(key)
    for i, row in enumerate(rows, 1):
        print(f"PULL {i}/74 {row['day']} {row['tip']}", flush=True)
        try:
            got, n = pull_one(client, row)
        except Exception as e:
            msg = str(e)[:240]
            low = msg.lower()
            if any(x in low for x in ("auth", "401", "403", "license", "subscription", "permission")):
                print("STOP", type(e).__name__, msg, flush=True)
                raise SystemExit(1)
            print("NO_BOOK", row["day"], row["tip"], type(e).__name__, msg, flush=True)
            continue
        row["book"] = got
        if i == 1 and got and got.get("top") is not None:
            print(f"SAMPLE top {got['top']:.2f} sit {got['sit']:.0f} updates {n}", flush=True)
        show(row)
    print("DONE", flush=True)
    summarize("ALL", rows)
    summarize("CARD", [r for r in rows if r["card"]])
    summarize("AFTER", [r for r in rows if not r["card"]])


if __name__ == "__main__":
    main()
