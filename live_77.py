#!/usr/bin/env python3
"""7/7 bounce support / fade resistance. Dual UNPLUGGED. DEMO SIM ON.

YESTERDAY'S FILE. Three bugfixes only:
  1) close must sit on the 6-pt shelf (kills 15-pt dump fades)
  2) spent is per RAIL, not per side (no flip / BRT reclaim)
  3) BE off — do not run manage_be20

Lock (this patch only): 120s after a send, then clear.
BE off means be20.jsonl is stale — a leftover submit.lock must not
ghost-lock the rest of the day. place_struct40 still refuses if SIM
is actually in a position.

Still: fire on the closed 1m that tagged, if hold + tape.
No volume-not-expanding. No WAIT_C2. No Dual.

Pine name is the side:
  H4L H1L PDL PWL → bounce long (price must hold OVER)
  H4H H1H PDH PWH → fade short  (price must hold UNDER)
  H4 / H1 (old pine) → LOCK from first 1m close of the visit
  Later 1m CLOSE on the other side = through → skip until leave 10 pts
  OPEN / ONH / ONL / EMA → not rails

Book: 5 MNQ DEMO, stop 20, TP 40, BE off. Session 04:00–11:30 CT M–F.
Rail = last webhook. Symbol MNQZ6.
Spent: that rail is dead until price is 20 pts away from it.
"""
from __future__ import annotations

import json, os, time, subprocess, sys
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/seven.jsonl"
LOCK = ROOT / "logs/submit.lock"
BE20 = ROOT / "logs/be20.jsonl"
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
sys.path.insert(0, str(ROOT / "apps" / "watcher7"))

FIRE = True
TICK, WATCH = 0.25, 10.0
ARM_PTS = 6.0  # close must be within 6 of the rail
STOP_PTS, TP_PTS, BE_PTS, QTY = 20.0, 40.0, 0.0, 5
OPP_RESET = 20.0  # rail spent until price is 20 pts away
SESSION_START, SESSION_END = 4 * 60, 11 * 60 + 30
TZ = ZoneInfo("America/Chicago")
HOLIDAYS = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
SKIP_TAGS = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
NOTE = "yesterday_shelf6_sticky_be_off"
SYMBOL = "MNQZ6"
BOOK = dict(qty=QTY, stop=STOP_PTS, tp=TP_PTS, be=BE_PTS, peel=False, runner=False, symbol=SYMBOL)


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


def session():
    dt = datetime.now(TZ)
    if dt.weekday() >= 5:
        return False, "weekend"
    if dt.date() in HOLIDAYS:
        return False, "holiday"
    mins = dt.hour * 60 + dt.minute
    if mins < SESSION_START:
        return False, "before_4am"
    if mins >= SESSION_END:
        return False, "after_1130"
    return True, "open"


def last_net():
    if not BE20.exists():
        return None
    try:
        with BE20.open("rb") as f:
            f.seek(0, os.SEEK_END)
            n = f.tell()
            f.seek(max(0, n - 32768), os.SEEK_SET)
            chunk = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    net = None
    for ln in chunk.splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("event") in ("flat", "tick", "be_move") and o.get("net") is not None:
            try:
                net = int(o["net"])
            except Exception:
                pass
    return net


def locked():
    """One position. BE off → be20 is stale. Do not 16h-ghost.
    120s after send = fill in flight. Then drop the lock.
    place_struct40 still refuses if Tradovate net != 0.
    """
    if BE_PTS:
        net = last_net()
        if net not in (0, None) and abs(int(net)) > 0:
            return True
    if not LOCK.exists():
        return False
    try:
        o = json.loads(LOCK.read_text() or "{}")
        ts = float(o.get("ts") or LOCK.stat().st_mtime)
        age = time.time() - ts
    except Exception:
        try:
            LOCK.unlink()
        except Exception:
            pass
        return False
    if age < 120:
        return True
    try:
        LOCK.unlink()
    except Exception:
        pass
    return False


def emit(**kw):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": int(time.time() * 1000), **kw}
    OUT.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def extreme_dist(px: float, lo: float, hi: float) -> float:
    if lo <= px <= hi:
        return 0.0
    if px > hi:
        return px - hi
    return lo - px


def sr_kind(name: str) -> str | None:
    u = (name or "").upper().strip()
    if not u:
        return None
    if any(u.startswith(x) or u == x for x in SKIP_TAGS):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def bounce_from_name(kind: str) -> bool | None:
    u = (kind or "").upper()
    if u in SUPPORT:
        return True
    if u in RESIST:
        return False
    return None


def loc_over(px: float, rail_px: float) -> bool | None:
    if px > rail_px:
        return True
    if px < rail_px:
        return False
    return None


@dataclass
class Rail:
    name: str
    px: float
    kind: str
    ts: float = 0.0
    recv: float = 0.0

    @property
    def key(self):
        return f"{self.kind}@{self.px:.2f}"


def load_pois() -> list[Rail]:
    """Last valid webhook only. No 5-day stash."""
    if not POI.exists():
        return []
    best = None
    for ln in POI.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        try:
            px = round(float(o.get("price") or 0), 2)
        except Exception:
            continue
        if px <= 0:
            continue
        raw = str(o.get("poi_name") or o.get("type") or "")
        kind = sr_kind(raw)
        if kind is None:
            continue
        recv = o.get("recv_ts") or o.get("ts") or 0
        t = o.get("ts") or recv or 0
        try:
            recv = float(recv)
            if recv > 1e12:
                recv /= 1000.0
        except Exception:
            recv = 0.0
        try:
            t = float(t)
            if t > 1e12:
                t /= 1000.0
        except Exception:
            t = 0.0
        if recv <= 0:
            continue
        r = Rail(kind, px, kind, t, recv)
        if best is None or recv >= best.recv:
            best = r
    return [best] if best else []


def alert_rail(rails: list[Rail], lo: float, hi: float) -> Rail | None:
    if not rails:
        return None
    r = rails[0]
    if extreme_dist(r.px, lo, hi) <= WATCH:
        return r
    return None


@dataclass
class Machine:
    key: str = ""
    phase: str = "IDLE"
    side: str | None = None
    picture: str = ""
    bounce: bool = True
    side_locked: bool | None = None
    visit_dead: bool = False
    spent_fill: bool = False

    def reset_attempt(self):
        self.phase = "IDLE"
        self.side = None
        self.picture = ""

    def clear_visit(self):
        self.reset_attempt()
        self.visit_dead = False
        self.spent_fill = False
        self.side_locked = None

    def out(self, reason, go=False):
        return dict(
            phase=self.phase, setup=self.picture, side=self.side,
            picture=self.picture, go=bool(FIRE and go), paper=go,
            fire_enabled=FIRE, reason=reason, bounce=self.bounce,
            side_locked=self.side_locked, visit_dead=self.visit_dead,
        )


def send_book(side: str, name: str, px: float, last: float):
    env = os.environ.copy()
    env.update({
        "MNQ_SIDE": side, "MNQ_QTY": str(QTY), "TRADOVATE_ENV": "demo",
        "TRADOVATE_SYMBOL": SYMBOL,
        "MNQ_POI_NAME": str(name), "MNQ_POI_PX": str(px),
        "MNQ_MID": str(last), "MNQ_ENTRY": str(round(last, 2)),
        "MNQ_STOP_PTS": str(STOP_PTS), "MNQ_T40": str(TP_PTS),
    })
    LOCK.write_text(json.dumps({
        "side": side, "poi": name, "px": px, "qty": QTY,
        "entry": round(last, 2), "stop_pts": STOP_PTS,
        "tp": TP_PTS, "be": BE_PTS, "symbol": SYMBOL, "ts": time.time(),
    }))
    r = subprocess.run([str(PY), str(SUBMIT)], cwd=str(ROOT),
                       env=env, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        try:
            LOCK.unlink()
        except Exception:
            pass
    return r.returncode, (r.stdout or "")[-400:]


def expire_spent(spent: dict, last_px: float):
    """Rail is free again once price is 20 pts away from it."""
    dead = []
    for k, px in spent.items():
        try:
            if abs(float(last_px) - float(px)) >= OPP_RESET:
                dead.append(k)
        except Exception:
            dead.append(k)
    for k in dead:
        spent.pop(k, None)
    return dead


def main():
    envload()
    os.environ["TRADOVATE_SYMBOL"] = SYMBOL
    try:
        from mnq_vol import start_from_env
        dbvol = start_from_env()
    except Exception as e:
        emit(event="fatal", err=str(e)[:300], note="databento required — dual unplugged")
        return
    emit(event="seven_start", fire=FIRE, book=BOOK, note=NOTE, symbol=SYMBOL,
         session_start="04:00", session_end="11:30", vol_src="databento_trades",
         dual="UNPLUGGED", opp_reset=OPP_RESET, arm_pts=ARM_PTS, be=BE_PTS,
         lock="120s_then_clear")

    m = Machine()
    last_poi = 0.0
    last_1m_t0 = None
    last_hb = 0.0
    rails: list[Rail] = []
    n = 0
    spent = {}  # rail.key -> rail.px  (BOTH sides)
    spent_day = datetime.now(TZ).date()

    while True:
        now = time.time()
        today = datetime.now(TZ).date()
        if today != spent_day:
            spent.clear()
            spent_day = today

        if now - last_hb > 60:
            ok, why = session()
            emit(event="heartbeat", session=ok, why=why, fire=FIRE,
                 locked=locked(), book=BOOK, note=NOTE, dual="UNPLUGGED",
                 rails=len(rails), symbol=SYMBOL, spent=len(spent))
            last_hb = now

        if now - last_poi > 5:
            rails = load_pois()
            last_poi = now

        last = dbvol.last()
        if last is None:
            time.sleep(0.25)
            continue
        last_px, last_ts = last
        n += 1

        expired = expire_spent(spent, last_px)
        if expired:
            emit(event="spent_cleared", keys=list(expired), mid=round(last_px, 3))

        if not dbvol.fresh(now):
            if n % 40 == 0:
                emit(event="score", reason="tape_stale", mid=round(last_px, 3),
                     age_s=round(dbvol.age_s(now), 2), prints=dbvol.prints())
            time.sleep(0.25)
            continue

        closed = dbvol.last_closed_1()
        forming = dbvol.m1
        bar_lo = min(x for x in (
            getattr(closed, "l", None), getattr(forming, "l", None), last_px) if x is not None)
        bar_hi = max(x for x in (
            getattr(closed, "h", None), getattr(forming, "h", None), last_px) if x is not None)

        rail = alert_rail(rails, bar_lo, bar_hi)
        rec = dict(
            event="score", mid=round(last_px, 3),
            bar=[round(bar_lo, 3), round(bar_hi, 3)],
            vol_src="databento_trades", prints=dbvol.prints(),
            submit=False, dual="UNPLUGGED", symbol=SYMBOL,
        )

        if rail is None:
            if m.key:
                m.clear_visit()
                m.key = ""
            rec["reason"] = "no_rail_in_watch"
            rec["n_rails"] = len(rails)
            if n % 40 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue

        if m.key != rail.key:
            m = Machine(key=rail.key)
        rec.update(poi=rail.key, px=rail.px, phase=m.phase)

        new_1m = closed is not None and closed.t0 != last_1m_t0
        if new_1m:
            last_1m_t0 = closed.t0

        in_pos = locked()
        if m.spent_fill:
            if n % 20 == 0:
                rec["reason"] = "same_sweep_spent"
                emit(**rec)
            time.sleep(0.25)
            continue
        if m.visit_dead:
            if n % 20 == 0:
                rec.update(reason="visit_dead", side_locked=m.side_locked,
                           snap=m.out("visit_dead"))
                emit(**rec)
            time.sleep(0.25)
            continue

        if not new_1m or closed is None:
            rec["reason"] = "idle_wait_1m"
            if n % 20 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue

        loc = loc_over(closed.c, rail.px)
        if loc is None:
            loc = loc_over(closed.o, rail.px)

        if m.side_locked is None:
            named = bounce_from_name(rail.kind)
            if named is not None:
                m.side_locked = named
                rec["side_lock"] = "name"
            elif loc is not None:
                m.side_locked = loc
                rec["side_lock"] = "first_close"
            else:
                rec["reason"] = "at_rail"
                rec["c1"] = dict(h=closed.h, l=closed.l, c=closed.c)
                emit(**rec)
                time.sleep(0.25)
                continue

        bounce = m.side_locked
        rec["sr"] = "support" if bounce else "resistance"
        rec["loc"] = "over" if loc else ("under" if loc is False else "on")
        rec["tag"] = "low" if bounce else "high"
        rec["inferred"] = rail.kind in BARE
        rec["side_locked"] = bounce

        if loc is None:
            rec["reason"] = "at_rail"
            rec["c1"] = dict(h=closed.h, l=closed.l, c=closed.c)
            emit(**rec)
            time.sleep(0.25)
            continue
        if loc != bounce:
            m.visit_dead = True
            rec.update(reason="through_close", snap=m.out("through_close"),
                       visit_dead=True)
            emit(**rec)
            time.sleep(0.25)
            continue

        hit = (abs(closed.l - rail.px) <= WATCH) if bounce else (abs(closed.h - rail.px) <= WATCH)
        if not hit:
            rec["reason"] = "idle_no_hit"
            rec["c1"] = dict(h=closed.h, l=closed.l, c=closed.c)
            if n % 20 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue

        hold = (closed.c >= rail.px) if bounce else (closed.c <= rail.px)
        on_shelf = abs(closed.c - rail.px) <= ARM_PTS
        lean, tmet = dbvol.tape_5m(bounce)
        rec.update(
            c1=dict(t0=closed.t0, h=closed.h, l=closed.l, c=closed.c),
            hold=hold, on_shelf=on_shelf, tape_lean=lean, tape=tmet,
            stop_pts=STOP_PTS, tp_pts=TP_PTS, arm_pts=ARM_PTS,
        )
        m.bounce = bounce
        m.picture = "bounce_long" if bounce else "fade_short"
        m.side = "Buy" if bounce else "Sell"

        if rail.key in spent:
            rec.update(reason="rail_spent", snap=m.out("rail_spent"),
                       spent_px=spent[rail.key])
            emit(**rec)
            time.sleep(0.25)
            continue

        if not hold:
            m.visit_dead = True
            rec.update(reason="body_gave_rail", snap=m.out("body_gave_rail"), visit_dead=True)
            emit(**rec)
            time.sleep(0.25)
            continue
        if not on_shelf:
            m.visit_dead = True
            rec.update(reason="off_shelf", snap=m.out("off_shelf"), visit_dead=True)
            emit(**rec)
            time.sleep(0.25)
            continue
        if not lean:
            rec.update(reason="tape_against", snap=m.out("tape_against"))
            emit(**rec)
            time.sleep(0.25)
            continue

        ok, sess = session()
        rec["event"] = "paper_fire"
        rec["book"] = BOOK
        rec["snap"] = m.out("fire", True)
        rec["reason"] = "fire"
        if not FIRE:
            rec["skip"] = "fire_off"
        elif not ok:
            rec["skip"] = sess
        elif in_pos:
            rec["skip"] = "open_position"
        else:
            rc, out = send_book(m.side, rail.key, rail.px, last_px)
            rec["submit"] = rc == 0
            rec["event"] = "struct40_submit" if rc == 0 else "struct40_fail"
            rec["rc"] = rc
            rec["out"] = out
            rec["symbol"] = SYMBOL
            if rc == 0:
                m.spent_fill = True
                m.phase = "FILLED"
        spent[rail.key] = rail.px
        rec["rail_spent"] = True
        if rec.get("skip"):
            m.spent_fill = True
            m.visit_dead = True
        emit(**rec)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
