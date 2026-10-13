"""Environment, league registry, and per-league settings snapshot."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
PACKET_DIR = DATA_DIR / "packets"

load_dotenv(ROOT / ".env")


@dataclass
class LeagueRef:
    name: str
    espn_id: int
    team_id: int | None = None
    discord_webhook: str | None = None


@dataclass
class Env:
    espn_s2: str | None
    swid: str | None
    season: int
    discord_webhook: str | None

    @property
    def has_cookies(self) -> bool:
        return bool(self.espn_s2 and self.swid)


def env() -> Env:
    return Env(
        espn_s2=os.getenv("ESPN_S2") or None,
        swid=os.getenv("SWID") or None,
        season=int(os.getenv("SEASON", "2026")),
        discord_webhook=os.getenv("DISCORD_WEBHOOK") or None,
    )


def leagues(only: str | None = None) -> list[LeagueRef]:
    path = ROOT / "leagues.toml"
    if not path.exists():
        raise SystemExit("leagues.toml not found: copy leagues.example.toml to leagues.toml and fill in your league ids "
                         "(or run `ff briefing --demo` for a synthetic league)")
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    refs = [LeagueRef(**{k: v for k, v in x.items() if k in LeagueRef.__dataclass_fields__}) for x in raw.get("league", [])]
    refs = [r for r in refs if r.espn_id]
    if only:
        refs = [r for r in refs if r.name == only]
        if not refs:
            raise SystemExit(f"No league named {only!r} in leagues.toml")
    if not refs:
        raise SystemExit("No leagues with a non-zero espn_id in leagues.toml (see leagues.example.toml)")
    return refs


@dataclass
class LeagueSettings:
    """Everything the model needs, read from ESPN. Never hardcode these."""
    name: str
    team_count: int
    scoring: dict[str, float]            # stat abbr -> points
    lineup_slots: dict[str, int]         # slot label -> count (starters only; BE/IR removed)
    bench_slots: int
    ir_slots: int
    reg_season_weeks: int
    playoff_team_count: int
    playoff_weeks: list[int]             # matchup periods that are playoffs
    matchup_periods: dict[int, list[int]]  # matchup period -> scoring periods
    faab: bool
    faab_budget: int
    trade_deadline_ms: int
    ppr: float = field(default=0.0)
    # ESPN's per-position roster caps ("Too many players with default position WR (maximum 6)"): pos -> max rostered,
    # IR included. Positions ESPN leaves unlimited are absent.
    position_limits: dict[str, int] = field(default_factory=dict)
    # "rolling" (a successful claim sends you to the back of the waiver order), "reset" (ESPN resets it to reverse
    # standings every week) or None when the snapshot predates the field. Prices what a claim costs in priority.
    waiver_order: str | None = None
    # The league's clocks (`model/deadlines.py`): hours a dropped player sits on waivers, the days ESPN processes
    # claims (its hour is a house constant; the settings' `waiverProcessHour` is not the run), the trade review
    # period in hours (an accepted trade executes that long after the acceptance) and the veto count.
    waiver_hours: int | None = None
    waiver_process_days: list[str] = field(default_factory=list)
    waiver_process_hour: int | None = None
    trade_revision_hours: int | None = None
    veto_votes: int | None = None
