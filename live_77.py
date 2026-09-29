#!/usr/bin/env python3
"""Fade. Demo only. The +961 card.

10:00–16:00 CT. Stop 20, target 40, 5 MNQZ6.
Hold bar trades the rail, closes within 15 of it, and closes on the hold side.
Sellers larger than buyers on a long. Buyers larger than sellers on a short.
The next 1-minute bar lifts off the rail. That close is the entry, even if it is more than 15 away.
Databento B is buying, A is selling. Delta is buy size minus sell size.
One position. A stop or a target ends it. If neither has traded by 16:00, flatten.
The rail goes quiet when the trade ends, until a later bar closes 20 points away.
The next trade can be the bar after the exit. No 120-second lock.
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/seven.jsonl"
STATE = ROOT / "logs/fade_state.json"
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
sys.path.insert(0, str(ROOT / "apps" / "watcher7"))

TZ = ZoneInfo("America/Chicago")
SYMBOL = "MNQZ6"
QTY, STOP, TP, NEAR = 5, 20.0, 40.0, 15.0
SESSION_START, SESSION_END = 10 * 60, 16 * 60
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
NOTE = "fade_hold15_10_16"


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


def emit(**kw):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": int(time.time() * 1000), **kw}
    OUT.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)



def _once(key):
    safe = "".join(c if c.isalnum() else "_" for c in key)[:140]
    path = ROOT / "logs" / "sent" / safe
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, key.encode())
    os.close(fd)
    return True


def discord(text):
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        return False
    body = json.dumps({"content": text[:1900]}).encode()
    try:
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "maximus-fade"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        return True
    except Exception as e:
        emit(event="discord_fail", err=str(e)[:200])
        return False


MARK_PNG = base64.b64decode("""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""")


def _paste_mark(img, xy=(72, 64), size=88):
    from PIL import Image
    logo = Image.open(io.BytesIO(MARK_PNG)).convert("RGBA").resize((size, size))
    img.paste(logo, xy, logo)


def _trade_card(path, headline, color, rows):
    from PIL import Image, ImageDraw
    W, H = 1080, 760
    bg, gold, fg, muted, line = (14, 17, 16), (212, 165, 116), (232, 235, 228), (139, 147, 140), (42, 49, 46)
    img = Image.new("RGB", (W, H), bg)
    _paste_mark(img)
    d = ImageDraw.Draw(img)
    d.text((180, 72), "THE ALGO FUND", font=_font("bold", 26), fill=gold)
    d.text((180, 110), "MAXIMUS", font=_font("sans", 20), fill=muted)
    d.text((72, 200), headline, font=_font("bold", 92), fill=color)
    y = 360
    for label, value in rows:
        d.text((72, y), label, font=_font("mono", 22), fill=muted)
        d.text((W - 72, y - 4), value, font=_font("bold", 36), fill=fg, anchor="ra")
        y += 78
        d.rectangle((72, y - 18, W - 72, y - 16), fill=line)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def _money(pts, qty=None):
    q = QTY if qty is None else qty
    return f"{pts:+.1f}    {pts * q * 2:+,.0f}"


def discord_in(side, name, rail, entry):
    if not _once(f"in|{side}|{name}|{rail:.2f}|{entry:.2f}"):
        return
    color = (61, 154, 106) if side == "Buy" else (196, 92, 74)
    path = ROOT / "logs" / "alert.png"
    try:
        _trade_card(path, side.upper(), color, [
            ("RAIL", f"{name}  @  {rail:,.2f}"),
            ("FILL", f"{entry:,.2f}"),
            ("BOOK", f"{QTY} {SYMBOL}   ·   {STOP:.0f} / {TP:.0f}"),
            ("TIME", datetime.now(TZ).strftime("%H:%M") + " CT"),
        ])
    except Exception as e:
        emit(event="discord_card_fail", err=str(e)[:200])
        discord(
            f"IN  {side}  {QTY} {SYMBOL}\n"
            f"{name} @ {rail:.2f}\n"
            f"fill {entry:.2f}\n"
            f"{datetime.now(TZ).strftime('%H:%M')} CT"
        )
        return
    if not discord_file(path):
        discord(f"IN  {side}  {name} @ {rail:.2f}  fill {entry:.2f}")


def discord_out(side, name, rail, entry, how, px=None):
    if not _once(f"out|{how}|{side}|{name}|{rail:.2f}|{entry:.2f}"):
        return
    if how == "stop":
        headline, color, pts = "STOP", (196, 92, 74), -STOP
    elif how == "tp":
        headline, color, pts = "TARGET", (61, 154, 106), TP
    elif how == "eod":
        headline, color = "FLAT", (212, 165, 116)
        pts = None if px is None else ((px - entry) if side == "Buy" else (entry - px))
    else:
        headline, color, pts = "FLAT", (212, 165, 116), None
    result = "16:00" if pts is None else _money(pts)
    path = ROOT / "logs" / "alert.png"
    try:
        _trade_card(path, headline, color, [
            ("RAIL", f"{name}  @  {rail:,.2f}"),
            ("ENTRY", f"{entry:,.2f}"),
            ("RESULT", result),
            ("TIME", datetime.now(TZ).strftime("%H:%M") + " CT"),
        ])
    except Exception as e:
        emit(event="discord_card_fail", err=str(e)[:200])
        discord(f"OUT  {side}  {headline}  {result}")
        return
    if not discord_file(path):
        discord(f"OUT  {side}  {headline}  {result}")


def _state():
    if not STATE.exists():
        return {}
    try:
        o = json.loads(STATE.read_text())
    except Exception:
        return {}
    return o if isinstance(o, dict) else {}


def eod_sent(day):
    return str(_state().get("eod") or "") == day


def mark_eod(day):
    o = _state()
    o["eod"] = day
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(o))


DEMO = "https://demo.tradovateapi.com/v1"
HERMES_DAY = "2026-09-28"  # card already sent: 5 trades, W 3, L 2, +80


def _get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _post(url, payload, headers=None):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def tv_session():
    base = os.environ.get("TRADOVATE_BASE", DEMO).rstrip("/")
    if "live.tradovateapi.com" in base:
        raise RuntimeError("live url")
    name = os.environ.get("TRADOVATE_NAME") or ""
    password = os.environ.get("TRADOVATE_PASSWORD") or ""
    if not name or not password:
        raise RuntimeError("missing creds")
    body = {
        "name": name,
        "password": password,
        "appId": os.environ.get("TRADOVATE_APP_ID", "MNQHybrid"),
        "appVersion": os.environ.get("TRADOVATE_APP_VERSION", "0.1"),
        "deviceId": os.environ.get("TRADOVATE_DEVICE_ID") or "maximus-eod",
    }
    if os.environ.get("TRADOVATE_CID"):
        try:
            body["cid"] = int(os.environ["TRADOVATE_CID"])
        except ValueError:
            body["cid"] = os.environ["TRADOVATE_CID"]
    if os.environ.get("TRADOVATE_SEC"):
        body["sec"] = os.environ["TRADOVATE_SEC"]
    auth = _post(base + "/auth/accesstokenrequest", body)
    token = auth.get("accessToken")
    if not token:
        raise RuntimeError(auth.get("errorText") or "auth_fail")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    account_id = os.environ.get("TRADOVATE_ACCOUNT_ID")
    if account_id:
        account_id = int(account_id)
    else:
        acc = _get(base + "/account/list", headers)
        pick = None
        if isinstance(acc, list):
            for a in acc:
                if "DEMO" in str(a.get("name") or "").upper():
                    pick = a
                    break
            if pick is None and acc:
                pick = acc[0]
        if not pick:
            raise RuntimeError("no account")
        account_id = int(pick["id"])
    found = _get(base + "/contract/find?name=" + SYMBOL, headers)
    contract_id = int((found or {}).get("id") or 0)
    if contract_id <= 0:
        raise RuntimeError("no contract")
    return base, headers, account_id, contract_id


def fill_when(raw):
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(TZ)


def pair_fills(fills):
    """One trade each time the account goes flat. Points are the real fill prices."""
    net = 0
    avg = 0.0
    side = None
    opened = None
    exit_notional = 0.0
    exit_qty = 0
    out = []

    def emit_trade():
        nonlocal exit_notional, exit_qty, avg, side, opened
        if exit_qty <= 0 or side is None:
            return
        exit_px = exit_notional / exit_qty
        pts = (exit_px - avg) if side == "Buy" else (avg - exit_px)
        out.append({
            "when": opened.strftime("%H:%M") if opened else "",
            "side": side,
            "qty": exit_qty,
            "entry": avg,
            "exit": exit_px,
            "pts": round(pts, 1),
        })
        exit_notional = 0.0
        exit_qty = 0

    for f in fills:
        action = f.get("action")
        if action not in ("Buy", "Sell"):
            continue
        try:
            qty = int(f.get("qty") or 0)
            px = float(f.get("price"))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        signed = qty if action == "Buy" else -qty
        when = fill_when(f.get("timestamp"))
        if net == 0 or (net > 0 and signed > 0) or (net < 0 and signed < 0):
            new = abs(net) + abs(signed)
            avg = (avg * abs(net) + px * abs(signed)) / new
            if net == 0:
                side = action
                opened = when
            net += signed
            continue
        prev = net
        closing = min(abs(signed), abs(prev))
        exit_notional += px * closing
        exit_qty += closing
        net = prev + signed
        crossed = net == 0 or (prev > 0 > net) or (prev < 0 < net)
        if not crossed:
            continue
        emit_trade()
        if net == 0:
            avg = 0.0
            side = None
            opened = None
        else:
            side = "Buy" if net > 0 else "Sell"
            avg = px
            opened = when
    return out


def day_trades(day):
    base, headers, account_id, contract_id = tv_session()
    raw = _get(base + "/fill/list", headers)
    if not isinstance(raw, list):
        raise RuntimeError("fill list")
    chosen = []
    for f in raw:
        if int(f.get("contractId") or 0) != contract_id:
            continue
        if f.get("accountId") not in (None, account_id, str(account_id)):
            continue
        when = fill_when(f.get("timestamp"))
        if when is None or when.date() != day:
            continue
        minute = when.hour * 60 + when.minute
        if minute < SESSION_START or minute > SESSION_END + 30:
            continue
        chosen.append(f)
    chosen.sort(key=lambda f: str(f.get("timestamp") or ""))
    return pair_fills(chosen)


def discord_file(path, text=""):
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not url or not path or not Path(path).exists():
        return False
    boundary = uuid.uuid4().hex
    payload = json.dumps({"content": text[:1900]}).encode() if text else b"{}"
    png = Path(path).read_bytes()
    body = b""
    for name, raw, filename, ctype in (
        ("payload_json", payload, None, "application/json"),
        ("files[0]", png, "maximus.png", "image/png"),
    ):
        disp = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disp += f'; filename="{filename}"'
        body += f"--{boundary}\r\n{disp}\r\nContent-Type: {ctype}\r\n\r\n".encode()
        body += raw + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "maximus-fade",
            },
        )
        opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=20) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as e:
        # A redirect means Discord already took the file. Do not post it again.
        if e.code in (204, 301, 302, 303, 307, 308):
            return True
        emit(event="discord_fail", err=str(e)[:200])
        return False
    except Exception as e:
        emit(event="discord_fail", err=str(e)[:200])
        return False


def _font(kind, size):
    from PIL import ImageFont
    picks = {
        "sans": (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ),
        "bold": (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        "mono": (
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ),
    }
    for path in picks[kind]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_eod_card(path, day, trades, tag):
    from PIL import Image, ImageDraw
    wins = [t for t in trades if t["pts"] > 0]
    losses = [t for t in trades if t["pts"] < 0]
    pts = round(sum(t["pts"] for t in trades), 1)
    gross_w = sum(t["pts"] for t in wins)
    gross_l = abs(sum(t["pts"] for t in losses))
    pf = None if gross_l == 0 else gross_w / gross_l
    wr = None if not trades else len(wins) / len(trades)
    rr = None
    if wins and losses:
        rr = (gross_w / len(wins)) / (gross_l / len(losses))
    qtys = {t.get("qty") for t in trades}
    lot = str(next(iter(qtys))) if len(qtys) == 1 else "—"
    dol = int(round(sum(t["pts"] * t.get("qty", QTY) * 2 for t in trades)))
    color = (61, 154, 106) if pts >= 0 else (196, 92, 74)
    rows = [
        ("DATE", day.strftime("%m-%d")),
        ("TRADES", str(len(trades))),
        ("WINS", str(len(wins))),
        ("LOSSES", str(len(losses))),
        ("WIN RATE", "—" if wr is None else f"{wr * 100:.0f}%"),
        ("PROFIT FACTOR", "—" if pf is None else f"{pf:.2f}"),
        ("RR", "—" if rr is None else f"{rr:.1f}"),
        ("DOLLARS", f"${dol:+}"),
    ]
    # last row would overflow a 4-row card. Keep six and drop RR into footer if needed.
    rows = rows[:6]
    rows.append(("LOT", lot))
    rows.append(("DOLLARS", f"${dol:+}"))
    # that's 8 rows * 78 = 624, start 340 = 964, too tall for 1080 with footer.
    rows = [
        ("TRADES", f"{len(trades)}    {len(wins)}W  {len(losses)}L"),
        ("WIN RATE", "—" if wr is None else f"{wr * 100:.0f}%"),
        ("PROFIT FACTOR", "—" if pf is None else f"{pf:.2f}"),
        ("DOLLARS", f"${dol:+}    LOT {lot}"),
    ]
    W = H = 1080
    bg, gold, fg, muted, line = (14, 17, 16), (212, 165, 116), (232, 235, 228), (139, 147, 140), (42, 49, 46)
    img = Image.new("RGB", (W, H), bg)
    _paste_mark(img)
    d = ImageDraw.Draw(img)
    d.text((180, 72), "THE ALGO FUND", font=_font("bold", 26), fill=gold)
    d.text((180, 110), "MAXIMUS", font=_font("sans", 20), fill=muted)
    d.text((72, 190), day.strftime("%m-%d"), font=_font("mono", 22), fill=gold)
    d.text((72, 230), f"{pts:+.1f}", font=_font("bold", 96), fill=color)
    d.text((72, 340), "POINTS", font=_font("mono", 22), fill=muted)
    y = 430
    for label, value in rows:
        d.text((72, y), label, font=_font("mono", 22), fill=muted)
        d.text((W - 72, y - 4), value, font=_font("bold", 36), fill=fg, anchor="ra")
        y += 90
        d.rectangle((72, y - 22, W - 72, y - 20), fill=line)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def eod_text(day, trades):
    pts = round(sum(t["pts"] for t in trades), 1)
    dol = int(round(sum(t["pts"] * t["qty"] * 2 for t in trades)))
    w = sum(1 for t in trades if t["pts"] > 0)
    l = sum(1 for t in trades if t["pts"] < 0)
    lines = [
        f"EOD  {day.strftime('%m-%d')}",
        f"{len(trades)} trade{'s' if len(trades) != 1 else ''}  W {w}  L {l}",
        f"{pts:+.1f} pts  ${dol:+}",
    ]
    for t in trades:
        lines.append(
            f"{t['when']} {t['side']} {t['qty']} @{t['entry']:.2f} -> {t['exit']:.2f}  {t['pts']:+.1f}"
        )
    if not trades:
        lines.append("no fills")
    return "\n".join(lines)


def maybe_eod(now=None):
    now = now or datetime.now(TZ)
    if now.weekday() >= 5:
        return
    if now.hour * 60 + now.minute < SESSION_END:
        return
    day = now.date()
    key = day.isoformat()
    if eod_sent(key):
        return
    if key == HERMES_DAY:
        mark_eod(key)
        return
    try:
        trades = day_trades(day)
    except Exception as e:
        emit(event="eod_pnl_fail", day=key, err=str(e)[:200], note=NOTE)
        return
    text = eod_text(day, trades)
    card = ROOT / "logs" / f"eod_{key}.png"
    try:
        render_eod_card(card, day, trades, "FILLS")
    except Exception as e:
        emit(event="eod_card_fail", err=str(e)[:200], note=NOTE)
        card = None
    sent = discord_file(card) if card else False
    if not sent and not discord(text):
        return
    pts = round(sum(t["pts"] for t in trades), 1)
    emit(event="eod_pnl", day=key, n=len(trades), pts=pts, note=NOTE, src="tradovate_fills")
    mark_eod(key)


def sr_kind(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def in_session(ts):
    dt = datetime.fromtimestamp(ts, TZ)
    if dt.weekday() >= 5:
        return False
    m = dt.hour * 60 + dt.minute
    return SESSION_START <= m < SESSION_END


def rails_asof(t):
    active = {}
    if not POI.exists():
        return active
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
        if recv <= t:
            active[kind] = px
    return active


def send_book(side, name, rail, entry, hold, lift):
    env = os.environ.copy()
    env.update({
        "MNQ_SIDE": side,
        "MNQ_QTY": str(QTY),
        "TRADOVATE_ENV": "demo",
        "TRADOVATE_SYMBOL": SYMBOL,
        "MNQ_POI_NAME": str(name),
        "MNQ_POI_PX": str(rail),
        "MNQ_MID": str(entry),
        "MNQ_ENTRY": str(round(entry, 2)),
        "MNQ_STOP_PTS": str(STOP),
        "MNQ_T40": str(TP),
    })
    env.pop("MNQ_FLATTEN", None)
    r = subprocess.run(
        [str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )
    fill = None
    for line in reversed((r.stdout or "").splitlines()):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("fill") is not None:
            try:
                fill = float(rec["fill"])
            except (TypeError, ValueError):
                fill = None
            break
    return r.returncode, (r.stdout or "")[-300:], fill


def send_flat():
    env = os.environ.copy()
    env.update({
        "MNQ_FLATTEN": "1",
        "TRADOVATE_ENV": "demo",
        "TRADOVATE_SYMBOL": SYMBOL,
    })
    env.pop("MNQ_SIDE", None)
    r = subprocess.run(
        [str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )
    return r.returncode, (r.stdout or "")[-300:]


def load_state():
    if not STATE.exists():
        return None, {}
    try:
        o = json.loads(STATE.read_text())
    except Exception:
        return None, {}
    quiet = {}
    for k, v in (o.get("quiet") or {}).items():
        try:
            quiet[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    raw = o.get("pos")
    if not raw:
        return None, quiet
    try:
        pos = (
            raw["side"], float(raw["entry"]), str(raw["name"]),
            float(raw["rail"]), float(raw["ts"]),
        )
    except (KeyError, TypeError, ValueError):
        return None, quiet
    if pos[0] not in ("Buy", "Sell"):
        return None, quiet
    return pos, quiet


def save_state(pos, quiet):
    rec = _state()
    rec["quiet"] = quiet
    rec["pos"] = None
    if pos is not None:
        side, entry, name, rail, ts = pos
        rec["pos"] = {
            "side": side, "entry": entry, "name": name, "rail": rail, "ts": ts,
        }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(rec))


def session_over(pos, close_ts):
    opened = datetime.fromtimestamp(pos[4], TZ)
    close_dt = datetime.fromtimestamp(close_ts, TZ)
    if close_dt.date() > opened.date():
        return True
    if close_dt.date() < opened.date():
        return False
    return close_dt.hour * 60 + close_dt.minute >= SESSION_END


def pick(hold, lift, active):
    best = None
    for name, rail in active.items():
        if not (hold.l <= rail <= hold.h):
            continue
        dist = abs(hold.c - rail)
        if dist > NEAR:
            continue
        if hold.c > rail and hold.delta < 0:
            if not (lift.delta > 0 and lift.c > hold.c and lift.c > rail):
                continue
            side = "Buy"
        elif hold.c < rail and hold.delta > 0:
            if not (lift.delta < 0 and lift.c < hold.c and lift.c < rail):
                continue
            side = "Sell"
        else:
            continue
        if best is None or dist < best[0]:
            best = (dist, side, name, rail)
    return best


def finish(pos, quiet, how):
    quiet[pos[2]] = pos[3]
    save_state(None, quiet)
    return None


def main():
    envload()
    os.environ["TRADOVATE_SYMBOL"] = SYMBOL
    os.environ["TRADOVATE_ENV"] = "demo"
    try:
        from mnq_vol import start_from_env
        vol = start_from_env()
    except Exception as e:
        emit(event="fatal", err=str(e)[:300], note=NOTE)
        return
    pos, quiet = load_state()
    emit(
        event="seven_start", fire=True, note=NOTE, symbol=SYMBOL,
        book={"qty": QTY, "stop": STOP, "tp": TP, "symbol": SYMBOL},
        session_start="10:00", session_end="16:00", near=NEAR,
        tape="hold_then_lift", vol_src="databento_trades",
        exit="flat_1600", quiet="bar_close_20", relock="next_bar",
        open=None if pos is None else pos[0],
    )
    if pos is not None and session_over(pos, time.time()):
        rc, out = send_flat()
        emit(event="eod_flat", how="startup", rc=rc, poi=f"{pos[2]}@{pos[3]:.2f}", out=out, note=NOTE)
        if rc == 0:
            discord_out(pos[0], pos[2], pos[3], pos[1], "startup")
            pos = finish(pos, quiet, "startup")
    maybe_eod()
    prev = None
    seen = None
    last_hb = 0.0
    while True:
        now = time.time()
        if now - last_hb > 60:
            emit(
                event="heartbeat", note=NOTE, session=in_session(now),
                quiet=len(quiet), open=None if pos is None else pos[0],
            )
            last_hb = now
        bar = vol.last_closed_1()
        if bar is None or bar.t0 == seen:
            time.sleep(0.5)
            continue
        hold, prev, seen = prev, bar, bar.t0
        if pos is not None:
            if bar.t0 + 60 <= pos[4]:
                continue
            side, entry, name, rail, _ts = pos
            stop_px = entry - STOP if side == "Buy" else entry + STOP
            tp_px = entry + TP if side == "Buy" else entry - TP
            if side == "Buy":
                hit_stop = bar.l <= stop_px
                hit_tp = bar.h >= tp_px
            else:
                hit_stop = bar.h >= stop_px
                hit_tp = bar.l <= tp_px
            if hit_stop or hit_tp:
                how = "stop" if hit_stop else "tp"
                emit(
                    event="trade_done", how=how, side=side,
                    poi=f"{name}@{rail:.2f}", mid=bar.c, note=NOTE,
                )
                discord_out(side, name, rail, entry, how)
                pos = finish(pos, quiet, how)
            elif session_over(pos, bar.t0 + 60):
                rc, out = send_flat()
                emit(
                    event="eod_flat", how="16:00", rc=rc, side=side,
                    poi=f"{name}@{rail:.2f}", mid=bar.c, out=out, note=NOTE,
                )
                if rc == 0:
                    discord_out(side, name, rail, entry, "eod", bar.c)
                    pos = finish(pos, quiet, "eod")
            continue
        if hold is None or bar.t0 - hold.t0 != 60:
            continue
        for name, rail in list(quiet.items()):
            if abs(bar.c - rail) >= 20:
                del quiet[name]
                save_state(pos, quiet)
        if not in_session(bar.t0 + 60):
            maybe_eod()
            continue
        active = rails_asof(hold.t0 + 60)
        for name in quiet:
            active.pop(name, None)
        hit = pick(hold, bar, active)
        if hit is None:
            continue
        dist, side, name, rail = hit
        rc, out, fill = send_book(side, name, rail, bar.c, hold, bar)
        entry = fill if fill else bar.c
        emit(
            event="struct40_submit" if rc == 0 else "struct40_fail",
            submit=rc == 0, rc=rc, side=side, poi=f"{name}@{rail:.2f}",
            mid=entry, dist=round(dist, 2),
            hold_delta=hold.delta, lift_delta=bar.delta,
            hold_c=hold.c, lift_c=bar.c, note=NOTE, out=out,
        )
        if rc == 0 and fill:
            pos = (side, entry, name, rail, time.time())
            save_state(pos, quiet)
            discord_in(side, name, rail, entry)


if __name__ == "__main__":
    main()
