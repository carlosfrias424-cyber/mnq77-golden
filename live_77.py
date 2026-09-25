#!/usr/bin/env python3
"""7/7 sniper. Dual UNPLUGGED. DEMO SIM ON.

Fire only on a failed retest. Not the first touch. Not a breakout.
  1) wick within 10 of the rail
  2) price left that rail by 20 points, then came back
     (the same push cannot be the second touch)
  3) the return failed: higher low while still over (buy),
     or lower high while still under (sell)
  4) the push into the rail is dying (live 5m delta past the last closed)
  5) last print still within 15, and the entry bar has not
     run more than 15 past the rail
One rail can fire again after price leaves and comes back. No first-print flip. No 20-pt re-arm.
Book: 5 MNQ DEMO, stop 20, TP 40, BE off. Session 04:00–16:00 CT M–F.
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
SPENT = ROOT / "logs/rail_spent.json"
BE20 = ROOT / "logs/be20.jsonl"
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
sys.path.insert(0, str(ROOT / "apps" / "watcher7"))

FIRE = True
TICK, WATCH, FIRE_NEAR = 0.25, 10.0, 15.0
STOP_PTS, TP_PTS, BE_PTS, QTY = 20.0, 40.0, 0.0, 5
OPP_RESET = 20.0
SESSION_START, SESSION_END = 4 * 60, 16 * 60
TZ = ZoneInfo("America/Chicago")
HOLIDAYS = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
SKIP_TAGS = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
NOTE = "retest_near15"
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
        return False, "after_1600"
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
    spent_fill: bool = False
    tried_t0: float | None = None

    def clear_visit(self):
        self.phase = "IDLE"
        self.side = None
        self.picture = ""
        self.spent_fill = False
        self.side_locked = None
        self.tried_t0 = None

    def out(self, reason, go=False):
        return dict(
            phase=self.phase, setup=self.picture, side=self.side,
            picture=self.picture, go=bool(FIRE and go), paper=go,
            fire_enabled=FIRE, reason=reason, bounce=self.bounce,
            side_locked=self.side_locked,
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


def load_spent(day) -> dict:
    try:
        o = json.loads(SPENT.read_text())
    except Exception:
        return {}
    if o.get("day") != day.isoformat():
        return {}
    out = {}
    for k, v in (o.get("rails") or {}).items():
        try:
            out[k] = float(v)
        except Exception:
            continue
    return out


def mark_spent(day, key: str, px: float) -> dict:
    cur = load_spent(day)
    cur[key] = float(px)
    SPENT.parent.mkdir(parents=True, exist_ok=True)
    SPENT.write_text(json.dumps({"day": day.isoformat(), "rails": cur}))
    return cur


def note_touch(tests: dict, key: str, now: float, lo: float, hi: float, tagged: bool,
               px: float, rail_px: float, allow: bool):
    """A test is a stretch where the wick is within 10 of the rail.
    It closes after the wick has been gone for 60s.
    A side arms only while price is 20 points away and not touching.
    The visit that prints that leave cannot be the visit that fires.
    """
    st = tests.setdefault(key, {"cur": None, "closed": [], "arm_buy": None, "arm_sell": None})
    if allow and not tagged:
        if px >= rail_px + OPP_RESET:
            st["arm_buy"] = now
        if px <= rail_px - OPP_RESET:
            st["arm_sell"] = now
    if not allow:
        return st
    cur = st["cur"]
    if not tagged:
        if cur and now - cur["last"] >= 60:
            st["closed"].append({"lo": cur["lo"], "hi": cur["hi"]})
            st["closed"] = st["closed"][-8:]
            st["cur"] = None
        return st
    if cur is None or now - cur["last"] >= 60:
        if cur:
            st["closed"].append({"lo": cur["lo"], "hi": cur["hi"]})
            st["closed"] = st["closed"][-8:]
        st["cur"] = {"lo": lo, "hi": hi, "last": now, "t0": now}
    else:
        cur["lo"] = min(cur["lo"], lo)
        cur["hi"] = max(cur["hi"], hi)
        cur["last"] = now
    return st


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
         session_start="04:00", session_end="16:00", vol_src="databento_trades",
         dual="UNPLUGGED", opp_reset=OPP_RESET, watch=WATCH, fire_near=FIRE_NEAR,
         be=BE_PTS, lock="120s_then_clear", fire_mode="sniper_live",
         tape="retest_dying")

    m = Machine()
    last_poi = 0.0
    last_hb = 0.0
    rails: list[Rail] = []
    n = 0
    spent = load_spent(datetime.now(TZ).date())
    tests = {}
    spent_day = datetime.now(TZ).date()
    tests_cleared = None

    while True:
        now = time.time()
        today = datetime.now(TZ).date()
        if today != spent_day:
            spent = {}
            tests = {}
            spent_day = today
            tests_cleared = None
            try:
                SPENT.write_text(json.dumps({"day": today.isoformat(), "rails": {}}))
            except Exception:
                pass
        sess_ok, _sess = session()
        if sess_ok and tests_cleared != today:
            tests.clear()
            tests_cleared = today

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

        # Rail stays alive. Same touch still will not fire twice.
        _ = expire_spent

        if not dbvol.fresh(now):
            if n % 40 == 0:
                emit(event="score", reason="tape_stale", mid=round(last_px, 3),
                     age_s=round(dbvol.age_s(now), 2), prints=dbvol.prints())
            time.sleep(0.25)
            continue

        forming = dbvol.m1
        if forming is None:
            time.sleep(0.25)
            continue
        bar_lo = min(forming.l, last_px)
        bar_hi = max(forming.h, last_px)
        t0 = forming.t0
        for st in tests.values():
            cur = st["cur"]
            if cur and now - cur["last"] >= 60:
                st["closed"].append({"lo": cur["lo"], "hi": cur["hi"]})
                st["closed"] = st["closed"][-8:]
                st["cur"] = None

        rail = alert_rail(rails, bar_lo, bar_hi)
        rec = dict(
            event="score", mid=round(last_px, 3),
            bar=[round(bar_lo, 3), round(bar_hi, 3)],
            vol_src="databento_trades", prints=dbvol.prints(),
            submit=False, dual="UNPLUGGED", symbol=SYMBOL, fire_mode="sniper_live",
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

        if m.spent_fill:
            if n % 20 == 0:
                rec["reason"] = "same_sweep_spent"
                emit(**rec)
            time.sleep(0.25)
            continue

        loc = loc_over(last_px, rail.px)
        includes = bar_lo <= rail.px <= bar_hi
        named = bounce_from_name(rail.kind)
        if named is not None:
            bounce = named
        elif loc is True:
            bounce = True
        elif loc is False:
            bounce = False
        else:
            bounce = None
        if bounce is None:
            tagged = False
        elif bounce:
            tagged = abs(bar_lo - rail.px) <= WATCH
        else:
            tagged = abs(bar_hi - rail.px) <= WATCH
        st = note_touch(tests, rail.key, now, bar_lo, bar_hi, tagged, last_px, rail.px, sess_ok)
        rec["sr"] = None if bounce is None else ("support" if bounce else "resistance")
        rec["loc"] = "over" if loc else ("under" if loc is False else "on")
        rec["inferred"] = rail.kind in BARE
        rec["includes"] = includes
        rec["tagged"] = tagged
        rec["visit"] = len(st["closed"]) + (1 if st["cur"] else 0)
        near = abs(last_px - rail.px) <= FIRE_NEAR
        _, tmet = dbvol.tape_5m(True if bounce else False)
        rec.update(
            near=near, tape=tmet,
            dist=round(abs(last_px - rail.px), 3),
            stop_pts=STOP_PTS, tp_pts=TP_PTS, watch=WATCH, fire_near=FIRE_NEAR, t0=t0,
        )
        if bounce is None:
            rec["reason"] = "at_rail"
            if n % 20 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue
        m.bounce = bounce
        m.side_locked = bounce
        m.picture = "bounce_long" if bounce else "fade_short"
        m.side = "Buy" if bounce else "Sell"
        rec["side_locked"] = bounce
        rec["tape_lean"] = False

        if named is not None and loc is not None and loc != named:
            rec.update(reason="through", snap=m.out("through"))
            if n % 10 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue
        if not tagged:
            rec["reason"] = "idle_no_hit"
            if n % 20 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue
        if not st["closed"]:
            rec["reason"] = "first_touch"
            if n % 10 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue
        prev = st["closed"][-1]
        cur = st["cur"]
        arm = st.get("arm_buy") if bounce else st.get("arm_sell")
        if not arm or not cur or cur.get("t0", 0) <= arm:
            rec["reason"] = "no_leave"
            if n % 10 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue
        if bounce:
            failed = bool(cur) and cur["lo"] > prev["lo"]
            struct_reason = "no_higher_low"
        else:
            failed = bool(cur) and cur["hi"] < prev["hi"]
            struct_reason = "no_lower_high"
        rec["prior_lo"] = prev["lo"]
        rec["prior_hi"] = prev["hi"]
        rec["this_lo"] = None if not cur else cur["lo"]
        rec["this_hi"] = None if not cur else cur["hi"]
        if not failed:
            rec.update(reason=struct_reason, snap=m.out(struct_reason))
            if n % 10 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue
        d_live, d_last = tmet.get("d_live"), tmet.get("d_last")
        if bounce:
            dying = d_live is not None and d_last is not None and float(d_live) > float(d_last)
        else:
            dying = d_live is not None and d_last is not None and float(d_live) < float(d_last)
        rec["tape_lean"] = dying
        if not dying:
            rec.update(reason="push_not_dying", snap=m.out("push_not_dying"))
            if n % 10 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue
        if not near:
            rec.update(reason="chase", snap=m.out("chase"))
            if n % 10 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue
        far = (bar_hi - rail.px) if bounce else (rail.px - bar_lo)
        if far > FIRE_NEAR:
            rec.update(reason="bar_left", snap=m.out("bar_left"), far=round(far, 3))
            if n % 10 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue

        if m.tried_t0 == t0:
            rec["reason"] = "already_tried_this_1m"
            if n % 20 == 0:
                emit(**rec)
            time.sleep(0.25)
            continue

        ok, sess = session()
        in_pos = locked()
        rec["event"] = "paper_fire"
        rec["book"] = BOOK
        rec["snap"] = m.out("fire", True)
        rec["reason"] = "fire"
        m.tried_t0 = t0
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
                rec["rail_open"] = True
        emit(**rec)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
