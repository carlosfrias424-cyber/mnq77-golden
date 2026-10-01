#!/usr/bin/env python3
"""Post today's realized demo card to Discord. No orders."""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
LOG = ROOT / "logs/seven.jsonl"
QTY = 5


def envload():
    for raw in (ROOT / ".env").read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def clock(ts):
    return datetime.fromtimestamp(ts / 1000, TZ)


def load_today():
    today = datetime.now(TZ).date()
    rows = []
    for ln in LOG.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        ts = o.get("ts")
        if not ts:
            continue
        dt = clock(ts)
        if dt.date() != today:
            continue
        o["_dt"] = dt
        rows.append(o)
    return today, rows


def fills(rows):
    out = []
    for o in rows:
        if o.get("event") == "struct40_submit" and o.get("submit"):
            out.append({
                "t": o["_dt"],
                "side": o.get("side"),
                "poi": o.get("poi"),
                "entry": o.get("mid"),
                "pts": None,
                "how": "open",
            })
        elif o.get("event") == "trade_done" and out and out[-1]["how"] == "open":
            out[-1]["pts"] = o.get("pts")
            out[-1]["how"] = o.get("how") or "done"
        elif o.get("event") == "eod_flat" and o.get("rc") == 0 and out and out[-1]["how"] == "open":
            out[-1]["how"] = "flat"
    return out


def main():
    envload()
    spec = importlib.util.spec_from_file_location(
        "live77", ROOT / "apps/watcher7/live_77.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    today, rows = load_today()
    book = fills(rows)
    closed = [t for t in book if t["pts"] is not None]
    wins = sum(1 for t in closed if t["pts"] > 0)
    losses = sum(1 for t in closed if t["pts"] < 0)
    net = sum(t["pts"] for t in closed)
    dollars = net * QTY * 2
    print(f"DAY {today.isoformat()} FILLS {len(book)}", flush=True)
    for t in book:
        pts = "open" if t["pts"] is None else f"{t['pts']:+.1f}"
        print(
            f"{t['t']:%H:%M} {t['side']} {t['poi']} @{t['entry']} {pts} {t['how']}",
            flush=True,
        )
    if not book:
        headline, color = "QUIET", (212, 165, 116)
        card = [
            ("DATE", today.strftime("%b %-d")),
            ("FILLS", "0"),
            ("NET", "0"),
            ("BOOK", "10:00  –  15:00"),
        ]
    else:
        if net > 0:
            headline, color = "GREEN", (61, 154, 106)
        elif net < 0:
            headline, color = "RED", (196, 92, 74)
        else:
            headline, color = "FLAT", (212, 165, 116)
        card = [
            ("DATE", today.strftime("%b %-d")),
            ("FILLS", str(len(book))),
            ("RECORD", f"{wins}  /  {losses}"),
            ("NET", f"{net:+.1f}    ${dollars:+,.0f}"),
        ]
        if any(t["how"] == "open" for t in book):
            card.append(("OPEN", "still on"))
    mod.discord_card(headline, color, card)
    print("SENT", headline, flush=True)


if __name__ == "__main__":
    main()
