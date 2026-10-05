#!/usr/bin/env python3
"""Ferrari 458. The rules that scored +747.8 on 2026-10-05.

Does not start or edit the bot. Does not touch live_77.py.
MNQU6 until MNQZ6 has more trades that day.
Named low is only a buy. Named high is only a sell.
The 15 points are on the entry close.
ATR minutes must be consecutive. Under 15.
Stop wins if stop and target are in the same minute.
"""
import gc
import json
import os
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import databento as db

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/ferrari_458_fills.tsv"
STOP, TP = 20.0, 40.0
SKIP = ("ONH", "ONL", "EMA", "OPEN", "YEL", "HEALTHCHECK")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
FROZEN = {
    "2026-08-24": 0, "2026-08-25": 0, "2026-08-26": 0, "2026-08-27": 0, "2026-08-28": 0,
    "2026-08-31": -20, "2026-09-01": 20, "2026-09-02": 20, "2026-09-03": 40, "2026-09-04": 80,
    "2026-09-07": 0, "2026-09-08": 40, "2026-09-09": 40, "2026-09-10": 69.2, "2026-09-11": 80,
    "2026-09-14": -20, "2026-09-15": 16, "2026-09-16": 120, "2026-09-17": 2.8, "2026-09-18": 80,
    "2026-09-21": 80, "2026-09-22": 40, "2026-09-23": 40, "2026-09-24": 19.8, "2026-09-25": -20,
    "2026-09-28": 0, "2026-09-29": 40, "2026-09-30": 0, "2026-10-01": -20, "2026-10-02": 0,
}


def envload():
    for raw in (ROOT / ".env").read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def kind_of(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def load_alerts():
    out = []
    for ln in POI.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        kind = kind_of(o.get("poi_name") or o.get("type") or "")
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
    del data
    return bars, n


def choose_side(name, hold, rail):
    if name in SUPPORT or (name in BARE and hold["c"] > rail):
        return "Buy"
    if name in RESIST or (name in BARE and hold["c"] < rail):
        return "Sell"
    return None


def fade_ok(side, hold, lift, rail):
    if side == "Buy":
        return (
            hold["c"] > rail and hold["sell"] > hold["buy"]
            and lift["buy"] > lift["sell"] and lift["c"] > hold["c"] and lift["c"] > rail
        )
    return (
        hold["c"] < rail and hold["buy"] > hold["sell"]
        and lift["sell"] > lift["buy"] and lift["c"] < hold["c"] and lift["c"] < rail
    )


def atr_of(keys, bars, i):
    if i < 14:
        return None
    use = keys[i - 14:i + 1]
    if any((use[j] - use[j - 1]).total_seconds() != 60 for j in range(1, 15)):
        return None
    trs = []
    for j in range(1, 15):
        prev, b = bars[use[j - 1]], bars[use[j]]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev["c"]), abs(b["l"] - prev["c"])))
    return sum(trs) / 14.0


def score(alerts, bars, sym):
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
                out.append({"t": opened, "side": side, "name": name, "rail": rail, "entry": entry, "pts": pts, "how": how, "dist": dist, "atr": atr, "sym": sym})
                pos = None
            elif dt.hour == 15 and dt.minute >= 59:
                out.append({"t": opened, "side": side, "name": name, "rail": rail, "entry": entry, "pts": pts_close, "how": "eod", "dist": dist, "atr": atr, "sym": sym})
                pos = None
            continue
        m = dt.hour * 60 + dt.minute
        if i < 1 or not (10 * 60 <= m < 16 * 60) or m >= 15 * 60 + 59:
            continue
        hold_dt = keys[i - 1]
        if (dt - hold_dt).total_seconds() != 60:
            continue
        hold = bars[hold_dt]
        atr = atr_of(keys, bars, i)
        if atr is None or atr >= 15:
            continue
        best = None
        for name, rail in active.items():
            if not (hold["l"] <= rail <= hold["h"]):
                continue
            side = choose_side(name, hold, rail)
            if side is None or not fade_ok(side, hold, b, rail):
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


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    client = db.Historical(key)
    alerts = load_alerts()
    print("FERRARI_458 ALERTS", len(alerts), "BOT NOT TOUCHED", flush=True)
    days, d = [], date(2026, 8, 24)
    while d <= date(2026, 10, 2):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    all_rows, by_day = [], {}
    lines = ["date\ttime\tside\tname\trail\tentry\tpts\thow\tdist\tatr\tsymbol"]
    for day in days:
        start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
        end = datetime(day.year, day.month, day.day, 16, 0, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
        nu = nrec(client, "MNQU6", start, end)
        nz = nrec(client, "MNQZ6", start, end)
        sym = "MNQZ6" if nz > nu else "MNQU6"
        print("DAY", day.isoformat(), "U", nu, "Z", nz, "USE", sym, flush=True)
        bars, _got = pull_bars(client, sym, start, end)
        rows = score(alerts, bars, sym)
        pts = round(sum(r["pts"] for r in rows), 1)
        by_day[day.isoformat()] = pts
        for r in rows:
            print(
                f"{r['t']:%Y-%m-%d}\t{r['t']:%H:%M}\t{r['side']}\t{r['name']}\t{r['rail']:.2f}\t"
                f"{r['entry']:.2f}\t{r['pts']:+.1f}\t{r['how']}\t{r['dist']:.2f}\t{r['atr']:.2f}\t{sym}",
                flush=True,
            )
            lines.append(
                f"{r['t']:%Y-%m-%d}\t{r['t']:%H:%M}\t{r['side']}\t{r['name']}\t{r['rail']:.2f}\t"
                f"{r['entry']:.2f}\t{r['pts']:+.1f}\t{r['how']}\t{r['dist']:.2f}\t{r['atr']:.2f}\t{sym}"
            )
        print("DAY_END", day.isoformat(), len(rows), f"{pts:+.1f}", flush=True)
        all_rows.extend(rows)
        del bars
        gc.collect()
    OUT.write_text("\n".join(lines) + "\n")
    w = [r for r in all_rows if r["pts"] > 0]
    l = [r for r in all_rows if r["pts"] <= 0]
    gw = sum(r["pts"] for r in w)
    gl = -sum(r["pts"] for r in l)
    pts = gw - gl
    pf = gw / gl if gl else 0.0
    print(f"FILLS {len(all_rows)} W {len(w)} L {len(l)} PF {pf:.2f} PNL {pts:+.1f}", flush=True)
    diffs = [k for k, a in FROZEN.items() if abs(a - by_day.get(k, 0)) >= 0.2]
    print("FROZEN_MATCH" if not diffs else "FROZEN_DIFF " + " ".join(diffs), flush=True)
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
