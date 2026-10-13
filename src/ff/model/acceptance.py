"""Would he say yes? A transparent scorecard, not a classifier.

A trade only has value if the rival accepts it, and the lineup delta the scan computes with my projections is a number
he never sees. What he sees: the chart he opens (taxed for an uneven package), whether he gets the best player, whether
the piece starts for him, what he is left with at the slot, whether he has to cut a body, his own app's grade of the
incoming player, and whether he is the kind of manager who trades at all. Each term is a factor with a one-line reason
the row can print; the product, clamped, is `p_accept`. The starting values come from the research in
docs/research/trade-acceptance.md and are meant to be tuned against the outcome log, not believed to two decimals.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .projections import PlayerProj

BASE = 0.5
P_MAX = 0.95
# Below this a package is not a row; at or above SENDABLE it is; a push needs PUSH.
SENDABLE = 0.35
PUSH = 0.55
# Words for the card, from fixed buckets: a number to two decimals would be a lie about how much we know.
BUCKETS = ((0.6, "he'd likely take it"), (SENDABLE, "coin flip"))


@dataclass
class Signals:
    fair_his: float | None = None      # chart ratio from his side, taxed (trades.fairness)
    best_side: str | None = None       # "theirs" when he gets the single best player, "mine" when I do
    n_give: int = 1
    n_get: int = 1
    starts_for_him: float = 0.0        # share of weighted weeks the best incoming piece starts for him
    asks_starter: int = 0              # pieces I ask for that he starts today
    cannot_field: bool = False         # he could not fill a slot after
    thin_after: list[str] = field(default_factory=list)   # QB/TE with no healthy backup after, that had one before
    streams_this_week: list[str] = field(default_factory=list)  # QB/TE/K/DST slots the wire fills for him this Sunday after (a bye, an injury)
    squeeze: bool = False              # 2-for-1 onto a full roster: he cuts someone
    need_match: bool = False           # the position he receives is his weakest starting slot by his numbers
    app_grade: str | None = None       # "above" / "below" his positional average on ESPN's numbers
    name_value_drop: bool = False      # the piece he gets is far less owned than the one he gives
    playoff_pct: float | None = None   # his sim odds
    fills_hole: bool = False           # the position he receives is below replacement for him
    trades: int | None = None          # ESPN transaction counter
    acquisitions: int | None = None
    recent_decline: bool = False       # he declined an offer of mine in the last week (a lapse or a counter is not that)
    prior: float | None = None         # per-rival acceptance prior from the outcome log, if any


def score(s: Signals) -> tuple[float, list[str]]:
    """(p_accept, reasons). Reasons are the factors that moved it, worded for the row. The factors multiply, so three
    mild negatives sink a package the way three small doubts sink a real offer; the hard rules in `trades.scan`
    (lowball, star, one QB, cannot field) never reach here."""
    p = BASE
    why: list[str] = []

    def mul(f: float, reason: str | None = None) -> None:
        nonlocal p
        p *= f
        if reason:
            why.append(reason)

    if s.cannot_field:
        return 0.0, ["he could not fill a slot after"]
    if s.fair_his is not None:
        if s.fair_his < 0.9:
            mul(0.3, f"reads as a lowball on his chart ({s.fair_his:.2f})")
        elif s.fair_his < 0.95:
            mul(0.8, f"a shade under on his chart ({s.fair_his:.2f})")
        elif s.fair_his > 1.15:
            mul(1.1)
    # The best player in the deal decides an uneven package; a 1-for-1 is already judged by the chart ratio.
    if s.n_give != s.n_get:
        if s.best_side == "theirs":
            mul(1.3, "he gets the best player in the deal")
        elif s.best_side == "mine":
            mul(0.6, "I get the best player in the deal")
    if s.app_grade == "below":
        mul(0.7, "his app grades the piece below what he has at the position")
    elif s.app_grade == "above":
        mul(1.15)
    if s.name_value_drop:
        mul(0.8, "the name he gets is a lot less owned than the one he gives")
    if s.starts_for_him >= 0.5:
        mul(1.25, "it starts for him")
    elif s.asks_starter:
        mul(0.5, "it rides his bench and I ask for his starter")
    else:
        mul(0.85, "bench for bench")
    if s.asks_starter and s.starts_for_him >= 0.5:
        mul(0.75 ** s.asks_starter, "I ask for a player he starts today")
    if s.thin_after:
        mul(0.85, f"leaves him no backup {', '.join(s.thin_after)}")
    if s.streams_this_week:
        # Oct 13: Purdy asked from a roster whose other QB (Burrow) was on bye that week. He would start a streamer on
        # Sunday to make the deal; nobody does that mid-week. Next week the same ask is a different trade.
        mul(0.5, f"he'd have to stream {', '.join(s.streams_this_week)} this week")
    if s.squeeze:
        mul(0.75, "he has to cut a body to take two")
    if s.need_match:
        mul(1.25, "it is his weakest slot")
    if s.playoff_pct is not None:
        if s.playoff_pct < 20:
            mul(0.85, "he is out of it and sellers go quiet")
        elif s.playoff_pct > 50 and s.fills_hole:
            mul(1.15, "a contender with a hole")
    if s.trades is not None and s.acquisitions is not None:
        if s.trades >= 1:
            mul(1.15, "he has traded this season")
        elif s.acquisitions < 3:
            mul(0.8, "he has barely touched his roster")
    if s.recent_decline:
        mul(0.7, "he turned one down this week")
    if s.prior is not None:
        # A per-rival prior from his answers so far, blended in rather than trusted outright.
        mul(0.5 + s.prior, None)
    return round(min(max(p, 0.0), P_MAX), 3), why


def bucket(p: float | None) -> str | None:
    if p is None:
        return None
    for floor, word in BUCKETS:
        if p >= floor:
            return word
    return None


def his_view(p: PlayerProj) -> float:
    """What his app shows him for a player: ESPN's own rest-of-season per-game number when we have it."""
    v = (p.sources or {}).get("espn_pg")
    return float(v) if v else float(p.mu_ros_active or p.mu_ros or 0.0)


def app_grade(incoming: list[PlayerProj], roster: list[PlayerProj]) -> str | None:
    """ESPN's trade grade compares the incoming player with the average of what he already has at the position.
    The best incoming piece against his healthy average at that position: 'above' or 'below', None without data."""
    if not incoming:
        return None
    best = max(incoming, key=his_view)
    have = [his_view(p) for p in roster if p.pos == best.pos and p.weeks_out < 1 and his_view(p) > 0]
    if not have:
        return "above"
    return "above" if his_view(best) >= sum(have) / len(have) else "below"


def name_value_drop(incoming: list[PlayerProj], outgoing: list[PlayerProj], gap: float = 25.0) -> bool:
    def owned(p: PlayerProj) -> float | None:
        v = (p.sources or {}).get("percent_owned")
        return float(v) if v is not None else None
    ins = [owned(p) for p in incoming if owned(p) is not None]
    outs = [owned(p) for p in outgoing if owned(p) is not None]
    return bool(ins and outs and max(ins) < max(outs) - gap)
