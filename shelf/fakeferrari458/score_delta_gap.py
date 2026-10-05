#!/usr/bin/env python3
"""How big was the delta gap on the trades this book already took?

Long: hold gap = sells minus buys. Lift gap = buys minus sells.
Short is the flip. The live gate only needs each gap to be greater than 0.
One extra contract passes. This does not change the bot.
"""
import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/delta_gap.tsv"
FIRST, LAST = date(2026, 8, 24), date(2026, 10, 5)
STOP, TP = 20.0, 40.0
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
SKIP_F = ("ONH", "ONL", "EMA", "OPEN", "YEL", "HEALTHCHECK")
SKIP_B = ("ONH", "ONL", "EMA", "OPEN")
CUTS = (1, 50, 100, 250, 500, 1000, 2000)


def envload():
    for raw in (ROOT / ".env").read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def kind_of(name, skip):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in skip):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def load_alerts(skip):
    out = []
    for ln in POI.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        kind = kind_of(o.get("poi_name") or o.get("type") or "", skip)
        if kind is None:
            continue
        try:
            px = round(float(o.get("price") or 0), 2)
            recv = float(o.get("recv_ts") or o.get("ts") or 0)
        except Exception:
            continue
        if px <= 0 or recv <= 0:
            continue
        if recv > 1e14:
            recv /= 1000.0
        if recv > 1e12:
            recv /= 1000.0
        out.append((recv, kind, px))
    out.sort()
    return out


def px_of(rec):
    raw = getattr(rec, "pretty_price", None)
    if raw is not None:
        try:
            return float(raw)
        except Exception:
            pass
    x = float(rec.price)
    return x / 1e9 if abs(x) > 1e7 else x


def signed_size(rec):
    sz = int(getattr(rec, "size", 0) or 0)
    s = str(getattr(rec, "side", "") or "").upper()
    if s in ("B", "BUY", "BID"):
        return sz
    if s in ("A", "SELL", "ASK"):
        return -sz
    return 0


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def symbol_for(d):
    return "MNQU6" if d < date(2026, 9, 15) else "MNQZ6"


def pull_bars(client, sym, start, end):
    data = client.timeseries.get_range(
        dataset="GLBX.MDP3", symbols=sym, stype_in="raw_symbol", schema="trades",
        start=iso(start), end=iso(end),
    )
    bars, n = {}, 0
    for rec in data:
        n += 1
        if n % 500000 == 0:
            print("TRADES", n, flush=True)
        ts = getattr(rec, "ts_event", None)
        if ts is None:
            continue
        px = px_of(rec)
        if px <= 0:
            continue
        dt = datetime.fromtimestamp(ts / 1e9, TZ).replace(second=0, microsecond=0)
        sg = signed_size(rec)
        b = bars.get(dt)
        if b is None:
            b = bars[dt] = {"o": px, "h": px, "l": px, "c": px, "buy": 0.0, "sell": 0.0}
        b["h"] = max(b["h"], px)
        b["l"] = min(b["l"], px)
        b["c"] = px
        if sg > 0:
            b["buy"] += sg
        elif sg < 0:
            b["sell"] += -sg
    return bars, n


def pull_safe(client, sym, start, end, d):
    for attempt in (1, 2):
        try:
            print(f"PULL {d.isoformat()} {sym} try {attempt}", flush=True)
            return pull_bars(client, sym, start, end)
        except Exception as e:
            print("PULL_FAIL", d.isoformat(), sym, str(e)[:140], flush=True)
    return None


def atr_of(keys, bars, i, gaps_ok):
    if i < 14:
        return None
    use = keys[i - 14:i + 1]
    for j in range(1, 15):
        gap = (use[j] - use[j - 1]).total_seconds()
        if gaps_ok:
            if gap <= 0:
                return None
        elif gap != 60:
            return None
    trs = []
    for j in range(1, 15):
        prev, b = bars[use[j - 1]], bars[use[j]]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev["c"]), abs(b["l"] - prev["c"])))
    return sum(trs) / 14.0


def score(alerts, bars, mode):
    keys = sorted(bars)
    ai, active, pos, out = 0, {}, None, []
    for i, dt in enumerate(keys):
        b = bars[dt]
        asof = dt.timestamp() + 60
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        if pos:
            side, entry, meta = pos
            if side == "Buy":
                hit_stop = b["l"] <= entry - STOP
                hit_tp = b["h"] >= entry + TP
                pts_close = b["c"] - entry
            else:
                hit_stop = b["h"] >= entry + STOP
                hit_tp = b["l"] <= entry - TP
                pts_close = entry - b["c"]
            if hit_stop or hit_tp:
                meta["pts"] = -STOP if hit_stop else TP
                out.append(meta)
                pos = None
            elif dt.hour == 15 and dt.minute >= 59:
                meta["pts"] = pts_close
                out.append(meta)
                pos = None
            continue
        m = dt.hour * 60 + dt.minute
        if i < 1 or not (10 * 60 <= m < 16 * 60) or m >= 15 * 60 + 59:
            continue
        hold_dt = keys[i - 1]
        if (dt - hold_dt).total_seconds() != 60:
            continue
        hold = bars[hold_dt]
        atr = atr_of(keys, bars, i, gaps_ok=(mode == "box"))
        if atr is None or atr >= 15:
            continue
        best = None
        for name, rail in active.items():
            if not (hold["l"] <= rail <= hold["h"]):
                continue
            if mode == "box":
                dist = abs(hold["c"] - rail)
                if dist > 15:
                    continue
                if hold["c"] > rail and hold["sell"] > hold["buy"]:
                    if not (b["buy"] > b["sell"] and b["c"] > hold["c"] and b["c"] > rail):
                        continue
                    side = "Buy"
                elif hold["c"] < rail and hold["buy"] > hold["sell"]:
                    if not (b["sell"] > b["buy"] and b["c"] < hold["c"] and b["c"] < rail):
                        continue
                    side = "Sell"
                else:
                    continue
            else:
                if name in SUPPORT or (name in BARE and hold["c"] > rail):
                    side = "Buy"
                elif name in RESIST or (name in BARE and hold["c"] < rail):
                    side = "Sell"
                else:
                    continue
                if side == "Buy":
                    ok = (
                        hold["c"] > rail and hold["sell"] > hold["buy"]
                        and b["buy"] > b["sell"] and b["c"] > hold["c"] and b["c"] > rail
                    )
                else:
                    ok = (
                        hold["c"] < rail and hold["buy"] > hold["sell"]
                        and b["sell"] > b["buy"] and b["c"] < hold["c"] and b["c"] < rail
                    )
                if not ok:
                    continue
                dist = abs(b["c"] - rail)
                if dist > 15:
                    continue
            if side == "Buy":
                hold_gap = hold["sell"] - hold["buy"]
                lift_gap = b["buy"] - b["sell"]
                hold_want, hold_other = hold["sell"], hold["buy"]
                lift_want, lift_other = b["buy"], b["sell"]
            else:
                hold_gap = hold["buy"] - hold["sell"]
                lift_gap = b["sell"] - b["buy"]
                hold_want, hold_other = hold["buy"], hold["sell"]
                lift_want, lift_other = b["sell"], b["buy"]
            meta = {
                "time": dt, "side": side, "poi": f"{name}@{rail:.2f}",
                "entry": b["c"], "hold_gap": hold_gap, "lift_gap": lift_gap,
                "hold_want": hold_want, "hold_other": hold_other,
                "lift_want": lift_want, "lift_other": lift_other,
            }
            if best is None or dist < best[0]:
                best = (dist, side, b["c"], meta)
        if best:
            pos = best[1:]
    return out


def med(xs):
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2


def line(trades):
    if not trades:
        return "n 0"
    w = sum(1 for t in trades if t["pts"] > 0)
    l = sum(1 for t in trades if t["pts"] < 0)
    pts = sum(t["pts"] for t in trades)
    wr = 100.0 * w / len(trades)
    return f"n {len(trades)} W {w} L {l} WR {wr:.0f}% {pts:+.1f} pts ${pts * 10:+.0f}"


def report(name, trades):
    print("---", name, line(trades), flush=True)
    wins = [t for t in trades if t["pts"] > 0]
    losses = [t for t in trades if t["pts"] < 0]
    print(
        f"  wins   med hold {med([t['hold_gap'] for t in wins])}  lift {med([t['lift_gap'] for t in wins])}",
        flush=True,
    )
    print(
        f"  losses med hold {med([t['hold_gap'] for t in losses])}  lift {med([t['lift_gap'] for t in losses])}",
        flush=True,
    )
    thin = [t for t in trades if t["hold_gap"] < 100 or t["lift_gap"] < 100]
    print(f"  thinner than 100 on either minute: {len(thin)}", flush=True)
    print("  BOTH minutes at least", flush=True)
    for cut in CUTS:
        sub = [t for t in trades if t["hold_gap"] >= cut and t["lift_gap"] >= cut]
        print(f"    {cut:5}  {line(sub)}", flush=True)
    print("  HOLD minute at least", flush=True)
    for cut in CUTS:
        sub = [t for t in trades if t["hold_gap"] >= cut]
        print(f"    {cut:5}  {line(sub)}", flush=True)
    print("  LIFT minute at least", flush=True)
    for cut in CUTS:
        sub = [t for t in trades if t["lift_gap"] >= cut]
        print(f"    {cut:5}  {line(sub)}", flush=True)


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    client = db.Historical(key)
    box_alerts = load_alerts(SKIP_B)
    fer_alerts = load_alerts(SKIP_F)
    print("BOT NOT TOUCHED", flush=True)
    print("GAP = contracts on the side the rule wanted, minus the other side.", flush=True)
    box, fer = [], []
    rows = ["book\ttime\tside\tpoi\tentry\thold_gap\tlift_gap\thold_want\thold_other\tlift_want\tlift_other\tpts"]
    d = FIRST
    while d <= LAST:
        if d.weekday() < 5:
            start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
            end = datetime(d.year, d.month, d.day, 16, 0, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
            got = pull_safe(client, symbol_for(d), start, end, d)
            if got is None:
                print(d.isoformat(), "FAIL", flush=True)
            else:
                bars, n = got
                for mode, bucket in (("box", box), ("ferrari", fer)):
                    alerts = box_alerts if mode == "box" else fer_alerts
                    for t in score(alerts, bars, mode):
                        t["book"] = mode
                        bucket.append(t)
                        rows.append(
                            f"{mode}\t{t['time'].strftime('%Y-%m-%d %H:%M')}\t{t['side']}\t{t['poi']}\t"
                            f"{t['entry']:.2f}\t{t['hold_gap']:.0f}\t{t['lift_gap']:.0f}\t"
                            f"{t['hold_want']:.0f}\t{t['hold_other']:.0f}\t"
                            f"{t['lift_want']:.0f}\t{t['lift_other']:.0f}\t{t['pts']:+.1f}"
                        )
                print(f"{d.isoformat()} trades {n} box {len(box)} fer {len(fer)}", flush=True)
        d += timedelta(days=1)
    OUT.write_text("\n".join(rows) + "\n")
    report("BOX", box)
    report("FERRARI", fer)
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
