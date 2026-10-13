"""Week-by-week roster value (model/season.py) and the acceptance scorecard (model/acceptance.py)."""
import pytest

from ff.model import acceptance as acc
from ff.model.season import TITLE_RUN_MULT, SeasonCtx, fa_pool, greedy_lineup, season_value, starters_this_week, week_weight, weekly_values
from ff.model.trades import evaluate, scan
from tests.conftest import P


def _hurt(p, weeks_out):
    p.mu_ros_active = p.mu
    p.weeks_out = weeks_out
    p.avail_ros = max(12 - weeks_out, 0) / 12
    p.mu_ros = round(p.mu * p.avail_ros, 2)
    return p


def test_weekly_values_zero_the_bye_and_the_weeks_before_he_is_back():
    p = _hurt(P(1, "QB", "QB", 20), 2)
    p.bye_weeks = [7]
    assert weekly_values(p, 5, 5) == [0.0, 0.0, 0.0, 20.0, 20.0]
    q = P(2, "Q", "RB", 10)
    q.weeks_out = 0.3  # a Questionable scales the first week only
    assert weekly_values(q, 5, 3) == [7.0, 10.0, 10.0]


def test_greedy_lineup_fills_fixed_slots_then_the_flex(slots):
    vals = [(P(1, "QB", "QB", 20), 20.0), (P(2, "RB1", "RB", 16), 16.0), (P(3, "RB2", "RB", 12), 12.0), (P(4, "RB3", "RB", 11), 11.0),
            (P(5, "WR1", "WR", 14), 14.0), (P(6, "WR2", "WR", 9), 9.0), (P(7, "TE", "TE", 8), 8.0), (P(8, "K", "K", 8), 8.0), (P(9, "D", "D/ST", 7), 7.0)]
    pts, used = greedy_lineup(vals, slots)
    assert pts == 20 + 16 + 12 + 14 + 9 + 8 + 8 + 7 + 11 and 4 in used


def test_the_wire_is_the_cost_of_shipping_his_only_qb(slots):
    """Without the wire an empty QB slot costs the whole QB; with it the cost is the gap to the best free agent."""
    theirs = [P(21, "Dak", "QB", 18, tid=2), P(22, "RB1", "RB", 14, tid=2), P(23, "RB2", "RB", 11, tid=2), P(25, "WR1", "WR", 13, tid=2),
              P(26, "WR2", "WR", 11, tid=2), P(27, "WR3", "WR", 9, tid=2), P(29, "TE", "TE", 8, tid=2), P(30, "K", "K", 8, tid=2), P(31, "D", "D/ST", 7, tid=2)]
    bare = SeasonCtx.build(5, 9)
    wire = SeasonCtx.build(5, 9, fas=[P(90, "FA QB", "QB", 13, tid=None), P(91, "FA RB", "RB", 6, tid=None)])
    before_bare, _ = season_value(theirs, slots, bare)
    before_wire, _ = season_value(theirs, slots, wire)
    after = [p for p in theirs if p.name != "Dak"] + [P(4, "Montgomery", "RB", 12.4, tid=2)]
    assert before_bare - season_value(after, slots, bare)[0] == pytest.approx(18 - (12.4 - 9), abs=0.01)   # QB gone, flex up
    assert before_wire - season_value(after, slots, wire)[0] == pytest.approx((18 - 13) - (12.4 - 9), abs=0.01)
    assert [p.name for p in starters_this_week(after, slots, wire, "QB")] == ["FA QB"]


def test_an_ir_qb_is_not_cover_week_by_week(slots):
    """The old rest-of-season average priced a QB back in week 9 as 55% of a starter every week, including next Sunday."""
    theirs = [P(21, "Dak", "QB", 18, tid=2), _hurt(P(32, "IRQB", "QB", 21, tid=2), 4), P(22, "RB1", "RB", 14, tid=2), P(23, "RB2", "RB", 11, tid=2),
              P(25, "WR1", "WR", 13, tid=2), P(26, "WR2", "WR", 11, tid=2), P(27, "WR3", "WR", 9, tid=2), P(29, "TE", "TE", 8, tid=2),
              P(30, "K", "K", 8, tid=2), P(31, "D", "D/ST", 7, tid=2)]
    ctx = SeasonCtx.build(5, 9)
    before, _ = season_value(theirs, slots, ctx)
    after, starts = season_value([p for p in theirs if p.name != "Dak"], slots, ctx)
    # the better QB already starts for him in the five weeks he is back; losing Dak empties the other four
    assert before - after == pytest.approx(4 * 18 / 9, abs=0.01)
    assert starts[32] == pytest.approx(5 / 9, abs=0.01)


def test_playoff_weeks_are_weighted_by_the_odds_and_the_title_run():
    """A playoff week is worth my odds of being there times TITLE_RUN_MULT: nothing at 0%, above a regular week to a lock."""
    ctx = SeasonCtx.build(12, 5, reg_season_weeks=14, playoff_pct=20)
    assert ctx.weights == pytest.approx([1.0, 1.0, 1.0, 0.2 * TITLE_RUN_MULT, 0.2 * TITLE_RUN_MULT])
    assert ctx.playoff_weight == pytest.approx(0.2 * TITLE_RUN_MULT) and ctx.playoff_weeks == {15, 16}
    assert SeasonCtx.build(12, 5, reg_season_weeks=14, playoff_pct=0).weights == [1.0, 1.0, 1.0, 0.0, 0.0]
    lock = SeasonCtx.build(12, 5, reg_season_weeks=14, playoff_pct=100)
    assert lock.weights[3] == pytest.approx(TITLE_RUN_MULT) and lock.weights[3] > 1.0
    # the playoff week set comes from the league settings, not from the regular-season count
    assert SeasonCtx.build(12, 5, reg_season_weeks=14, playoff_pct=90, playoff_weeks=[16]).weights == pytest.approx([1.0, 1.0, 1.0, 1.0, 0.9 * TITLE_RUN_MULT])
    # odds unknown: a playoff week is a plain week
    assert SeasonCtx.build(12, 5, reg_season_weeks=14, playoff_pct=None).weights == [1.0] * 5
    assert week_weight(16, frozenset({15, 16}), 90) == pytest.approx(0.9 * TITLE_RUN_MULT) and week_weight(14, frozenset({15, 16}), 90) == 1.0


def test_no_playoff_weeks_in_the_settings_weights_every_week_one():
    ctx = SeasonCtx.build(12, 6, reg_season_weeks=14, playoff_pct=90, playoff_weeks=[])
    assert ctx.weights == [1.0] * 6 and ctx.playoff_weight is None and ctx.playoff_weeks == frozenset()
    assert ctx.with_odds(14, 10).weights == [1.0] * 6  # the week set survives a change of odds


def test_a_bye_in_the_playoffs_costs_a_contender_more_and_a_dead_team_nothing():
    """Two RBs alike except for the bye week: to a team at 90% the week-16 bye is the dearer one (a playoff week is worth
    more than a regular week); to a team at 0% a week-16 bye costs nothing at all."""
    slots = {"RB": 1}
    def rb(i, bye):
        p = P(i, f"RB{i}", "RB", 12, tid=1)
        p.mu_ros_active = 12
        p.bye_weeks = [bye] if bye else []
        return p
    reg_bye, po_bye, no_bye = rb(1, 13), rb(2, 16), rb(3, None)
    contender = SeasonCtx.build(12, 6, reg_season_weeks=14, playoff_pct=90, playoff_weeks=[15, 16, 17])
    v_reg, v_po, v_none = (season_value([p], slots, contender)[0] for p in (reg_bye, po_bye, no_bye))
    assert v_none > v_reg > v_po
    assert v_none - v_po == pytest.approx(12 * 0.9 * TITLE_RUN_MULT / sum(contender.weights), abs=0.01)
    dead = contender.with_odds(14, 0)
    d_reg, d_po, d_none = (season_value([p], slots, dead)[0] for p in (reg_bye, po_bye, no_bye))
    assert d_po == d_none > d_reg


def test_fa_pool_keeps_the_best_two_per_position():
    pool = fa_pool([P(1, "a", "RB", 9), P(2, "b", "RB", 11), P(3, "c", "RB", 10), P(4, "q", "QB", 14)])
    assert [p.name for p in pool] == ["q", "b", "c"]


# ---------- the scorecard ----------

def test_score_sinks_the_laughed_at_shapes_and_lifts_the_accepted_ones():
    laughed = acc.Signals(fair_his=0.77, best_side="mine", n_give=2, n_get=1, starts_for_him=1.0, squeeze=True)
    taken = acc.Signals(fair_his=1.0, best_side="theirs", n_give=1, n_get=2, starts_for_him=1.0, need_match=True, trades=1, acquisitions=5)
    p_bad, why_bad = acc.score(laughed)
    p_good, why_good = acc.score(taken)
    assert p_bad < 0.2 < acc.SENDABLE <= p_good
    assert any("lowball" in w for w in why_bad) and any("best player" in w for w in why_good)
    assert acc.bucket(p_good) in ("coin flip", "he'd likely take it") and acc.bucket(0.1) is None


def test_score_never_exceeds_the_cap_and_a_one_for_one_ignores_best_side():
    p, _ = acc.score(acc.Signals(fair_his=1.3, best_side="theirs", starts_for_him=1.0, need_match=True, app_grade="above", trades=3, acquisitions=20, fills_hole=True, playoff_pct=80))
    assert p == acc.P_MAX
    even = acc.score(acc.Signals(fair_his=1.0, best_side="mine", n_give=1, n_get=1, starts_for_him=1.0))[0]
    uneven = acc.score(acc.Signals(fair_his=1.0, best_side="mine", n_give=2, n_get=1, starts_for_him=1.0))[0]
    assert even > uneven


def test_app_grade_compares_with_his_positional_average():
    theirs = [P(1, "RB1", "RB", 20), P(2, "RB2", "RB", 10)]
    assert acc.app_grade([P(3, "x", "RB", 16)], theirs) == "above"
    assert acc.app_grade([P(3, "x", "RB", 12)], theirs) == "below"
    good = P(4, "y", "RB", 12); good.sources = {"espn_pg": 18}   # his app says 18 whatever my blend says
    assert acc.app_grade([good], theirs) == "above"


def test_scan_reports_what_he_is_left_with(slots):
    """The row carries who starts for him at the slot I ask for, the wire included, so the paste cannot contradict it."""
    mine = [P(1, "QB1", "QB", 17, tid=1), P(2, "QB2", "QB", 16, tid=1), P(3, "RB1", "RB", 16, tid=1), P(4, "RB2", "RB", 14, tid=1), P(5, "RB3", "RB", 12, tid=1),
            P(6, "WR1", "WR", 14, tid=1), P(7, "WR2", "WR", 12, tid=1), P(8, "WR3", "WR", 10, tid=1), P(9, "TE", "TE", 8, tid=1), P(10, "K", "K", 8, tid=1), P(11, "D", "D/ST", 7, tid=1)]
    theirs = [P(21, "TheirQB", "QB", 20, tid=2), P(22, "TheirQB2", "QB", 15, tid=2), P(23, "RB1", "RB", 7, tid=2), P(24, "RB2", "RB", 6, tid=2),
              P(25, "WR1", "WR", 14, tid=2), P(26, "WR2", "WR", 12, tid=2), P(27, "WR3", "WR", 11, tid=2), P(28, "TE", "TE", 9, tid=2), P(29, "K", "K", 8, tid=2), P(30, "D", "D/ST", 7, tid=2)]
    repl = {"QB": 14, "RB": 7, "WR": 8, "TE": 5, "K": 6, "D/ST": 5}
    out = scan(1, {1: mine, 2: theirs}, slots, repl, {2: {"name": "R", "wins": 2, "losses": 2, "trades": 1, "acquisitions": 4}}, values={},
               ctx=SeasonCtx.build(5, 9, fas=[P(90, "Wire QB", "QB", 12, tid=None)]), max_per_rival=10)
    qb_asks = [c for c in out if "TheirQB" in c["get"] and not any(g.startswith("QB") for g in c["give"])]
    assert qb_asks, [(c["give"], c["get"]) for c in out]
    for c in qb_asks:
        row = next(r for r in c["rival_after"] if r["pos"] == "QB")
        assert row["starters"] and row["starters"][0]["name"] == "TheirQB2" and not row["starters"][0]["wire"]
        assert "he'd start TheirQB2 at QB after" in c["after_line"]
        # 0.878 here: he starts it, it is his weakest slot, he has traded. The word is the top bucket, not "any word".
        assert acc.BUCKETS[0][0] <= c["p_accept"] <= acc.P_MAX
        assert c["accept_word"] == acc.BUCKETS[0][1] == "he'd likely take it"


def test_evaluate_prices_my_side_week_by_week(slots):
    mine = [P(1, "QB", "QB", 20, tid=1), P(2, "RB1", "RB", 16, tid=1), P(3, "RB2", "RB", 15, tid=1), P(5, "WR1", "WR", 14, tid=1), P(6, "WR2", "WR", 12, tid=1),
            P(7, "TE", "TE", 3, tid=1), P(8, "K", "K", 8, tid=1), P(9, "D", "D/ST", 7, tid=1)]
    theirs = [P(11, "QB", "QB", 20, tid=2), P(18, "TEgood", "TE", 12, tid=2), P(22, "TE2", "TE", 6, tid=2), P(19, "K", "K", 8, tid=2), P(20, "D", "D/ST", 7, tid=2)]
    repl = {"QB": 14, "RB": 7, "WR": 8, "TE": 5, "K": 6, "D/ST": 5}
    out = evaluate(1, 2, [mine[1]], [theirs[1]], {1: mine, 2: theirs}, slots, repl, values={},
                   ctx=SeasonCtx.build(5, 9, fas=[P(90, "Wire RB", "RB", 8, tid=None)]))
    assert out["my_after"][0]["pos"] == "RB" and any(s["wire"] for s in out["my_after"][0]["starters"])


def test_scan_output_is_plain_json_even_with_numpy_odds(slots):
    """The sim hands over numpy floats; a numpy bool in the packet serialises as the string "False", which the renderer
    reads as true. The 2026-10-07 email pushed three trades that way. Everything the scan emits must be plain Python."""
    import json

    import numpy as np
    mine = [P(1, "QB", "QB", 20, tid=1), P(2, "RB1", "RB", 16, tid=1), P(3, "RB2", "RB", 15, tid=1), P(4, "RB3", "RB", 14, tid=1),
            P(5, "WR1", "WR", 14, tid=1), P(6, "WR2", "WR", 12, tid=1), P(21, "WR3", "WR", 10, tid=1), P(7, "TE", "TE", 3, tid=1), P(8, "K", "K", 8, tid=1), P(9, "D", "D/ST", 7, tid=1)]
    theirs = [P(11, "QB", "QB", 20, tid=2), P(12, "RB1", "RB", 8, tid=2), P(13, "RB2", "RB", 6, tid=2), P(15, "WR1", "WR", 14, tid=2),
              P(16, "WR2", "WR", 12, tid=2), P(17, "WR3", "WR", 11, tid=2), P(18, "TEgood", "TE", 12, tid=2), P(22, "TE2", "TE", 6, tid=2), P(19, "K", "K", 8, tid=2), P(20, "D", "D/ST", 7, tid=2)]
    repl = {"QB": 14, "RB": 7, "WR": 8, "TE": 5, "K": 6, "D/ST": 5}
    out = scan(1, {1: mine, 2: theirs}, slots, repl, {2: {"name": "R", "wins": 2, "losses": 2}}, values={},
               ctx=SeasonCtx.build(12, 5, 14, None), playoff_pct={1: np.float64(55.5), 2: np.float64(20.0)}, reg_season_weeks=14)
    assert out
    text = json.dumps(out)  # no default=str: anything numpy would raise here
    back = json.loads(text)
    assert all(isinstance(c["sendable"], bool) and isinstance(c["must_try"], bool) for c in back)


def test_a_rostered_qb_on_bye_this_week_is_still_the_qb_he_keeps(slots):
    """Oct 13: BUTT MUNCH had Purdy and Burrow, Burrow on bye in week 6. The row asking for Purdy said "he'd start
    Aaron Rodgers (the wire) at QB after", so the card read as if it asked a one-QB team for its only QB. The after-line
    is his rest-of-season starter, with the bye noted; the wire is his starter only when it really is."""
    mine = [P(1, "QB1", "QB", 17, tid=1), P(2, "QB2", "QB", 16, tid=1), P(3, "RB1", "RB", 16, tid=1), P(4, "RB2", "RB", 14, tid=1), P(5, "RB3", "RB", 12, tid=1),
            P(6, "WR1", "WR", 14, tid=1), P(7, "WR2", "WR", 12, tid=1), P(8, "WR3", "WR", 10, tid=1), P(9, "TE", "TE", 8, tid=1), P(10, "K", "K", 8, tid=1), P(11, "D", "D/ST", 7, tid=1)]
    burrow = P(22, "TheirQB2", "QB", 15, tid=2); burrow.bye_weeks = [5]
    theirs = [P(21, "TheirQB", "QB", 20, tid=2), burrow, P(23, "RB1", "RB", 7, tid=2), P(24, "RB2", "RB", 6, tid=2),
              P(25, "WR1", "WR", 14, tid=2), P(26, "WR2", "WR", 12, tid=2), P(27, "WR3", "WR", 11, tid=2), P(28, "TE", "TE", 9, tid=2), P(29, "K", "K", 8, tid=2), P(30, "D", "D/ST", 7, tid=2)]
    repl = {"QB": 14, "RB": 7, "WR": 8, "TE": 5, "K": 6, "D/ST": 5}
    ctx = SeasonCtx.build(5, 9, fas=[P(90, "Wire QB", "QB", 12, tid=None)])
    out = scan(1, {1: mine, 2: theirs}, slots, repl, {2: {"name": "R", "wins": 2, "losses": 2, "trades": 1, "acquisitions": 4}}, values={},
               ctx=ctx, max_per_rival=10)
    qb_asks = [c for c in out if "TheirQB" in c["get"] and not any(g.startswith("QB") for g in c["give"])]
    assert qb_asks
    for c in qb_asks:
        row = next(r for r in c["rival_after"] if r["pos"] == "QB")
        assert [s["name"] for s in row["starters"]] == ["TheirQB2"] and not row["starters"][0]["wire"]
        assert row["starters"][0]["idle"] == "on bye this week"
        assert "he'd start TheirQB2 (on bye this week) at QB after" in c["after_line"]
    # the one-week view still says who plays Sunday
    from ff.model.season import starters_ros, starters_this_week
    left = [p for p in theirs if p.name != "TheirQB"]
    assert [p.name for p in starters_this_week(left, slots, ctx, "QB")] == ["Wire QB"]
    assert [p.name for p in starters_ros(left, slots, ctx, "QB")] == ["TheirQB2"]


def test_asking_for_a_qb_whose_cover_is_on_bye_this_week_is_a_streaming_ask_and_not_sendable(slots):
    """The same BUTT MUNCH shape, scored: Purdy asked in the week Burrow is on bye means he streams QB on Sunday to do
    the deal. The scorecard halves p(accept) for it, the why says so, and the row is not sendable until the bye
    passes; a week later (no bye) the identical ask is a normal row."""
    mine = [P(1, "QB1", "QB", 17, tid=1), P(2, "QB2", "QB", 16, tid=1), P(3, "RB1", "RB", 16, tid=1), P(4, "RB2", "RB", 14, tid=1), P(5, "RB3", "RB", 12, tid=1),
            P(6, "WR1", "WR", 14, tid=1), P(7, "WR2", "WR", 12, tid=1), P(8, "WR3", "WR", 10, tid=1), P(9, "TE", "TE", 8, tid=1), P(10, "K", "K", 8, tid=1), P(11, "D", "D/ST", 7, tid=1)]

    def theirs(bye):
        qb2 = P(22, "TheirQB2", "QB", 15, tid=2); qb2.bye_weeks = [5] if bye else [9]
        return [P(21, "TheirQB", "QB", 20, tid=2), qb2, P(23, "RB1", "RB", 7, tid=2), P(24, "RB2", "RB", 6, tid=2),
                P(25, "WR1", "WR", 14, tid=2), P(26, "WR2", "WR", 12, tid=2), P(27, "WR3", "WR", 11, tid=2), P(28, "TE", "TE", 9, tid=2), P(29, "K", "K", 8, tid=2), P(30, "D", "D/ST", 7, tid=2)]
    repl = {"QB": 14, "RB": 7, "WR": 8, "TE": 5, "K": 6, "D/ST": 5}
    meta = {2: {"name": "R", "wins": 2, "losses": 2, "trades": 1, "acquisitions": 4}}
    rows = {}
    for bye in (True, False):
        out = scan(1, {1: mine, 2: theirs(bye)}, slots, repl, meta, values={},
                   ctx=SeasonCtx.build(5, 9, fas=[P(90, "Wire QB", "QB", 12, tid=None)]), max_per_rival=10)
        rows[bye] = [c for c in out if "TheirQB" in c["get"] and not any(g.startswith("QB") for g in c["give"])]
    assert rows[True] and rows[False]
    on_bye, clear = rows[True][0], rows[False][0]
    assert "they'd have to stream QB this week" in on_bye["why"] and "he'd have to stream QB this week" in on_bye["accept_why"]
    assert on_bye["p_accept"] <= 0.5 * clear["p_accept"] + 1e-6 and not on_bye["sendable"]
    assert "they'd have to stream QB this week" not in clear["why"]
