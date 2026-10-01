#!/usr/bin/env python3
"""Paper score only. No orders.

Asia this week: 17:00-02:00 CT. Sunday night counts.
Same fade as the day book. The hold bar must trade the rail.
Close on the hold side, within 15. Stop 20, target 40.
One trade at a time. Flat at 02:00. A trade still on at the
end of the pull is marked open, not as a finished result.
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
POI = ROOT / "logs/tv_poi.jsonl"
STOP, TP = 20.0, 40.0
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}


def envload():
    for raw in (ROOT / ".env").read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def sr_kind(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def load_alerts():
    import json
    out = []
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
            recv = float(o.get("recv_ts") or o.get("ts") or 0)
        except Exception:
            continue
        if px <= 0 or recv <= 0:
            continue
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


def in_asia(dt):
    if dt.weekday() == 5:
        return False
    m = dt.hour * 60 + dt.minute
    if dt.weekday() == 6:
        return m >= 17 * 60
    return m < 2 * 60 or m >= 17 * 60


def night_of(dt):
    d = dt.date()
    if dt.hour < 12:
        d = d - timedelta(days=1)
    return d.isoformat()


def parse_end(raw):
    text = str(raw).strip().replace("Z", "+00:00")
    if "." in text:
        head, tail = text.split(".", 1)
        if "+" in tail:
            text = head + "+" + tail.split("+", 1)[1]
        else:
            text = head + "+00:00"
    avail = datetime.fromisoformat(text)
    if avail.tzinfo is None:
        avail = avail.replace(tzinfo=timezone.utc)
    return avail.astimezone(timezone.utc)


def tape_end(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        rng = client.metadata.get_dataset_range(dataset="GLBX.MDP3")
        raw = rng["end"] if isinstance(rng, dict) else getattr(rng, "end", None)
        if raw is not None:
            end = min(now, parse_end(raw))
            return end.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as e:
        print("RANGE", type(e).__name__, str(e)[:180], flush=True)
    return (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_trades(client, start, end):
    return client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols="MNQZ6",
        stype_in="raw_symbol",
        schema="trades",
        start=start,
        end=end,
    )


def pull_bars(key):
    client = db.Historical(key)
    start = "2026-09-27T22:00:00Z"
    end = tape_end(client)
    print(f"PULL {start} {end}", flush=True)
    try:
        data = get_trades(client, start, end)
    except Exception as e:
        m = re.search(r"available up to '([^']+)'", str(e))
        if not m:
            raise
        end = (parse_end(m.group(1)) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"RETRY {end}", flush=True)
        data = get_trades(client, start, end)
    bars = {}
    n = 0
    for rec in data:
        n += 1
        if n % 500000 == 0:
            print("TRADES", n, flush=True)
        ts = rec.ts_event / 1e9
        dt = datetime.fromtimestamp(ts, TZ).replace(second=0, microsecond=0)
        if not in_asia(dt):
            continue
        px = px_of(rec)
        sz = float(getattr(rec, "size", 0) or 0)
        s = str(getattr(rec, "side", "") or "").upper()
        b = bars.get(dt)
        if b is None:
            b = bars[dt] = {"o": px, "h": px, "l": px, "c": px, "buy": 0.0, "sell": 0.0}
        b["h"] = max(b["h"], px)
        b["l"] = min(b["l"], px)
        b["c"] = px
        if s in ("B", "BUY", "BID"):
            b["buy"] += sz
        elif s in ("A", "SELL", "ASK"):
            b["sell"] += sz
    print("TRADES", n, "BARS", len(bars), flush=True)
    return bars


def close_pos(pos, px, how, out):
    side, entry, _stop, _tp, meta = pos
    pts = (px - entry) if side == "Buy" else (entry - px)
    if how == "stop":
        pts = -STOP
    elif how == "tp":
        pts = TP
    out.append((meta, pts, how))
    return meta["name"], meta["rail"]


def score(alerts, bars):
    keys = sorted(bars)
    ai = 0
    active = {}
    quiet = {}
    pos = None
    out = []
    last = None
    for i in range(1, len(keys)):
        dt = keys[i]
        prev_dt = keys[i - 1]
        gap = (dt - prev_dt).total_seconds() != 60
        hold = None if gap else bars[prev_dt]
        b = bars[dt]
        last = (dt, b)
        t = dt.timestamp()
        asof = (prev_dt.timestamp() + 60) if hold else t
        while ai < len(alerts) and alerts[ai][0] <= asof:
            _, kind, px = alerts[ai]
            active[kind] = px
            ai += 1
        if pos and (gap or (dt.hour == 1 and dt.minute >= 59)):
            px = bars[prev_dt]["c"] if gap else b["c"]
            name, rail = close_pos(pos, px, "asia_end", out)
            quiet[name] = rail
            pos = None
            if gap or (dt.hour == 1 and dt.minute >= 59):
                continue
        if pos:
            side, entry, stop_px, tp_px, meta = pos
            if side == "Buy":
                hit_stop = b["l"] <= stop_px
                hit_tp = b["h"] >= tp_px
            else:
                hit_stop = b["h"] >= stop_px
                hit_tp = b["l"] <= tp_px
            if hit_stop or hit_tp:
                how = "stop" if hit_stop else "tp"
                name, rail = close_pos(pos, b["c"], how, out)
                quiet[name] = rail
                pos = None
            continue
        for name, px in list(quiet.items()):
            if abs(b["c"] - px) >= 20:
                del quiet[name]
        if hold is None or (dt.hour == 1 and dt.minute >= 59):
            continue
        best = None
        for name, rail in active.items():
            if name in quiet or not (hold["l"] <= rail <= hold["h"]):
                continue
            if name in SUPPORT or (name in BARE and hold["c"] > rail):
                side = "Buy"
            elif name in RESIST or (name in BARE and hold["c"] < rail):
                side = "Sell"
            else:
                continue
            if side == "Buy":
                if not (hold["c"] > rail and hold["sell"] > hold["buy"]):
                    continue
                if not (b["buy"] > b["sell"] and b["c"] > hold["c"] and b["c"] > rail):
                    continue
            else:
                if not (hold["c"] < rail and hold["buy"] > hold["sell"]):
                    continue
                if not (b["sell"] > b["buy"] and b["c"] < hold["c"] and b["c"] < rail):
                    continue
            dist = abs(b["c"] - rail)
            if dist > 15:
                continue
            if best is None or dist < best[0]:
                best = (dist, side, name, rail)
        if best is None:
            continue
        dist, side, name, rail = best
        entry = b["c"]
        if side == "Buy":
            stop_px, tp_px = entry - STOP, entry + TP
        else:
            stop_px, tp_px = entry + STOP, entry - TP
        meta = {
            "t": dt, "side": side, "name": name, "rail": rail,
            "entry": entry, "dist": dist, "night": night_of(dt),
        }
        pos = (side, entry, stop_px, tp_px, meta)
    if pos and last:
        close_pos(pos, last[1]["c"], "open", out)
    return out


def show(rows):
    done = [(m, p, h) for m, p, h in rows if h != "open"]
    opens = [(m, p, h) for m, p, h in rows if h == "open"]
    wins = [p for _, p, _ in done if p > 0]
    losses = [p for _, p, _ in done if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    net = gp - gl
    n = len(done)
    wr = (len(wins) / n) if n else 0
    pf = (gp / gl) if gl else 0
    print(
        f"CLOSED {n}  W {len(wins)} L {len(losses)}  WR {wr:.1%}  PF {pf:.2f}  "
        f"PNL {net:+.1f} pts  ${net * 10:+.0f}",
        flush=True,
    )
    nights = {}
    for meta, pts, how in done:
        nights.setdefault(meta["night"], []).append(pts)
    for night in sorted(nights):
        pts = nights[night]
        print(f"NIGHT {night}  n {len(pts)}  {sum(pts):+.1f} pts", flush=True)
    for meta, pts, how in rows:
        print(
            f"{meta['t']:%m-%d %H:%M} {meta['side']:4} {meta['name']}@{meta['rail']:.2f} "
            f"@{meta['entry']:.2f} {pts:+.1f} {how} dist {meta['dist']:.2f}",
            flush=True,
        )
    if not opens:
        print("OPEN none", flush=True)


def main():
    envload()
    key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
    alerts = load_alerts()
    print("RAILS", len(alerts), flush=True)
    print("ASIA 17:00-02:00 CT  touch  close within 15  stop 20  target 40", flush=True)
    print("WEEK sun 2026-09-27 night through now", flush=True)
    bars = pull_bars(key)
    show(score(alerts, bars))


if __name__ == "__main__":
    main()
