"""Rival roster scan and trade candidate generation, evaluated by both-side lineup delta and title-odds delta."""
from __future__ import annotations

from collections import Counter
from itertools import combinations

from . import acceptance as acc
from .lineup import optimize
from .projections import PlayerProj
from .season import SeasonCtx, greedy_lineup, idle_this_week, season_value, starters_ros, weekly_values

POS_ORDER = ("QB", "RB", "WR", "TE", "K", "D/ST")
# Weeks the scan assumes when nobody tells it the season (tests, a bare snapshot).
DEFAULT_WEEKS = 12


def lineup_strength(roster: list[PlayerProj], slots: dict[str, int]) -> tuple[float, float]:
    """(mu, var) of the E[points]-optimal lineup using rest-of-season per-week expectations.

    The injury lives in `mu_ros` (games missed are already netted out), so every player gets the same small residual
    sit risk here. The old `min(p_zero, 0.15)` cap flattened "questionable this week" and "torn ACL" into one number.
    """
    L = optimize([p.ros() for p in roster], slots, objective="ev")
    return L.mu, L.var


def needs(roster: list[PlayerProj], slots: dict[str, int], repl: dict[str, float], margin: float = 0.0) -> dict:
    """Per-position surplus/hole: starters' avg value over replacement, and count of startable depth.

    A hole means the weakest starter is genuinely below what the waiver wire offers (VORP < 0). The margin is 0 on
    purpose: in a 1-QB league replacement at QB is ~18.5 points and every starting QB but the elite few sits within a
    point of it, so any cushion here labels the whole league as having a "QB hole" and the word stops meaning anything.
    """
    ros = [p.ros() for p in roster]
    L = optimize(ros, slots, objective="ev")
    starters = {p.espn_id for ps in L.assignment.values() for p in ps}
    out = {}
    for pos in POS_ORDER:
        ps = sorted([p for p in ros if p.pos == pos], key=lambda p: -p.mu)
        st = [p for p in ps if p.espn_id in starters]
        bench = [p for p in ps if p.espn_id not in starters]
        weakest_starter = min((p.mu for p in st), default=None)
        best_bench = max((p.mu for p in bench), default=None)
        out[pos] = {
            "weakest_starter": weakest_starter, "best_bench": best_bench,
            "hole": weakest_starter is not None and weakest_starter < repl.get(pos, 0) + margin,
            "surplus": best_bench is not None and best_bench > repl.get(pos, 0) + 3.0,
        }
    return out


# Positions where the flex cannot cover an absence, so the last body at the position is the whole slot.
NO_FLEX_COVER = ("QB", "TE", "K", "D/ST")

# Market value I get back as a fraction of what I ship. Below this I am the one overpaying, which is not an offer
# worth putting in front of a coworker however well the lineup math reads. Same floor `evaluate` uses on offers I receive.
MARKET_FLOOR = 0.8
# FantasyCalc reprices an injury days late. For a player I would ship who is out for weeks, judge the market checks on
# his value discounted by availability so the scan neither refuses to propose the deal ("I give more") nor proposes
# handing over a season-ender as if he were healthy. The raw figure still rides on the row as a caveat.
MARKET_INJURY_FLOOR = 0.25
# A package this good is worth nagging about: the card keeps it at the top every morning until Dustin sends it or
# says no (rulings.record_pushes). Points per week over the rest-of-season lineup; the title-odds delta is not used
# because at 1500 sims it is noise of the same size as any threshold.
MUST_TRY_PPW = 2.0
# A rival reads an offer by what the pieces are worth to him, not by my lineup math. A piece worth under this share of
# what he gives up, that would also ride his bench, is a throw-in: it carries no weight in the market check, and a
# 2-for-1 built on one is the 1-for-1 it really is, which the scan proposes on its own anyway. The lesson of the Purdy
# ask (2026-10-06): Kyler Murray at QB21 money rode in on David Montgomery's value, the lineup math priced the rival's
# backup QB at zero, and the card called QB3-for-a-spare "neutral for them". He laughed.
THROW_IN_FRAC = 0.25
# ESPN lineup slots that mean the manager is starting him today (PlayerProj.slot is ESPN's current slot).
_BENCH_SLOTS = ("BE", "IR", "")
# The side sending more bodies pays this much over the single player it gets back; PFF's redraft chart says 5-10%,
# Cummings (CBS) 10% on a 2-for-1 and 25% on a 3-for-1, KeepTradeCut and Draft Sharks strip the throw-ins first.
# Summing two WR2s to a WR1 is how McMillan + Washington for Ja'Marr Chase (8118 against 3946 + 2968) passed a 1.2x
# cap at 1.17 on 2026-10-07; taxed, the same package reads 0.77 on the chart the rival opens before he declines.
CONSOLIDATION_TAX = {2: 0.10, 3: 0.25}
# A 1-for-1 reads as even inside this (the charts call 5% even; the endowment effect and a stale price on one piece
# make 10% the band a real person tolerates); below it the receiver reads a lowball, and under twice it the package
# is not worth a row.
FAIR_BAND = 0.10
# Nobody sells a top-12 player, or one of the top three at his position, for pieces; the only version that lands is
# the one where the best player in the deal comes back to him, and even then he wants to see the premium.
STAR_OVERALL_RANK = 12
STAR_POS_RANK = 3
STAR_GIVE_RANK = 24
STAR_PREMIUM = 1.10


def _value(p: PlayerProj, values: dict[str, dict]) -> float:
    return values.get(str(p.espn_id), {}).get("redraft_value", 0) or 0


def _rank(p: PlayerProj, values: dict[str, dict], key: str) -> int | None:
    v = values.get(str(p.espn_id), {}).get(key)
    return int(v) if v else None


def fairness(recv: float, give: float, n_recv: int, n_give: int) -> float | None:
    """How a trade reads on the chart from the side that receives `recv` for `give`: 1.0 is even, below 1 - FAIR_BAND
    is a lowball. The side sending more bodies pays CONSOLIDATION_TAX over the single player it gets, so a receiver
    of two for one expects to see the premium and a sender of two expects to pay it."""
    if not recv or not give:
        return None
    if n_give > n_recv:
        return round(recv * (1 + CONSOLIDATION_TAX.get(n_give, 0.25)) / give, 3)
    if n_recv > n_give:
        return round(recv / (give * (1 + CONSOLIDATION_TAX.get(n_recv, 0.25))), 3)
    return round(recv / give, 3)


def is_star(p: PlayerProj, values: dict[str, dict]) -> bool:
    o, r = _rank(p, values, "overall_rank"), _rank(p, values, "pos_rank")
    return (o is not None and o <= STAR_OVERALL_RANK) or (r is not None and r <= STAR_POS_RANK)


def startable_qbs(roster: list[PlayerProj], repl: dict[str, float]) -> int:
    """QBs he could start this week: healthy now and at least a replacement-level body. A stash on IR and a QB4 nobody
    would roster are not the second quarterback that makes his first one tradeable."""
    floor = 0.85 * repl.get("QB", 0.0)
    return len([p for p in roster if p.pos == "QB" and p.weeks_out < 1 and (p.mu_ros_active or 0) > 0
                and (p.mu_ros_active or 0) >= floor])


def throw_ins(pieces: list[PlayerProj], other_side: float, starters: set[int], values: dict[str, dict]) -> list[PlayerProj]:
    """The pieces of a package the receiver would neither start nor price: outside his optimal lineup once the deal
    is done and under THROW_IN_FRAC of the market value he gives up. Nothing is a throw-in when the market has no
    price for his side, since then there is nothing to measure against."""
    if not other_side:
        return []
    return [p for p in pieces if p.espn_id not in starters and _value(p, values) < THROW_IN_FRAC * other_side]


def _lineup(roster: list[PlayerProj], slots: dict[str, int]):
    """(mu, starter ids) of the rest-of-season optimal lineup; `lineup_strength` for callers who also need who starts."""
    L = optimize([p.ros() for p in roster], slots, objective="ev")
    return L.mu, {p.espn_id for ps in L.assignment.values() for p in ps}


def starts_today(p: PlayerProj) -> bool:
    return p.slot not in _BENCH_SLOTS


def market_value(players: list[PlayerProj], values: dict[str, dict], discount_injured: bool = False) -> float:
    total = 0.0
    for p in players:
        v = values.get(str(p.espn_id), {}).get("redraft_value", 0) or 0
        if discount_injured and p.weeks_out >= 1:
            v *= max(p.avail_ros, MARKET_INJURY_FLOOR)
        total += v
    return round(total, 1)


def injury_caveats(players: list[PlayerProj], values: dict[str, dict]) -> list[str]:
    """One line per hurt player whose market value predates the injury."""
    out = []
    for p in players:
        v = values.get(str(p.espn_id), {}).get("redraft_value", 0) or 0
        if p.weeks_out >= 1 and v:
            wk = "the season" if p.avail_ros == 0 else f"~{p.weeks_out:.0f} wks"
            out.append(f"market still prices {p.name} healthy ({v:.0f}); he is out {wk}")
    return out


def depth(roster: list[PlayerProj], slots: dict[str, int]) -> tuple[bool, list[str]]:
    """(can_field_a_lineup, positions_with_no_backup).

    The trade math compares optimal lineups only, so shipping a body costs nothing there: trading down to one QB reads
    as free right up until that QB is hurt or on bye and the slot is empty. This is the check that math is missing.
    """
    # Only bodies that could actually start count as depth: a QB out for the season is not a backup QB, and
    # counting him hides the cost of shipping the healthy one (mu_ros is 0 for out/IR players). Nor is a QB on
    # IR for a month: `mu_ros` averages him over the season, but the slot he is meant to cover is empty next Sunday.
    counts = Counter(p.pos for p in roster if p.mu_ros > 0 and p.weeks_out < 1)
    ok, thin = True, []
    for pos in POS_ORDER:
        need = slots.get(pos, 0)
        if not need:
            continue
        if counts[pos] < need:
            ok = False
        elif counts[pos] == need and pos in NO_FLEX_COVER:
            thin.append(pos)
    return ok, thin


def _swap(roster: list[PlayerProj], out_ids: set[int], incoming: list[PlayerProj]) -> list[PlayerProj]:
    return [p for p in roster if p.espn_id not in out_ids] + list(incoming)


def over_cap(roster: list[PlayerProj], limits: dict[str, int]) -> dict[str, int]:
    """Positions where the roster holds more than ESPN's cap, and by how many. The IR occupant counts: the cap is
    on default position across the whole roster, not on the bench."""
    counts = Counter(p.pos for p in roster)
    return {pos: counts[pos] - cap for pos, cap in (limits or {}).items() if counts[pos] > cap}


def cap_drops(roster: list[PlayerProj], limits: dict[str, int], keep: set[int] = frozenset()) -> list[PlayerProj] | None:
    """The cuts ESPN demands before it accepts this roster: at each position over its cap, the cheapest bodies.

    This is what the trade screen means by "You must drop a player with default position WR to clear your roster":
    a WR-for-RB swap at six WRs is legal only with a WR drop in the same transaction, so the scan has to name one
    and price the roster without him. `keep` is the players the trade just brought in (cutting them is not a
    trade); the IR occupant stays too, since freeing his slot is the injury rule's call, not this one's.
    Returns [] when nothing is over, None when the excess cannot be cut legally."""
    drops: list[PlayerProj] = []
    for pos, n in over_cap(roster, limits).items():
        cands = sorted([p for p in roster if p.pos == pos and p.espn_id not in keep and p.slot != "IR"],
                       key=lambda p: (p.mu_ros, p.mu))
        if len(cands) < n:
            return None
        drops += cands[:n]
    return drops


def _drop_rows(drops: list[PlayerProj], limits: dict[str, int]) -> list[dict]:
    return [{"name": p.name, "pos": p.pos, "cap": limits[p.pos]} for p in drops]


def weakest_slot(roster: list[PlayerProj], slots: dict[str, int], repl: dict[str, float], ctx: SeasonCtx) -> str | None:
    """His weakest starting position by his own numbers: the fixed slot whose worst starter sits closest to (or
    below) the wire. The piece that fixes it is the one his app grades up."""
    vals = [(p, weekly_values(p, ctx.week, 1)[0]) for p in roster]
    _, used = greedy_lineup(vals, slots)
    worst: tuple[float, str] | None = None
    for pos in ("QB", "RB", "WR", "TE"):
        if not slots.get(pos):
            continue
        st = [acc.his_view(p) for p in roster if p.espn_id in used and p.pos == pos]
        if not st:
            return pos  # an empty slot is the weakest there is
        gap = min(st) - repl.get(pos, 0.0)
        if worst is None or gap < worst[0]:
            worst = (gap, pos)
    return worst[1] if worst else None


def rival_after(new_theirs: list[PlayerProj], get: list[PlayerProj], slots: dict[str, int], ctx: SeasonCtx) -> list[dict]:
    """What he is left with at each position I ask for: who starts there the rest of the season, whether that body is
    the wire, and `idle` when he gives nothing this week (a bye, an injury) so the row can say the wire fills in once
    without calling the streamer his starter. The row prints it so the paste cannot tell a one-QB team it is set at QB."""
    out = []
    for pos in dict.fromkeys(p.pos for p in get):
        st = starters_ros(new_theirs, slots, ctx, pos)
        out.append({"pos": pos, "starters": [{"name": p.name, "wire": p.fantasy_team_id is None,
                                              "ppg": round(acc.his_view(p), 1), "idle": idle_this_week(p, ctx)} for p in st]})
    return out


def _after_line(rows: list[dict]) -> str | None:
    parts = []
    for r in rows:
        if not r["starters"]:
            parts.append(f"nobody at {r['pos']}")
            continue
        if r["pos"] in NO_FLEX_COVER or any(s["wire"] for s in r["starters"]):
            who = ", ".join(f"{s['name']}{' (the wire)' if s['wire'] else ''}{' (' + s['idle'] + ')' if s.get('idle') else ''}"
                            for s in r["starters"])
            parts.append(f"{who} at {r['pos']}")
    return ("he'd start " + "; ".join(parts) + " after") if parts else None


def scan(my_id: int, rosters: dict[int, list[PlayerProj]], slots: dict[str, int], repl: dict[str, float],
         team_meta: dict[int, dict], values: dict[str, dict], max_per_rival: int = 3, top: int = 10,
         limits: dict[str, int] | None = None, roster_max: int | None = None, ctx: SeasonCtx | None = None,
         playoff_pct: dict[int, float] | None = None, reg_season_weeks: int | None = None,
         history: dict[int, dict] | None = None) -> list[dict]:
    """
    For each rival: 1-for-1, 2-for-1, 1-for-2 and 2-for-2 packages from my assets and his, each priced week by week
    on both rosters (`season.season_value`: byes, return dates, the wire as the fallback body, playoff weeks weighted
    by each team's odds) and scored for whether he would say yes (`acceptance.score`). Ranked by `p_accept` times
    my gain: a trade only has value if he accepts it.

    Hard rules before the math: the chart he opens must not read a lowball after the consolidation tax; a star is
    not asked for with pieces; a QB is asked for only from a roster with two startable ones; neither roster may be
    left unable to field a lineup; a piece he would neither start nor price is a throw-in and the package is dropped.

    `limits` are ESPN's per-position roster caps. A package that puts me over one is priced with the cheapest body
    at that position gone and carries him as `drops`, because that is the only form ESPN will accept it in; one
    that leaves me no legal cut is not proposed at all. The same cut on the rival's side is a caveat, not a veto.
    `roster_max` is the roster size (starters + bench): a 2-for-1 to a full roster makes the rival cut someone to
    take it, which the row says, because people decline exactly that.

    Without `ctx` (tests, `ff trades` on a bare snapshot) the season is a flat DEFAULT_WEEKS weeks with no wire, so a
    lost slot costs its whole value.
    """
    limits = limits or {}
    ctx = ctx or SeasonCtx.build(1, DEFAULT_WEEKS)
    odds = playoff_pct or {}
    my_ctx = ctx.with_odds(reg_season_weeks, odds.get(my_id))
    mine = rosters[my_id]
    my_mu0, _ = season_value(mine, slots, my_ctx)
    my_needs = needs(mine, slots, repl)
    _, thin0 = depth(mine, slots)  # positions already without a backup: a trade is not to blame for those
    cands = []
    # tradeable assets: skip K/DST
    my_assets = sorted([p for p in mine if p.pos not in ("K", "D/ST") and p.mu_ros > 0], key=lambda p: -p.mu_ros)[:10]
    for rid, theirs in rosters.items():
        if rid == my_id:
            continue
        their_ctx = ctx.with_odds(reg_season_weeks, odds.get(rid))
        their_mu0, _ = season_value(theirs, slots, their_ctx)
        their_needs = needs(theirs, slots, repl)
        their_assets = sorted([p for p in theirs if p.pos not in ("K", "D/ST") and p.mu_ros > 0], key=lambda p: -p.mu_ros)[:10]
        their_qbs = startable_qbs(theirs, repl)
        _, their_thin0 = depth(theirs, slots)
        weakest = weakest_slot(theirs, slots, repl, their_ctx)
        meta = team_meta.get(rid, {})
        hist = (history or {}).get(rid) or {}
        rival_cands = []
        packages = [((a,), (b,)) for a in my_assets for b in their_assets]
        packages += [((a1, a2), (b,)) for a1, a2 in combinations(my_assets, 2) for b in their_assets[:5]]
        packages += [((a,), (b1, b2)) for a in my_assets[:6] for b1, b2 in combinations(their_assets[:6], 2)]
        packages += [((a1, a2), (b1, b2)) for a1, a2 in combinations(my_assets[:6], 2) for b1, b2 in combinations(their_assets[:6], 2)]
        for give, get in packages:
            # crude pre-filter on market value to avoid absurd asks; my hurt players count at their discounted value
            gv, gv_raw = market_value(give, values, discount_injured=True), market_value(give, values)
            rv = market_value(get, values)
            if gv and rv and gv > rv * 2.2:
                continue
            # How the chart he opens reads it: what he receives over what he gives, taxed when one side sends more
            # bodies. He prices my hurt player healthy until the chart catches up, so his side reads the raw figure
            # (the discounted one guards my side, below). Under 0.8 he reads a lowball and the package is not a row.
            fair_his = fairness(gv_raw, rv, len(give), len(get))
            if fair_his is not None and fair_his < 1 - 2 * FAIR_BAND:
                continue
            # The star guard: his top-12 player (or top three at the position) is not for sale for pieces. The one
            # version that lands sends him a top-24 player back and still shows him the premium.
            star_ask = any(is_star(p, values) for p in get)
            if star_ask and not any((_rank(p, values, "overall_rank") or 999) <= STAR_GIVE_RANK for p in give):
                continue
            if star_ask and fair_his is not None and fair_his < STAR_PREMIUM:
                continue
            # The QB rule (1-QB league): a man with one quarterback is not selling it, whatever the chart says it is
            # worth, because the chart says it is worth nothing and the wire would start his backup. And a top-24
            # piece is never the price of a QB in a league where five of them sit on the wire.
            if any(p.pos == "QB" for p in get) and slots.get("QB", 0) == 1 and not slots.get("OP"):
                if their_qbs < 2:
                    continue
                if all(p.pos == "QB" for p in get) and any((_rank(p, values, "overall_rank") or 999) <= STAR_GIVE_RANK for p in give):
                    continue
            new_mine = _swap(mine, {p.espn_id for p in give}, get)
            new_theirs = _swap(theirs, {p.espn_id for p in get}, give)
            drops = cap_drops(new_mine, limits, keep={p.espn_id for p in get})
            their_drops = cap_drops(new_theirs, limits, keep={p.espn_id for p in give})
            if drops is None or their_drops is None:
                continue
            new_mine = _swap(new_mine, {p.espn_id for p in drops}, [])
            new_theirs = _swap(new_theirs, {p.espn_id for p in their_drops}, [])
            can_field, thin = depth(new_mine, slots)
            if not can_field:
                continue
            # The same check on his side: a package that leaves him unable to field a lineup next Sunday is not an
            # offer, and one that takes his last backup QB is a hard sell (a TE he can stream; K/DST never move).
            their_can_field, their_thin = depth(new_theirs, slots)
            if not their_can_field:
                continue
            their_new_thin = [pos for pos in their_thin if pos not in their_thin0 and pos == "QB"]
            new_thin = [pos for pos in thin if pos not in thin0]
            my_mu1, _ = season_value(new_mine, slots, my_ctx)
            their_mu1, their_starts = season_value(new_theirs, slots, their_ctx)
            d_me, d_them = my_mu1 - my_mu0, their_mu1 - their_mu0
            # Must help me and be at least roughly neutral for them (win-win or need-matching),
            # otherwise it's a dump nobody accepts.
            if d_me < 0.75 or d_them < -0.75:
                continue
            their_starters = {pid for pid, share in their_starts.items() if share >= 0.5}
            # A piece they would neither start nor price is a throw-in; the package is the smaller deal in disguise.
            if throw_ins(give, rv, their_starters, values):
                continue
            # Whose side holds the single best player in the deal. The receiver of the best player is assumed to
            # win the trade, so a 2-for-1 that sends it to him is the one people take.
            best_give, best_get = max((_value(p, values) for p in give), default=0), max((_value(p, values) for p in get), default=0)
            best_side = None if not (best_give and best_get) else ("theirs" if best_give >= best_get else "mine")
            why = []
            if fair_his is not None and fair_his < 1 - FAIR_BAND:
                tax = f" after the {len(give)}-for-{len(get)} tax" if len(give) != len(get) else ""
                why.append(f"he reads it as a lowball on any chart ({fair_his:.2f}{tax})")
            if best_side == "theirs" and len(give) != len(get):
                why.append("he gets the best player in the deal")
            elif best_side == "mine" and len(give) > len(get):
                why.append("I get the best player in the deal, which is the version people decline")
            for pos in their_new_thin:
                why.append(f"leaves them no backup {pos}")
            # ESPN says he is in their lineup today while my rest-of-season math benches him: they value him more
            # than I do, and an ask for a man's starter is a harder sell than the delta reads.
            asks_starter = [p for p in get if starts_today(p)]
            for p in asks_starter:
                why.append(f"they start {p.name} today")
            # A 2-for-1 into a full roster makes them cut someone to take it.
            squeeze = bool(roster_max and len(give) > len(get) and len([p for p in new_theirs if p.slot != "IR"]) > roster_max)
            if squeeze:
                why.append("they have to cut a body to take two")
            for p in get:
                if my_needs.get(p.pos, {}).get("hole") and f"fills my {p.pos} hole" not in why: why.append(f"fills my {p.pos} hole")
            fills_hole = False
            for p in give:
                if their_needs.get(p.pos, {}).get("hole"):
                    fills_hole = True
                    if f"fills their {p.pos} hole" not in why: why.append(f"fills their {p.pos} hole")
            for pos in new_thin:
                why.append(f"leaves me no backup {pos}")
            for p in their_drops:
                why.append(f"they have to cut a {p.pos} to take it ({limits[p.pos]} max)")
            # Same lowball check `evaluate` applies to offers people send me, applied to the ones I would send.
            ratio = (rv / gv) if gv and rv else None
            if ratio is not None and ratio < MARKET_FLOOR:
                why.append(f"market says I give more ({rv} vs {gv})")
            why += injury_caveats(give, values)
            games = meta.get("wins", 0) + meta.get("losses", 0)
            if games >= 4 and meta.get("losses", 0) >= meta.get("wins", 0) + 2: why.append("rival is losing (motivated)")
            # Would he say yes: the scorecard, from what he sees.
            sig = acc.Signals(
                fair_his=fair_his, best_side=best_side, n_give=len(give), n_get=len(get),
                starts_for_him=max((their_starts.get(p.espn_id, 0.0) for p in give), default=0.0),
                asks_starter=len(asks_starter), thin_after=their_new_thin, squeeze=squeeze,
                need_match=any(p.pos == weakest for p in give), app_grade=acc.app_grade(list(give), theirs),
                name_value_drop=acc.name_value_drop(list(give), list(get)), playoff_pct=odds.get(rid), fills_hole=fills_hole,
                trades=meta.get("trades"), acquisitions=meta.get("acquisitions"),
                recent_decline=bool(hist.get("recent_decline")), prior=hist.get("prior"),
            )
            p_accept, reasons = acc.score(sig)
            if p_accept < 0.2:
                continue  # not a row, not a detail line: the shapes people laugh at are not worth the ink
            # My gain, with a backup lost on my side charged against it; ranking is p_accept times that.
            my_gain = d_me - 0.75 * len(new_thin)
            after = rival_after(new_theirs, list(get), slots, their_ctx)
            sendable = bool(p_accept >= acc.SENDABLE and my_gain >= 1.0 and d_them >= -0.25 and (ratio is None or ratio >= MARKET_FLOOR))
            rival_cands.append({
                "rival_team_id": rid, "rival": meta.get("name"), "give": [p.name for p in give], "get": [p.name for p in get],
                "my_delta_ppw": round(float(d_me), 2), "their_delta_ppw": round(float(d_them), 2),
                # Cuts ESPN demands in the trade screen (position cap), priced into the deltas above.
                "drops": _drop_rows(drops, limits), "their_drops": [p.name for p in their_drops],
                "get_pos": [p.pos for p in get],
                "market_give": gv_raw, "market_give_eff": gv, "market_get": rv, "market_ratio": round(ratio, 2) if ratio is not None else None,
                "fair_his": fair_his, "best_side": best_side,
                "p_accept": p_accept, "accept_word": acc.bucket(p_accept), "accept_why": reasons,
                "rival_after": after, "after_line": _after_line(after),
                "sendable": sendable,
                "must_try": bool(sendable and p_accept >= acc.PUSH and my_gain >= MUST_TRY_PPW),
                "why": why,
                "score": round(p_accept * my_gain, 3),
            })
        rival_cands.sort(key=lambda c: -c["score"])
        seen_get, kept = set(), []
        for c in rival_cands:
            k = tuple(sorted(c["get"]))
            if k in seen_get:
                continue
            seen_get.add(k); kept.append(c)
            # The sweetener, ready: the same ask with a different package of mine that he is likelier to take. The
            # row leads with the version that gains me more (anchoring), and names this one for when he says no.
            alts = [a for a in rival_cands if a["get"] == c["get"] and a["give"] != c["give"] and a["p_accept"] > c["p_accept"] + 0.05]
            if alts:
                alt = max(alts, key=lambda a: a["p_accept"])
                c["fallback"] = {"give": alt["give"], "p_accept": alt["p_accept"], "my_delta_ppw": alt["my_delta_ppw"],
                                 "text": f"if he says no, offer {', '.join(alt['give'])} instead ({alt['accept_word'] or 'likelier'})"}
            if len(kept) >= max_per_rival:
                break
        cands += kept
    cands.sort(key=lambda c: -c["score"])
    return cands[:top]


def evaluate(my_id: int, rival_id: int, give: list[PlayerProj], get: list[PlayerProj], rosters: dict[int, list[PlayerProj]],
             slots: dict[str, int], repl: dict[str, float], values: dict[str, dict],
             limits: dict[str, int] | None = None, ctx: SeasonCtx | None = None, playoff_pct: dict[int, float] | None = None,
             reg_season_weeks: int | None = None, _counter_search: bool = True) -> dict:
    """
    Score an offer someone sent me: `give` leaves my roster, `get` joins it. Any size, K/DST allowed.

    Verdict is lineup-delta first (the same week-by-week math as scan), with the taxed chart ratio as a lowball check:
    accept when it clearly helps my lineup and the chart side isn't a fleece; decline when it hurts or the chart gap
    is large; everything else is a counter (close enough that the right tweak makes it a yes).
    """
    limits = limits or {}
    ctx = ctx or SeasonCtx.build(1, DEFAULT_WEEKS)
    odds = playoff_pct or {}
    my_ctx, their_ctx = ctx.with_odds(reg_season_weeks, odds.get(my_id)), ctx.with_odds(reg_season_weeks, odds.get(rival_id))
    mine, theirs = rosters.get(my_id, []), rosters.get(rival_id, [])
    my_mu0, _ = season_value(mine, slots, my_ctx)
    their_mu0, _ = season_value(theirs, slots, their_ctx)
    new_mine = _swap(mine, {p.espn_id for p in give}, get)
    new_theirs = _swap(theirs, {p.espn_id for p in get}, give)
    # Accepting can force a cut (position cap); price the roster ESPN would actually leave me with.
    drops = cap_drops(new_mine, limits, keep={p.espn_id for p in get})
    new_mine = _swap(new_mine, {p.espn_id for p in drops or []}, [])
    my_mu1, my_starts = season_value(new_mine, slots, my_ctx)
    their_mu1, _ = season_value(new_theirs, slots, their_ctx)
    d_me, d_them = my_mu1 - my_mu0, their_mu1 - their_mu0
    my_starters = {pid for pid, share in my_starts.items() if share >= 0.5}
    gv, gv_raw = market_value(give, values, discount_injured=True), market_value(give, values)
    rv = market_value(get, values)
    # Same rule the scan applies to my offers: a body I would neither start nor price does not pad their side.
    filler = throw_ins(get, gv, my_starters, values)
    rv_eff = market_value([p for p in get if p not in filler], values)
    # The same chart test the scan applies to his side, from mine: taxed when one side sends more bodies.
    market_ratio = fairness(rv_eff, gv, len(get), len(give))

    can_field, thin = depth(new_mine, slots)
    _, thin0 = depth(mine, slots)
    my_needs, their_needs = needs(mine, slots, repl), needs(theirs, slots, repl)
    why = []
    for p in get:
        if my_needs.get(p.pos, {}).get("hole"): why.append(f"fills my {p.pos} hole")
    for p in give:
        if their_needs.get(p.pos, {}).get("hole"): why.append(f"fills their {p.pos} hole")
    new_needs = needs(new_mine, slots, repl)
    for pos, n in new_needs.items():
        if n["hole"] and not my_needs.get(pos, {}).get("hole"): why.append(f"opens a {pos} hole for me")
    if not can_field:
        why.append("leaves me unable to fill a starting slot")
    for pos in thin:
        if pos not in thin0:
            why.append(f"leaves me no backup {pos}")
    if len(give) > len(get):
        why.append("2-for-1: I consolidate, they get depth")
    elif len(get) > len(give):
        why.append("1-for-2: I take on depth and need a roster spot")
    for p in drops or []:
        why.append(f"accepting means cutting {p.name} ({limits[p.pos]}-{p.pos} cap)")
    if drops is None:
        why.append("over a position cap with no legal cut: ESPN would not process it")
    for p in filler:
        why.append(f"{p.name} is a throw-in (market {_value(p, values):.0f}, rides the bench)")
    if market_ratio is not None:
        if market_ratio < MARKET_FLOOR: why.append(f"market says I give more ({rv_eff} vs {gv})")
        elif market_ratio > 1.2: why.append(f"market says I get more ({rv_eff} vs {gv})")
    why += injury_caveats(give, values)
    why = list(dict.fromkeys(why))

    if not can_field or drops is None:
        verdict = "decline"
    elif d_me <= -0.5 or (market_ratio is not None and market_ratio < 0.65):
        verdict = "decline"
    elif d_me >= 0.75 and (market_ratio is None or market_ratio >= MARKET_FLOOR):
        verdict = "accept"
    else:
        verdict = "counter"
    after = rival_after(new_mine, list(give), slots, my_ctx)  # what I am left with where he asks
    counter = (_counter(my_id, rival_id, give, get, rosters, slots, repl, values, limits, ctx, playoff_pct, reg_season_weeks)
               if verdict == "counter" and _counter_search else None)
    return {
        "counter": counter,
        "give": [p.name for p in give], "get": [p.name for p in get],
        "my_delta_ppw": round(d_me, 2), "their_delta_ppw": round(d_them, 2),
        "market_give": gv_raw, "market_give_eff": gv, "market_get": rv, "market_get_eff": rv_eff, "market_ratio": market_ratio,
        "why": why, "verdict": verdict,
        "drops": _drop_rows(drops or [], limits), "my_after": after,
    }


def _counter(my_id, rival_id, give, get, rosters, slots, repl, values, limits, ctx, playoff_pct, reg_season_weeks) -> dict | None:
    """The one swap that turns a counter into a yes for me while staying fair on his chart: replace one piece I give
    with another player of mine, or drop one piece from what I give. Returns {give, get, my_delta_ppw, fair_his} or
    None when no single change gets there."""
    mine = rosters.get(my_id, [])
    in_deal = {p.espn_id for p in give} | {p.espn_id for p in get}
    options: list[list[PlayerProj]] = []
    for g in give:
        for q in mine:
            if q.espn_id in in_deal or q.pos in ("K", "D/ST") or q.mu_ros <= 0:
                continue
            if values and not _value(q, values):
                continue  # a piece the chart does not price cannot be shown to be fair
            options.append([q if p.espn_id == g.espn_id else p for p in give])
        if len(give) > 1:
            options.append([p for p in give if p.espn_id != g.espn_id])
    best = None
    rv = market_value(get, values)
    for new_give in options:
        ev = _evaluate_once(my_id, rival_id, new_give, get, rosters, slots, repl, values, limits, ctx, playoff_pct, reg_season_weeks)
        if ev["verdict"] != "accept":
            continue
        fair_his = fairness(market_value(new_give, values), rv, len(new_give), len(get))
        if fair_his is not None and fair_his < 1 - FAIR_BAND:
            continue  # a counter he reads as a lowball is not a counter
        key = (-(abs((fair_his or 1.0) - 1.0)), ev["my_delta_ppw"])
        if best is None or key > best[0]:
            best = (key, {"give": [p.name for p in new_give], "get": [p.name for p in get], "my_delta_ppw": ev["my_delta_ppw"], "fair_his": fair_his})
    return best[1] if best else None


def _evaluate_once(my_id, rival_id, give, get, rosters, slots, repl, values, limits, ctx, playoff_pct, reg_season_weeks) -> dict:
    """`evaluate` without the counter search, so the search cannot recurse."""
    return evaluate(my_id, rival_id, give, get, rosters, slots, repl, values, limits=limits, ctx=ctx, playoff_pct=playoff_pct,
                    reg_season_weeks=reg_season_weeks, _counter_search=False)


def drop_already_offered(cands: list[dict], pending: list[dict] | None, by_id: dict[int, PlayerProj]) -> list[dict]:
    """Remove candidates that re-propose a deal already sitting in a rival's inbox.

    Open offers are the one piece of state the scan cannot see: it re-derives the same package every morning, so the
    card says "offer Maye for Jameson Williams" while exactly that ask is pending with a day left on it. A `get` set
    identifies the rival on its own (a player is on one roster), so this needs no team ids.
    """
    open_offers = []
    for tx in pending or []:
        if tx.get("direction") == "incoming":
            continue
        open_offers.append(({by_id[i].name for i in tx["give"] if i in by_id},
                            {by_id[i].name for i in tx["get"] if i in by_id}))
    out = []
    for c in cands:
        give, get = set(c["give"]), set(c["get"])
        if any(get == o_get or (get & o_get and give & o_give) for o_give, o_get in open_offers):
            continue
        out.append(c)
    return out
