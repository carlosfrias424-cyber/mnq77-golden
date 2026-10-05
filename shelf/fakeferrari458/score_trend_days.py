#!/usr/bin/env python3
"""Are strong trend days red for the box rules and for Ferrari 458?

A strong trend day, 10:00 to 16:00 Chicago:
  the close-to-close move is at least 80 points
  and that move is at least half the day's high-low range.

No contract-count call. That call is what hung.
September contract before 2026-09-15, December contract from that day on.
Days already scored are skipped. Does not start or edit the bot.
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
OUT = ROOT / "logs/trend_days.tsv"
FIRST, LAST = date(2026, 8, 24), date(2026, 10, 5)
STOP, TP = 20.0, 40.0
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
SKIP_F = ("ONH", "ONL", "EMA", "OPEN", "YEL", "HEALTHCHECK")
SKIP_B = ("ONH", "ONL", "EMA", "OPEN")
DONE = """
2026-08-24	+61.0	199.0	0.31	0	1	+40.0	0	+0.0	MNQU6
2026-08-25	+65.8	116.0	0.57	0	1	-20.0	0	+0.0	MNQU6
2026-08-26	+182.8	267.2	0.68	1	4	+40.0	3	+0.0	MNQU6
2026-08-27	+31.0	196.8	0.16	0	0	+0.0	0	+0.0	MNQU6
2026-08-28	-298.0	374.8	0.80	1	0	+0.0	0	+0.0	MNQU6
2026-08-31	+85.2	174.8	0.49	0	1	-20.0	1	-20.0	MNQU6
2026-09-01	-100.2	315.5	0.32	0	5	+20.0	2	+20.0	MNQU6
2026-09-02	+32.5	106.2	0.31	0	7	+100.0	2	+20.0	MNQU6
2026-09-03	+121.8	223.0	0.55	1	2	+20.0	1	+40.0	MNQU6
""".strip().splitlines()


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
            side, entry, opened = pos
            if side == "Buy":
                hit_stop = b["l"] <= entry - STOP
                hit_tp = b["h"] >= entry + TP
                pts_close = b["c"] - entry
            else:
                hit_stop = b["h"] >= entry + STOP
                hit_tp = b["l"] <= entry - TP
                pts_close = entry - b["c"]
            if hit_stop or hit_tp:
                pts = -STOP if hit_stop else TP
                out.append((opened, pts))
                pos = None
            elif dt.hour == 15 and dt.minute >= 59:
                out.append((opened, pts_close))
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
                best = (dist, side, b["c"])
        if best:
            pos = (best[1], best[2], dt)
    return out


def session_shape(bars):
    keys = [k for k in sorted(bars) if 10 * 60 <= k.hour * 60 + k.minute < 16 * 60]
    if len(keys) < 30:
        return None
    o, c = bars[keys[0]]["c"], bars[keys[-1]]["c"]
    hi = max(bars[k]["h"] for k in keys)
    lo = min(bars[k]["l"] for k in keys)
    span = hi - lo
    if span <= 0:
        return None
    move = c - o
    return move, span, abs(move) / span


def load_done():
    rows, seen = [], set()
    text = ""
    if OUT.exists():
        text = OUT.read_text()
    else:
        text = "date\tmove\trange\teff\tstrong\tbox_n\tbox_pts\tfer_n\tfer_pts\tsymbol\n" + "\n".join(DONE) + "\n"
        OUT.write_text(text)
    for ln in text.splitlines():
        if not ln.strip() or ln.startswith("date"):
            continue
        parts = ln.split("\t")
        if len(parts) < 9 or parts[1] == "FAIL":
            continue
        d = date.fromisoformat(parts[0])
        seen.add(d)
        rows.append({
            "date": d,
            "ok": True,
            "strong": parts[4] == "1",
            "box": float(parts[6]),
            "fer": float(parts[8]),
            "move": float(parts[1]),
        })
    return rows, seen


def append_line(line):
    with OUT.open("a") as f:
        f.write(line + "\n")


def summarize(label, rows, key):
    print(label, flush=True)
    for name, pred in (
        ("STRONG", lambda r: r["strong"]),
        ("OTHER", lambda r: not r["strong"]),
    ):
        sub = [r for r in rows if r["ok"] and pred(r)]
        red = sum(1 for r in sub if r[key] < 0)
        green = sum(1 for r in sub if r[key] > 0)
        flat = sum(1 for r in sub if r[key] == 0)
        pts = sum(r[key] for r in sub)
        print(f"  {name} days {len(sub)} red {red} green {green} flat {flat} {pts:+.1f}", flush=True)


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    client = db.Historical(key)
    box_alerts = load_alerts(SKIP_B)
    fer_alerts = load_alerts(SKIP_F)
    rows, seen = load_done()
    print("BOT NOT TOUCHED", flush=True)
    print("RESUME", len(seen), "days already scored. No contract-count call.", flush=True)
    print("STRONG = abs(10:00 to 16:00 move) >= 80 and move is at least half the day's range", flush=True)
    d = FIRST
    while d <= LAST:
        if d.weekday() < 5 and d not in seen:
            start = datetime(d.year, d.month, d.day, 9, 30, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
            end = datetime(d.year, d.month, d.day, 16, 0, tzinfo=TZ).astimezone(ZoneInfo("UTC"))
            sym = symbol_for(d)
            got = pull_safe(client, sym, start, end, d)
            if got is None:
                print(d.isoformat(), "FAIL", flush=True)
                append_line(f"{d.isoformat()}\tFAIL")
                rows.append({"date": d, "ok": False, "strong": False, "box": 0.0, "fer": 0.0, "move": 0.0})
            else:
                bars, n = got
                shape = session_shape(bars)
                box = score(box_alerts, bars, "box")
                fer = score(fer_alerts, bars, "ferrari")
                bp, fp = sum(p for _, p in box), sum(p for _, p in fer)
                if shape is None:
                    move = span = eff = 0.0
                    strong = False
                else:
                    move, span, eff = shape
                    strong = abs(move) >= 80 and eff >= 0.50
                tag = "STRONG" if strong else "other"
                print(
                    f"{d.isoformat()} {tag} move {move:+.1f} range {span:.1f} eff {eff:.2f} "
                    f"BOX {len(box)} {bp:+.1f} FER {len(fer)} {fp:+.1f} {sym} trades {n}",
                    flush=True,
                )
                rows.append({"date": d, "ok": True, "strong": strong, "box": bp, "fer": fp, "move": move})
                append_line(
                    f"{d.isoformat()}\t{move:+.1f}\t{span:.1f}\t{eff:.2f}\t{int(strong)}\t"
                    f"{len(box)}\t{bp:+.1f}\t{len(fer)}\t{fp:+.1f}\t{sym}"
                )
        d += timedelta(days=1)
    print("---", flush=True)
    summarize("BOX", rows, "box")
    summarize("FERRARI", rows, "fer")
    print("STRONG DAYS", flush=True)
    for r in rows:
        if r["ok"] and r["strong"]:
            print(f"  {r['date'].isoformat()} move {r['move']:+.1f} BOX {r['box']:+.1f} FER {r['fer']:+.1f}", flush=True)
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
