"""End-to-end over the synthetic demo league: analyze_league -> report.render, no network."""
from datetime import UTC, datetime

import pytest

from ff import demo, report
from ff.model.season import TITLE_RUN_MULT
from ff.packet import PACKET_VERSION, analyze_league

FakeXW = demo.FakeCrosswalk
make_snapshot = demo.make_snapshot


def test_end_to_end_synthetic(monkeypatch):
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    snap = make_snapshot()
    blk = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides=None, sims=300)
    assert blk["my_team_id"] == 1
    assert sum(len(v) for v in blk["lineup_win"]["slots"].values()) == 9
    assert blk["lineup_win"]["p_win"] is not None
    assert abs(sum(o["title_pct"] for o in blk["odds"].values()) - 100) < 2
    assert blk["waivers"] and all(w["bid"] <= blk["faab_remaining"] for w in blk["waivers"])
    assert blk["settings"]["ir_slots"] == 1 and blk["settings"]["reg_season_weeks"] == 14
    # the playoff-week weight the season pricing used for me: the settings' weeks, my odds times TITLE_RUN_MULT
    pw = blk["playoff_weight"]
    me = blk["odds"][str(blk["my_team_id"])]
    assert pw["weeks"] == blk["settings"]["playoff_weeks"] == [15, 16] and pw["playoff_pct"] == me["playoff_pct"]
    assert pw["weight"] == pytest.approx(TITLE_RUN_MULT * me["playoff_pct"] / 100, abs=1e-3) and pw["mult"] == TITLE_RUN_MULT
    md = "\n".join(report._detail_matchup(blk))
    assert ("playoff weeks 15-16 weighted ×" in md) == (abs(pw["weight"] - 1.0) >= 0.05)
    hurt = {r["name"]: r for r in blk["injuries"]}
    ir_guy = next(p for p in blk["roster"] if p["sources"]["espn_status"] == "INJURY_RESERVE")
    assert ir_guy["weeks_out"] == 4 and ir_guy["mu_ros"] < ir_guy["mu_ros_active"] and hurt[ir_guy["name"]]["verdict"] == "ir"
    assert any(x["kind"] == "injury" for x in report.todos(blk))
    packet = {"version": PACKET_VERSION, "generated": "2026-09-10T07:00:00", "season": 2026,
              "shared": {"injury_watchlist": [], "exposure": {}, "trending_adds": [], "usage_error": None, "unmatched_ids": []},
              "leagues": [blk]}
    md = report.render(packet)
    assert "Demo League" in md and "## Waivers" in md and "## League odds" in md and "Lineup" in md
    # incoming offer: evaluated, first in the checklist, outgoing listed separately
    inc = blk["incoming_trades"]
    by_id = {r["espn_id"]: r["name"] for r in snap["roster"]}
    offer = snap["pending_trades"][0]
    give, get = [by_id[i] for i in offer["give"]], [by_id[i] for i in offer["get"]]
    rival2, rival3 = snap["teams"][1]["name"], snap["teams"][2]["name"]
    assert len(inc) == 1 and inc[0]["verdict"] in ("accept", "decline", "counter") and inc[0]["rival"] == rival2
    assert inc[0]["give"] == give and inc[0]["get"] == get and "my_title_delta" in inc[0]
    assert len(blk["outgoing_trades"]) == 1 and blk["outgoing_trades"][0]["rival"] == rival3
    first = report.todos(blk)[0]
    assert first["kind"] == "trade_in" and f"{rival2} offers {get[0]} for your {give[0]}, {give[1]}" in first["text"]
    assert "## Incoming offers" in md and "Your open offers" in md
    short = report.render(packet, reads={"demo": {"reply": "thanks but no", "reply_to": rival2}}, only_incoming=True)
    assert short.startswith("# FF trade offer") and f"Reply to {rival2}" in short and "## Waivers" not in short
    # overrides flow through, including the season horizon into the research list
    done = {str(ir_guy["espn_id"]): {"weeks_out": "season", "note": "torn ACL"}}
    blk3 = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides=done, sims=100)
    gone = next(p for p in blk3["roster"] if p["espn_id"] == ir_guy["espn_id"])
    assert gone["mu_ros"] == 0 and gone["return_week"] is None and gone["sources"]["weeks_out_source"] == "override"
    assert {r["verdict"] for r in blk3["injuries"] if r["espn_id"] == ir_guy["espn_id"]} == {"ir"}


def _starters(blk: dict, key: str) -> set[str]:
    return {name for names in blk[key]["slots"].values() for name in names}


def test_p_zero_override_sits_the_starter(monkeypatch):
    """A `p_zero` of 1.0 in overrides.json is a sit, not a note: this week's EV goes to zero, the player leaves both
    lineups and the card's lineup row, the EV-ordered roster puts him in the zero tail, and the horizon counts the game
    missed. (The old assertion ended in `or any(p_zero == 1.0)`, which the override itself made true, so an `ev` that
    ignored sit risk still passed.)"""
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    snap = make_snapshot()
    base = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides=None, sims=100)
    star = base["roster"][0]  # EV-ordered: the top healthy starter
    assert star["p_zero"] < 0.1 and star["name"] in _starters(base, "lineup_ev") & _starters(base, "lineup_win")
    base_lineup = next(x for x in report.todos(base) if x["kind"] == "lineup")
    assert f"{star['name']}: bench → " in base_lineup["text"]  # the demo benches him; the card says start him
    blk = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides={str(star["espn_id"]): {"p_zero": 1.0}}, sims=100)
    me = next(p for p in blk["roster"] if p["espn_id"] == star["espn_id"])
    assert me["p_zero"] == 1.0 and me["ev"] == 0.0 and "llm:p_zero" in me["flags"]
    assert me["mu"] == star["mu"]  # sit risk, not a downgrade: the when-he-plays number is untouched
    assert me["weeks_out"] == 1.0 and me["return_week"] == blk["week"] + 1 and me["mu_ros"] < star["mu_ros"]
    assert me["name"] not in _starters(blk, "lineup_ev") | _starters(blk, "lineup_win")
    assert sum(len(v) for v in blk["lineup_win"]["slots"].values()) == 9  # the backup fills the slot
    assert all(p["ev"] == 0.0 for p in blk["roster"][blk["roster"].index(me):])
    lineup = next(x for x in report.todos(blk) if x["kind"] == "lineup")
    assert star["name"] not in lineup["text"] and f"{star['name']}: bench → " not in lineup["text"]


def _trade(rival: str, give: list[str], get: list[str], mine: float, theirs: float, **kw) -> dict:
    return {"rival": rival, "give": give, "get": get, "my_delta_ppw": mine, "their_delta_ppw": theirs, "sendable": theirs >= 0,
            "must_try": False, "pushed": None, "why": [], "drops": [], "accept_word": None, "p_accept": None,
            "after_line": None, "fallback": None, "rival_after": [], **kw}


def _trade_rows(blk: dict, trades: list[dict]) -> list[dict]:
    blk["trades"] = trades
    return [t for t in report.todos(blk) if t["kind"] == "trade"]


def test_card_lists_every_worth_sending_trade(monkeypatch):
    """The trade card: the one pushed package first, then sendable packages in scan order, `TRADE_ROWS` rows in all,
    and never a reach (a package only I like). Hand-built packages over the demo block, so each row's id, label and
    text is known before the card is rendered."""
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    blk = analyze_league(make_snapshot(), FakeXW(), {}, {}, {}, {}, overrides=None, sims=100)
    assert report.TRADE_ROWS == 2
    reach = _trade("Bye Week Blues", ["Pat Reach"], ["Vic Target"], 4.0, -0.8)  # my biggest gain, but he loses
    fair = _trade("Sunday Scaries", ["Al Fair"], ["Bo Swap"], 1.1, 0.3)
    big = _trade("Ctrl+Alt+Delete Kelce", ["Cy Big"], ["Dee Star"], 2.6, 0.1, must_try=True, accept_word="he'd likely take it", p_accept=0.7)
    even = _trade("Bye Week Blues", ["Ed Even"], ["Fy Even"], 0.9, 0.0)
    rows = _trade_rows(blk, [reach, fair, big, even])
    assert [r["id"] for r in rows] == ["trade:dee-star", "trade:bo-swap"]  # pushed first, then scan order; `even` is over the cap
    assert [r["label"] for r in rows] == ["Trade (do this one)", "Trade (worth sending)"]
    assert [r["push"] for r in rows] == [True, False] and all(r["worth"] for r in rows)
    assert rows[0]["rival"] == "Ctrl+Alt+Delete Kelce" and rows[0]["give"] == ["Cy Big"] and rows[0]["get"] == ["Dee Star"]
    assert rows[0]["text"] == ("offer Ctrl+Alt+Delete Kelce your Cy Big for Dee Star (+2.6 pts/wk for you, +0.1 for them, he'd likely take it, p(accept) 0.70) "
                               "— too good to let slide (+2.6 pts/wk is a big lineup gain by the math); first ask. "
                               "Send it, or skip it with a reason and it stops")
    assert rows[1]["text"] == "offer Sunday Scaries your Al Fair for Bo Swap (+1.1 pts/wk for you, +0.3 for them)"
    assert rows[0]["p_accept"] == 0.7 and rows[1]["p_accept"] is None and rows[0]["drops"] == [] and rows[0]["warn"] == []
    # a neutral package reads "neutral for them"; with no push the sendable ones fill the card in scan order
    rows = _trade_rows(blk, [reach, even, fair])
    assert [r["id"] for r in rows] == ["trade:fy-even", "trade:bo-swap"] and not any(r["push"] for r in rows)
    assert rows[0]["text"] == "offer Bye Week Blues your Ed Even for Fy Even (+0.9 pts/wk for you, neutral for them)"
    # two packages the math flags: only the bigger gain is pushed, the other is demoted and not on the card this morning
    small = _trade("Sunday Scaries", ["Gus Small"], ["Hal Small"], 2.2, 0.2, must_try=True)
    rows = _trade_rows(blk, [fair, small, big])
    assert [(r["id"], r["push"]) for r in rows] == [("trade:dee-star", True), ("trade:bo-swap", False)]
    assert small["must_try"] is False and big["must_try"] is True
    # Claude's remembered push leads even when the math no longer calls it sendable, with his note and the day count
    held = _trade("Bye Week Blues", ["Ian Hold"], ["Jo Hold"], 1.4, -0.2, pushed={"source": "claude", "days": 3, "note": "he needs a QB"})
    rows = _trade_rows(blk, [fair, held])
    assert [(r["id"], r["label"], r["worth"]) for r in rows] == [("trade:jo-hold", "Trade (do this one)", False), ("trade:bo-swap", "Trade (worth sending)", True)]
    assert rows[0]["text"].endswith("— too good to let slide (he needs a QB); asked 3 mornings running. Send it, or skip it with a reason and it stops")
    # nothing sendable: no trade row at all, not a "reach" row
    assert _trade_rows(blk, [reach]) == [] and _trade_rows(blk, []) == []


def test_empty_offer_has_zero_title_delta(monkeypatch):
    """A3: trade and offer rows compare a re-sim against the baseline with common random numbers, so an offer that
    moves nobody reads 0.0 at any draw count; a re-sim on its own seed used to read up to +-1.5 title points."""
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    snap = make_snapshot()
    null = {**snap["pending_trades"][0], "give": [], "get": []}
    snap["pending_trades"] = [null]
    for sims in (100, 300):
        blk = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides=None, sims=sims)
        inc = blk["incoming_trades"]
        assert len(inc) == 1 and inc[0]["my_title_delta"] == 0.0 and inc[0]["their_title_delta"] == 0.0
        assert type(inc[0]["my_title_delta"]) is float


def _bench_best_rb(snap: dict, tid: int) -> tuple[dict, dict]:
    """Swap the rival's top RB to the bench and his fourth RB into the slot; returns (benched, promoted)."""
    rbs = sorted((r for r in snap["roster"] if r["fantasy_team_id"] == tid and r["pos"] == "RB"), key=lambda r: -r["proj_week"])
    best, scrub = rbs[0], rbs[-1]
    assert best["slot"] == "RB" and scrub["slot"] == "BE"
    best["slot"], scrub["slot"] = "BE", "RB"
    return best, scrub


def _fill_flex(snap: dict, tid: int) -> None:
    wr = sorted((r for r in snap["roster"] if r["fantasy_team_id"] == tid and r["pos"] == "WR" and r["slot"] == "BE"), key=lambda r: -r["proj_week"])[0]
    wr["slot"] = "RB/WR/TE"


def test_a_set_lineup_on_sunday_is_the_opponents_strength(monkeypatch):
    """A4: from Saturday on the rival's lineup is set and visible in each player's `slot`, so a rival who benched his
    best RB projects for less than the optimizer's guess; midweek, with a slot still open, the optimizer stands."""
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    sunday, wednesday = datetime(2026, 10, 11, 14, tzinfo=UTC), datetime(2026, 10, 7, 14, tzinfo=UTC)
    snap = make_snapshot()
    opp = snap["matchups"][0]["away"]
    run = lambda s, now: analyze_league(s, FakeXW(), {}, {}, {}, {}, overrides=None, sims=100, now=now)
    full = run(snap, sunday)["opponent"]
    best, scrub = _bench_best_rb(snap, opp)
    benched = run(snap, sunday)["opponent"]
    assert benched["lineup"] == "set" and full["lineup"] == "set"
    drop = full["mu"] - benched["mu"]
    assert 0.5 * (best["proj_week"] - scrub["proj_week"]) < drop < 1.5 * (best["proj_week"] - scrub["proj_week"]), (full, benched)
    # midweek the flex is still empty (the demo never fills it), so the optimizer's lineup is the forecast
    mid = run(snap, wednesday)["opponent"]
    assert mid["lineup"] == "projected" and mid["mu"] > benched["mu"]
    # ...unless every starter slot is already filled: a set lineup is a set lineup whatever the day
    _fill_flex(snap, opp)
    mid_full = run(snap, wednesday)["opponent"]
    assert mid_full["lineup"] == "set" and mid_full["mu"] < mid["mu"]
    # fixtures and the demo pass no `now`: nothing changes with the calendar there
    assert run(snap, None)["opponent"]["lineup"] == "set"


def test_an_accepted_trade_is_applied_to_the_rosters_before_it_processes(monkeypatch):
    """Oct 13: the Hubbard trade was accepted Tuesday and processed Wednesday. Until then ESPN keeps the players on
    their old rosters, so the card named one of them as a claim's drop (ESPN refuses) and put another in Sunday's
    lineup. The packet now moves them up front: the incoming player is mine (on the bench, `pending_trade` "in"),
    the outgoing ones are gone from every row but the Accepted one, today's open spots are still today's bodies,
    and the spots the trade frees are listed with who fills them."""
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    base_snap = make_snapshot()
    base = analyze_league(base_snap, FakeXW(), {}, {}, {}, {}, overrides=None, sims=100)
    snap = make_snapshot()
    by_id = {r["espn_id"]: r["name"] for r in snap["roster"]}
    out = snap["pending_trades"][1]
    extra = next(r["espn_id"] for r in snap["roster"] if r["fantasy_team_id"] == 1 and r["pos"] == "WR" and r["slot"] == "BE")
    out.update({"accepted": True, "process_ts": 1791986732413, "give": out["give"] + [extra]})  # a 2-for-1 I send
    give, get = [by_id[i] for i in out["give"]], [by_id[i] for i in out["get"]]
    blk = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides=None, sims=100)
    names = {p["name"]: p for p in blk["roster"]}
    assert get[0] in names and names[get[0]]["pending_trade"] == "in" and names[get[0]]["slot"] == "BE"
    assert not any(n in names for n in give)
    assert blk["pending_moves"] == [{"id": "out-1", "in": get, "out": give, "process_ts": 1791986732413}]
    assert blk["open_spots"] == base["open_spots"] and blk["trade_spots_after"] == 1
    assert len(blk["trade_adds_after"]) == 1 and blk["trade_adds_after"][0]["name"] not in {a["name"] for a in blk["open_spot_adds"]}
    acc = [t for t in blk["outgoing_trades"] if t.get("accepted")]
    assert len(acc) == 1 and acc[0]["give"] == give and acc[0]["get"] == get and acc[0]["processes_iso"]
    assert len(blk["incoming_trades"]) == 1  # the other proposal is still an offer to rule on
    rows = report.todos(blk)
    sent = next(x for x in rows if x["kind"] == "sent")
    assert "is accepted, processes" in sent["text"] and "frees 1 bench spot when it processes" in sent["text"]
    assert f"then add {blk['trade_adds_after'][0]['name']}" in sent["text"]
    for x in rows:
        if x["kind"] != "sent":
            assert not any(n in x["text"] for n in give), (x["kind"], x["text"])
    # the rival's side: he has my two, not his one
    rival_rows = [p for p in snap["roster"] if p["fantasy_team_id"] == 3]
    assert rival_rows and blk["rival_needs"]  # his roster still prices (no crash on the moved bodies)
