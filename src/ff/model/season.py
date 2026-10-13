"""Rest-of-season lineup value, week by week.

`lineup_strength` prices a roster as one rest-of-season lineup over availability-weighted averages, which is how a QB on
IR for a month read as 55% of a weekly starter and nobody's slot ever went empty. Here each remaining week gets its
own greedy lineup: a player is worth his per-game number in the weeks he plays, nothing on his bye and nothing until
he is back, and the best free agent at each position sits in the pool as the body the wire would supply, so the cost
of shipping a man's only QB is the gap to the QB he could pick up, not zero and not infinity. Weeks are weighted by
`week_weight` (1 in the regular season; in the fantasy playoffs, the team's playoff odds times `TITLE_RUN_MULT`, so a
dead team prices a week-16 bye at nothing and a lock prices it above a regular week; `injuries.week_weights` is the
same number), and the result is in points per week so nothing downstream changes units.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .projections import PlayerProj

FIXED = ("QB", "RB", "WR", "TE", "K", "D/ST")
# Free agents per position kept in the fallback pool. One is the body the wire supplies; the second covers his bye.
FA_PER_POS = 2
# What a playoff week is worth to a team that is sure to be there, in regular-season weeks. The weight of a playoff
# week is `playoff_pct × TITLE_RUN_MULT`: a week-16 game decides the title and there is no next week to make up a
# bad one, so a lock weights it 1.5 regular weeks, a 67% team a plain week, a 20% team 0.3 and a dead team nothing.
# The playoff week set is the league's (`settings.playoff_weeks`), never a hard-coded 15-17.
TITLE_RUN_MULT = 1.5


def playoff_week_set(week: int, weeks_remaining: int, reg_season_weeks: int | None = None,
                     playoff_weeks: list[int] | frozenset[int] | None = None) -> frozenset[int]:
    """The remaining weeks that are fantasy playoff weeks: the league's `playoff_weeks` when the settings give them
    (an empty list means no playoffs), else every week past `reg_season_weeks`, else none."""
    if playoff_weeks is not None:
        return frozenset(int(t) for t in playoff_weeks)
    if reg_season_weeks is None:
        return frozenset()
    return frozenset(t for t in range(week, week + max(int(weeks_remaining), 1)) if t > reg_season_weeks)


def week_weight(t: int, playoff_weeks: frozenset[int], playoff_pct: float | None) -> float:
    """Weight of matchup week `t`: 1 in the regular season; in the playoffs my odds of playing it times
    `TITLE_RUN_MULT`, or a plain week when the odds are unknown."""
    if t not in playoff_weeks or playoff_pct is None:
        return 1.0
    # float(): the sim hands over numpy floats, and a numpy bool downstream serialises as the string "False",
    # which every reader of the packet takes for true (the 2026-10-07 email pushed three trades that way).
    return round(TITLE_RUN_MULT * float(playoff_pct) / 100.0, 4)


@dataclass
class SeasonCtx:
    week: int
    weeks_remaining: int
    weights: list[float] = field(default_factory=list)   # per remaining week, len == weeks_remaining
    fa_pool: list[PlayerProj] = field(default_factory=list)
    playoff_weeks: frozenset[int] = frozenset()          # the remaining weeks that are fantasy playoff weeks
    playoff_weight: float | None = None                  # what one of them weighs for this team (None: no playoff week left)
    _explicit_weeks: bool = False                        # the set came from the settings, so `with_odds` keeps it

    @classmethod
    def build(cls, week: int, weeks_remaining: int, reg_season_weeks: int | None = None, playoff_pct: float | None = None,
              fas: list[PlayerProj] | None = None, playoff_weeks: list[int] | frozenset[int] | None = None) -> SeasonCtx:
        W = max(int(weeks_remaining), 1)
        po = playoff_week_set(week, W, reg_season_weeks, playoff_weeks)
        weeks = range(week, week + W)
        weights = [week_weight(t, po, playoff_pct) for t in weeks]
        if sum(weights) <= 0:
            weights = [1.0] * W
        left = po.intersection(weeks)
        return cls(week=week, weeks_remaining=W, weights=weights, fa_pool=fa_pool(fas or []), playoff_weeks=frozenset(left),
                   playoff_weight=(week_weight(min(left), po, playoff_pct) if left else None), _explicit_weeks=playoff_weeks is not None)

    def with_odds(self, reg_season_weeks: int | None, playoff_pct: float | None) -> SeasonCtx:
        """The same weeks and wire, weighted by another team's playoff odds."""
        c = SeasonCtx.build(self.week, self.weeks_remaining, reg_season_weeks, playoff_pct,
                            playoff_weeks=self.playoff_weeks if self._explicit_weeks else None)
        c.fa_pool = self.fa_pool
        return c


def fa_pool(fas: list[PlayerProj], per_pos: int = FA_PER_POS) -> list[PlayerProj]:
    """The best free agents at each position by per-game value: the bodies anyone can add."""
    out: list[PlayerProj] = []
    for pos in FIXED:
        ps = sorted([p for p in fas if p.pos == pos and (p.mu_ros_active or 0) > 0], key=lambda p: -(p.mu_ros_active or 0))
        out += ps[:per_pos]
    return out


def weekly_values(p: PlayerProj, week: int, weeks_remaining: int) -> list[float]:
    """What `p` is worth in each remaining week: his per-game number when he plays, nothing on his bye, nothing until
    he is back. A fractional `weeks_out` (0.3 for a Questionable) scales the first week; whole games missed zero
    whole weeks."""
    base = p.mu_ros_active if p.mu_ros_active is not None else p.mu_ros
    byes = set(p.bye_weeks or [])
    out = []
    for i, t in enumerate(range(week, week + max(weeks_remaining, 1))):
        if t in byes:
            out.append(0.0)
            continue
        avail = min(max((i + 1) - (p.weeks_out or 0.0), 0.0), 1.0)
        out.append(round(base * avail, 3))
    return out


def greedy_lineup(vals: list[tuple[PlayerProj, float]], slots: dict[str, int]) -> tuple[float, set[int]]:
    """(points, starter ids) of the EV-greedy lineup for one week: fixed slots first, best eligible each, then the
    flex slots from what is left. Exact for the standard RB/WR/TE flex; the full search stays in `lineup.optimize`
    for anything the card prints."""
    avail = sorted([(v, p) for p, v in vals if v > 0], key=lambda x: -x[0])
    used: set[int] = set()
    total = 0.0
    for slot in FIXED:
        n = slots.get(slot, 0)
        for v, p in avail:
            if n <= 0:
                break
            if p.espn_id in used or slot not in p.eligible:
                continue
            used.add(p.espn_id); total += v; n -= 1
    for slot, n in slots.items():
        if slot in FIXED:
            continue
        for v, p in avail:
            if n <= 0:
                break
            if p.espn_id in used or slot not in p.eligible:
                continue
            used.add(p.espn_id); total += v; n -= 1
    return total, used


def season_value(roster: list[PlayerProj], slots: dict[str, int], ctx: SeasonCtx) -> tuple[float, dict[int, float]]:
    """(weighted points per week, share of weighted weeks each rostered player starts). The free-agent pool is in the
    lineup search but not in the starts dict, which is about the roster being judged."""
    pool = list(roster) + [f for f in ctx.fa_pool if f.espn_id not in {p.espn_id for p in roster}]
    per_week = {p.espn_id: weekly_values(p, ctx.week, ctx.weeks_remaining) for p in pool}
    total_w = sum(ctx.weights) or 1.0
    acc = 0.0
    starts: dict[int, float] = {p.espn_id: 0.0 for p in roster}
    for i, w in enumerate(ctx.weights):
        if w <= 0:
            continue
        pts, used = greedy_lineup([(p, per_week[p.espn_id][i]) for p in pool], slots)
        acc += w * pts
        for pid in used:
            if pid in starts:
                starts[pid] += w / total_w
    return round(acc / total_w, 3), starts


def starters_this_week(roster: list[PlayerProj], slots: dict[str, int], ctx: SeasonCtx, pos: str) -> list[PlayerProj]:
    """Who fills `pos` in the first remaining week's lineup, free agents included (so the row can say "the wire")."""
    pool = list(roster) + [f for f in ctx.fa_pool if f.espn_id not in {p.espn_id for p in roster}]
    vals = [(p, weekly_values(p, ctx.week, 1)[0]) for p in pool]
    _, used = greedy_lineup(vals, slots)
    return [p for p in pool if p.espn_id in used and p.pos == pos]


def starters_ros(roster: list[PlayerProj], slots: dict[str, int], ctx: SeasonCtx, pos: str) -> list[PlayerProj]:
    """Who fills `pos` over the rest of the season (each week weighted as the context weighs it), free agents
    included. `starters_this_week` is one week, and a bye there makes the wire's streamer his "starter": Oct 13, BUTT
    MUNCH's Burrow on bye read as "he'd start Aaron Rodgers (the wire) at QB" on a row asking for Purdy, when Burrow
    was the QB he kept."""
    pool = list(roster) + [f for f in ctx.fa_pool if f.espn_id not in {p.espn_id for p in roster}]
    W = max(ctx.weeks_remaining, 1)
    weights = list(ctx.weights) if len(ctx.weights) == W else [1.0] * W
    vals = [(p, sum(v * w for v, w in zip(weekly_values(p, ctx.week, W), weights))) for p in pool]
    _, used = greedy_lineup(vals, slots)
    return [p for p in pool if p.espn_id in used and p.pos == pos]


def idle_this_week(p: PlayerProj, ctx: SeasonCtx) -> str | None:
    """Why `p` gives nothing in the first remaining week: "on bye this week", "out this week", else None."""
    if ctx.week in set(p.bye_weeks or []):
        return "on bye this week"
    if weekly_values(p, ctx.week, 1)[0] <= 0:
        return "out this week"
    return None
