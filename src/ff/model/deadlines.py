"""The league's clocks, from ESPN: when waivers run, when a dropped player clears, when an accepted trade executes.

Oct 13 2026: the card named a claim's drop and a trade's freed spots without knowing which came first, and the
briefing could not say when an offer accepted on a Saturday would actually move the players. ESPN states every one
of these. The waiver run is the `processDate` ESPN stamps on a pending claim (3:00 AM ET on each processing day in
all three leagues; the settings' `waiverProcessHour` of 11 is not that clock), the trade review period is
`tradeSettings.revisionHours` (the `processDate` on an accepted trade is acceptance plus exactly that), and a
dropped player sits on waivers `acquisitionSettings.waiverHours` and clears at the next run after that.

Code owns these numbers; the rows put them into words next to each click. `HOME_TZ` is Dustin's clock.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HOME_TZ = ZoneInfo("America/Denver")

# ESPN runs waivers at this hour Eastern on each processing day. Read off the `processDate` of pending claims on
# 2026-10-13 (three leagues, all 03:00 ET); a league's own pending claim overrides it (`build`).
WAIVER_RUN_HOUR_ET = 3
# ESPN's default processing days (every day but Tuesday, the day the week rolls), used when the settings have none.
DEFAULT_PROCESS_DAYS = ["MONDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
WEEKDAYS = {"MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3, "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def next_waiver_run(after: datetime, days: list[str] | None, hour: int = WAIVER_RUN_HOUR_ET) -> datetime | None:
    """The first waiver run strictly after `after`: `hour` Eastern on the next allowed weekday. None when the
    league processes on no day at all."""
    allowed = {WEEKDAYS[d.upper()] for d in (days or DEFAULT_PROCESS_DAYS) if d.upper() in WEEKDAYS}
    if not allowed:
        return None
    after = _aware(after)
    base = after.astimezone(ET).replace(hour=hour, minute=0, second=0, microsecond=0)
    for i in range(9):
        cand = base + timedelta(days=i)
        if cand > after and cand.weekday() in allowed:
            return cand
    return None


def clears_at(drop_ts_ms: int | float | None, waiver_hours: int | float | None, days: list[str] | None,
              now: datetime) -> datetime | None:
    """When a player dropped at `drop_ts_ms` comes off waivers: the first run at or after the drop plus the waiver
    period. None when that is already behind `now` (he is a free agent) or when the drop time is unknown."""
    if not drop_ts_ms:
        return None
    eligible = datetime.fromtimestamp(drop_ts_ms / 1000, UTC) + timedelta(hours=float(waiver_hours or 0))
    run = next_waiver_run(eligible - timedelta(seconds=1), days)
    return run if run is not None and run > _aware(now) else None


def word(dt: datetime | None) -> str | None:
    """"Wed 1:00 AM", in Dustin's clock."""
    if dt is None:
        return None
    return _aware(dt).astimezone(HOME_TZ).strftime("%a %I:%M %p").replace(" 0", " ")


def iso(dt: datetime | None) -> str | None:
    return _aware(dt).astimezone(HOME_TZ).isoformat(timespec="minutes") if dt is not None else None


def ms(dt: datetime | None) -> int | None:
    return int(_aware(dt).timestamp() * 1000) if dt is not None else None


def _kickoff(line: dict | None) -> datetime | None:
    k = (line or {}).get("kickoff")
    if not k:
        return None
    try:
        return datetime.fromisoformat(k.replace("Z", "+00:00"))
    except ValueError:
        return None


def build(now: datetime, settings: dict, lines: dict | None, waiver_run_ts: int | float | None,
          pending_moves: list[dict] | None) -> dict:
    """The deadlines block of one league: the next waiver run (ESPN's own stamp when a claim is pending, else the
    house hour on the league's processing days), when an offer accepted now executes and whether that is past the
    next kickoff, the next kickoff itself, and when each accepted trade processes. Everything is Denver time in
    words and ISO, plus the clock in ms for the ledger."""
    now = _aware(now)
    days = settings.get("waiver_process_days") or DEFAULT_PROCESS_DAYS
    run, source = None, None
    if waiver_run_ts and waiver_run_ts > ms(now):
        run, source = datetime.fromtimestamp(waiver_run_ts / 1000, UTC), "espn"
    else:
        run, source = next_waiver_run(now, days), "default"
    rev = settings.get("trade_revision_hours")
    executes = now + timedelta(hours=float(rev)) if rev is not None else None
    kicks = sorted(k for k in (_kickoff(ln) for ln in (lines or {}).values()) if k is not None and k > now)
    nxt = kicks[0] if kicks else None
    return {
        "waiver_run": iso(run), "waiver_run_word": word(run), "waiver_run_ts": ms(run), "waiver_run_source": source,
        "waiver_process_days": [d.title()[:3] for d in days], "waiver_hours": settings.get("waiver_hours"),
        "trade_review_hours": rev, "veto_votes": settings.get("veto_votes"),
        "offer_executes": iso(executes), "offer_executes_word": word(executes), "offer_executes_ts": ms(executes),
        "next_kickoff": iso(nxt), "next_kickoff_word": word(nxt),
        "offer_after_kickoff": bool(executes is not None and nxt is not None and executes > nxt),
        "trade_processes": [{"in": m.get("in") or [], "out": m.get("out") or [], "processes_ts": m.get("process_ts"),
                             "processes": iso(datetime.fromtimestamp(m["process_ts"] / 1000, UTC)) if m.get("process_ts") else None,
                             "processes_word": word(datetime.fromtimestamp(m["process_ts"] / 1000, UTC)) if m.get("process_ts") else None}
                            for m in pending_moves or []],
    }
