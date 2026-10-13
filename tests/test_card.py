"""The action card: what reaches the phone, and how Claude's per-row rulings fold into it."""
import pytest

from ff import report


def player(name, pos="WR", mu=10.0, ros=10.0, p0=0.02, slot="BE", locked=False, bye=False):
    return {"name": name, "pos": pos, "team": "GB", "mu": mu, "mu_ros": ros, "p_zero": p0, "slot": slot,
            "locked": locked, "bye": bye, "sources": {}, "flags": []}


def league(**kw):
    lg = {
        "name": "L1", "league_name": "Test League", "week": 3, "my_team_id": 1, "my_record": "1-1",
        "settings": {"lineup_slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "K": 1, "D/ST": 1}},
        "roster": [player("Starter WR", slot="WR"), player("Bench WR", ros=3.0)],
        "lineup_win": {"slots": {"WR": ["Starter WR"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
        "lineup_diff": [], "waivers": [], "trades": [], "incoming_trades": [], "outgoing_trades": [],
        "week_state": {"phase": "pre", "my_left": [], "opp_left": []},
    }
    lg.update(kw)
    return lg


def trade(get, give, rival="Rival", mine=1.5, theirs=0.5, sendable=True, why=()):
    return {"rival": rival, "rival_team_id": 2, "give": list(give), "get": list(get), "my_delta_ppw": mine,
            "their_delta_ppw": theirs, "sendable": sendable, "why": list(why), "market_give": 100, "market_get": 90}


def full(x):
    """A row's click and its detail line together, for assertions on wording that moved under the row."""
    return x["text"] + " " + report.detail_line(x)


def ids(lg, reads=None):
    return [x["id"] for x in report.apply_reads(report.todos(lg), reads)]


def by_id(lg, reads=None):
    return {x["id"]: x for x in report.apply_reads(report.todos(lg), reads)}


# ---------- the in-progress line ----------

def test_phase_line_counts_starters_without_naming_them():
    lg = league(week_state={"phase": "in_progress", "espn_my_score": 35.2, "espn_opp_score": 5.3,
                            "my_left": [f"P{i}" for i in range(8)], "opp_left": [f"Q{i}" for i in range(8)]})
    line = report.phase_line(lg)
    assert "8 starters left" in line and "35.2 – 5.3" in line
    assert "P0" not in line  # a roster dump on Friday morning is not information


def test_phase_line_names_the_last_few():
    lg = league(week_state={"phase": "in_progress", "espn_my_score": 90, "espn_opp_score": 88,
                            "my_left": ["Kittle", "Herbert"], "opp_left": []})
    assert "(Kittle, Herbert)" in report.phase_line(lg)


def test_phase_line_silent_before_kickoff():
    assert report.phase_line(league()) is None


# ---------- what the why line is allowed to say ----------

def test_why_skips_a_default_questionable_and_keeps_a_researched_one():
    lg = league(roster=[player("Default Q", slot="WR", p0=0.30), player("Researched", slot="WR", p0=0.55)],
                lineup_win={"slots": {"WR": ["Default Q", "Researched"]}, "p_win": 0.7, "bench": []})
    why = " ".join(report.why_parts(lg))
    assert "Researched 55% to sit" in why
    assert "Default Q" not in why


def test_why_only_mentions_game_script_when_it_moved_a_slot():
    lg = league()
    assert not any("play it safe" in w for w in report.why_parts(lg))
    lg["lineup_diff"] = [{"name": "Starter WR", "in": "win", "ev": 9.0, "sd": 6.0}]
    assert any("play it safe" in w for w in report.why_parts(lg))


def test_why_states_the_p_win_gain_behind_a_lineup_that_leaves_points_on_the_bench():
    lg = league(lineup_diff=[{"name": "Starter WR", "in": "win", "ev": 9.0, "sd": 6.0}])
    lg["lineup_win"]["win_gain"] = 0.021
    why = " ".join(report.why_parts(lg))
    assert "not the highest-points lineup (+2.1 pp P(win))" in why


# ---------- rows and rulings ----------

def test_open_offers_show_up_as_a_row():
    lg = league(outgoing_trades=[{"give": ["Drake Maye"], "get": ["Jameson Williams"], "rival": "Them",
                                  "hours_left": 22.0}])
    row = by_id(lg)["sent:jameson-williams"]
    assert "Jameson Williams" in row["text"] and "22h left" in full(row)


def test_skip_ruling_marks_the_row_and_carries_the_reason():
    lg = league(trades=[trade(["Kai"], ["Maye"])])
    row = by_id(lg, {"items": {"trade:kai": {"verdict": "skip", "note": "he is in a boot"}}})["trade:kai"]
    assert row["ruling"] == "skip" and row["ruling_note"] == "he is in a boot"


def test_unruled_rows_are_untouched():
    lg = league(trades=[trade(["Kai"], ["Maye"])])
    row = by_id(lg)["trade:kai"]
    assert row["ruling"] is None and row["ruling_note"] is None


def test_trades_shipping_the_same_player_are_flagged_as_alternatives():
    lg = league(trades=[trade(["Kai"], ["Maye"]), trade(["Odunze"], ["Maye"])])
    rows = by_id(lg)
    assert not rows["trade:kai"].get("warn")
    assert any("alternatives" in w for w in rows["trade:odunze"]["warn"])


def test_a_skipped_alternative_stops_being_an_alternative():
    """Two of three struck leaves one live move, and calling it an alternative is the confusion we set out to kill."""
    lg = league(trades=[trade(["Kai"], ["Maye"]), trade(["Odunze"], ["Maye"])])
    rows = by_id(lg, {"items": {"trade:kai": {"verdict": "skip", "note": "no"}}})
    assert not rows["trade:odunze"].get("warn")


def test_depth_warnings_speak_to_dustin():
    lg = league(trades=[trade(["Kai"], ["Maye"], why=["leaves me no backup QB"])])
    assert by_id(lg)["trade:kai"]["warn"] == ["leaves you no backup QB"]


def test_a_reach_is_not_a_row():
    """An offer only he would decline is not a thing to do today; the card used to print one anyway."""
    lg = league(trades=[trade(["Kai"], ["Maye"], sendable=False, theirs=-0.4)])
    assert "trade:kai" not in by_id(lg)


def test_neutral_for_them_reads_as_words_not_plus_zero():
    lg = league(trades=[trade(["Kai"], ["Maye"], theirs=0.0)])
    assert "neutral for them" in by_id(lg)["trade:kai"]["text"]


# ---------- the cover item ----------

def cover_league(**kw):
    return league(
        roster=[player("Sick K", pos="K", slot="K", p0=0.5), player("WR", slot="WR")],
        lineup_win={"slots": {"K": ["Sick K"], "WR": ["WR"]}, "p_win": 0.7, "bench": []},
        waivers=[{"name": "Backup K", "pos": "K", "team": "DEN", "streamer": True, "delta_over_starter": 0.2,
                  "delta_week": 3.9, "week_slot": "K", "slot": "K", "why": [], "bid": 1, "mu_week": 8.0, "mu_ros": 8.0}],
        **kw)


def test_cover_item_when_the_only_kicker_may_sit():
    row = by_id(cover_league())["cover:k"]
    assert "Sick K" in row["text"] and "Backup K" in row["text"] and "only K" in row["text"]


def test_no_cover_item_when_a_healthy_backup_is_on_the_bench():
    lg = cover_league()
    lg["roster"].append(player("Spare K", pos="K", p0=0.02))
    assert "cover:k" not in ids(lg)


def test_no_cover_item_for_a_merely_questionable_starter():
    lg = cover_league()
    lg["roster"][0]["p_zero"] = 0.3
    assert "cover:k" not in ids(lg)


def test_cover_row_names_a_free_agent_over_a_waiver_claim():
    # B7: the first K on the wire is still on waivers (the claim lands Wednesday); the free agent behind him is the
    # one who can be added before kickoff.
    lg = cover_league()
    lg["waivers"][0]["on_waivers"] = True
    lg["waivers"].append({**lg["waivers"][0], "name": "Free K", "team": "KC", "on_waivers": False})
    text = by_id(lg)["cover:k"]["text"]
    assert "add Free K" in text
    assert "Backup K" not in text


def test_cover_row_says_claim_when_only_a_waiver_claim_covers():
    lg = cover_league(waiver_rank=3)
    lg["waivers"][0]["on_waivers"] = True
    text = full(by_id(lg)["cover:k"])
    assert "claim" in text and "priority #3" in text and "Backup K" in text
    assert "add Backup K" not in text


def test_cover_row_takes_its_spot_from_the_ledger():
    # No open bench spot: the add needs a drop, and the ledger names the cheapest one.
    lg = cover_league()
    lg["roster"].append(player("Cheap WR", ros=2.0))
    lg["roster"].append(player("Good WR", ros=9.0))
    text = by_id(lg)["cover:k"]["text"]
    assert "drop Cheap WR" in text
    assert "Good WR" not in text


def test_cover_row_uses_the_open_spot_and_the_pickup_above_does_not_share_a_drop():
    lg = cover_league(open_spots=1)
    lg["roster"].append(player("Cheap WR", ros=2.0))
    lg["waivers"].append({"name": "Hot WR", "pos": "WR", "team": "KC", "streamer": False, "delta_over_starter": 0.5,
                          "delta_week": 4.0, "week_slot": "WR", "slot": "WR", "why": [], "bid": 1, "mu_week": 12.0,
                          "mu_ros": 12.0, "on_waivers": False})
    rows = by_id(lg)
    assert "open bench spot" in full(rows["waiver:hot-wr"])
    assert "drop Cheap WR" in rows["cover:k"]["text"]  # the spot above is spent; the cover needs a drop of its own


def test_cover_pickup_is_not_added_twice_and_a_waiver_row_at_the_position_is_the_cover():
    lg = cover_league(open_spots=2, open_spot_adds=[{"name": "Backup K", "pos": "K", "why": "best K on the wire"}])
    rows = by_id(lg)
    assert "open bench spot" in full(rows["cover:k"])
    assert "waiver:backup-k" not in rows  # the cover row already spent a spot on him
    # A waiver row above that brings in a K already covers the slot.
    lg = cover_league()
    lg["waivers"][0].update(streamer=False, delta_week=3.9)
    rows = by_id(lg)
    assert "waiver:backup-k" in rows and "cover:k" not in rows


# ---------- lint ----------

def packet(lg):
    return {"generated": "2026-09-18T07:00:00", "shared": {"injury_watchlist": [], "exposure": {}}, "leagues": [lg]}


def test_read_lint_catches_a_typo_in_an_item_id():
    p = packet(league(trades=[trade(["Kai"], ["Maye"])]))
    warns = report.read_lint(p, {"L1": {"items": {"trade:kia": {"verdict": "skip", "note": "typo"}}}})
    assert any("no such row" in w for w in warns)


def test_read_lint_wants_a_ruling_on_every_trade():
    p = packet(league(trades=[trade(["Kai"], ["Maye"])]))
    assert any("no ruling" in w for w in report.read_lint(p, {}))
    assert not any("no ruling" in w for w in
                   report.read_lint(p, {"L1": {"items": {"trade:kai": {"verdict": "do"}}}}))


def test_read_lint_wants_a_reason_for_a_skip():
    p = packet(league(trades=[trade(["Kai"], ["Maye"])]))
    warns = report.read_lint(p, {"L1": {"items": {"trade:kai": {"verdict": "skip"}}}})
    assert any("needs a note" in w for w in warns)


def test_read_lint_catches_an_unknown_league():
    p = packet(league())
    assert any("no league by that name" in w for w in report.read_lint(p, {"L7": {"read": "hi"}}))


# ---------- the shared watch list ----------

def watch(name, league_name, starting_note=None, locked=False, p_zero=0.3):
    return {"name": name, "pos": "WR", "team": "NO", "league": league_name, "status": "QUESTIONABLE",
            "notes": None, "espn_id": 1, "p_zero": p_zero, "override_note": starting_note, "locked": locked}


@pytest.fixture
def two_leagues():
    l1 = league(name="L1", roster=[player("Olave", slot="WR")], lineup_win={"slots": {"WR": ["Olave"]}, "p_win": 0.6, "bench": []})
    l3 = league(name="L3", roster=[player("Olave", slot="WR")], lineup_win={"slots": {"WR": ["Olave"]}, "p_win": 0.6, "bench": []})
    return {"generated": "2026-09-18T07:00:00", "leagues": [l1, l3],
            "shared": {"injury_watchlist": [], "exposure": {}}}


def test_watchlist_merges_a_player_held_in_two_leagues(two_leagues):
    two_leagues["shared"]["injury_watchlist"] = [watch("Olave", "L1", "hamstring, limited Thu"), watch("Olave", "L3")]
    rows = report.watchlist(two_leagues)
    assert len(rows) == 1
    assert rows[0]["leagues"] == ["L1", "L3"] and rows[0]["override_note"] == "hamstring, limited Thu"


def test_watchlist_drops_a_bench_player_nobody_researched(two_leagues):
    two_leagues["shared"]["injury_watchlist"] = [watch("Monangai", "L1")]
    assert report.watchlist(two_leagues) == []


def test_watchlist_keeps_a_bench_player_the_research_found_something_on(two_leagues):
    two_leagues["shared"]["injury_watchlist"] = [watch("Bowers", "L1", "has not practiced since the meniscus trim")]
    rows = report.watchlist(two_leagues)
    assert len(rows) == 1 and rows[0]["starting"] is False


def test_watchlist_drops_players_who_already_played(two_leagues):
    two_leagues["shared"]["injury_watchlist"] = [watch("Olave", "L1", "hamstring", locked=True)]
    assert report.watchlist(two_leagues) == []


def test_watchlist_puts_starters_first(two_leagues):
    two_leagues["shared"]["injury_watchlist"] = [watch("Bowers", "L1", "note"), watch("Olave", "L1", "note")]
    assert [r["name"] for r in report.watchlist(two_leagues)] == ["Olave", "Bowers"]


# ---------- hurt players ----------

def injury(name, verdict, pos="RB", weeks_out=4.0, return_week=7, avail=0.6, fa=("Pickup", 1.2), occupant=None, trades=()):
    return {"espn_id": 1, "name": name, "pos": pos, "slot": "BE", "weeks_out": weeks_out, "return_week": return_week,
            "avail_ros": avail, "mu_ros_active": 14.0, "hold_ppw": 3.0, "hold_value": 29.4, "drop_value": 13.8,
            "back_eff": 9.8, "w_eff": 13.8, "ir_eligible": True, "ir_open": 1, "ir_occupant": occupant,
            "best_fa": {"name": fa[0], "pos": pos, "delta_over_starter": fa[1], "ppw": fa[1]} if fa else None,
            "add": {"name": fa[0], "pos": pos, "kind": "upgrade", "why": f"+{fa[1]:.1f}/wk"} if fa and verdict in ("ir", "drop") and not occupant else None,
            "trades": list(trades), "market": None, "verdict": verdict, "why": []}


def test_each_verdict_is_a_row_with_the_numbers_in_words():
    lg = league(injuries=[injury("Ankle Guy", "hold"), injury("Knee Guy", "ir", weeks_out=15, return_week=None, avail=0.0),
                          injury("Done Guy", "drop", weeks_out=15, return_week=None, avail=0.0),
                          injury("Sell Guy", "trade", trades=[{"rival": "Them", "get": ["Star"]}]),
                          injury("Back Guy", "activate", weeks_out=1, return_week=4)])
    rows = by_id(lg)
    assert rows["injury:ankle-guy"]["kind"] == "hold" and rows["injury:ankle-guy"]["label"] == "Holding"
    assert rows["injury:ankle-guy"]["text"] == "Ankle Guy (RB) out ~4 wks, back wk 7" and rows["injury:ankle-guy"]["detail"] == ["10 games at ~14/g on return"]
    assert rows["injury:knee-guy"]["text"] == "move Knee Guy (RB) to IR, then add Pickup (RB)" and rows["injury:knee-guy"]["detail"] == ["out the season", "Pickup: +1.2/wk"]
    assert rows["injury:done-guy"]["label"] == "Drop"
    assert rows["injury:done-guy"]["text"] == "drop Done Guy (RB); add Pickup (RB)"
    assert rows["injury:done-guy"]["detail"] == ["out the season, worth ~29 pts the rest of the way", "Pickup: +1.2/wk"]
    assert "the offer to Them for Star ships Sell Guy (RB)" in rows["injury:sell-guy"]["text"] and rows["injury:sell-guy"]["trade_ids"] == ["trade:star"]
    assert rows["injury:back-guy"]["label"] == "Activate" and rows["injury:back-guy"]["text"].startswith("move Back Guy (RB) off IR")


def test_the_checklist_runs_in_espn_click_order_with_holds_last():
    """Offer, IR, pickups, drops with their add, then the lineup once the roster is set, trades, and the holds as a
    footnote at the very end. The lineup used to come first and was wrong the moment the add below it went through."""
    lg = league(injuries=[injury("Ankle Guy", "hold"), injury("Knee Guy", "ir", weeks_out=15, return_week=None, avail=0.0),
                          injury("Done Guy", "drop", weeks_out=15, return_week=None, avail=0.0)],
                trades=[{"rival": "Them", "give": ["A"], "get": ["Kai"], "my_delta_ppw": 1.0, "their_delta_ppw": 0.5, "why": [], "sendable": True}])
    lg["open_spot_adds"] = [{"name": "Body", "pos": "WR", "kind": "depth", "why": "best body on the wire, depth only"}]
    assert ids(lg) == ["injury:knee-guy", "injury:done-guy", "open:body", "lineup", "trade:kai", "injury:ankle-guy"]


def test_a_one_game_hold_is_not_a_row():
    """Out this week and worth keeping is the lineup's business; a row saying "hold him" is just the injury report."""
    lg = league(injuries=[injury("Ankle Guy", "hold", weeks_out=1.0, return_week=4), injury("Knee Guy", "ir", weeks_out=1.0, return_week=4)])
    assert ids(lg) == ["injury:knee-guy", "lineup"]


def test_ir_swap_names_the_occupant():
    lg = league(injuries=[injury("Knee Guy", "ir", occupant="Old Stash")])
    assert "in place of Old Stash" in by_id(lg)["injury:knee-guy"]["text"]


def test_the_ir_occupant_is_never_the_drop_candidate():
    lg = league(roster=[player("Starter WR", slot="WR"), player("Bench WR", ros=3.0), {**player("Stash", ros=0.0), "slot": "IR"}])
    assert report._drop_candidate(lg) == "Bench WR"


def test_a_season_ender_on_the_bench_is_the_drop_candidate():
    lg = league(roster=[player("Starter WR", slot="WR"), player("Bench WR", ros=3.0), player("Done", ros=0.0)])
    assert report._drop_candidate(lg) == "Done"


def market(value, trend=0):
    return {"redraft_value": value, "rank": None, "trend_30d": trend}


def test_the_market_keeps_a_rookie_the_market_would_pay_for_off_the_drop_list():
    """The rookie RB is the cheaper body by ROS points but the market prices him like a startable piece; the WR4
    nobody would trade for goes first, and the row shows the two numbers side by side (B6)."""
    lg = league(roster=[player("Starter WR", slot="WR"), {**player("Rookie RB", pos="RB", ros=2.0), "market": market(2400)},
                        {**player("WR Four", ros=3.0), "market": market(300)}],
                waivers=[{"name": "Now Guy", "pos": "WR", "streamer": False, "delta_week": 3.0, "delta_over_starter": 0.5, "week_slot": "WR", "on_waivers": False}])
    assert report._drop_order(lg) == ["WR Four", "Rookie RB"]
    text = full(by_id(lg)["waiver:now-guy"])
    assert "; drop WR Four" in text and "WR Four: market 300, 3.0/wk" in text and "Rookie RB (market 2400, 2.0/wk" in text


def test_the_market_penalty_does_not_save_a_buried_player():
    """A big market number is a tie-breaker-plus, not a veto: half a point a week behind a 7-a-week body is still the cut."""
    lg = league(roster=[player("Starter WR", slot="WR"), {**player("Rookie RB", pos="RB", ros=0.5), "market": market(2400)},
                        {**player("WR Four", ros=7.0), "market": market(300)}])
    assert report._drop_order(lg) == ["Rookie RB", "WR Four"]


def test_a_rising_market_counts_for_more_than_a_falling_one():
    """Same value today, opposite 30-day trends: the one the market is souring on is the cut."""
    lg = league(roster=[player("Starter WR", slot="WR"), {**player("Rising", ros=3.0), "market": market(1500, trend=+800)},
                        {**player("Falling", ros=3.0), "market": market(1500, trend=-800)}])
    assert report._drop_order(lg) == ["Falling", "Rising"]


def test_a_handcuff_for_my_rb1_is_not_the_cheapest_cut():
    """The backup to my RB1 is the cheaper body by ROS points; cutting him also cuts the insurance, so the WR4 goes
    first and the row says why he was passed over (B6b)."""
    lg = league(roster=[player("Starter RB", pos="RB", slot="RB", ros=15.0), player("Starter WR", slot="WR"),
                        player("Backup RB", pos="RB", ros=3.0), player("WR Four", ros=4.0)],
                lineup_win={"slots": {"RB": ["Starter RB"], "WR": ["Starter WR"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                handcuffs=[{"starter": "Starter RB", "handcuff": "Backup RB", "owner_team_id": 1, "est_value": 2.0}],
                waivers=[{"name": "Now Guy", "pos": "WR", "streamer": False, "delta_week": 3.0, "delta_over_starter": 0.5, "week_slot": "WR", "on_waivers": False}])
    assert report._drop_order(lg) == ["WR Four", "Backup RB"]
    text = full(by_id(lg)["waiver:now-guy"])
    assert "; drop WR Four" in text and "Backup RB (3.0/wk; handcuff for Starter RB, not a cut)" in text


def test_a_body_on_bye_this_week_is_the_cheaper_cut():
    """Two bench bodies, the one on bye a shade better the rest of the way: he gives the Sunday pickup's week nothing,
    so with four weeks left that week is a quarter of his value and he goes first (B6b)."""
    lg = league(week=14, weeks_remaining=4,
                roster=[player("Starter WR", slot="WR"), player("Bye Guy", ros=3.2, bye=True, p0=1.0), player("Plays", ros=3.0)])
    assert report._drop_order(lg) == ["Bye Guy", "Plays"]
    assert report._drop_note(lg, "Bye Guy") == " (not priced by the market, 3.2/wk, on bye this week)"


def test_a_bench_player_likely_to_sit_this_week_is_the_cheaper_cut():
    """A 90% sit risk on a bench body with no games netted out of his `mu_ros` yet counts this week as spent (B6b)."""
    lg = league(week=14, weeks_remaining=4,
                roster=[player("Starter WR", slot="WR"), player("Doubtful", ros=3.2, p0=0.9), player("Plays", ros=3.0)])
    assert report._drop_order(lg) == ["Doubtful", "Plays"]
    assert report._drop_note(lg, "Doubtful").endswith(", 90% to sit this week)")


def test_a_sit_risk_already_netted_out_of_mu_ros_is_not_counted_twice():
    """An Out (p_zero 1, weeks_out 1) already has the game out of his `mu_ros`; the cost is `mu_ros`, not less."""
    lg = league(week=14, weeks_remaining=4,
                roster=[player("Starter WR", slot="WR"), {**player("Out Guy", ros=3.0, p0=1.0), "weeks_out": 1.0, "mu_ros_active": 4.0},
                        player("Plays", ros=2.9)])
    assert report._drop_order(lg) == ["Plays", "Out Guy"]


def test_a_drop_without_a_market_price_prints_plain():
    lg = league(roster=[player("Starter WR", slot="WR"), player("Bench WR", ros=3.0), player("Bench B", ros=5.0)],
                waivers=[{"name": "Now Guy", "pos": "WR", "streamer": False, "delta_week": 3.0, "delta_over_starter": 0.5, "week_slot": "WR", "on_waivers": False}])
    assert by_id(lg)["waiver:now-guy"]["text"].endswith("; drop Bench WR")


def test_read_lint_wants_a_ruling_on_a_drop_but_not_a_hold():
    p = packet(league(injuries=[injury("Done Guy", "drop"), injury("Ankle Guy", "hold")]))
    warns = report.read_lint(p, {})
    assert any("injury:done-guy" in w and "no ruling" in w for w in warns)
    assert not any("injury:ankle-guy" in w for w in warns)
    assert not any("injury:done-guy" in w for w in report.read_lint(p, {"L1": {"items": {"injury:done-guy": {"verdict": "skip", "note": "IR-eligible per the app"}}}}))


def test_a_skipped_injury_row_is_struck_like_any_other():
    lg = league(injuries=[injury("Done Guy", "drop")])
    row = by_id(lg, {"items": {"injury:done-guy": {"verdict": "skip", "note": "he is in a boot, not done"}}})["injury:done-guy"]
    assert row["ruling"] == "skip" and row["ruling_note"]


def test_out_line_merges_leagues_and_carries_the_note():
    p = packet(league())
    p["shared"]["injured"] = [{"name": "Olave", "pos": "WR", "team": "NO", "league": "L1", "weeks_out": 4, "return_week": 7, "override_note": None},
                              {"name": "Olave", "pos": "WR", "team": "NO", "league": "L3", "weeks_out": 6, "return_week": 9, "override_note": "Schefter: 4-6 weeks"}]
    rows = report.injured_list(p)
    assert len(rows) == 1 and rows[0]["leagues"] == ["L1", "L3"] and rows[0]["weeks_out"] == 6
    line = report.injured_line(rows[0])
    assert "Olave out ~6 wks, back wk 9 (L1, L3)" in line and "Schefter" in line


def test_an_open_bench_spot_is_a_row_naming_who_fills_it():
    lg = league()
    lg["open_spots"] = 1
    lg["open_spot_adds"] = [{"name": "Seth McGowan", "pos": "RB", "kind": "handcuff", "why": "handcuff for Jonathan Taylor"}]
    row = by_id(lg)["open:seth-mcgowan"]
    assert row["label"] == "Open spot" and row["text"] == "add Seth McGowan (RB) to the open bench spot" and row["detail"] == ["handcuff for Jonathan Taylor"]


# ---------- ESPN's position cap on trade rows ----------

def capped_league(**kw):
    """Six WRs rostered against a cap of six; a WR-for-RB trade has to drop one in the trade screen."""
    roster = [player("Starter WR", slot="WR"), player("WR Two", slot="WR"), player("WR Three", ros=8.0), player("WR Four", ros=6.0),
              player("WR Five", ros=4.0), player("WR Six", ros=1.0), player("RB One", pos="RB", slot="RB"), player("RB Two", pos="RB", ros=5.0)]
    lg = league(roster=roster, lineup_win={"slots": {"WR": ["Starter WR", "WR Two"], "RB": ["RB One"]}, "p_win": 0.6, "mu": 100.0, "sd": 20.0, "bench": []},
                settings={"lineup_slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1, "K": 1, "D/ST": 1}, "position_limits": {"WR": 6, "RB": 6}})
    lg.update(kw)
    return lg


def capped_trade(**kw):
    t = trade(["Garrett Wilson"], ["RB Two"], **kw)
    t.update({"drops": [{"name": "WR Six", "pos": "WR", "cap": 6}], "get_pos": ["WR"]})
    return t


def test_trade_row_names_the_drop_the_cap_forces():
    row = by_id(capped_league(trades=[capped_trade()]))["trade:garrett-wilson"]
    assert row["drops"] == ["WR Six"]
    assert "drop WR Six in the trade screen (ESPN caps WR at 6)" in full(row)


def test_a_waiver_drop_that_clears_the_cap_spares_the_trade_a_second_cut():
    """The pickup takes the cheapest body (the sixth WR); after that the WR-for-RB trade fits without a drop."""
    w = {"name": "Pickup RB", "pos": "RB", "streamer": False, "delta_week": 2.0, "delta_over_starter": 2.0, "week_slot": "RB", "slot": "RB"}
    rows = by_id(capped_league(trades=[capped_trade()], waivers=[w]))
    assert "drop WR Six" in rows["waiver:pickup-rb"]["text"]
    assert rows["trade:garrett-wilson"]["drops"] == [] and "trade screen" not in full(rows["trade:garrett-wilson"])


def test_a_waiver_pickup_at_the_capped_position_moves_the_cut_to_the_next_body():
    """Adding a WR and dropping the sixth keeps the count at six, so the trade still needs a cut, and not the same player."""
    w = {"name": "Pickup WR", "pos": "WR", "streamer": False, "delta_week": 2.0, "delta_over_starter": 2.0, "week_slot": "WR", "slot": "WR"}
    rows = by_id(capped_league(trades=[capped_trade()], waivers=[w]))
    assert "drop WR Six" in rows["waiver:pickup-wr"]["text"]
    assert rows["trade:garrett-wilson"]["drops"] == ["WR Five"]


def test_a_trade_row_without_cap_data_is_unchanged():
    row = by_id(league(trades=[trade(["Kai"], ["Maye"])]))["trade:kai"]
    assert row["drops"] == [] and "trade screen" not in full(row)


def test_incoming_offer_says_what_accepting_would_cut():
    t = {"rival": "Rival", "give": ["RB Two"], "get": ["Garrett Wilson"], "my_delta_ppw": 1.2, "their_delta_ppw": 0.3, "verdict": "accept",
         "why": [], "drops": [{"name": "WR Six", "pos": "WR", "cap": 6}]}
    row = by_id(capped_league(incoming_trades=[t]))["offer:garrett-wilson"]
    assert "accepting means dropping WR Six (6-WR cap)" in full(row)


def test_an_activation_names_the_body_the_stash_added_when_it_is_the_drop():
    """Daniels off IR on Oct 7 dropped the McGowan the Oct 3 stash had added: the row says so, so the round trip reads as one."""
    row = injury("Back Guy", "activate", weeks_out=1, return_week=4)
    row["stash"] = {"left_days": None, "stashed": "2026-10-03", "added": ["Bench WR"]}
    lg = league(injuries=[row])
    act = by_id(lg)["injury:back-guy"]
    assert act["text"].endswith("; drop Bench WR to make room") and "Bench WR, added when he was stashed Oct 3" in report.detail_line(act)
    # a different drop, or a stash nobody came in on, says nothing extra
    row["stash"] = {"left_days": None, "stashed": "2026-10-03", "added": ["Someone Else"]}
    assert by_id(league(injuries=[row]))["injury:back-guy"]["text"].endswith("; drop Bench WR to make room")


# ---------- ids never collide (B9) ----------

def test_two_packages_for_the_same_player_get_distinct_ids_and_one_ruling_strikes_one_row():
    """`trade:<get>` ignored `give`, so two asks for Kai shared an id and a skip on one struck both. The suffix only
    appears when it is needed; a lone ask keeps the short id the rulings memory and reads.json already use."""
    lg = league(trades=[trade(["Kai"], ["A"]), trade(["Kai"], ["B"])])
    rows = ids(lg)
    assert rows.count("trade:kai") == 0 and "trade:kai-for-a" in rows and "trade:kai-for-b" in rows
    assert len(rows) == len(set(rows))
    ruled = by_id(lg, {"items": {"trade:kai-for-a": {"verdict": "skip", "note": "A stays"}}})
    assert ruled["trade:kai-for-a"].get("ruling") == "skip" and ruled["trade:kai-for-b"].get("ruling") is None
    assert by_id(league(trades=[trade(["Kai"], ["A"])]))["trade:kai"]["give"] == ["A"]


def test_an_injury_trade_row_follows_the_trade_ids_it_names_once_they_grow_a_suffix():
    lg = league(trades=[trade(["Star"], ["A"]), trade(["Star"], ["B"])],
                injuries=[injury("Sell Guy", "trade", trades=[{"rival": "Rival", "get": ["Star"]}])])
    rows = by_id(lg)
    assert rows["injury:sell-guy"]["trade_ids"] == ["trade:star-for-a", "trade:star-for-b"]


def test_an_open_spot_row_has_its_own_prefix_so_it_never_shares_an_id_with_a_waiver_row():
    lg = league(open_spots=1, open_spot_adds=[{"name": "Body", "pos": "WR", "kind": "depth", "why": "depth"}])
    rows = by_id(lg)
    assert "open:body" in rows and "waiver:body" not in rows and rows["open:body"]["label"] == "Open spot"


# ---------- the alert email's offer card rules the same way ----------

def test_offer_card_prints_ids_before_reads_and_folds_the_ruling_in():
    """The trade-offer routine rules on `offer:<get>` in reads.json like any row; the alert card used to print the
    verdict and Claude's read under it and nothing could strike the badge."""
    t = {"rival": "Them", "rival_team_id": 2, "give": ["My RB"], "get": ["Garrett Wilson"], "verdict": "accept",
         "my_delta_ppw": 1.2, "their_delta_ppw": -0.4, "why": ["you get the best player"], "hours_left": 20.0}
    packet = {"generated": "2026-10-08T06:00:00", "leagues": [league(incoming_trades=[t])]}
    draft = report.offer_card(packet)
    assert "`[offer:garrett-wilson]`" in draft and "**ACCEPT:**" in draft
    ruled = report.offer_card(packet, {"L1": {"items": {"offer:garrett-wilson": {"verdict": "skip", "note": "decline, he is in a boot"}},
                                               "read": "boot on Wednesday"}})
    assert "~~ACCEPT~~" in ruled and "**skip:** decline, he is in a boot" in ruled and "`[offer:" not in ruled
    amended = report.offer_card(packet, {"L1": {"items": {"offer:garrett-wilson": {"verdict": "amend", "note": "only if he practices Friday"}}}})
    assert "**ACCEPT:**" in amended and "**note:** only if he practices Friday" in amended


# ---------- game context on the rows (D7) ----------

def line(opp="KC", implied=27.5, day="Thu", home=True):
    return {"opp": opp, "implied": implied, "spread": -4.0, "total": 51.0, "kickoff": None, "home": home, "day": day}


def test_game_note_reads_opponent_implied_total_and_day():
    assert report.game_note({**player("A"), "odds_line": line()}) == "vs KC 27.5 Thu"
    assert report.game_note({**player("A"), "odds_line": line("DET", 21.0, "Sun", home=False)}) == "@DET 21.0 Sun"
    assert report.game_note({**player("A"), "odds_line": line(implied=None, day=None)}) == "vs KC"
    assert report.game_note(player("A")) == ""


def test_usage_note_prefers_usage_and_falls_back_to_the_market_move():
    assert report.usage_note({**player("A"), "usage": {"tgt_share": 0.262, "tgt_pg": 8.1, "carry_pg": 0.0}}) == "tgt 26%"
    assert report.usage_note({**player("A", pos="RB"), "usage": {"tgt_share": 0.08, "tgt_pg": 2.0, "carry_pg": 15.4}}) == "15 car/g"
    assert report.usage_note({**player("A"), "usage": {"tgt_share": None, "tgt_pg": 5.2, "carry_pg": 0.0}}) == "5 tgt/g"
    assert report.usage_note({**player("A"), "usage": None, "market": market(1500, trend=+320)}) == "mkt +320/30d"
    assert report.usage_note({**player("A"), "usage": None, "market": market(1500, trend=0)}) == ""
    assert report.usage_note(player("A")) == ""


def test_lineup_moves_for_a_thursday_player_say_set_by_thu_and_go_first_in_their_group():
    """Two bench players coming in: the Thursday one reads "set by Thu" and prints before the Sunday one, while the
    slot freed stays first (the app order). A Monday player is marked too; a Sunday player is not."""
    lg = league(roster=[{**player("Sun Out", slot="WR"), "odds_line": line(day="Sun")},
                        {**player("Sun In"), "odds_line": line("DET", 21.0, "Sun", home=False)},
                        {**player("Thu In"), "odds_line": line()},
                        {**player("Mon In"), "odds_line": line("BUF", 24.0, "Mon")}],
                lineup_win={"slots": {"WR": ["Sun In", "Thu In"], "RB/WR/TE": ["Mon In"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []})
    moves = by_id(lg)["lineup"]["moves"]
    assert moves == ["Sun Out: WR → bench", "Thu In: bench → WR, set by Thu", "Sun In: bench → WR", "Mon In: bench → RB/WR/TE (Mon)"]


def test_lineup_moves_without_kickoff_data_read_as_before():
    lg = league(roster=[player("Out Guy", slot="WR"), player("In Guy")],
                lineup_win={"slots": {"WR": ["In Guy"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []})
    assert by_id(lg)["lineup"]["moves"] == ["Out Guy: WR → bench", "In Guy: bench → WR"]


def waiver(name="Now Guy", **kw):
    w = {"name": name, "pos": "WR", "team": "KC", "streamer": False, "delta_week": 3.0, "delta_over_starter": 0.5, "week_slot": "WR",
         "slot": "WR", "on_waivers": False, "why": [], "mu_week": 12.0, "mu_ros": 9.0}
    w.update(kw)
    return w


def test_a_pickup_who_starts_rides_on_the_lineup_row_after_the_add():
    """Oct 10: the card said "set it this way" and then "add Keon Coleman and start him at WR", so the lineup was set
    twice. The add comes first and his start is a move on the lineup row, worded so the lineup still stands if he
    was gone by the time Dustin clicked (he was)."""
    lg = league(open_spots=1, roster=[player("Starter WR", slot="WR"), player("Weak WR", mu=6.0, slot="WR"), player("Bench WR", ros=3.0)],
                lineup_win={"slots": {"WR": ["Starter WR", "Weak WR"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                waivers=[waiver("Now Guy", kickoff_day="Thu")])
    rows = by_id(lg)
    assert ids(lg).index("waiver:now-guy") < ids(lg).index("lineup")
    assert rows["lineup"]["moves"] == ["Now Guy: free agent → WR over Weak WR once the add above is through, set by Thu"]
    assert rows["lineup"]["text"] == rows["lineup"]["moves"][0]


def test_a_claim_who_starts_is_not_on_the_lineup_row():
    """A claim lands on waiver day, so today's lineup cannot include him; the waiver row still says where he starts."""
    lg = league(open_spots=1, waivers=[waiver("Now Guy", on_waivers=True)])
    rows = by_id(lg)
    assert rows["lineup"]["moves"] == [] and "start him at WR" in rows["waiver:now-guy"]["text"]


def test_a_locked_starter_is_not_the_one_the_pickup_replaces():
    lg = league(open_spots=1, roster=[player("Starter WR", slot="WR"), player("Played WR", mu=2.0, slot="WR", locked=True)],
                lineup_win={"slots": {"WR": ["Starter WR", "Played WR"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                waivers=[waiver("Now Guy")])
    assert by_id(lg)["lineup"]["moves"] == ["Now Guy: free agent → WR over Starter WR once the add above is through"]


def test_the_waiver_row_names_the_next_body_in_case_he_is_gone():
    """The wire moves between the 6 AM sync and the click; the row carries the fallback so the email is not stale."""
    lg = league(open_spots=2, waivers=[waiver("Now Guy"), waiver("Second Guy", delta_week=2.0),
                                      waiver("Up Guy", delta_week=0.5, delta_over_starter=1.5), waiver("Up Two", delta_week=0.5, delta_over_starter=1.1)])
    rows = by_id(lg)
    assert rows["waiver:now-guy"]["detail"][-2:] == ["there is an open bench spot", "if he is gone, Second Guy (WR, +2 this week)"]
    assert rows["waiver:up-guy"]["detail"][-2:] == ["there is an open bench spot", "if he is gone, Up Two (WR, +1.1/wk)"]
    assert "waiver:second-guy" not in rows and "waiver:up-two" not in rows


def test_a_locked_body_is_the_drop_only_when_nobody_else_can_go():
    """ESPN refuses to drop a player whose game has kicked off until the week rolls; the ledger names him last and
    says so, and names the unlocked body it took instead of a cheaper locked one."""
    lg = league(roster=[player("Starter WR", slot="WR"), player("Played WR", ros=2.0, locked=True), player("Bench WR", ros=3.0)])
    assert report._drop_order(lg) == ["Bench WR", "Played WR"]
    assert report._drop_note(lg, "Bench WR") == " (not priced by the market, 3.0/wk) over Played WR (2.0/wk; locked until the week rolls, not a cut today)"
    lg = league(roster=[player("Starter WR", slot="WR"), player("Played WR", ros=2.0, locked=True)])
    assert report._drop_note(lg, "Played WR") == " (not priced by the market, 2.0/wk, locked until the week rolls, his game has kicked off)"


def test_waiver_row_carries_the_kickoff_day():
    lg = league(open_spots=1, waivers=[waiver(kickoff_day="Thu")])
    row = by_id(lg)["waiver:now-guy"]
    assert row["text"] == "add Now Guy (WR) and start him at WR" and row["detail"] == ["+3 pts this week, set by Thu", "there is an open bench spot"]
    lg = league(open_spots=1, waivers=[waiver(delta_week=0.5, delta_over_starter=1.2, kickoff_day="Thu")])
    row = by_id(lg)["waiver:now-guy"]
    assert row["text"] == "add Now Guy (WR)" and row["detail"] == ["+1.2/wk over your WR, set by Thu", "there is an open bench spot"]
    lg = league(waivers=[waiver("Texans D/ST", pos="D/ST", streamer=True, delta_over_starter=2.6, kickoff_day="Thu")])
    row = by_id(lg)["stream:texans-d-st"]
    assert row["text"] == "swap in Texans D/ST at D/ST" and row["detail"] == ["+2.6 this week, set by Thu"]


def test_waiver_row_without_a_kickoff_day_reads_as_before():
    lg = league(open_spots=1, waivers=[waiver()])
    assert by_id(lg)["waiver:now-guy"]["detail"] == ["+3 pts this week", "there is an open bench spot"]
    lg = league(open_spots=1, waivers=[waiver(kickoff_day="Sun")])
    assert by_id(lg)["waiver:now-guy"]["detail"] == ["+3 pts this week", "there is an open bench spot"]


def test_trade_row_prints_p_accept_next_to_the_word():
    t = {**trade(["Kai"], ["Maye"]), "p_accept": 0.62, "accept_word": "he'd likely take it"}
    assert ": +1.5/wk for you, +0.5 for them, he'd likely take it (62%)" in by_id(league(trades=[t]))["trade:kai"]["text"]
    bare = by_id(league(trades=[trade(["Kai"], ["Maye"])]))["trade:kai"]["text"]
    assert bare.startswith("offer Rival your Maye for Kai: +1.5/wk for you, +0.5 for them") and "%" not in bare


def test_detail_tables_show_the_game_and_the_usage_next_to_the_projection():
    lg = league(roster=[{**player("Starter WR", slot="WR"), "odds_line": line(), "usage": {"tgt_share": 0.262, "tgt_pg": 8.1, "carry_pg": 0.0}},
                        player("Bench WR", ros=3.0)],
                lineup_win={"slots": {"WR": ["Starter WR"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": ["Bench WR"]},
                lineup_ev={"mu": 100.0}, odds={}, current_lineup_mu=100.0, opponent={"name": "Them", "mu": 90.0, "sd": 20.0}, handcuffs=[], weeks_remaining=12)
    roster = "\n".join(report._detail_roster(lg))
    assert "| Starter WR | WR | 10.0 | 10.0 | vs KC 27.5 Thu | tgt 26% |" in roster
    assert "| Bench WR | WR | 10.0 | 3.0 |  |  |" in roster  # no data: empty cells, nothing invented
    matchup = "\n".join(report._detail_matchup(lg))
    assert "- WR: Starter WR (vs KC 27.5 Thu)" in matchup


def test_an_accepted_trade_row_says_when_it_processes_and_what_opens_and_the_lineup_waits_for_the_arrival():
    lg = league(roster=[player("Starter WR", slot="WR"), player("New RB", pos="RB", mu=14.0, ros=14.0)],
                lineup_win={"slots": {"WR": ["Starter WR"], "RB": ["New RB"]}, "p_win": 0.7, "mu": 100.0, "sd": 20.0, "bench": []},
                outgoing_trades=[{"give": ["Old RB", "Old WR"], "get": ["New RB"], "rival": "Them", "accepted": True,
                                  "process_ts": 1791986732413, "processes_iso": "2026-10-14T14:05"}],
                trade_spots_after=1, trade_adds_after=[{"name": "Wire RB", "pos": "RB", "kind": "upgrade", "why": "+1.2/wk"}])
    lg["roster"][1]["pending_trade"] = "in"
    rows = by_id(lg)
    row = rows["sent:new-rb"]
    assert row["label"] == "Accepted" and "processes Wed 8:05 AM" in row["text"]
    assert "New RB on your roster for Sunday, Old RB, Old WR off it" in report.detail_line(row)
    assert "1 bench spot open then: add Wire RB (RB)" in row["text"] and "Wire RB: +1.2/wk" in report.detail_line(row)
    assert report.words(row["text"]) <= report.MAX_ROW_WORDS
    assert "New RB: bench → RB (once the accepted trade processes)" in rows["lineup"]["text"]
    # a plain open offer is unchanged
    lg2 = league(outgoing_trades=[{"give": ["A"], "get": ["B"], "rival": "Them", "hours_left": 5.0}])
    assert by_id(lg2)["sent:b"]["label"] == "Already sent"


def test_every_row_is_one_short_click_with_the_reasons_underneath():
    """Oct 13: the Accepted row ran to 90 words on a phone. A row's `text` is the click, under MAX_ROW_WORDS; the
    reasons, clocks and numbers ride in `detail`. The lineup row is the one exception (it is a list of moves)."""
    from tests.test_ledger import _rich_league
    for lg in (_rich_league(), league(outgoing_trades=[{"give": ["A", "B", "C"], "get": ["D"], "rival": "Them", "accepted": True, "process_ts": 1}],
                                     trade_spots_after=2, trade_adds_after=[{"name": "X", "pos": "RB", "why": "+1/wk"}, {"name": "Y", "pos": "QB", "why": "depth"}])):
        for x in report.todos(lg):
            if x["kind"] != "lineup":
                assert report.words(x["text"]) <= report.MAX_ROW_WORDS, (x["id"], x["text"])
                assert isinstance(x.get("detail", []), list)
