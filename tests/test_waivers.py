"""Waivers: the ranking, the claim / add wording and the priority number on the rows, and the FAAB sizing kept to
its shape. All three leagues use waiver priority, so the priority path is the one the card exercises every morning;
FAAB (`faab_bid`) is a feature no league uses and is pinned only as behaviour (bounded, monotone, odd, zero when
there is no budget), not as the rule-of-thumb constants behind it.

What the model does with priority today is wording: `rank_free_agents` tags each target `on_waivers` from ESPN's pool
status, the packet carries `waiver_rank` and a `faab_remaining` of None, and the checklist row says `claim (waivers,
you are priority #N)` or `add`. Nothing prices the cost of spending priority #N; that is TODO D3, not a bug here.
"""
import re
from types import SimpleNamespace

from ff import report
from ff.email_html import render_email
from ff.model.waivers import faab_bid, rank_free_agents
from ff.sources import espn
from tests.conftest import P
from tests.test_card import by_id, league

# ---------- FAAB sizing, as behaviour ----------

def test_faab_bid_never_exceeds_the_budget_and_is_zero_without_one():
    for delta in (0, 2, 5, 10):
        for budget in (1, 7, 100):
            assert 0 <= faab_bid(delta, 12, budget, is_streamer=False) <= budget
            assert 0 <= faab_bid(delta, 12, budget, is_streamer=True) <= budget
    assert faab_bid(10, 12, 0, is_streamer=False) == 0
    assert faab_bid(10, 12, 0, is_streamer=True) == 0


def test_faab_bid_grows_with_the_upgrade_and_with_the_weeks_left():
    bids = [faab_bid(d, 12, 100, is_streamer=False) for d in (0, 1, 3, 5, 8, 12)]
    assert bids == sorted(bids) and bids[-1] > bids[0]
    assert faab_bid(4, 12, 100, is_streamer=False) >= faab_bid(4, 3, 100, is_streamer=False)


def test_faab_bid_is_odd_and_a_streamer_costs_less_than_a_real_upgrade():
    for d in (1, 3, 5, 9):
        b = faab_bid(d, 12, 100, is_streamer=False)
        assert b % 2 == 1
    assert faab_bid(5, 12, 100, is_streamer=True) < faab_bid(5, 12, 100, is_streamer=False)


def test_faab_bid_outbids_the_richest_rival_for_a_league_winner_when_the_budget_allows():
    rich = faab_bid(10, 12, 100, is_streamer=False, league_max_remaining=70)
    assert rich > 70 and rich <= 100
    assert faab_bid(10, 12, 50, is_streamer=False, league_max_remaining=70) <= 50  # cannot spend what I do not have
    # a modest upgrade does not chase the rival's whole wallet
    assert faab_bid(2, 12, 100, is_streamer=False, league_max_remaining=70) <= 70


# ---------- ranking ----------

def test_rank_free_agents_prefers_lineup_upgrade():
    lineup = {"RB": [P(1, "a", "RB", 10), P(2, "b", "RB", 6)], "WR": [P(3, "w", "WR", 12)], "K": [P(5, "k0", "K", 5)]}
    bench = [P(4, "bench", "RB", 4)]
    fas = [P(20, "good_rb", "RB", 9, tid=None), P(21, "meh_wr", "WR", 5, tid=None), P(22, "kicker", "K", 8, tid=None)]
    out = rank_free_agents(fas, lineup, bench, {"RB": 5, "WR": 6, "K": 6}, 12, 100, {})
    assert out[0]["name"] == "good_rb" and out[0]["bid"] > 0
    assert any(o["streamer"] for o in out if o["name"] == "kicker")


def test_a_priority_league_ranks_the_same_targets_with_no_bid():
    """No FAAB means a budget of 0: the order is the same as the FAAB league's and every bid is 0, never a bogus $1."""
    lineup = {"RB": [P(1, "a", "RB", 10), P(2, "b", "RB", 6)], "WR": [P(3, "w", "WR", 12)], "K": [P(5, "k0", "K", 5)]}
    bench = [P(4, "bench", "RB", 4)]
    fas = [P(20, "good_rb", "RB", 9, tid=None), P(21, "ok_wr", "WR", 11, tid=None), P(22, "kicker", "K", 8, tid=None)]
    faab = rank_free_agents(fas, lineup, bench, {"RB": 5, "WR": 6, "K": 6}, 12, 100, {})
    pri = rank_free_agents(fas, lineup, bench, {"RB": 5, "WR": 6, "K": 6}, 12, 0, {})
    assert [o["name"] for o in pri] == [o["name"] for o in faab] and len(pri) >= 2
    assert all(o["bid"] == 0 for o in pri) and any(o["bid"] > 0 for o in faab)


def test_on_waivers_follows_espns_pool_status():
    """ESPN's pool says WAIVERS (a claim that processes on waiver day) or FREEAGENT (mine the moment I click); an
    unknown status is treated as a free agent, so the card never invents a claim."""
    lineup = {"RB": [P(1, "a", "RB", 10), P(2, "b", "RB", 6)]}
    claim, free, unknown = (P(20, "claim", "RB", 9, tid=None), P(21, "free", "RB", 8.5, tid=None), P(22, "unknown", "RB", 8, tid=None))
    claim.sources["waiver_status"] = "WAIVERS"
    free.sources["waiver_status"] = "FREEAGENT"
    out = {o["name"]: o for o in rank_free_agents([claim, free, unknown], lineup, [], {"RB": 5}, 12, 0, {})}
    assert out["claim"]["on_waivers"] is True
    assert out["free"]["on_waivers"] is False and out["unknown"]["on_waivers"] is False


def test_a_locked_slot_is_not_an_upgrade_opportunity():
    """The flex holds a player whose game kicked off, so ESPN will not let anyone else in this week. A free agent
    who "beats" his banked score is not a move, and a card that says start him is wrong."""
    played = P(2, "already_played", "WR", 3.0, tid=1)
    played.locked, played.actual = True, 3.0
    week_lineup = {"RB/WR/TE": [played], "RB": [P(1, "rb", "RB", 12)]}
    ros_lineup = {"RB": [P(1, "rb", "RB", 12)], "WR": [P(3, "wr", "WR", 11)]}
    fas = [P(20, "vele", "WR", 9.0, tid=None)]
    out = rank_free_agents(fas, ros_lineup, [P(4, "bench", "WR", 4)], {"RB": 5, "WR": 6}, 12, 100, {},
                           week_lineup=week_lineup)
    assert "vele" in [o["name"] for o in out]
    assert all(o["delta_week"] == 0 for o in out)


def test_an_unlocked_body_in_the_same_slot_is_still_replaceable():
    played = P(2, "already_played", "WR", 3.0, tid=1)
    played.locked, played.actual = True, 3.0
    week_lineup = {"RB/WR/TE": [played, P(5, "still_to_play", "WR", 4.0)]}
    fas = [P(20, "vele", "WR", 9.0, tid=None)]
    out = rank_free_agents(fas, {"WR": [P(3, "wr", "WR", 11)]}, [P(4, "bench", "WR", 4)], {"WR": 6}, 12, 100, {},
                           week_lineup=week_lineup)
    assert out[0]["delta_week"] > 0 and out[0]["week_slot"] == "RB/WR/TE"


def test_a_kicker_who_may_sit_surfaces_a_replacement():
    """Rest-of-season the two kickers are the same player, so only the week view sees the problem."""
    sick = P(9, "sick_k", "K", 8.0, p0=0.5)
    fas = [P(30, "healthy_k", "K", 7.5, tid=None)]
    out = rank_free_agents(fas, {"K": [sick]}, [], {"K": 6}, 12, 100, {}, week_lineup={"K": [sick]})
    assert [o["name"] for o in out] == ["healthy_k"]


# ---------- the ESPN pool status ----------

def _pool_league(players, fail=False):
    def league_get(params, headers):
        if fail:
            raise RuntimeError("ESPN said no")
        return {"players": players}
    return SimpleNamespace(espn_request=SimpleNamespace(league_get=league_get))


def test_waiver_statuses_keeps_only_the_two_pool_states():
    lg = _pool_league([{"id": 1, "status": "WAIVERS"}, {"id": 2, "status": "FREEAGENT"}, {"id": 3, "status": "ONTEAM"},
                       {"id": 4}])
    assert espn.waiver_statuses(lg, 5) == {1: "WAIVERS", 2: "FREEAGENT"}


def test_waiver_statuses_failure_leaves_the_status_unknown_not_the_run_dead():
    assert espn.waiver_statuses(_pool_league([], fail=True), 5) == {}


def test_free_agent_rows_carry_the_pool_status_by_player_id():
    def fa(pid, name):
        return SimpleNamespace(playerId=pid, name=name, position="RB", proTeam="GB", eligibleSlots=["RB", "RB/WR/TE", "BE"],
                               injuryStatus="ACTIVE", stats={5: {"projected_points": 8.0, "points": 0.0}},
                               projected_total_points=120.0, percent_owned=30.0, posRank=40, schedule={"5": {}}, injured=False)
    lg = _pool_league([{"id": 1, "status": "WAIVERS"}, {"id": 2, "status": "FREEAGENT"}])
    lg.free_agents = lambda week, size: [fa(1, "Claim Guy"), fa(2, "Add Guy"), fa(3, "Unknown Guy")]
    rows = {r.name: r for r in espn.free_agent_rows(lg, 5)}
    assert rows["Claim Guy"].waiver_status == "WAIVERS"
    assert rows["Add Guy"].waiver_status == "FREEAGENT"
    assert rows["Unknown Guy"].waiver_status is None
    assert rows["Claim Guy"].slot == "FA" and rows["Claim Guy"].fantasy_team_id is None


# ---------- the row wording ----------

def _waiver(name, pos="RB", d_week=0.0, d_start=0.0, on_waivers=False, bid=0, streamer=False):
    return {"name": name, "pos": pos, "team": "GB", "streamer": streamer, "delta_week": d_week, "week_slot": pos,
            "delta_over_starter": d_start, "slot": pos, "bid": bid, "on_waivers": on_waivers, "why": [],
            "mu_week": 9.0, "mu_ros": 9.0}


def test_a_start_now_pickup_on_waivers_is_a_claim_with_my_priority():
    lg = league(waivers=[_waiver("Bassett", d_week=3.0, on_waivers=True)], waiver_rank=7, faab_remaining=None)
    text = by_id(lg)["waiver:bassett"]["text"]
    assert text.startswith("claim (waivers, you are priority #7) Bassett (RB) and start him at RB")
    lg = league(waivers=[_waiver("Bassett", d_week=3.0, on_waivers=False)], waiver_rank=7, faab_remaining=None)
    assert by_id(lg)["waiver:bassett"]["text"].startswith("add Bassett (RB) and start him at RB")


def test_an_upgrade_pickup_on_waivers_is_a_claim_with_my_priority():
    """The upgrade row is built separately from the start-now row and has to say the same thing."""
    lg = league(waivers=[_waiver("Later Guy", d_start=2.0, on_waivers=True)], waiver_rank=4, faab_remaining=None)
    row = by_id(lg)["waiver:later-guy"]
    assert row["kind"] == "waiver_up"
    assert row["text"].startswith("claim (waivers, you are priority #4) Later Guy (RB), +2.0/wk over your RB")
    lg = league(waivers=[_waiver("Later Guy", d_start=2.0, on_waivers=False)], waiver_rank=4, faab_remaining=None)
    assert by_id(lg)["waiver:later-guy"]["text"].startswith("add Later Guy (RB), +2.0/wk over your RB")


def test_a_claim_with_no_priority_known_still_says_it_is_a_claim():
    lg = league(waivers=[_waiver("Bassett", d_week=3.0, on_waivers=True)], waiver_rank=None, faab_remaining=None)
    text = by_id(lg)["waiver:bassett"]["text"]
    assert text.startswith("claim (waivers) Bassett (RB)") and "priority" not in text


def test_a_faab_league_puts_the_bid_on_the_claim_instead_of_the_priority():
    lg = league(waivers=[_waiver("Bassett", d_week=3.0, on_waivers=True, bid=7)], waiver_rank=7, faab_remaining=40)
    text = by_id(lg)["waiver:bassett"]["text"]
    assert text.startswith("claim (waivers, bid $7) Bassett (RB)") and "priority" not in text
    # a free agent costs nothing to click whatever the budget says
    lg = league(waivers=[_waiver("Bassett", d_week=3.0, on_waivers=False, bid=7)], waiver_rank=7, faab_remaining=40)
    assert by_id(lg)["waiver:bassett"]["text"].startswith("add Bassett (RB)")


def test_a_streamer_on_waivers_says_the_swap_lands_on_waiver_day():
    lg = league(waivers=[_waiver("Wire K", pos="K", d_start=2.0, on_waivers=True, streamer=True)], waiver_rank=5)
    text = by_id(lg)["stream:wire-k"]["text"]
    assert text.startswith("swap in Wire K at K (+2.0 this week)") and text.endswith("(he is on waivers, so the claim lands on waiver day)")
    lg = league(waivers=[_waiver("Wire K", pos="K", d_start=2.0, on_waivers=False, streamer=True)], waiver_rank=5)
    assert by_id(lg)["stream:wire-k"]["text"] == "swap in Wire K at K (+2.0 this week)"


# ---------- a priority league end to end ----------

def test_a_priority_league_carries_the_rank_and_no_faab_through_packet_markdown_and_email(monkeypatch):
    """The demo league is FAAB; flip it to priority, tag a few free agents as still on waivers, and the packet, the
    markdown body and the HTML email all have to speak priority: no budget, no bid column, my rank in the header."""
    from ff.packet import analyze_league
    from tests.test_packet_synthetic import FakeXW, make_snapshot
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    snap = make_snapshot()
    snap["settings"]["faab"] = False
    snap["settings"]["waiver_order"] = "rolling"
    snap["teams"][0]["waiver_rank"] = 6
    on_waivers = {fa["espn_id"] for fa in snap["free_agents"][::2]}
    for fa in snap["free_agents"]:
        fa["waiver_status"] = "WAIVERS" if fa["espn_id"] in on_waivers else "FREEAGENT"
    blk = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides=None, sims=100)
    assert blk["faab_remaining"] is None and blk["waiver_rank"] == 6
    assert blk["waivers"] and all(w["bid"] == 0 for w in blk["waivers"])
    assert all(w["on_waivers"] == (w["espn_id"] in on_waivers) for w in blk["waivers"])
    assert all(t["faab_left"] is None for t in blk["standings"])
    rows = report.todos(blk)
    for r in rows:
        if r["kind"] in ("waiver", "waiver_up"):
            assert "$" not in r["text"]
            assert re.match(r"claim \(waivers, you are priority #6(, lands [^)]+)?\) ", r["text"]) or r["text"].startswith("add ")
            assert ("of future claims" in r["text"]) == r["text"].startswith("claim")  # the priority is priced on a claim only
    packet = {"version": 3, "generated": "2026-09-10T07:00:00", "season": 2026,
              "shared": {"injury_watchlist": [], "exposure": {}, "trending_adds": [], "usage_error": None, "unmatched_ids": []},
              "leagues": [blk]}
    md = report.render(packet)
    waivers_md = md.split("## Waivers", 1)[1].split("\n## ", 1)[0]
    assert "| bid |" not in waivers_md and "$" not in waivers_md and "| target |" in waivers_md
    html = render_email(packet, None, full=True)
    assert "waiver priority #6" in html and "FAAB left" not in html and ">Bid<" not in html


# ---------- the priority a claim spends (D3) ----------

def _rolling(**kw):
    """A rolling-priority league (a successful claim sends you to the back) with a stacked wire: three claim-worthy
    upgrades beyond the row's own player, so holding a high priority is worth something."""
    wire = [_waiver("Big Guy", d_start=2.1, on_waivers=True), _waiver("Next Guy", d_start=1.5, on_waivers=True),
            _waiver("Third Guy", d_start=1.2, on_waivers=True), _waiver("Depth", d_start=0.3)]
    settings = {"lineup_slots": {"RB": 2}, "team_count": 10, "waiver_order": "rolling"}
    return league(waivers=wire, waiver_rank=2, faab_remaining=None, weeks_remaining=12, settings=settings, **kw)


def test_a_claim_in_a_rolling_league_prices_the_priority_it_spends():
    """Priority #2 of 10 with 12 weeks left: the row says what claiming now gets against the future claims it gives
    up, and the two numbers are the model's (`priority_cost`), not a guess in prose."""
    text = by_id(_rolling())["waiver:big-guy"]["text"]
    assert text.startswith("claim (waivers, you are priority #2) Big Guy (RB), +2.1/wk over your RB")
    assert "; worth it: +2.1/wk now vs ~0.7/wk of future claims given up" in text


def test_a_small_upgrade_says_pass_when_the_priority_is_worth_more():
    """A one-week fill-in at priority #2: the start-now row spreads his week over the season and the priority wins."""
    lg = _rolling()
    lg["waivers"].insert(0, _waiver("Fill In", d_week=3.6, d_start=0.0, on_waivers=True))
    text = by_id(lg)["waiver:fill-in"]["text"]
    assert text.startswith("claim (waivers, you are priority #2) Fill In (RB) and start him at RB (+4 pts this week)")
    assert "; pass: you'd give up priority #2 for +0.3/wk (~0.7/wk of future claims)" in text


def test_a_reverse_standings_league_shows_no_cost():
    """ESPN resets the order to reverse standings every week, so priority costs nothing to spend; an unknown order
    (an older snapshot) prints nothing rather than a made-up number."""
    for order in ("reset", None):
        lg = _rolling()
        lg["settings"]["waiver_order"] = order
        text = by_id(lg)["waiver:big-guy"]["text"]
        assert text == "claim (waivers, you are priority #2) Big Guy (RB), +2.1/wk over your RB; drop Bench WR; if he is gone, Next Guy (RB, +1.5/wk)", (order, text)


def test_a_free_agent_row_is_untouched_by_the_priority_value():
    lg = _rolling()
    lg["waivers"][0]["on_waivers"] = False
    assert by_id(lg)["waiver:big-guy"]["text"] == "add Big Guy (RB), +2.1/wk over your RB; drop Bench WR; if he is gone, Next Guy (RB, +1.5/wk)"


def test_priority_cost_is_the_rolling_formula():
    """V(n) = sum_k Binom(n-1, k; RIVAL_CLAIM_P) * d_(k+1) over the wire's claim-worthy deltas sorted best first
    (0 past the end); cost = (V(rank) - V(last)) * (weeks_remaining - 1) / (2 * weeks_remaining)."""
    from ff.model import waivers as wv
    d = [2.1, 1.5, 1.2]
    p = wv.RIVAL_CLAIM_P
    v2 = (1 - p) * 2.1 + p * 1.5
    v10 = sum(__import__("math").comb(9, k) * p ** k * (1 - p) ** (9 - k) * (d[k] if k < 3 else 0.0) for k in range(10))
    assert wv.priority_value(2, d) == round(v2, 4) and wv.priority_value(10, d) == round(v10, 4)
    assert wv.priority_cost(2, 10, d, 12, "rolling") == round((v2 - v10) * 11 / 24, 2)
    assert wv.priority_cost(1, 10, d, 12, "rolling") > wv.priority_cost(2, 10, d, 12, "rolling") > wv.priority_cost(10, 10, d, 12, "rolling") == 0.0
    assert wv.priority_cost(2, 10, d, 1, "rolling") == 0.0  # the last week: nothing left to claim for
    assert wv.priority_cost(2, 10, [], 12, "rolling") == 0.0  # an empty wire: no future claims to give up
    assert wv.priority_cost(2, 10, d, 12, "reset") is None and wv.priority_cost(2, 10, d, 12, None) is None
    assert wv.priority_cost(None, 10, d, 12, "rolling") is None


def test_waiver_order_is_read_from_espns_acquisition_settings():
    """`waiverOrderReset` true is the weekly reset to reverse standings, false the rolling order; absent is unknown."""
    from ff.sources.espn import waiver_order
    assert waiver_order({"waiverOrderReset": True}) == "reset"
    assert waiver_order({"waiverOrderReset": False}) == "rolling"
    assert waiver_order({}) is None
