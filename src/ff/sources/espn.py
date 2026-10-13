"""ESPN league reads via espn-api. Read-only by design."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from espn_api.football import League
from espn_api.football.constant import POSITION_MAP
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague

from ..config import LeagueRef, LeagueSettings, env

COOKIE_HELP = (
    "ESPN cookies missing or expired. See scripts/setup_cookies.md, then put ESPN_S2 and SWID in .env."
)

# Slots that hold starters. Everything else (BE, IR, '' ) is not a lineup slot.
NON_STARTER = {"BE", "IR", ""}
# `rosterSettings.positionLimits` is keyed by defaultPositionId, a different id space from the lineup slots in
# POSITION_MAP (WR is slot 4 but default position 3). Only the fantasy positions; IDP ids are ignored.
DEFAULT_POSITION = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}


class CookieError(RuntimeError):
    pass


def connect(ref: LeagueRef) -> League:
    e = env()
    try:
        return League(league_id=ref.espn_id, year=e.season, espn_s2=e.espn_s2, swid=e.swid)
    except ESPNAccessDenied as exc:
        raise CookieError(f"{ref.name}: {COOKIE_HELP} ({exc})") from exc
    except ESPNInvalidLeague as exc:
        raise SystemExit(f"{ref.name}: invalid league id {ref.espn_id} ({exc})") from exc


def my_team(league: League, team_id: int | None = None):
    """The team from leagues.toml team_id, else the team owned by the SWID in .env."""
    if team_id is not None:
        for t in league.teams:
            if t.team_id == team_id:
                return t
    swid = (env().swid or "").strip().upper()
    for t in league.teams:
        for o in t.owners:
            oid = str(o.get("id", "")).upper() if isinstance(o, dict) else str(o).upper()
            if swid and oid == swid:
                return t
    return None


def position_limits(roster_settings: dict) -> dict[str, int]:
    """pos -> the most players ESPN lets one team roster there. -1 means unlimited and is left out.

    This is the cap behind "Too many players with default position WR (maximum 6)" on the trade screen: a trade
    that brings in a WR when six are rostered has to name a WR to drop, and the scan needs to know that."""
    out = {}
    for k, v in (roster_settings.get("positionLimits") or {}).items():
        pos = DEFAULT_POSITION.get(int(k))
        if pos and v is not None and int(v) >= 0:
            out[pos] = int(v)
    return out


def waiver_order(acq: dict) -> str | None:
    """"reset" when ESPN's `acquisitionSettings.waiverOrderReset` is true (the order goes back to reverse standings
    every week, so a claim costs no priority), "rolling" when it is false (a successful claim sends you to the back),
    None when the setting is absent."""
    if "waiverOrderReset" not in acq:
        return None
    return "reset" if acq.get("waiverOrderReset") else "rolling"


def settings(ref: LeagueRef, league: League) -> LeagueSettings:
    s = league.settings
    raw = league.espn_request.league_get(params={"view": "mSettings"})["settings"]
    slot_counts = {POSITION_MAP.get(int(k), str(k)): v for k, v in raw["rosterSettings"]["lineupSlotCounts"].items() if v}
    lineup = {k: v for k, v in slot_counts.items() if k not in NON_STARTER}
    scoring = {item["abbr"]: float(item["points"]) for item in s.scoring_format}
    ppr = scoring.get("REC", 0.0)
    playoff_weeks = [mp for mp in range(s.reg_season_count + 1, len(s.matchup_periods) + 1)]
    return LeagueSettings(
        name=s.name,
        team_count=s.team_count,
        scoring=scoring,
        lineup_slots=lineup,
        bench_slots=slot_counts.get("BE", 0),
        ir_slots=slot_counts.get("IR", 0),
        reg_season_weeks=s.reg_season_count,
        playoff_team_count=s.playoff_team_count,
        playoff_weeks=playoff_weeks,
        matchup_periods={int(k): v for k, v in s.matchup_periods.items()},
        faab=bool(s.faab),
        faab_budget=int(s.acquisition_budget or 0),
        trade_deadline_ms=int(s.trade_deadline or 0),
        ppr=ppr,
        position_limits=position_limits(raw["rosterSettings"]),
        waiver_order=waiver_order(raw.get("acquisitionSettings") or {}),
    )


@dataclass
class PlayerRow:
    espn_id: int
    name: str
    pos: str
    team: str
    eligible: list[str]
    slot: str
    fantasy_team_id: int | None
    injury_status: str | None
    proj_week: float
    actual_week: float
    proj_season: float              # ESPN's full-season total (a static preseason-style figure, not "remaining")
    percent_owned: float
    pos_rank: int | None
    bye: bool
    injured: bool = False           # ESPN's own flag on the player record
    ir_eligible_raw: bool = False   # ESPN listed the IR slot in eligibleSlots (evidence for whether that is a real signal)
    waiver_status: str | None = None  # free agents only: "WAIVERS" (claim, processes on waiver day) or "FREEAGENT" (add now)
    bye_weeks: list[int] = field(default_factory=list)  # weeks his NFL team does not play, from the pro schedule
    actual_season: float = 0.0      # points scored so far this season (ESPN's season total)
    games_played: int = 0           # games he has played this season (ESPN's season total over its per-game average)
    actual_prev_week: float | None = None  # ESPN's final for last week, in league scoring: truth for the projection log


def games_played(p) -> int:
    """ESPN's `appliedAverage` is points per game played, so total / average is the games he has played; a player
    whose games all scored zero reads as none, which only leaves the preseason prior in charge of him."""
    total, avg = float(getattr(p, "total_points", 0) or 0), float(getattr(p, "avg_points", 0) or 0)
    return int(round(total / avg)) if avg > 0 else 0


def bye_weeks(p) -> list[int]:
    """The weeks missing from espn-api's per-player pro schedule. A schedule that is mostly missing is not a bye list."""
    sched = getattr(p, "schedule", None) or {}
    if not sched:
        return []
    missing = [w for w in range(1, 19) if str(w) not in sched]
    return missing if len(missing) <= 2 else []


def _status(p) -> str | None:
    """espn-api's json_parsing returns [] for a missing key, which D/ST rows always hit; that is not a designation."""
    st = getattr(p, "injuryStatus", None)
    return st if isinstance(st, str) and st else None


def _week_stat(p, week: int, key: str) -> float:
    st = p.stats.get(week) or {}
    return float(st.get(key, 0) or 0)


def roster_rows(league: League, week: int) -> list[PlayerRow]:
    rows: list[PlayerRow] = []
    for t in league.teams:
        for p in t.roster:
            rows.append(PlayerRow(
                espn_id=p.playerId, name=p.name, pos=p.position, team=p.proTeam,
                eligible=[s for s in p.eligibleSlots if s not in NON_STARTER],
                slot=p.lineupSlot, fantasy_team_id=t.team_id,
                injury_status=_status(p),
                proj_week=_week_stat(p, week, "projected_points"),
                actual_week=_week_stat(p, week, "points"),
                proj_season=p.projected_total_points,
                percent_owned=p.percent_owned, pos_rank=p.posRank,
                bye=(str(week) not in p.schedule) if p.schedule else False,
                injured=bool(getattr(p, "injured", False)), ir_eligible_raw="IR" in p.eligibleSlots,
                bye_weeks=bye_weeks(p),
                actual_season=float(getattr(p, "total_points", 0) or 0), games_played=games_played(p),
                actual_prev_week=_week_stat(p, week - 1, "points") if week > 1 else None,
            ))
    return rows


def waiver_statuses(league: League, week: int, size: int = 300) -> dict[int, str]:
    """player id -> "WAIVERS" | "FREEAGENT" for the same pool `League.free_agents` returns.

    espn-api's Player drops the pool entry's `status`, and in a waiver-priority league that is the difference between
    "click add" and "place a claim that processes Wednesday behind six other teams". Same filter as espn-api, so the
    ids line up; a failure here just leaves the status unknown."""
    import json as _json
    filters = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]}, "filterSlotIds": {"value": []}, "limit": size,
                           "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                           "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"}}}
    try:
        data = league.espn_request.league_get(params={"view": "kona_player_info", "scoringPeriodId": week},
                                              headers={"x-fantasy-filter": _json.dumps(filters)})
    except Exception:
        return {}
    return {int(e["id"]): e["status"] for e in data.get("players", []) if e.get("status") in ("WAIVERS", "FREEAGENT")}


def free_agent_rows(league: League, week: int, size: int = 300) -> list[PlayerRow]:
    rows = []
    wstat = waiver_statuses(league, week, size)
    for p in league.free_agents(week=week, size=size):
        rows.append(PlayerRow(
            espn_id=p.playerId, name=p.name, pos=p.position, team=p.proTeam,
            eligible=[s for s in p.eligibleSlots if s not in NON_STARTER],
            slot="FA", fantasy_team_id=None, injury_status=_status(p), waiver_status=wstat.get(p.playerId),
            proj_week=_week_stat(p, week, "projected_points"),
            actual_week=_week_stat(p, week, "points"),
            proj_season=p.projected_total_points,
            percent_owned=p.percent_owned, pos_rank=p.posRank,
            bye=(str(week) not in p.schedule) if p.schedule else False,
            injured=bool(getattr(p, "injured", False)), ir_eligible_raw="IR" in p.eligibleSlots,
        ))
    return rows


def pending_trades(league: League, my_team_id: int | None) -> list[dict[str, Any]]:
    """Trade proposals still open on ESPN that involve my team, parsed from the raw `mPendingTransactions` view.

    espn-api's Transaction wrapper drops the item direction and the proposal id, so this reads the JSON directly.
    Use this view, not mTransactions2: the history view keeps showing a proposal as PENDING after it was declined
    (what it does expose about a closed proposal is read by `trade_resolutions`).
    An accepted trade stays in this view through the league's review period: the same record, same id, with its
    `type` turned from TRADE_PROPOSAL to TRADE_ACCEPT, every team ACCEPTED in `teamActions` and `processDate` the
    clock ESPN executes it on (probed 2026-10-13: accepted Tuesday morning, processed Wednesday 8:05 AM Denver).
    Until that date the players are still on their old rosters; ESPN's app lists it under Pending Trades as
    "Accepted". It used to be dropped here as not-a-proposal, so the outcome log closed it as a decline.
    Each entry: {id, proposer_team_id, proposed_ts, expires_ts, process_ts, status, accepted, team_actions,
    direction, give, get} where `give` are espn player ids leaving my roster and `get` are ids joining it (direction
    is from my point of view) and `accepted` says both sides have said yes and only the processing is left.
    """
    if my_team_id is None:
        return []
    raw = league.espn_request.league_get(params={"view": "mPendingTransactions"})
    out = []
    for tx in raw.get("pendingTransactions") or []:
        if tx.get("type") not in ("TRADE_PROPOSAL", "TRADE_ACCEPT"):
            continue
        items = [i for i in tx.get("items", []) if i.get("type") == "TRADE"]
        give = [i["playerId"] for i in items if i.get("fromTeamId") == my_team_id]
        get = [i["playerId"] for i in items if i.get("toTeamId") == my_team_id]
        if not give and not get:
            continue
        proposer = tx.get("teamId")
        other = {i.get("fromTeamId") for i in items} | {i.get("toTeamId") for i in items}
        other.discard(my_team_id); other.discard(None)
        out.append({
            "id": tx.get("id"), "proposer_team_id": proposer,
            "rival_team_id": next(iter(other), None) if proposer == my_team_id else proposer,
            "proposed_ts": tx.get("proposedDate"), "expires_ts": tx.get("expirationDate"),
            "process_ts": tx.get("processDate") if tx.get("type") == "TRADE_ACCEPT" else None,
            "status": tx.get("status"), "accepted": tx.get("type") == "TRADE_ACCEPT",
            "team_actions": tx.get("teamActions") or {},
            "direction": "outgoing" if proposer == my_team_id else "incoming",
            "give": give, "get": get,
        })
    return out


def trade_resolutions(league: League, my_team_id: int | None) -> dict[str, dict[str, Any]]:
    """How ESPN closed each trade proposal, from the `mTransactions2` history view: {proposal id: {status, by, ts}}.

    Probed live 2026-10-08 (three leagues). The proposal itself stays PENDING in that view forever, which is why
    `pending_trades` reads mPendingTransactions; the terminal state is a separate record pointing back at it through
    `relatedTransactionId`. A decline is a `TRADE_DECLINE` (teamId and memberId the rival's) plus a copy of the
    proposal with `executionType: CANCEL`, `status: CANCELED` carrying his member id, same millisecond. A lapse is the
    CANCEL copy alone under a member id that owns no team (ESPN's job, 51 s after `expirationDate`). A proposal I
    withdraw in the app has not been observed (this code never writes); by the same pattern it is the CANCEL copy with
    my member id and no decline. An acceptance is a `TRADE_ACCEPT` record under the rival's member id pointing back
    at the proposal (probed 2026-10-13; its items are the drops his roster needed to fit the trade), written the moment
    he clicks, before the review period runs out, and it wins over any cancel copy. `status` is accepted / declined /
    withdrawn / expired, `by` the team id of the member who did it (None for ESPN), `ts` the record's clock. Every
    proposal the view relates to is listed; the caller looks up its own.
    """
    if my_team_id is None:
        return {}
    owners: dict[str, int] = {}
    for t in league.teams:
        for o in t.owners:
            oid = str(o.get("id", "")) if isinstance(o, dict) else str(o)
            if oid:
                owners[oid.upper()] = t.team_id
    swid = (env().swid or "").strip().upper()
    if swid:
        owners.setdefault(swid, my_team_id)
    raw = league.espn_request.league_get(params={"view": "mTransactions2"})
    out: dict[str, dict[str, Any]] = {}
    for tx in raw.get("transactions") or []:
        rel = tx.get("relatedTransactionId")
        if not rel:
            continue
        by = owners.get(str(tx.get("memberId") or "").upper())
        if tx.get("type") == "TRADE_ACCEPT":
            out[rel] = {"status": "accepted", "by": by, "ts": tx.get("proposedDate")}
        elif tx.get("type") == "TRADE_DECLINE" and (out.get(rel) or {}).get("status") != "accepted":
            out[rel] = {"status": "declined", "by": by, "ts": tx.get("proposedDate")}   # his answer, whatever the copy says
        elif tx.get("type") == "TRADE_PROPOSAL" and tx.get("executionType") == "CANCEL" and rel not in out:
            status = "withdrawn" if by == my_team_id else "declined" if by is not None else "expired"
            out[rel] = {"status": status, "by": by, "ts": tx.get("proposedDate")}
    return out


def snapshot(ref: LeagueRef, league: League, week: int) -> dict[str, Any]:
    """Everything the model needs from one league, JSON-serializable."""
    me = my_team(league, ref.team_id)
    teams = []
    for t in league.teams:
        teams.append({
            "team_id": t.team_id, "name": t.team_name, "abbrev": t.team_abbrev,
            "owners": [o.get("displayName") if isinstance(o, dict) else str(o) for o in t.owners],
            "wins": t.wins, "losses": t.losses, "ties": t.ties,
            "points_for": t.points_for, "points_against": t.points_against,
            "faab_spent": t.acquisition_budget_spent, "waiver_rank": t.waiver_rank,
            # ESPN's transaction counter: a manager who has traded this season takes offers; one with no moves at all does not
            "trades": int(getattr(t, "trades", 0) or 0), "acquisitions": int(getattr(t, "acquisitions", 0) or 0),
            "streak": f"{t.streak_type}{t.streak_length}", "seed": t.standing,
            "espn_playoff_pct": t.playoff_pct,
            "schedule": [getattr(o, "team_id", None) for o in t.schedule],
            "scores": t.scores, "outcomes": t.outcomes,
            "is_me": bool(me and t.team_id == me.team_id),
        })
    matchups = []
    for m in league.scoreboard(week):
        matchups.append({"home": m.home_team.team_id, "away": getattr(m.away_team, "team_id", None),
                         "home_proj": getattr(m, "home_projected", None), "away_proj": getattr(m, "away_projected", None),
                         "home_score": getattr(m, "home_score", None), "away_score": getattr(m, "away_score", None)})
    # FAAB history: all bids incl. losing ones
    bids = []
    try:
        for tx in league.transactions(types={"WAIVER", "WAIVER_ERROR"}):
            bids.append({"team_id": tx.team.team_id, "status": tx.status, "bid": tx.bid_amount,
                         "week": tx.scoring_period,
                         "items": [{"type": i.type, "player_id": i.playerId, "player": str(i.player)} for i in tx.items]})
    except Exception:
        pass
    # Open trade proposals. Keep the error separate from "no offers" so a changed API is visible in the briefing.
    pending, pending_err = [], None
    try:
        pending = pending_trades(league, me.team_id if me else None)
    except Exception as exc:
        pending_err = f"{type(exc).__name__}: {exc}"
    # How ESPN closed the proposals that are gone (declined / withdrawn / expired): the outcome log's first choice,
    # with the roster and the clock as the fallback when this view cannot be read.
    resolutions, resolutions_err = {}, None
    try:
        resolutions = trade_resolutions(league, me.team_id if me else None)
    except Exception as exc:
        resolutions_err = f"{type(exc).__name__}: {exc}"
    return {
        "ref": asdict(ref), "week": week, "settings": asdict(settings(ref, league)),
        "my_team_id": me.team_id if me else None,
        "teams": teams, "matchups": matchups,
        "roster": [asdict(r) for r in roster_rows(league, week)],
        "free_agents": [asdict(r) for r in free_agent_rows(league, week)],
        "faab_bids": bids,
        "pending_trades": pending, "pending_trades_error": pending_err,
        "trade_resolutions": resolutions, "trade_resolutions_error": resolutions_err,
    }
