"""Blend projection sources into a per-player weekly distribution.

X ~ (1 - p_zero) * N(mu, sigma^2) + p_zero * delta(0)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil

import numpy as np

# Position-level projection-to-actual error (weekly points, PPR-ish). Rough priors from
# published accuracy studies; scaled by the FantasyPros rank-sd where available.
# `sigma = max(SIGMA_FLOOR_FRAC * BASE_SIGMA[pos], CV_FLOOR[pos] * mu)` (see `_sigma`); `ff accuracy --fit`
# (`projlog.sigma_fit`) fits both numbers per position to the projlog residuals in exactly this form and prints them next
# to these, but writes nothing: a position is fitted only at `projlog.FIT_MIN_N` scored forecasts, and as of 2026-10-08
# (weeks 1-4 scored: QB 18, RB 49, WR 46, TE 21, K 10, D/ST 10) none is, so every value here is still the prior.
BASE_SIGMA = {"QB": 7.0, "RB": 6.5, "WR": 6.5, "TE": 5.0, "K": 4.0, "D/ST": 5.5}
# Coefficient of variation floor so that low projections still have spread.
CV_FLOOR = {"QB": 0.30, "RB": 0.45, "WR": 0.50, "TE": 0.55, "K": 0.45, "D/ST": 0.60}
# The absolute floor is this fraction of BASE_SIGMA (the fitter reports BASE_SIGMA, never the floor itself).
SIGMA_FLOOR_FRAC = 0.6

# Injury designation -> probability of a zero (inactive). LLM may override via packet.
P_ZERO = {
    None: 0.02, "ACTIVE": 0.02, "NORMAL": 0.02, "PROBABLE": 0.08, "QUESTIONABLE": 0.30, "DAY_TO_DAY": 0.15,
    "DOUBTFUL": 0.80, "OUT": 1.0, "INJURY_RESERVE": 1.0, "IR": 1.0, "PUP": 1.0, "SUSPENSION": 1.0,
    "SUS": 1.0, "NA": 1.0, "DNR": 1.0, "COV": 1.0,
}
# A Questionable tag early in the week is last week's, carried until Wednesday's first practice report. Benching a
# starter on Tuesday for a 30% that is really a 15% churns the lineup; the Friday number is the 30%. Keyed by
# weekday (Monday = 0); days not listed use P_ZERO. Claude's `p_zero` override still wins.
QUESTIONABLE_BY_WEEKDAY = {0: 0.15, 1: 0.15, 2: 0.15, 3: 0.20}

# Designations that mean more than this week. NFL injured reserve and reserve/PUP are a four-game minimum, so 4 is a
# floor rather than an estimate; a suspension's length is public, so Claude sets it in overrides. Single-week
# designations fall out of `p_zero` on their own (Questionable is 0.3 of a game, Doubtful 0.8, Out 1.0).
MULTI_WEEK = {"INJURY_RESERVE": 4, "IR": 4, "PUP": 4, "NA": 1, "DNR": 1, "COV": 1, "SUSPENSION": 1, "SUS": 1}
# Sleeper's roster status (not its injury designation) is the freshest "he is on NFL IR" signal.
SLEEPER_ROSTER_MULTI_WEEK = {"Injured Reserve": 4, "Physically Unable to Perform": 4, "Non Football Injury": 4}
ACTIVE_STATUSES = (None, "", "ACTIVE", "NORMAL")

FLEX_LABELS = {"RB/WR/TE", "RB/WR", "WR/TE", "OP"}

# ESPN's `proj_season` is a full-season total, so its per-game reading is over the NFL's 17 games, never over the
# weeks left in the fantasy season (that read 18.9 a game for a 14.5-a-game receiver in week 5 and grew every week).
NFL_GAMES = 17
# The preseason season total is a prior on a player's level that his own games replace: full weight before his first
# game, gone after this many.
PRIOR_FADE_GAMES = 6


@dataclass
class PlayerProj:
    espn_id: int
    name: str
    pos: str
    team: str
    eligible: list[str]
    fantasy_team_id: int | None
    slot: str
    mu: float
    sigma: float
    p_zero: float
    mu_ros: float
    bye: bool = False
    sources: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    locked: bool = False          # game started/finished: ESPN won't let this player change slots
    pending_trade: str | None = None  # "in" / "out": moved by an accepted trade ESPN has not processed yet (packet.apply_accepted_trades)
    actual: float | None = None   # points scored so far this week (None if not started)
    mu_pre: float | None = None   # the projection before the clock banked his points: what the accuracy log scores
    sigma_ros: float | None = None  # pre-lock sigma, kept so rest-of-season copies aren't deterministic
    # Injury horizon. `mu_ros` is what the roster spot yields per remaining week; `mu_ros_active` is what he scores in
    # the games he plays. They differ by `avail_ros`, the share of remaining weeks he is expected to be available.
    weeks_out: float = 0.0        # expected games missed from this week on (0.3 for a Questionable, 4+ for IR)
    avail_ros: float = 1.0        # (weeks_remaining - weeks_out) / weeks_remaining, floored at 0
    mu_ros_active: float | None = None  # per-game expectation when he plays; defaults to mu_ros
    return_week: int | None = None      # first matchup week he is back, None if not out or not back this season
    bye_weeks: list[int] = field(default_factory=list)  # NFL bye weeks for his team (one, normally); [] when unknown

    def __post_init__(self):
        if self.mu_ros_active is None:
            self.mu_ros_active = self.mu_ros

    def ros(self, p_zero: float = 0.05) -> PlayerProj:
        """Rest-of-season view: per-game expectation, no lock, no bye, normal variance."""
        return PlayerProj(**{**self.__dict__, "mu": self.mu_ros, "p_zero": p_zero, "bye": False,
                             "locked": False, "actual": None, "sigma": self.sigma_ros or self.sigma})

    @property
    def ev(self) -> float:
        return (1 - self.p_zero) * self.mu

    @property
    def var(self) -> float:
        # variance of the mixture
        m, s2, q = self.mu, self.sigma**2, 1 - self.p_zero
        return q * (s2 + m * m) - (q * m) ** 2

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ev"] = round(self.ev, 2)
        d["sd"] = round(self.var**0.5, 2)
        return d


def sigma_shape(mu, base: float, cv: float):
    """The positional sigma before the FantasyPros rank-sd fudge: `max(SIGMA_FLOOR_FRAC * base, cv * mu)`.

    Works on a float or a numpy array of `mu`; the fitter in `projlog` minimises the Gaussian log-likelihood of this
    same shape, so a fitted pair drops straight into BASE_SIGMA / CV_FLOOR."""
    return np.maximum(SIGMA_FLOOR_FRAC * base, cv * np.asarray(mu, dtype=float))


def _sigma(pos: str, mu: float, fp_sd: float | None) -> float:
    base = BASE_SIGMA.get(pos, 6.0)
    cv = CV_FLOOR.get(pos, 0.5)
    s = float(sigma_shape(mu, base, cv))
    if fp_sd is not None and np.isfinite(fp_sd):
        # rank-sd of ~1 is tight consensus; ~6+ is real disagreement. Scale 0.85x..1.4x
        s *= float(np.clip(0.8 + 0.1 * fp_sd, 0.85, 1.4))
    return float(s)


VEGAS_K = {"QB": 0.35, "RB": 0.3, "WR": 0.35, "TE": 0.3, "K": 0.4, "D/ST": 0.0}
LEAGUE_AVG_IMPLIED = 23.0

# Wind at kickoff (`sources/weather.py`, Open-Meteo 10 m wind in mph) discounts this week's number for the players
# the ball has to fly for. The public play-by-play studies (FiveThirtyEight's and 4for4's wind pieces, the Fantasy
# Points weather reviews) agree on the shape and roughly on the size: passing yards, completion rate and field-goal
# rate are flat up to the mid-teens and fall from there; at 20+ mph a QB's fantasy output lands about 10-15% under
# projection, and kickers lose more because the long attempts stop being tried. The ramp is linear from
# WIND_MPH_START to WIND_MPH_CAP and flat after it; the cap penalty is the small end of those figures, since every
# projection source already glances at the forecast by Sunday morning and the cost of a wrong number is a benching.
# Domes and retractable roofs are exempt (`dome` in the forecast), a missing or unavailable forecast is calm, and
# the rest-of-season numbers never see it: wind is a matchup, not a level.
WIND_MPH_START = 15.0
WIND_MPH_CAP = 25.0
WIND_PENALTY_AT_CAP = {"QB": 0.10, "WR": 0.10, "TE": 0.10, "K": 0.15}   # RB and D/ST: no penalty


def wind_mult(pos: str, weather: dict | None) -> float:
    """This week's multiplier for the kickoff forecast: 1.0 for a dome, a missing forecast, a calm day or a position the
    wind does not touch; 1 - penalty at WIND_MPH_CAP and above, linear in between."""
    if not weather or weather.get("dome") or weather.get("unavailable"):
        return 1.0
    wind = _num(weather.get("wind_mph"))
    pen = WIND_PENALTY_AT_CAP.get(pos, 0.0)
    if wind is None or wind <= WIND_MPH_START or not pen:
        return 1.0
    frac = min((wind - WIND_MPH_START) / (WIND_MPH_CAP - WIND_MPH_START), 1.0)
    return 1.0 - pen * frac


def season_pg(ytd_pg: float | None, games: int, prior_pg: float | None) -> float | None:
    """A player's healthy per-game level from the season so far: his own points per game played, with ESPN's
    preseason total (per game) as a prior that fades out over his first PRIOR_FADE_GAMES games. None without either."""
    if ytd_pg is None or games <= 0:
        return prior_pg
    if not prior_pg:
        return ytd_pg
    w_prior = max(0.0, 1.0 - games / PRIOR_FADE_GAMES)
    return w_prior * prior_pg + (1 - w_prior) * ytd_pg


def per_game(season: float | None, weekly: float, plays_this_week: bool) -> float:
    """Rest-of-season per-game expectation: the season level shrunk halfway toward this week's projection when he is
    expected to play it (this week's number carries the freshest read on his role), the season level alone when
    he is not (a player listed Out projects ~0 this week, which says nothing about his healthy level)."""
    if season is None:
        return weekly
    if plays_this_week and weekly:
        return 0.5 * season + 0.5 * weekly
    return season


def _num(x) -> float | None:
    """A number from a source column, or None: the FantasyPros mirror writes NA for a missing value and a column with
    one NA in it arrives as text, so a missing consensus must read as missing, not crash the packet."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def blend(row: dict, fp: dict | None, sleeper: dict | None, weeks_remaining: int, overrides: dict | None = None,
          sleeper_pts: float | None = None, implied_total: float | None = None, week: int | None = None,
          weekday: int | None = None, weather: dict | None = None) -> PlayerProj:
    """
    row: PlayerRow dict from sources.espn. fp: matched fp_latest_weekly row. sleeper: injury_table row.
    sleeper_pts: Sleeper/Rotowire stat projection in this league's scoring. implied_total: Vegas implied team total.
    week: the current matchup week, used only to date `return_week`. weekday: today (Monday = 0), which scales a
    Questionable early in the week; None means the Friday number. weather: the kickoff forecast for his game
    (`sources.weather.forecast`), None when unknown; only its wind moves a number (`wind_mult`), this week only.

    Overrides (Claude's parameters, never decisions): `p_zero` and `mu_mult` are this week only; `weeks_out` (a number,
    or "season") and `ros_mult` are the rest of the season. Code owns the arithmetic between them.

    Blend rule (per FFA's 12-season finding that equal-weight averaging beats accuracy-weighting):
    equal-weight mean of the stat-based sources available (ESPN/Clay, Sleeper/Rotowire). FantasyPros rank-to-points
    (r2p_pts) is a rank lookup, not a stat projection, so it is a fallback only. Vegas implied total is applied as a
    small multiplicative adjuster, never averaged in (every expert already looks at the line).
    """
    pos = row["pos"]
    espn_pts = float(row.get("proj_week") or 0)
    fp_pts = _num(fp.get("r2p_pts")) if fp else None
    fp_sd = _num(fp.get("sd")) if fp else None

    stat_srcs = [x for x in (espn_pts, sleeper_pts) if x is not None and x > 0]
    if stat_srcs:
        mu = sum(stat_srcs) / len(stat_srcs)
        if fp_pts and len(stat_srcs) == 1:
            mu = 0.7 * mu + 0.3 * fp_pts   # one stat source: let consensus rank temper it
    elif fp_pts:
        mu = fp_pts
    else:
        mu = 0.0
    mu_neutral = mu   # the blend before this week's matchup (Vegas, the opponent) is applied: the rest of the season's input
    if implied_total and mu and VEGAS_K.get(pos, 0):
        mu *= 1 + VEGAS_K[pos] * (implied_total / LEAGUE_AVG_IMPLIED - 1)
    wx_mult = wind_mult(pos, weather)
    mu *= wx_mult

    status = (row.get("injury_status") or "").upper() or None
    sl_status = ((sleeper or {}).get("status") or "").upper() or None
    # Sleeper tends to be fresher on designations; take the more severe of the two.
    p_q = QUESTIONABLE_BY_WEEKDAY.get(weekday, P_ZERO["QUESTIONABLE"]) if weekday is not None else P_ZERO["QUESTIONABLE"]
    p_zero = {**P_ZERO, "QUESTIONABLE": p_q}
    p0 = max(p_zero.get(status, 0.05), p_zero.get(sl_status, 0.02))
    ov = (overrides or {}).get(str(row["espn_id"]), {})
    inj_p0 = float(ov["p_zero"]) if "p_zero" in ov else p0  # sit risk from the injury alone, before the bye
    flags = []
    if row.get("bye"):
        mu, p0 = 0.0, 1.0
        flags.append("bye")
    if status and status not in ("ACTIVE", "NORMAL"):
        flags.append(f"espn:{status}")
    if sl_status and sl_status not in ("ACTIVE",):
        flags.append(f"sleeper:{sl_status}" + (f" ({sleeper.get('body_part')})" if sleeper and sleeper.get("body_part") else ""))
    if fp and fp.get("start_sit_grade"):
        flags.append(f"fp:{fp['start_sit_grade']}")
    wind_mph = _num((weather or {}).get("wind_mph"))
    if wx_mult < 1.0:
        flags.append(f"wind:{wind_mph:.0f}mph x{wx_mult:.2f}")

    sl_roster = (sleeper or {}).get("roster_status") or ""
    designated = (status not in ACTIVE_STATUSES or sl_status not in ACTIVE_STATUSES or sl_roster in SLEEPER_ROSTER_MULTI_WEEK
                  or "p_zero" in ov)

    # Rest-of-season per-game expectation (the healthy, when-he-plays number): his season so far per game played,
    # ESPN's full-season total per NFL game as the fading prior, shrunk toward this week's matchup-neutral blend when
    # he is expected to play this week. `weeks_remaining` is not in the denominator: ESPN's total is a season figure,
    # not what is left of it. `espn_pg` is the same arithmetic on ESPN's own numbers: what a rival's app shows him.
    games = int(row.get("games_played") or 0)
    ytd_pg = float(row.get("actual_season") or 0) / games if games > 0 else None
    prior_pg = float(row.get("proj_season") or 0) / NFL_GAMES or None
    plays = inj_p0 < 0.5 and not row.get("bye")
    level = season_pg(ytd_pg, games, prior_pg)
    mu_ros_active = per_game(level, mu_neutral, plays)
    espn_pg = per_game(level, espn_pts, plays) if level is not None else None
    if "p_zero" in ov and not row.get("bye"):
        p0 = float(ov["p_zero"]); flags.append("llm:p_zero")
    if "mu_mult" in ov:
        mu *= float(ov["mu_mult"]); flags.append("llm:mu")   # this week only, by design (see docs/plans)
    if "ros_mult" in ov:
        mu_ros_active *= float(ov["ros_mult"]); flags.append("llm:ros")

    # Games missed from this week on. Default from the designations; Claude's `weeks_out` wins.
    W = max(weeks_remaining, 1)
    if "weeks_out" in ov:
        weeks_out = float(W if str(ov["weeks_out"]).lower() == "season" else ov["weeks_out"]); flags.append("llm:weeks_out")
    elif designated:
        weeks_out = max(inj_p0, MULTI_WEEK.get(status, 0), MULTI_WEEK.get(sl_status, 0), SLEEPER_ROSTER_MULTI_WEEK.get(sl_roster, 0))
    else:
        weeks_out = 0.0
    weeks_out = min(max(weeks_out, 0.0), float(W))
    avail_ros = (W - weeks_out) / W
    mu_ros = mu_ros_active * avail_ros
    return_week = week + ceil(weeks_out) if (week is not None and 1 <= weeks_out < W) else None

    return PlayerProj(
        espn_id=row["espn_id"], name=row["name"], pos=pos, team=row["team"], eligible=list(row["eligible"]),
        fantasy_team_id=row.get("fantasy_team_id"), slot=row.get("slot", ""),
        mu=round(mu, 2), sigma=round(_sigma(pos, mu, fp_sd), 2), p_zero=round(p0, 3), mu_ros=round(mu_ros, 2),
        bye=bool(row.get("bye")),
        weeks_out=round(weeks_out, 2), avail_ros=round(avail_ros, 3), mu_ros_active=round(mu_ros_active, 2), return_week=return_week,
        bye_weeks=sorted({int(w) for w in (row.get("bye_weeks") or [])} | ({week} if row.get("bye") and week else set())),
        sources={"fp_pts": fp_pts, "espn_pts": espn_pts, "sleeper_pts": sleeper_pts, "implied_total": implied_total,
                 # kickoff wind and the this-week multiplier it earned (1.0 for a dome, calm air or no forecast)
                 "wind_mph": wind_mph, "weather_mult": round(wx_mult, 3),
                 # ESPN's own rest-of-season number per game: what a rival's app shows him, injury docking included
                 "espn_pg": round(espn_pg, 2) if espn_pg is not None else None,
                 "ytd_pg": round(ytd_pg, 2) if ytd_pg is not None else None, "games_played": games,
                 "actual_prev": row.get("actual_prev_week"),   # ESPN's final for last week: the accuracy log's truth
                 "pos_rank": row.get("pos_rank"),
                 "ecr": fp.get("ecr") if fp else None, "fp_sd": fp_sd,
                 "grade": fp.get("start_sit_grade") if fp else None, "espn_status": status, "sleeper_status": sl_status,
                 "sleeper_notes": (sleeper or {}).get("notes"), "sleeper_roster_status": sl_roster or None,
                 "body_part": (sleeper or {}).get("body_part"), "percent_owned": row.get("percent_owned"),
                 "waiver_status": row.get("waiver_status"),
                 "override_note": ov.get("note"), "weeks_out_source": "override" if "weeks_out" in ov else "default"},
        flags=flags,
    )
