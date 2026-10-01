import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
for raw in (ROOT/".env").read_text().splitlines():
    if not raw.strip() or raw.strip().startswith("#") or "=" not in raw: continue
    k,_,v = raw.partition("=")
    k,v = k.strip(), v.strip().strip('"').strip("'")
    if k and k not in os.environ: os.environ[k]=v
EV = [
    ("09:00 short", "08:57", "09:02", "Sell", 30725.00),
    ("09:23 long",  "09:22", "09:24", "Buy",  30582.50),
    ("09:40 short", "09:39", "09:41", "Sell", 30725.00),
    ("10:11 wick",  "10:10", "10:13", "Buy",  None),
    ("10:19 bot",   "10:18", "10:20", "Buy",  30639.75),
    ("10:27 short", "10:26", "10:29", "Sell", 30682.25),
    ("10:43 bot",   "10:42", "10:45", "Sell", 30582.50),
    ("11:09 bot",   "11:08", "11:11", "Sell", 30639.75),
    ("11:35 short", "11:33", "11:38", "Sell", 30671.00),
]
import databento as db
key = os.environ.get("DATABENTO_API_KEY") or os.environ["DATABENTO_KEY"]
print("PULL", flush=True)
data = None
for end in ("2026-10-01T16:45:00Z", "2026-10-01T16:30:00Z"):
    try:
        data = db.Historical(key).timeseries.get_range(
            dataset="GLBX.MDP3", symbols="MNQZ6", stype_in="raw_symbol",
            schema="trades", start="2026-10-01T13:30:00Z", end=end)
        print("END", end, flush=True)
        break
    except Exception as e:
        print("RETRY", end, type(e).__name__, flush=True)
if data is None:
    raise SystemExit("no data")
tr = []
for rec in data:
    ts = datetime.fromtimestamp(rec.ts_event/1e9, TZ)
    x = float(rec.price)
    px = x/1e9 if abs(x) > 1e7 else x
    sz = float(getattr(rec, "size", 0) or 0)
    s = str(getattr(rec, "side", "") or "").upper()
    signed = sz if s in ("B", "BUY", "BID") else -sz if s in ("A", "SELL", "ASK") else 0.0
    tr.append((ts, px, signed))
print("TRADES", len(tr), flush=True)

def dsum(a, b):
    return sum(sz for t, _, sz in tr if a <= t < b)

def px_at(ts):
    last = None
    for t, p, _ in tr:
        if t <= ts:
            last = p
        else:
            break
    return last

def show(tag, a, c, side, rail):
    a = datetime.strptime("2026-10-01 " + a, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    c = datetime.strptime("2026-10-01 " + c, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    w = [(t, p, s) for t, p, s in tr if a <= t <= c]
    if not w:
        print(tag, "NO TRADES", flush=True)
        return
    if side == "Buy":
        i = min(range(len(w)), key=lambda i: (w[i][1], w[i][0]))
    else:
        i = min(range(len(w)), key=lambda i: (-w[i][1], w[i][0]))
    tip_ts, tip_px, _ = w[i]
    if rail is None:
        gap, flag, rail_s = 0.0, "WICK", "wick"
    else:
        gap = (rail - tip_px) if side == "Buy" else (tip_px - rail)
        flag = "TOUCH" if gap >= -0.25 else "NO_TOUCH"
        rail_s = f"{rail:.2f}"
    into = dsum(tip_ts - timedelta(seconds=20), tip_ts)
    at = sum(sz for t, p, sz in tr if abs((t - tip_ts).total_seconds()) <= 10 and abs(p - tip_px) <= 2)
    reject = dsum(tip_ts, tip_ts + timedelta(seconds=20))
    p20 = px_at(tip_ts + timedelta(seconds=20))
    off20 = (p20 - tip_px) if side == "Buy" else (tip_px - p20)
    mend = tip_ts.replace(second=0, microsecond=0) + timedelta(minutes=1)
    pc = px_at(mend - timedelta(milliseconds=1))
    offc = (pc - tip_px) if side == "Buy" else (tip_px - pc)
    print(
        f"{tag} {side} {rail_s} {flag} tip {tip_ts:%H:%M:%S} {tip_px:.2f} gap {gap:.2f} "
        f"into20 {into:.0f} at {at:.0f} reject20 {reject:.0f} off20 {off20:+.2f} offclose {offc:+.2f}",
        flush=True,
    )

for row in EV:
    show(*row)
