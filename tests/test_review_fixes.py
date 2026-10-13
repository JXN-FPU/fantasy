"""The 2026-09-23 review: wrong instructions the live card produced that morning, each pinned here."""
from datetime import UTC, date, datetime

import pytest

from ff import report, rulings
from ff.model.lineup import optimize
from ff.model.projections import blend
from tests.conftest import P
from tests.test_card import by_id, full, ids, injury, league, player, trade

# ---------- lineup: no-op swaps ----------

def test_optimizer_keeps_starters_in_the_slots_they_already_hold(slots):
    """Same starters, so no moves: Henderson stays in the flex and Tuten at RB instead of swapping for nothing."""
    ps = [P(1, "QB1", "QB", 20), P(2, "Jeanty", "RB", 18), P(3, "Tuten", "RB", 12), P(4, "Henderson", "RB", 14),
          P(5, "WR1", "WR", 14), P(6, "WR2", "WR", 12), P(7, "TE1", "TE", 9), P(8, "K", "K", 8), P(9, "D", "D/ST", 7)]
    cur = {"QB1": "QB", "Jeanty": "RB", "Tuten": "RB", "Henderson": "RB/WR/TE", "WR1": "WR", "WR2": "WR", "TE1": "TE", "K": "K", "D": "D/ST"}
    for p in ps:
        p.slot = cur[p.name]
    L = optimize(ps, slots, objective="ev")
    assert L.assignment["RB/WR/TE"][0].name == "Henderson" and {p.name for p in L.assignment["RB"]} == {"Jeanty", "Tuten"}
    lg = league(roster=[{**player(p.name, pos=p.pos, slot=p.slot), "mu_ros": p.mu} for p in ps], lineup_win=L.to_dict())
    assert by_id(lg)["lineup"]["moves"] == []


def test_optimizer_still_moves_a_bench_player_in_when_he_is_better(slots):
    ps = [P(1, "QB1", "QB", 20), P(2, "RB1", "RB", 18), P(3, "RB2", "RB", 12), P(4, "RB3", "RB", 14),
          P(5, "WR1", "WR", 14), P(6, "WR2", "WR", 12), P(7, "TE1", "TE", 9), P(8, "K", "K", 8), P(9, "D", "D/ST", 7), P(10, "WR3", "WR", 5)]
    cur = {"QB1": "QB", "RB1": "RB", "RB2": "RB", "WR3": "RB/WR/TE", "WR1": "WR", "WR2": "WR", "TE1": "TE", "K": "K", "D": "D/ST", "RB3": "BE"}
    for p in ps:
        p.slot = cur[p.name]
    L = optimize(ps, slots, objective="ev")
    names = {s: [p.name for p in v] for s, v in L.assignment.items()}
    assert names["RB"] == ["RB1", "RB2"] and names["RB/WR/TE"] == ["RB3"]  # the two RBs stay put, RB3 takes the flex


# ---------- projections ----------

ROW = {"espn_id": 1, "name": "X", "pos": "WR", "team": "KC", "eligible": ["WR"], "slot": "BE", "fantasy_team_id": 1,
       "proj_week": 12.0, "proj_season": 150.0, "bye": False}


def test_a_questionable_on_his_bye_is_not_a_missed_game():
    p = blend({**ROW, "bye": True, "injury_status": "QUESTIONABLE", "proj_week": 0.0}, None, None, 15, None, week=3)
    assert p.weeks_out == 0.3 and p.return_week is None and p.p_zero == 1.0  # the bye still zeroes this week


def test_questionable_is_lighter_early_in_the_week_until_claude_says_otherwise():
    q = {**ROW, "injury_status": "QUESTIONABLE"}
    assert blend(q, None, None, 15, None, weekday=1).p_zero == 0.15   # Tuesday: last week's tag
    assert blend(q, None, None, 15, None, weekday=4).p_zero == 0.30   # Friday: the real one
    assert blend(q, None, None, 15, None).p_zero == 0.30              # no day given: Friday number
    assert blend(q, None, None, 15, {"1": {"p_zero": 0.5}}, weekday=1).p_zero == 0.5


def test_day_to_day_is_a_designation_and_waiver_status_rides_along():
    p = blend({**ROW, "injury_status": "DAY_TO_DAY", "waiver_status": "WAIVERS"}, None, None, 15, None)
    assert p.p_zero == 0.15 and "espn:DAY_TO_DAY" in p.flags and p.sources["waiver_status"] == "WAIVERS"


def test_missing_espn_status_is_none_not_a_list():
    from ff.sources.espn import _status

    class Fake:
        injuryStatus = []
    assert _status(Fake()) is None
    Fake.injuryStatus = "OUT"
    assert _status(Fake()) == "OUT"


# ---------- the roster-spot ledger ----------

def test_an_activation_takes_the_drop_row_with_it_instead_of_spending_it_twice():
    """Daniels comes off IR and Dart is the cut: one click, one drop, not "drop Dart" on two rows."""
    lg = league(roster=[player("Starter WR", slot="WR"), player("Dart", pos="QB", ros=0.8), player("Bench WR", ros=3.0)],
                injuries=[injury("Daniels", "activate", pos="QB", weeks_out=1, return_week=4, fa=None),
                          {**injury("Dart", "drop", pos="QB", weeks_out=1, return_week=4, fa=("Concepcion", 0.0)), "hold_value": 0.0}])
    lg["injuries"][0]["espn_status"] = "QUESTIONABLE"
    rows = by_id(lg)
    assert "injury:dart" not in rows
    text = full(rows["injury:daniels"])
    assert text.startswith("move Daniels (QB) off IR; drop Dart to make room") and "ESPN lists him questionable" in text and "Dart (QB): out ~1 wk" in text
    assert "Concepcion" not in text


@pytest.mark.parametrize("drop_row_first", [False, True])
def test_an_activation_folds_the_drop_row_whichever_order_decide_put_them_in(drop_row_first):
    """`injuries.decide` orders rows by `mu_ros_active`, so Dart's Drop row can come before Daniels' activation; the
    card once printed "drop Dart to make room" and "drop Dart: out ... add Concepcion" on that morning. One drop, one row."""
    rows = [injury("Daniels", "activate", pos="QB", weeks_out=1, return_week=4, fa=None),
            {**injury("Dart", "drop", pos="QB", weeks_out=1, return_week=4, fa=("Concepcion", 0.0)), "hold_value": 0.0}]
    rows[0]["espn_status"] = "QUESTIONABLE"
    if drop_row_first:
        rows.reverse()
    lg = league(roster=[player("Starter WR", slot="WR"), player("Dart", pos="QB", ros=0.8), player("Bench WR", ros=3.0)], injuries=rows)
    items = report.todos(lg)
    assert "injury:dart" not in {i["id"] for i in items}
    assert sum("drop Dart" in i["text"] for i in items) == 1
    assert "Dart (QB): out ~1 wk" in full(by_id(lg)["injury:daniels"])
    assert not any("Concepcion" in full(i) for i in items)


def test_a_folded_drop_rows_add_is_not_counted_on_the_ledger():
    """Dart's Drop row would add Concepcion (QB); Daniels' activation folds it in whatever the order, so Concepcion is
    never added and the trade row's cap check sees Dart off and nobody on: one QB, not two, against a cap of one."""
    rows = [{**injury("Dart", "drop", pos="QB", weeks_out=1, return_week=4, fa=("Concepcion", 0.0)), "hold_value": 0.0},
            injury("Daniels", "activate", pos="QB", weeks_out=1, return_week=4, fa=None)]
    lg = league(roster=[player("Starter WR", slot="WR"), player("Dart", pos="QB", ros=0.8), player("Bench WR", ros=3.0)], injuries=rows,
                settings={"lineup_slots": {"QB": 1}, "position_limits": {"QB": 1}},
                trades=[{**trade(["QB In"], ["Bench WR"]), "get_pos": ["QB"], "drops": []}])
    row = by_id(lg)["trade:qb-in"]
    assert row["drops"] == [] and "ESPN caps" not in full(row)


def test_an_activation_uses_an_open_spot_before_naming_a_drop():
    lg = league(roster=[player("Starter WR", slot="WR"), player("Bench WR", ros=3.0)],
                injuries=[injury("Daniels", "activate", pos="QB", weeks_out=1, return_week=4, fa=None)], open_spots=1, open_spot_adds=[])
    assert "open bench spot" in full(by_id(lg)["injury:daniels"]) and "drop" not in by_id(lg)["injury:daniels"]["text"]


def waiver(name, pos="RB", d_week=3.0, d_start=0.0, on_waivers=False):
    return {"name": name, "pos": pos, "team": "GB", "streamer": False, "delta_week": d_week, "week_slot": "RB", "delta_over_starter": d_start,
            "slot": "RB", "bid": 0, "on_waivers": on_waivers}


def test_a_waiver_add_uses_the_open_spot_and_the_open_spot_row_does_not_repeat_him():
    lg = league(waivers=[waiver("Bassett")], open_spots=1, open_spot_adds=[{"name": "Bassett", "pos": "RB", "kind": "upgrade", "why": "+7.7/wk"}])
    rows = ids(lg)
    assert rows.count("waiver:bassett") == 1
    text = full(by_id(lg)["waiver:bassett"])
    assert "open bench spot" in text and "drop" not in text


def test_two_pickups_name_two_different_drops():
    lg = league(roster=[player("Starter WR", slot="WR"), player("Bench A", ros=3.0), player("Bench B", ros=5.0)],
                waivers=[waiver("Now Guy", d_week=3.0), waiver("Later Guy", d_week=0.0, d_start=2.0)])
    rows = by_id(lg)
    assert rows["waiver:now-guy"]["text"].endswith("; drop Bench A") and rows["waiver:later-guy"]["text"].endswith("; drop Bench B")


def test_a_drop_row_with_its_own_add_is_not_the_drop_for_a_pickup_too():
    """Done Guy is cut and Pickup takes his spot; a start-worthy waiver add needs a spot of its own, so it names the
    next cheapest body (Bench B), not Done Guy a second time. The position counts end up one down per drop, not two."""
    lg = league(roster=[player("Starter WR", slot="WR"), player("Done Guy", pos="RB", ros=0.5), player("Bench B", ros=5.0)],
                injuries=[injury("Done Guy", "drop", weeks_out=15, return_week=None, avail=0.0, fa=("Pickup", 1.2))],
                waivers=[waiver("Now Guy", d_week=3.0)])
    rows = by_id(lg)
    assert rows["injury:done-guy"]["text"].endswith("; add Pickup (RB)")
    assert rows["waiver:now-guy"]["text"].endswith("; drop Bench B")


def test_a_drop_is_counted_once_even_when_the_ledger_hands_him_out():
    """Dart has a Drop row and the activation takes him as its cut: one body off at QB, not two."""
    lg = league(roster=[player("Starter WR", slot="WR"), player("Dart", pos="QB", ros=0.8), player("Bench WR", ros=3.0)],
                injuries=[injury("Daniels", "activate", pos="QB", weeks_out=1, return_week=4, fa=None),
                          {**injury("Dart", "drop", pos="QB", weeks_out=1, return_week=4, fa=None), "hold_value": 0.0}])
    spots = report._Spots(lg, 0, set())
    spots.note(drop="Dart")
    report._injury_items(lg, spots)
    assert spots.dropped == ["Dart"] and spots.counts["QB"] == 0


def test_a_player_headed_to_ir_is_never_the_drop_for_a_pickup():
    lg = league(roster=[player("Starter WR", slot="WR"), player("Stash", ros=0.5), player("Bench B", ros=5.0)],
                injuries=[injury("Stash", "ir", weeks_out=15, return_week=None, avail=0.0, fa=("Pickup", 1.2))],
                waivers=[waiver("Now Guy", d_week=3.0)])
    assert by_id(lg)["waiver:now-guy"]["text"].endswith("; drop Bench B")


def test_a_pickup_the_injury_row_already_adds_is_not_a_second_row():
    lg = league(roster=[player("Starter WR", slot="WR"), player("Stash", ros=0.5), player("Bench B", ros=5.0)],
                injuries=[injury("Stash", "ir", weeks_out=15, return_week=None, avail=0.0, fa=("Now Guy", 1.2))],
                waivers=[waiver("Now Guy", d_week=3.0)])
    assert ids(lg).count("waiver:now-guy") == 0 and "then add Now Guy" in by_id(lg)["injury:stash"]["text"]


def test_a_cap_with_nobody_to_drop_says_your_call_instead_of_naming_a_cut():
    """`_Spots.cut` reaching the `stuck` branch: the trade puts a position over ESPN's cap and every body there is one
    the ledger will not cut (a starter, or the IR occupant, who counts toward the cap but frees no bench spot). The
    row must say so and name nobody, not invent a drop or print the cap as if a body had been found."""
    one_qb = league(roster=[player("Only QB", pos="QB", slot="QB"), player("Starter WR", slot="WR"), player("Bench WR", ros=3.0)],
                    lineup_win={"slots": {"QB": ["Only QB"], "WR": ["Starter WR"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                    settings={"lineup_slots": {"QB": 1, "WR": 2}, "position_limits": {"QB": 1}},
                    trades=[{**trade(["QB In"], ["Bench WR"]), "get_pos": ["QB"], "drops": []}])
    spots = report._Spots(one_qb, 0, set())
    assert spots.cut(one_qb["trades"][0]) == ([], "; ESPN caps QB at 1 and there is no obvious QB to drop, your call")
    assert spots.dropped == [] and spots.counts["QB"] == 1  # a stuck cut spends nothing on the ledger
    row = by_id(one_qb)["trade:qb-in"]
    assert row["drops"] == []
    assert row["text"] == "offer Rival your Bench WR for QB In: +1.5/wk for you, +0.5 for them"
    assert row["detail"] == ["ESPN caps QB at 1 and there is no obvious QB to drop, your call"]
    # the IR occupant counts against the cap but is never the cut: three WRs against a cap of three, one of them on IR
    ir_wr = league(roster=[player("Starter WR", slot="WR"), player("WR Two", slot="WR"), player("IR WR", slot="IR", ros=0.0),
                           player("RB Two", pos="RB", ros=5.0)],
                   lineup_win={"slots": {"WR": ["Starter WR", "WR Two"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                   settings={"lineup_slots": {"RB": 2, "WR": 2}, "position_limits": {"WR": 3}},
                   trades=[{**trade(["WR In"], ["RB Two"]), "get_pos": ["WR"], "drops": []}])
    assert report._Spots(ir_wr, 0, set()).cut(ir_wr["trades"][0]) == ([], "; ESPN caps WR at 3 and there is no obvious WR to drop, your call")
    assert by_id(ir_wr)["trade:wr-in"]["drops"] == []


def test_a_cap_cut_the_bench_only_half_covers_names_the_drop_and_says_one_more_has_to_go():
    """B11: a 2-for-1 brings two WRs onto a roster at the WR cap with one bench WR. The ledger finds one body and is
    short one; the row must name the body with its cap note and say one more has to go, not print empty parentheses
    and then claim nobody was found."""
    lg = league(roster=[player("Starter WR", slot="WR"), player("WR Two", slot="WR"), player("Bench WR", ros=3.0),
                        player("RB Two", pos="RB", ros=5.0)],
                lineup_win={"slots": {"WR": ["Starter WR", "WR Two"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                settings={"lineup_slots": {"RB": 2, "WR": 2}, "position_limits": {"WR": 3}},
                trades=[{**trade(["WR In", "WR Also"], ["RB Two"]), "get_pos": ["WR", "WR"], "drops": []}])
    spots = report._Spots(lg, 0, set())
    drops, cut = spots.cut(lg["trades"][0])
    assert drops == ["Bench WR"]
    assert cut == "; drop Bench WR in the trade screen (ESPN caps WR at 3) and one more WR has to go, your call"
    assert "()" not in cut and "no obvious" not in cut
    assert spots.dropped == [] and spots.counts["WR"] == 3  # trade rows are alternatives: nothing spent on the ledger
    row = by_id(lg)["trade:wr-in-wr-also"]
    assert row["drops"] == ["Bench WR"]
    assert row["text"] == "offer Rival your RB Two for WR In, WR Also: +1.5/wk for you, +0.5 for them"
    assert row["detail"] == ["drop Bench WR in the trade screen (ESPN caps WR at 3) and one more WR has to go, your call"]
    # two short: the count is spelled out and the position is plural
    lg2 = league(roster=[player("Starter WR", slot="WR"), player("WR Two", slot="WR"), player("Bench WR", ros=3.0),
                         player("RB Two", pos="RB", ros=5.0)],
                 lineup_win={"slots": {"WR": ["Starter WR", "WR Two"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                 settings={"lineup_slots": {"RB": 2, "WR": 2}, "position_limits": {"WR": 3}},
                 trades=[{**trade(["WR In", "WR Also", "WR Three"], ["RB Two"]), "get_pos": ["WR", "WR", "WR"], "drops": []}])
    assert report._Spots(lg2, 0, set()).cut(lg2["trades"][0]) == (
        ["Bench WR"], "; drop Bench WR in the trade screen (ESPN caps WR at 3) and two more WRs have to go, your call")


# ---------- claim vs add ----------

def test_a_player_on_waivers_is_a_claim_with_the_priority_spelled_out():
    lg = league(waivers=[waiver("Bassett", on_waivers=True)], waiver_rank=7, faab_remaining=None)
    assert by_id(lg)["waiver:bassett"]["text"].startswith("claim Bassett (RB)") and "you are priority #7" in full(by_id(lg)["waiver:bassett"])
    lg = league(waivers=[waiver("Bassett", on_waivers=False)], waiver_rank=7)
    assert by_id(lg)["waiver:bassett"]["text"].startswith("add Bassett (RB)")


# ---------- trade deadline and skipped-trade memory ----------

def test_the_deadline_closes_the_trade_rows():
    lg = league(trades=[], trades_closed=True, trade_deadline_iso="2026-11-18T22:00")
    row = by_id(lg)["deadline"]
    assert "trade deadline passed" in row["text"] and "2026-11-18" in full(row)


def test_the_scan_stops_at_the_deadline(monkeypatch):
    from ff.packet import analyze_league
    from tests.test_packet_synthetic import FakeXW, make_snapshot
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    snap = make_snapshot()
    snap["settings"]["trade_deadline_ms"] = 1_000
    blk = analyze_league(snap, FakeXW(), {}, {}, {}, {}, sims=100, now=datetime(2026, 11, 20, tzinfo=UTC))
    assert blk["trades"] == [] and blk["trades_closed"] is True
    snap["settings"]["trade_deadline_ms"] = 4_000_000_000_000
    blk = analyze_league(snap, FakeXW(), {}, {}, {}, {}, sims=100, now=datetime(2026, 11, 20, tzinfo=UTC))
    assert blk["trades_closed"] is False


def test_a_skipped_trade_is_remembered_for_two_weeks(tmp_path):
    path = tmp_path / "skipped_trades.json"
    lg = league(trades=[{"rival": "Them", "give": ["A"], "get": ["Kai"], "my_delta_ppw": 1.0, "their_delta_ppw": 0.5, "why": [], "sendable": True}])
    packet = {"leagues": [lg]}
    reads = {"L1": {"items": {"trade:kai": {"verdict": "skip", "note": "not selling A"}}}}
    assert rulings.record_skips(packet, reads, path, today=date(2026, 9, 23)) == ["L1|Kai"]
    skips = rulings.load_skips(path)
    assert skips["L1|Kai"]["note"] == "not selling A"
    cands = [{"get": ["Kai"], "give": ["A"]}, {"get": ["Other"], "give": ["A"]}]
    assert [c["get"] for c in rulings.drop_recently_skipped(cands, skips, "L1", today=date(2026, 9, 30))] == [["Other"]]
    assert len(rulings.drop_recently_skipped(cands, skips, "L1", today=date(2026, 10, 30))) == 2   # memory expires
    assert len(rulings.drop_recently_skipped(cands, skips, "L2", today=date(2026, 9, 30))) == 2    # per league
    assert rulings.record_skips(packet, {"L1": {"items": {"trade:kai": "do"}}}, path, today=date(2026, 9, 23)) == []


# ---------- pushes: keep asking about a great trade ----------

def big(get="Star", give=("A",), mine=2.5, title=None, **kw):
    t = {"rival": "Them", "give": list(give), "get": [get], "my_delta_ppw": mine, "their_delta_ppw": 0.5, "why": [], "sendable": True,
         "must_try": mine >= 2.0, **kw}
    if title is not None:
        t["my_title_delta"] = title
    return t


def test_the_math_flags_a_big_package_and_the_card_puts_it_first():
    lg = league(trades=[{**big("Meh", mine=1.0), "must_try": False}, big("Star", mine=2.5)])
    rows = ids(lg)
    assert rows.index("trade:star") < rows.index("trade:meh")
    row = by_id(lg)["trade:star"]
    assert row["label"] == "Trade (do this one)" and row["push"] and "first ask" in full(row) and "skip it with a reason" in full(row)


def test_a_push_survives_the_three_row_cap():
    trades = [{**big(f"P{i}", give=(f"G{i}",), mine=1.0), "must_try": False} for i in range(3)] + [big("Star", give=("Z",), mine=2.5)]
    assert "trade:star" in ids(league(trades=trades))


def test_claude_can_push_a_row_and_lint_wants_a_reason():
    from tests.test_card import packet
    lg = league(trades=[{**big("Star", mine=1.0), "must_try": False}])
    p = packet(lg)
    assert any("needs a note" in w for w in report.read_lint(p, {"L1": {"items": {"trade:star": "push"}}}))
    assert report.read_lint(p, {"L1": {"items": {"trade:star": {"verdict": "push", "note": "Allen wins leagues"}}}}) == []
    assert any("trade rows only" in w for w in report.read_lint(p, {"L1": {"items": {"lineup": {"verdict": "push", "note": "x"}}}}))


def test_a_push_is_remembered_counted_and_closed_by_sending_or_skipping(tmp_path):
    path = tmp_path / "pushed.json"
    lg = league(trades=[{**big("Star", mine=1.0), "must_try": False}])
    packet = {"leagues": [lg]}
    reads = {"L1": {"items": {"trade:star": {"verdict": "push", "note": "Allen wins leagues"}}}}
    assert rulings.record_pushes(packet, reads, path, today=date(2026, 9, 23)) == ["L1|Star: opened"]
    pushes = rulings.load_pushes(path)
    assert pushes["L1|Star"]["status"] == "open" and pushes["L1|Star"]["note"] == "Allen wins leagues"
    # the next morning the scan produces it again: it comes back marked, day 2, with the reason
    cands = rulings.mark_pushed([{"get": ["Star"], "give": ["A"]}, {"get": ["Other"]}], pushes, "L1", today=date(2026, 9, 24))
    assert cands[0]["pushed"]["days"] == 2 and cands[0]["pushed"]["note"] == "Allen wins leagues" and "pushed" not in cands[1]
    lg2 = league(trades=[{**big("Star", mine=1.0), "must_try": False, "pushed": cands[0]["pushed"]}])
    assert "asked 2 mornings running" in full(by_id(lg2)["trade:star"]) and "Allen wins leagues" in full(by_id(lg2)["trade:star"])
    assert rulings.record_pushes({"leagues": [lg2]}, {}, path, today=date(2026, 9, 24)) == []  # still open, nothing new
    # Dustin sent it: the package is among his pending offers
    lg3 = {**lg2, "outgoing_trades": [{"rival": "Them", "give": ["A"], "get": ["Star"], "hours_left": 20}]}
    assert rulings.record_pushes({"leagues": [lg3]}, {}, path, today=date(2026, 9, 25)) == ["L1|Star: sent"]
    assert rulings.load_pushes(path)["L1|Star"]["status"] == "sent"
    assert "pushed" not in rulings.mark_pushed([{"get": ["Star"]}], rulings.load_pushes(path), "L1")[0]
    # a fresh push, then a skip with a reason closes it
    assert rulings.record_pushes(packet, reads, path, today=date(2026, 9, 26)) == ["L1|Star: opened"]
    ev = rulings.record_rulings(packet, {"L1": {"items": {"trade:star": {"verdict": "skip", "note": "not selling A"}}}},
                                tmp_path / "skips.json", path, today=date(2026, 9, 27))
    assert ev == ["skip L1|Star", "push L1|Star: skipped"]
    assert rulings.load_pushes(path)["L1|Star"]["status"] == "skipped"


def test_a_pushed_row_wears_the_badge_in_the_email(monkeypatch):
    from ff.email_html import render_email
    from tests.test_email_html import _packet
    p = _packet(monkeypatch)
    lg = p["leagues"][0]
    assert lg["trades"], "fixture should offer a trade"
    for t in lg["trades"]:
        t["must_try"] = False
    lg["trades"][0]["pushed"] = {"since": "2026-09-20", "days": 4, "note": "Allen wins leagues", "source": "claude"}
    html = render_email(p, {})
    assert "TRADE (DO IT)" in html and "asked 4 mornings running" in html and "Allen wins leagues" in html


def test_only_one_package_is_pushed_at_a_time_and_alternatives_are_not_both_pushed():
    trades = [big("Star", give=("A",), mine=2.5), big("Other", give=("A",), mine=2.2)]
    rows = by_id(league(trades=trades))
    assert rows["trade:star"]["push"] and not rows["trade:other"]["push"]


def test_a_remembered_math_push_drops_when_the_row_stops_being_sendable():
    """B3: the math pushed Star on Monday; by Wednesday a rival move left the package underwater at market value
    (`sendable` False). The row must not stay "do this one" on the strength of the memory; a Claude push keeps."""
    def stale():
        return {**big("Star", mine=2.5), "sendable": False, "must_try": False,
                "pushed": {"since": "2026-10-06", "days": 3, "note": None, "source": "math"}}
    t = stale()
    rows = by_id(league(trades=[t, {**big("Meh", mine=1.0), "must_try": False}]))
    assert "trade:star" not in rows and not rows["trade:meh"]["push"]
    assert "pushed" not in t  # the card's word is final: the dropped memory does not ride on as a push
    # the same morning the math flags a different package, that one is pushed instead
    rows = by_id(league(trades=[stale(), big("Fresh", give=("B",), mine=2.4)]))
    assert "trade:star" not in rows and rows["trade:fresh"]["push"] and "first ask" in full(rows["trade:fresh"])
    # a math push that is still sendable keeps its place and its day count
    rows = by_id(league(trades=[{**stale(), "sendable": True}]))
    assert rows["trade:star"]["push"] and "asked 3 mornings running" in full(rows["trade:star"])
    # Claude's push survives the math turning: his note is the reason, not the lineup gain
    claude = {**stale(), "my_delta_ppw": 0.5, "pushed": {"since": "2026-10-06", "days": 3, "note": "Allen wins leagues", "source": "claude"}}
    rows = by_id(league(trades=[claude]))
    assert rows["trade:star"]["push"] and "Allen wins leagues" in full(rows["trade:star"]) and "asked 3 mornings running" in full(rows["trade:star"])


def test_a_sent_or_skipped_push_stays_closed_until_claude_pushes_again(tmp_path):
    """B4: the math pushed Star, Dustin sent it, the rival declined two days later. The morning the offer leaves
    `pending_trades` the scan re-derives the same `must_try` row; the push must not reopen as a fresh "first ask"
    for SKIP_DAYS. Claude ruling `push` on it again (with a note) is the one thing that reopens it."""
    path = tmp_path / "pushed.json"
    math_row = league(trades=[big("Star", mine=2.5)])  # must_try: the math's push
    assert rulings.record_pushes({"leagues": [math_row]}, {}, path, today=date(2026, 9, 23)) == ["L1|Star: opened"]
    sent = {**league(trades=[]), "outgoing_trades": [{"rival": "Them", "give": ["A"], "get": ["Star"], "hours_left": 30}]}
    assert rulings.record_pushes({"leagues": [sent]}, {}, path, today=date(2026, 9, 24)) == ["L1|Star: sent"]
    # declined: the offer is gone from pending and the math flags the package again
    assert rulings.record_pushes({"leagues": [math_row]}, {}, path, today=date(2026, 9, 26)) == []
    assert rulings.load_pushes(path)["L1|Star"] == {**rulings.load_pushes(path)["L1|Star"], "status": "sent", "closed": "2026-09-24"}
    assert rulings.record_pushes({"leagues": [math_row]}, {}, path, today=date(2026, 10, 7)) == []  # still inside SKIP_DAYS
    # Claude says push anyway: that reopens it, with his note as the reason
    reads = {"L1": {"items": {"trade:star": {"verdict": "push", "note": "he is thin at QB now"}}}}
    assert rulings.record_pushes({"leagues": [math_row]}, reads, path, today=date(2026, 9, 26)) == ["L1|Star: opened"]
    p = rulings.load_pushes(path)["L1|Star"]
    assert p["status"] == "open" and p["since"] == "2026-09-26" and p["source"] == "claude" and p["note"] == "he is thin at QB now"
    # a skip with a reason closes it; a math row the next week (skip memory lost) does not reopen it...
    skip = {"L1": {"items": {"trade:star": {"verdict": "skip", "note": "not selling A"}}}}
    assert rulings.record_pushes({"leagues": [math_row]}, skip, path, today=date(2026, 9, 27)) == ["L1|Star: skipped"]
    assert rulings.record_pushes({"leagues": [math_row]}, {}, path, today=date(2026, 10, 4)) == []
    assert rulings.load_pushes(path)["L1|Star"]["status"] == "skipped"
    # ...until SKIP_DAYS have passed, when the math may ask again
    assert rulings.record_pushes({"leagues": [math_row]}, {}, path, today=date(2026, 10, 12)) == ["L1|Star: opened"]
