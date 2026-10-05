#!/usr/bin/env python3
"""Score 2026-10-05 with the box rules and with Ferrari 458.

One tape pull. Does not start or edit the bot.
"""
import json
import os
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
DAY = date(2026, 10, 5)
STOP, TP = 20.0, 40.0
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
SKIP_F = ("ONH", "ONL", "EMA", "OPEN", "YEL", "HEALTHCHECK")
SKIP_B = ("ONH", "ONL", "EMA", "OPEN")


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


def nrec(client, sym, start, end):
    try:
        return int(client.metadata.get_record_count(
            dataset="GLBX.MDP3", symbols=sym, schema="trades",
            start=iso(start), end=iso(end), stype_in="raw_symbol",
        ))
    except Exception as e:
        print("COUNT_FAIL", sym, str(e)[:140], flush=True)
        return -1


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
    """mode box: 15 on the hold, any rail can flip. mode ferrari: 15 on the fill, named side."""
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
            side, entry, name, rail, opened, dist, atr = pos
            if side == "Buy":
                hit_stop = b["l"] <= entry - STOP
                hit_tp = b["h"] >= entry + TP
                pts_close = b["c"] - entry
            else:
                hit_stop = b["h"] >= entry + STOP
                hit_tp = b["l"] <= entry - TP
                pts_close = entry - b["c"]
            if hit_stop or hit_tp:
                pts, how = (-STOP, "stop") if hit_stop else (TP, "tp")
                out.append((opened, side, name, rail, entry, pts, how, dist, atr))
                pos = None
            elif dt.hour == 15 and dt.minute >= 59:
                out.append((opened, side, name, rail, entry, pts_close, "eod", dist, atr))
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
            if best is None or dist < best[0]:
                best = (dist, side, name, rail)
        if best:
            dist, side, name, rail = best
            pos = (side, b["c"], name, rail, dt, dist, atr)
    return out


def show(label, rows):
    print(label, flush=True)
    if not rows:
        print("  none", flush=True)
    pts = 0.0
    for t, side, name, rail, entry, p, how, dist, atr in rows:
        pts += p
        print(
            f"  {t:%H:%M} {side} {name}@{rail:.2f} @{entry:.2f} {p:+.1f} {how} dist {dist:.2f} atr {atr:.2f}",
            flush=True,
        )
    w = sum(1 for r in rows if r[5] > 0)
    print(f"  N {len(rows)} W {w} L {len(rows) - w} {pts:+.1f}", flush=True)


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    client = db.Historical(key)
    start = datetime(DAY.year, DAY.month, DAY.day, 9, 30, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    end = datetime(DAY.year, DAY.month, DAY.day, 16, 0, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
    print("DAY", DAY.isoformat(), "USE MNQZ6", "BOT NOT TOUCHED", flush=True)
    bars, n = pull_bars(client, "MNQZ6", start, end)
    buy = sum(b["buy"] for b in bars.values())
    sell = sum(b["sell"] for b in bars.values())
    print(f"TRADES {n} BARS {len(bars)} TAPE B {buy:.0f} A {sell:.0f}", flush=True)
    show("BOX", score(load_alerts(SKIP_B), bars, "box"))
    show("FERRARI", score(load_alerts(SKIP_F), bars, "ferrari"))


if __name__ == "__main__":
    main()
