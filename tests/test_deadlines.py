"""The league's clocks (`model/deadlines.py`): the waiver run, a dropped player's clear time, when an accepted
offer executes, and how the rows and the ledger read them."""
from datetime import UTC, datetime

from ff import ledger, report
from ff.model import deadlines as dl
from tests.test_card import by_id, league, player
from tests.test_review_fixes import waiver

ET = dl.ET
DAYS = ["MONDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def et(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ET)


def test_the_next_waiver_run_is_3am_eastern_on_the_next_processing_day():
    assert dl.next_waiver_run(et(2026, 10, 13, 12), DAYS) == et(2026, 10, 14, 3)   # Tuesday noon -> Wednesday 3 AM
    assert dl.next_waiver_run(et(2026, 10, 14, 2, 59), DAYS) == et(2026, 10, 14, 3)
    assert dl.next_waiver_run(et(2026, 10, 14, 3), DAYS) == et(2026, 10, 15, 3)    # exactly at the run: the next one
    assert dl.next_waiver_run(et(2026, 10, 12, 4), DAYS) == et(2026, 10, 14, 3)    # Monday after the run skips Tuesday
    assert dl.next_waiver_run(et(2026, 10, 13, 12), ["WEDNESDAY"]) == et(2026, 10, 14, 3)
    assert dl.next_waiver_run(et(2026, 10, 13, 12), []) == et(2026, 10, 14, 3)     # no days in the settings: ESPN's default
    assert dl.next_waiver_run(et(2026, 10, 13, 12), ["FUNDAY"]) is None
    # a naive clock is UTC
    assert dl.next_waiver_run(datetime(2026, 10, 13, 16), DAYS) == et(2026, 10, 14, 3)


def test_a_dropped_player_clears_at_the_first_run_after_his_waiver_period():
    drop = int(et(2026, 10, 13, 9, 4).timestamp() * 1000)          # dropped Tuesday 9:04 AM ET
    now = et(2026, 10, 13, 12)
    assert dl.clears_at(drop, 24, DAYS, now) == et(2026, 10, 15, 3)  # eligible Wed 9:04, so Thursday's run
    assert dl.clears_at(drop, 0, DAYS, now) == et(2026, 10, 14, 3)   # no waiver period: the next run
    assert dl.clears_at(drop, 24, DAYS, et(2026, 10, 15, 4)) is None  # already cleared: a free agent
    assert dl.clears_at(None, 24, DAYS, now) is None


def test_words_are_in_denver_time():
    assert dl.word(et(2026, 10, 14, 3)) == "Wed 1:00 AM"
    assert dl.word(et(2026, 10, 14, 10, 5)) == "Wed 8:05 AM"
    assert dl.word(None) is None and dl.iso(None) is None and dl.ms(None) is None


def test_build_prefers_espns_own_stamp_and_prices_an_offer_against_the_next_kickoff():
    now = et(2026, 10, 13, 12)
    settings = {"waiver_process_days": DAYS, "waiver_hours": 24, "trade_revision_hours": 24, "veto_votes": 4}
    lines = {"KC": {"kickoff": et(2026, 10, 15, 20, 15).astimezone(UTC).isoformat().replace("+00:00", "Z")},
             "GB": {"kickoff": et(2026, 10, 18, 13).astimezone(UTC).isoformat().replace("+00:00", "Z")}}
    stamp = int(et(2026, 10, 14, 3).timestamp() * 1000)
    d = dl.build(now, settings, lines, stamp, [{"in": ["Hubbard"], "out": ["Tuten"], "process_ts": int(et(2026, 10, 14, 10, 5).timestamp() * 1000)}])
    assert d["waiver_run_source"] == "espn" and d["waiver_run_word"] == "Wed 1:00 AM" and d["waiver_run_ts"] == stamp
    assert d["offer_executes_word"] == "Wed 10:00 AM" and d["offer_after_kickoff"] is False
    assert d["next_kickoff_word"] == "Thu 6:15 PM" and d["trade_review_hours"] == 24 and d["veto_votes"] == 4
    assert d["trade_processes"][0]["processes_word"] == "Wed 8:05 AM" and d["waiver_process_days"][0] == "Mon"
    # no pending claim: the house hour on the league's days. Thursday evening: Thursday's game is gone, the next
    # kickoff is Sunday's and a Friday execution beats it; Saturday afternoon: it executes Sunday 3 PM, after kickoff
    d2 = dl.build(et(2026, 10, 15, 21), settings, lines, None, [])
    assert d2["waiver_run_source"] == "default" and d2["waiver_run_word"] == "Fri 1:00 AM"
    assert d2["offer_after_kickoff"] is False and d2["next_kickoff_word"] == "Sun 11:00 AM"
    d3 = dl.build(et(2026, 10, 17, 15), settings, lines, None, [])
    assert d3["offer_after_kickoff"] is True and d3["offer_executes_word"] == "Sun 1:00 PM" and d3["waiver_run_word"] == "Sun 1:00 AM"
    # a stale stamp (yesterday's run) is ignored
    assert dl.build(now, settings, {}, stamp - 86_400_000, [])["waiver_run_source"] == "default"
    # no review period in the settings: no execution clock
    assert dl.build(now, {}, {}, None, [])["offer_executes"] is None and dl.build(now, {}, {}, None, [])["trade_processes"] == []


def _dl(run_word="Wed 1:00 AM", run_ts=1, executes="Wed 10:05 AM", after=False):
    return {"waiver_run_word": run_word, "waiver_run_ts": run_ts, "trade_review_hours": 24, "offer_executes_word": executes,
            "offer_after_kickoff": after, "next_kickoff_word": "Thu 6:15 PM"}


def test_claim_rows_say_when_the_claim_lands_and_the_header_carries_the_clocks():
    w = {**waiver("Claim Guy", d_week=3.0, on_waivers=True), "lands": "Wed 1:00 AM", "lands_ts": 10}
    lg = league(waivers=[w], open_spots=1, waiver_rank=7, deadlines=_dl())
    row = by_id(lg)["waiver:claim-guy"]
    assert "claim (waivers, you are priority #7, lands Wed 1:00 AM) Claim Guy" in row["text"]
    assert report.clocks_line(lg) == " · waivers run Wed 1:00 AM · a trade executes 24h after acceptance"
    assert report.clocks_line(league()) == ""


def test_a_claim_landing_after_the_trade_processes_takes_the_spot_the_trade_frees_and_one_before_it_needs_a_drop():
    proc = 100
    base = dict(roster=[player("Starter WR", slot="WR"), player("Bench WR", ros=3.0), player("New RB", pos="RB", mu=14.0, ros=14.0)],
                lineup_win={"slots": {"WR": ["Starter WR"], "RB": ["New RB"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                outgoing_trades=[{"give": ["Old RB", "Old WR"], "get": ["New RB"], "rival": "Them", "accepted": True, "process_ts": proc}],
                pending_moves=[{"id": "t", "in": ["New RB"], "out": ["Old RB", "Old WR"], "process_ts": proc}],
                trade_spots_after=1, trade_adds_after=[{"name": "Wire RB", "pos": "RB", "kind": "upgrade", "why": "+1.2/wk"}], open_spots=0)
    # lands after: the freed spot covers it, no drop; the Accepted row says one spot went to the claim
    late = {**waiver("Late Guy", d_week=3.0, on_waivers=True), "lands": "Wed 9:00 AM", "lands_ts": proc + 1}
    rows = by_id(league(**base, waivers=[late], deadlines=_dl(run_ts=proc + 1)))
    assert "lands after the accepted trade processes, so the spot the trade frees covers it; no drop" in rows["waiver:late-guy"]["text"]
    assert "1 of them goes to the claim above" in rows["sent:new-rb"]["text"] and "then add" not in rows["sent:new-rb"]["text"]
    # lands before: a drop today, the freed spot stays listed with its add, and the row says the waiver run comes first
    early = {**waiver("Early Guy", d_week=3.0, on_waivers=True), "lands": "Wed 1:00 AM", "lands_ts": proc - 1}
    rows = by_id(league(**base, waivers=[early], deadlines=_dl(run_ts=proc - 1)))
    assert "drop Bench WR" in rows["waiver:early-guy"]["text"]
    sent = rows["sent:new-rb"]["text"]
    assert "the waiver run (Wed 1:00 AM) comes first, so a claim placed today still needs a drop" in sent and "then add Wire RB" in sent
    # the ledger alone
    lg = league(**base)
    sp = ledger.Spots(lg, 0, set())
    assert sp.take(adding="RB", lands_ts=proc + 5) == (None, " (it lands after the accepted trade processes, so the spot the trade frees covers it; no drop)")
    assert sp.after_open == 0 and sp.take(adding="RB", lands_ts=proc + 5)[0] == "Bench WR"


def test_offer_and_trade_rows_say_when_the_deal_executes():
    t = {"rival": "Them", "rival_team_id": 2, "give": ["A"], "get": ["B"], "my_delta_ppw": 1.0, "their_delta_ppw": -0.5, "verdict": "accept", "why": [],
         "hours_left": 20.0, "executes": "Wed 10:05 AM", "executes_after_kickoff": False}
    assert report.incoming_line(t).endswith("; expires in 20h; accept now and it executes Wed 10:05 AM")
    t2 = {**t, "executes": "Thu 9:00 PM", "executes_after_kickoff": True}
    assert "executes Thu 9:00 PM, after the next kickoff: a player whose game has started by then is locked until next week" in report.incoming_line(t2)
    from tests.test_card import trade
    lg = league(trades=[trade(["Star"], ["Bench WR"])], deadlines=_dl(executes="Wed 10:05 AM"))
    assert "; if he accepts today it executes Wed 10:05 AM" in by_id(lg)["trade:star"]["text"]
    lg2 = league(trades=[trade(["Star"], ["Bench WR"])], deadlines=_dl(executes="Thu 9:00 PM", after=True))
    assert "after the Thu 6:15 PM kickoff" in by_id(lg2)["trade:star"]["text"]
    assert "executes" not in by_id(league(trades=[trade(["Star"], ["Bench WR"])]))["trade:star"]["text"]
