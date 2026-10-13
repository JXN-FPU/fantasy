"""The offer outcome log (rulings.record_outcomes / history), the fallback on a trade row, the counter on an offer."""
from datetime import UTC, date, datetime

from ff.model.trades import evaluate, scan
from ff.rulings import history, load_outcomes, record_outcomes
from tests.conftest import P


def _packet(outgoing, roster_names=("Keeper",), error=None, incoming=()):
    return {"leagues": [{"name": "L1", "outgoing_trades": list(outgoing), "incoming_trades": list(incoming),
                         "pending_trades_error": error, "roster": [{"name": n} for n in roster_names]}]}


def _ms(iso_utc: str) -> int:
    return int(datetime.fromisoformat(iso_utc).replace(tzinfo=UTC).timestamp() * 1000)


def offer(oid, give, get, rid=4, expires="2026-10-09T12:00", proposed="2026-10-07T09:00"):
    # `expires_iso` is what `_ms_iso` writes on a UTC machine (naive, machine-local); `expires_ts` is the clock
    return {"id": oid, "rival": "Hawk Tua", "rival_team_id": rid, "give": list(give), "get": list(get),
            "expires_iso": expires, "expires_ts": _ms(expires), "proposed_ts": _ms(proposed)}


def test_an_offer_is_opened_when_seen_and_closed_by_what_happened(tmp_path):
    path = tmp_path / "offer_outcomes.json"
    d1, d2 = date(2026, 10, 7), date(2026, 10, 8)
    assert record_outcomes(_packet([offer("a", ["Montgomery"], ["Dak"]), offer("b", ["Coker"], ["Warren"], rid=5)]), path, d1) == ["L1|a: opened", "L1|b: opened"]
    # next morning: 'a' is gone and Dak is on my roster (accepted); 'b' is gone, nothing moved, not yet expired (declined)
    events = record_outcomes(_packet([], roster_names=("Dak", "Coker")), path, d2)
    assert sorted(events) == ["L1|a: accepted", "L1|b: declined"]
    outs = load_outcomes(path)
    assert outs["L1|a"]["status"] == "accepted" and outs["L1|b"]["closed"] == "2026-10-08"


def test_an_offer_past_its_expiry_closes_as_expired_and_an_unreadable_list_closes_nothing(tmp_path):
    path = tmp_path / "o.json"
    record_outcomes(_packet([offer("c", ["X"], ["Y"], expires="2026-10-08T12:00")]), path, date(2026, 10, 7))
    assert record_outcomes(_packet([], error="ESPN said no"), path, date(2026, 10, 10)) == []
    assert record_outcomes(_packet([]), path, date(2026, 10, 10)) == ["L1|c: expired"]
    assert record_outcomes(_packet([]), path, date(2026, 10, 11)) == []  # closed once


def test_history_gives_a_prior_after_two_answers_and_flags_a_fresh_no():
    outs = {"L1|1": {"league": "L1", "rival_team_id": 4, "status": "declined", "closed": "2026-10-06"},
            "L1|2": {"league": "L1", "rival_team_id": 4, "status": "accepted", "closed": "2026-09-20"},
            "L1|3": {"league": "L1", "rival_team_id": 5, "status": "expired", "closed": "2026-09-01"},
            "L2|9": {"league": "L2", "rival_team_id": 4, "status": "declined", "closed": "2026-10-06"}}
    h = history(outs, "L1", date(2026, 10, 7))
    assert h[4] == {"n": 2, "recent_decline": True, "prior": 0.5}
    assert h[5] == {"n": 1, "recent_decline": False, "prior": None}
    assert 9 not in h and "L2" not in str(h)


def test_an_offer_that_lapsed_the_evening_before_in_denver_is_expired_not_declined(tmp_path):
    """B5: the expiry is 9 PM Denver on the 8th, which is 03:00 UTC on the 9th; the 6 AM Denver run on the 9th used
    to compare the date part only ("2026-10-09" < "2026-10-09" is false) and call it his decline."""
    path = tmp_path / "o.json"
    record_outcomes(_packet([offer("a", ["Coker"], ["Rice"], expires="2026-10-09T03:00")]), path, date(2026, 10, 8))
    run = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert record_outcomes(_packet([], roster_names=("Coker",)), path, now=run) == ["L1|a: expired"]
    assert load_outcomes(path)["L1|a"]["closed"] == "2026-10-09"
    # and one that still had six hours to run when it vanished is a decline
    record_outcomes(_packet([offer("b", ["Coker"], ["Rice"], expires="2026-10-09T18:00")]), path, date(2026, 10, 8))
    assert record_outcomes(_packet([], roster_names=("Coker",)), path, now=run) == ["L1|b: declined"]


def test_a_counter_from_the_rival_and_a_piece_i_moved_are_not_his_decline(tmp_path):
    path = tmp_path / "o.json"
    d1, d2 = date(2026, 10, 7), date(2026, 10, 8)
    record_outcomes(_packet([offer("a", ["Coker"], ["Rice"]), offer("b", ["Evans", "Washington"], ["London"], rid=10),
                             offer("c", ["Pollard"], ["Hall"], rid=6), offer("d", ["Mixon"], ["Henry"], rid=7)]), path, d1)
    his_counter = {"id": "x", "rival_team_id": 4, "proposed_ts": _ms("2026-10-07T20:00"), "give": ["Coker", "TE2"], "get": ["Rice"]}
    # a: gone, Coker still mine, his counter is pending -> countered. b: gone, London arrived, Washington stayed -> he took
    # a different package. c: gone, Pollard left my roster and nothing came back -> I moved the piece, ESPN voided it.
    # d: gone, Mixon still mine, no counter, not expired -> declined.
    events = record_outcomes(_packet([], roster_names=("Coker", "London", "Washington", "Mixon"), incoming=[his_counter]), path, d2)
    assert sorted(events) == ["L1|a: countered", "L1|b: countered", "L1|c: withdrawn", "L1|d: declined"]
    h = history(load_outcomes(path), "L1", d2)
    assert h[7] == {"n": 1, "recent_decline": True, "prior": None}
    assert 4 not in h and 6 not in h and 10 not in h  # none of those is an answer from him


def test_only_a_decline_carries_the_recent_decline_factor():
    outs = {"L1|1": {"league": "L1", "rival_team_id": 4, "status": "expired", "closed": "2026-10-06"},
            "L1|2": {"league": "L1", "rival_team_id": 4, "status": "declined", "closed": "2026-09-20"},
            "L1|3": {"league": "L1", "rival_team_id": 5, "status": "withdrawn", "closed": "2026-10-06"},
            "L1|4": {"league": "L1", "rival_team_id": 6, "status": "countered", "closed": "2026-10-06"}}
    h = history(outs, "L1", date(2026, 10, 7))
    assert h[4] == {"n": 2, "recent_decline": False, "prior": 0.167}  # the lapse still counts as a no in the prior
    assert 5 not in h and 6 not in h


def _rosters():
    mine = [P(1, "QB", "QB", 20, tid=1), P(2, "RB1", "RB", 16, tid=1), P(3, "RB2", "RB", 15, tid=1), P(4, "RB3", "RB", 14, tid=1),
            P(5, "WR1", "WR", 14, tid=1), P(6, "WR2", "WR", 12, tid=1), P(21, "WR3", "WR", 10, tid=1), P(7, "TE", "TE", 3, tid=1), P(8, "K", "K", 8, tid=1), P(9, "D", "D/ST", 7, tid=1)]
    theirs = [P(11, "QB", "QB", 20, tid=2), P(12, "RB1", "RB", 8, tid=2), P(13, "RB2", "RB", 6, tid=2),
              P(15, "WR1", "WR", 14, tid=2), P(16, "WR2", "WR", 12, tid=2), P(17, "WR3", "WR", 11, tid=2), P(18, "TEgood", "TE", 12, tid=2), P(22, "TE2", "TE", 6, tid=2), P(19, "K", "K", 8, tid=2), P(20, "D", "D/ST", 7, tid=2)]
    return mine, theirs


REPL = {"QB": 14, "RB": 7, "WR": 8, "TE": 5, "K": 6, "D/ST": 5}


def test_a_fresh_no_from_the_rival_lowers_every_package_to_him(slots):
    mine, theirs = _rosters()
    meta = {2: {"name": "Rival", "wins": 2, "losses": 2}}
    cold = scan(1, {1: mine, 2: theirs}, slots, REPL, meta, values={})
    warm = scan(1, {1: mine, 2: theirs}, slots, REPL, meta, values={}, history={2: {"recent_decline": True, "prior": None}})
    by = {tuple(c["get"]): c for c in warm}
    for c in cold:
        w = by.get(tuple(c["get"]))
        assert w is None or w["p_accept"] < c["p_accept"]
        if w:
            assert any("turned one down" in r for r in w["accept_why"])


def test_the_row_carries_a_fallback_package_for_the_same_ask(slots):
    """Two packages for his TE: the one that gains me more leads, the one he is likelier to take is the fallback."""
    mine, theirs = _rosters()
    values = {"2": {"redraft_value": 3000}, "3": {"redraft_value": 2800}, "4": {"redraft_value": 2200}, "18": {"redraft_value": 2500}}
    out = scan(1, {1: mine, 2: theirs}, slots, REPL, {2: {"name": "Rival", "wins": 2, "losses": 2}}, values)
    rows = [c for c in out if c["get"] == ["TEgood"]]
    assert rows and rows[0].get("fallback"), rows
    fb = rows[0]["fallback"]
    assert fb["give"] != rows[0]["give"] and fb["p_accept"] > rows[0]["p_accept"] and fb["text"].startswith("if he says no, offer ")


def test_a_counter_names_the_one_swap_that_makes_it_a_yes(slots):
    """He offers his good TE for my RB1 at a price that is a lowball to me; the counter swaps in my RB3, fair both ways."""
    mine, theirs = _rosters()
    by_id = {p.espn_id: p for p in mine + theirs}
    values = {"2": {"redraft_value": 3500}, "3": {"redraft_value": 3000}, "4": {"redraft_value": 2600}, "18": {"redraft_value": 2500}}
    out = evaluate(1, 2, [by_id[2]], [by_id[18]], {1: mine, 2: theirs}, slots, REPL, values)
    assert out["verdict"] == "counter"
    assert out["counter"] and out["counter"]["get"] == ["TEgood"] and out["counter"]["give"] in (["RB3"], ["RB2"])
    assert out["counter"]["my_delta_ppw"] >= 0.75 and out["counter"]["fair_his"] >= 0.9
    accepted = evaluate(1, 2, [by_id[4]], [by_id[18]], {1: mine, 2: theirs}, slots, REPL, values)
    assert accepted["verdict"] == "accept" and accepted["counter"] is None


# ---------- the IR-slot memory (rulings.note_ir_roster / ir_memory) ----------

def _ir_roster(*players):
    return [{"espn_id": i, "name": n, "slot": slot} for i, n, slot in players]


def test_ir_moves_stamp_the_stash_with_who_came_in_and_the_exit():
    from ff.rulings import ir_memory, load_ir_moves, note_ir_roster
    moves = load_ir_moves(__file__ + ".missing")  # no file: an empty memory, not an error
    d1, d2, d3 = date(2026, 10, 1), date(2026, 10, 3), date(2026, 10, 7)
    # first run only seeds the roster: Daniels is already on IR, nothing is claimed about how he got there
    assert note_ir_roster(moves, "L2", _ir_roster((1, "Daniels", "IR"), (2, "Jacobs", "BE")), d1) == []
    assert ir_memory(moves, "L2", d1) == {}
    # Daniels came off, Jacobs was cut for the spot (not a stash, not recorded)
    assert note_ir_roster(moves, "L2", _ir_roster((1, "Daniels", "BE")), d2) == ["L2|1: left"]
    assert ir_memory(moves, "L2", d2)[1] == {"left_days": 0, "stashed": None, "added": []}
    # back on IR the same week, McGowan came in on the freed spot
    assert note_ir_roster(moves, "L2", _ir_roster((1, "Daniels", "IR"), (3, "McGowan", "BE")), d2) == ["L2|1: stashed"]
    mem = ir_memory(moves, "L2", d3)
    assert mem[1] == {"left_days": 4, "stashed": "2026-10-03", "added": ["McGowan"]}
    assert ir_memory(moves, "L1", d3) == {}  # another league sees nothing


def test_ir_moves_survive_a_round_trip_through_the_file(tmp_path):
    from ff.rulings import load_ir_moves, note_ir_roster, save_ir_moves
    path = tmp_path / "ir_moves.json"
    moves = load_ir_moves(path)
    note_ir_roster(moves, "L1", _ir_roster((5, "Williams", "BE")), date(2026, 10, 5))
    note_ir_roster(moves, "L1", _ir_roster((5, "Williams", "IR"), (6, "Brissett", "BE")), date(2026, 10, 6))
    save_ir_moves(moves, path)
    again = load_ir_moves(path)
    assert again["moves"]["L1|5"]["added"] == ["Brissett"] and again["rosters"]["L1"]["ir"] == ["5"]
    # a drop straight off IR (not on the roster any more) is not an activation
    assert note_ir_roster(again, "L1", _ir_roster((6, "Brissett", "BE")), date(2026, 10, 7)) == []


def test_an_offer_i_withdrew_in_the_app_closes_as_withdrawn_from_espn_history(tmp_path):
    """B10: a cancel with my roster unchanged left no trace in the pending view and read as his decline. ESPN's
    history view (`trade_resolutions` on the league block, from mTransactions2) says who closed it."""
    path = tmp_path / "o.json"
    d1 = date(2026, 10, 7)
    record_outcomes(_packet([offer("a", ["Coker"], ["Rice"]), offer("b", ["Evans"], ["London"], rid=5),
                             offer("c", ["Mixon"], ["Henry"], rid=6, expires="2026-10-08T14:00")]), path, d1)
    res = {"a": {"status": "withdrawn", "by": 1, "ts": 1}, "b": {"status": "declined", "by": 5, "ts": 2},
           "c": {"status": "declined", "by": 6, "ts": 3}}
    pk = _packet([], roster_names=("Coker", "Evans", "Mixon"))
    pk["leagues"][0]["trade_resolutions"] = res
    # a: I cancelled it. b: he declined (the history says so, the roster could not). c: he declined before it lapsed and
    # the run is after the expiry: his answer, not a lapse.
    events = record_outcomes(pk, path, now=datetime(2026, 10, 8, 16, 0, tzinfo=UTC))
    assert sorted(events) == ["L1|a: withdrawn", "L1|b: declined", "L1|c: declined"]
    h = history(load_outcomes(path), "L1", date(2026, 10, 8))
    assert 4 not in h and h[5]["recent_decline"] and h[6]["recent_decline"]


def test_the_history_view_does_not_overrule_a_counter_or_an_acceptance(tmp_path):
    path = tmp_path / "o.json"
    record_outcomes(_packet([offer("a", ["Coker"], ["Rice"]), offer("b", ["Pollard"], ["Hall"], rid=6)]), path, date(2026, 10, 7))
    his_counter = {"id": "x", "rival_team_id": 4, "proposed_ts": _ms("2026-10-07T20:00"), "give": ["Coker", "TE2"], "get": ["Rice"]}
    pk = _packet([], roster_names=("Coker", "Hall"), incoming=[his_counter])
    # ESPN declines the original when he counters; the counter on the table is what matters. And a roster that
    # shows the swap is an acceptance whatever the history says.
    pk["leagues"][0]["trade_resolutions"] = {"a": {"status": "declined", "by": 4, "ts": 1}, "b": {"status": "declined", "by": 6, "ts": 1}}
    assert sorted(record_outcomes(pk, path, date(2026, 10, 8))) == ["L1|a: countered", "L1|b: accepted"]


def test_an_accepted_trade_stays_open_through_the_review_period_and_closes_as_accepted(tmp_path):
    """2026-10-13: the Hubbard trade was accepted Tuesday morning and processed Wednesday. The pending view kept it
    (type TRADE_ACCEPT) but the reader dropped it, the players had not moved yet, and the log closed it as his
    decline (`recent_decline` on every package to him for a week). Now the row rides `outgoing_trades` with
    `accepted` while it waits, and ESPN's history (`trade_resolutions`) says accepted even before the rosters move."""
    path = tmp_path / "o.json"
    d1, d2, d3 = date(2026, 10, 13), date(2026, 10, 13), date(2026, 10, 14)
    o = offer("a", ["Tuten", "Coker", "Rodriguez"], ["Hubbard"], expires="2026-10-14T17:04")
    assert record_outcomes(_packet([o]), path, d1) == ["L1|a: opened"]
    # later that morning: accepted, still pending on ESPN -> still open, nothing to close
    accepted = {**o, "accepted": True, "process_ts": _ms("2026-10-14T14:05"), "processes_iso": "2026-10-14T08:05"}
    assert record_outcomes(_packet([accepted], roster_names=("Tuten", "Coker", "Rodriguez")), path, d2) == []
    assert load_outcomes(path)["L1|a"]["status"] == "open"
    # Wednesday: processed, gone from pending, Hubbard on my roster -> accepted
    assert record_outcomes(_packet([], roster_names=("Hubbard",)), path, d3) == ["L1|a: accepted"]
    # and the history view alone is enough when the rosters have not moved (a run between his click and the processing
    # in a league whose reader lost the row)
    record_outcomes(_packet([offer("b", ["X"], ["Y"])]), path, d1)
    pk = _packet([], roster_names=("X",))
    pk["leagues"][0]["trade_resolutions"] = {"b": {"status": "accepted", "by": 4, "ts": 1}}
    assert record_outcomes(pk, path, d2) == ["L1|b: accepted"]
    h = history(load_outcomes(path), "L1", d3)
    assert h[4]["recent_decline"] is False and h[4]["n"] == 2 and h[4]["prior"] == 0.833
