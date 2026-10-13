"""HTML email renderer over the synthetic league: structure, reads, no leaked jargon, size."""
import re
from html import escape

from ff import report
from ff.email_html import render_email
from ff.packet import analyze_league
from tests.test_packet_synthetic import FakeXW, make_snapshot


def _packet(monkeypatch):
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    snap = make_snapshot()
    snap["settings"]["name"] = "Bill & Ted's League"
    snap["teams"][1]["name"] = "O'Brien's Boys"
    blk = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides=None, sims=200)
    blk["name"] = "L9"
    watch = [{"name": blk["roster"][0]["name"], "pos": "RB", "team": "GB", "league": "L9", "status": "QUESTIONABLE",
              "notes": None, "espn_id": 1, "p_zero": 0.3, "override_note": "limited Friday, expected to play"}]
    return {"version": 3, "generated": "2026-09-10T07:00:00", "season": 2026,
            "shared": {"injury_watchlist": watch, "exposure": {"RB1_0": ["L9", "L8"]}, "trending_adds": [], "usage_error": None, "unmatched_ids": []},
            "leagues": [blk]}


def test_render_email(monkeypatch):
    p = _packet(monkeypatch)
    reads = {"L9": {"read": "Coin flip, lineup is fine.", "paste": "Want to swap RBs?", "paste_to": "O'Brien's Boys"}}
    html = render_email(p, reads)
    assert "Bill &amp; Ted&#x27;s League" in html and "O&#x27;Brien&#x27;s Boys" in html
    assert "Coin flip, lineup is fine." in html and "Want to swap RBs?" in html and "Paste to O&#x27;Brien" in html
    assert "limited Friday, expected to play" in html
    assert " to IR" in html and ">IR<" in html and "out ~" in html  # the hurt-player row, with its verdict as the badge, the horizon on the grey line
    for todo in report.todos(p["leagues"][0]):
        if not todo.get("moves"):
            assert escape(todo["text"])[:30] in html
    for jargon in ("p_zero", "fp:", "llm:", "espn:", "sleeper:"):
        assert jargon not in html
    # default: action cards only, small enough to transcribe into a mail tool
    assert len(html) < 20_000
    assert "Full detail" not in html and "League odds" not in html
    assert "OFFER" in html
    assert "white-space:nowrap" in html  # "ACCEPT OFFER" badge must never wrap onto two lines
    # every closing </tr>/</table>/</div> ends a line, so no read chunk can split a tag
    assert "</tr><" not in html and "</table><" not in html and "</div><" not in html
    assert max(len(ln) for ln in html.splitlines()) < 2_000
    # --full keeps the old appendix
    detailed = render_email(p, reads, full=True)
    assert "Full detail" in detailed and "League odds" in detailed
    assert "Offers on the table" in detailed and "SENT" in detailed
    # the detail always lives in the plain-text part
    assert "League odds" in report.render(p, reads=reads)
    # alert-tier email: only the offer, with the reply callout
    short = render_email(p, {"L9": {"reply": "how about RB2 straight up?", "reply_to": "Team 2"}}, only_incoming=True)
    assert "FF trade offer" in short and "how about RB2 straight up?" in short and "Waivers" not in short
    assert len(short) < 20_000
    # a ruling on the offer row reaches the alert email: skip strikes the verdict badge and carries the note
    oid = next(x["id"] for x in report.todos(p["leagues"][0]) if x["kind"] == "trade_in")
    ruled = render_email(p, {"L9": {"items": {oid: {"verdict": "skip", "note": "decline, he is in a boot"}}}}, only_incoming=True)
    assert ">SKIP<" in ruled and "line-through" in ruled and "decline, he is in a boot" in ruled
    assert "line-through" not in short
    # no reads -> no callouts
    bare = render_email(p)
    assert "Claude" not in bare.replace("read from Claude", "")


def test_markdown_lists_render(monkeypatch):
    p = _packet(monkeypatch)
    md = report.render(p, reads={"L9": {"read": "hold"}})
    head = md.split("---")[0]
    lines = head.splitlines()
    first = next(i for i, ln in enumerate(lines) if ln.startswith("- "))
    assert lines[first - 1] == ""  # blank line so markdown makes a real list
    assert "**Claude's read:** hold" in head
    assert "p_zero=" not in md


def test_voice_lint_flags_ai_tells():
    reads = {"L1": {"read": "It's not the lineup — it's the matchup. Not a bad spot, but a risky one.", "paste": "Per my model you gain 2.1 ppw. Worth noting this is robust."},
             "L2": {"read": "Coin flip, leave it.", "paste": "hey, any interest in Rice + Montgomery for Henry? you're thin at WR. no worries if not"}}
    warns = report.voice_lint(reads)
    assert any(w.startswith("L1.read") and "em dash" in w for w in warns)
    assert any("not X, but Y" in w for w in warns)
    assert any(w.startswith("L1.paste") and "model" in w for w in warns)
    assert not any(w.startswith("L2") for w in warns)


def test_depth_warning_reaches_both_renderers(monkeypatch):
    """A 'leaves me no backup QB' caveat is useless if it only lands in the detail tables the HTML no longer carries."""
    p = _packet(monkeypatch)
    lg = p["leagues"][0]
    assert lg["trades"], "the demo league should produce at least one candidate"
    lg["trades"][0]["why"].append("leaves me no backup QB")  # the caveat the scan attaches when a package ships the spare QB
    warns = [w for t in report.todos(lg) for w in (t.get("warn") or [])]
    assert warns
    html, md = render_email(p), report.render(p)
    for w in warns:
        assert escape(w) in html, f"missing from the card: {w}"
        assert w in md, f"missing from the plain text: {w}"


def test_a_skipped_row_is_struck_through_with_its_reason(monkeypatch):
    """The whole point of the rulings: the email ends with one answer per row, not a card saying do it and a
    paragraph underneath saying don't."""
    p = _packet(monkeypatch)
    lg = p["leagues"][0]
    trade_ids = [t["id"] for t in report.todos(lg) if t["kind"] == "trade"]
    assert trade_ids, "fixture should offer at least one trade to rule on"
    reads = {"L9": {"items": {trade_ids[0]: {"verdict": "skip", "note": "he is in a boot, not this week"}}}}
    html = render_email(p, reads)
    assert "line-through" in html and "SKIP" in html
    assert escape("he is in a boot, not this week") in html


def test_the_paste_message_rides_on_the_trade_it_belongs_to(monkeypatch):
    p = _packet(monkeypatch)
    lg = p["leagues"][0]
    trades = [t for t in report.todos(lg) if t["kind"] == "trade"]
    assert trades, "fixture should offer at least one trade for the message to attach to"
    reads = {"L9": {"paste": "any interest in this one?", "paste_to": trades[0]["rival"]}}
    html = render_email(p, reads)
    assert html.count("any interest in this one?") == 1
    # the message sits inside the checklist table (before it closes), not adrift at the bottom of the card
    at = html.index("any interest in this one?")
    assert html.rindex("TRADE", 0, at) < at < html.index("</table>", at)


def test_title_odds_are_not_in_the_action_card(monkeypatch):
    """The league header reads record · opponent · win · playoffs; the title odds cell was cut from it (two decimals
    of noise in September) and the standings table that carries the Title column is `--full` only. The check is on
    those two elements, not on the word: a rival called "Title Town" is not a leak, and a lowercase cell would be."""
    p = _packet(monkeypatch)
    lg = p["leagues"][0]
    me = lg["odds"][str(lg["my_team_id"])]
    me["playoff_pct"], me["title_pct"] = 41.0, 7.0  # distinct numbers, so the header can be read cell by cell
    lg["opponent"]["name"] = "Title Town"
    html = render_email(p, {})
    at = html.index("vs Title Town")
    header = html[html.rindex("<div", 0, at):html.index("</div>", at)]
    cells = [c.strip() for c in header.split("&nbsp;·&nbsp;")]
    assert any(c.startswith("Playoffs") and "41%" in c for c in cells), header
    # the weight the season pricing gave the playoff weeks rides the same header, from the packet, never computed here
    lg["playoff_weight"] = {"weeks": [15, 16, 17], "weight": 1.35, "playoff_pct": 90.0, "mult": 1.5}
    html = render_email(p, {})
    assert "playoff weeks 15-17 weighted ×1.4" in html
    lg["playoff_weight"] = {"weeks": [], "weight": None, "playoff_pct": 90.0, "mult": 1.5}
    assert "weighted ×" not in render_email(p, {})
    odds_cells = [c for c in cells if "Title Town" not in c]
    assert not any(re.search("title", c, re.I) for c in odds_cells), header  # no title cell, whatever its case
    assert not any(re.search(r"(?<![\d.])7%", c) for c in odds_cells), header  # nor the number under another label
    # the standings table (with its Title column) is appended only by --full
    assert "League odds" not in html and ">Title</th>" not in html
    full = render_email(p, {}, full=True)
    assert "League odds" in full and ">Title</th>" in full and ">7%</td>" in full  # the markers are the real ones


def test_a_skipped_hold_is_struck_through_in_the_html(monkeypatch):
    """B8: `~~` is the only skip marker a hold carries. The markdown keeps it; the email used to strip it and print
    the hold as if Claude had agreed with it."""
    p = _packet(monkeypatch)
    lg = p["leagues"][0]
    lg["injuries"] = [{"name": "Backup Bench", "pos": "WR", "verdict": "hold", "weeks_out": 3, "back_eff": 4.0,
                       "mu_ros_active": 9.0, "hold_value": 36.0, "return_week": 8, "why": []}]
    holds = [t for t in report.todos(lg) if t["kind"] == "hold"]
    assert len(holds) == 1, "the fixture should carry exactly one hold row to rule on"
    reads = {"L9": {"items": {holds[0]["id"]: {"verdict": "skip", "note": "he was cut on Tuesday"}}}}
    html = render_email(p, reads)
    md = report.render(p, reads=reads)
    assert "~~Backup Bench (WR)" in md  # the markdown strikes the hold
    assert "~~" not in html  # no raw markdown in the email
    at = html.index("Holding:")
    line = html[at:html.index("</div>", at)]
    assert "line-through" in line, "the skipped hold renders as a plain hold in the email"
    assert escape("he was cut on Tuesday") in line
    # a hold that is not skipped stays plain
    plain = render_email(p, {})
    at = plain.index("Holding:")
    assert "line-through" not in plain[at:plain.index("</div>", at)]


# ---------- game context (D7): opponent, implied total, kickoff day, usage, p(accept) ----------

THU, SUN = "2027-09-10T00:15Z", "2027-09-12T17:00Z"  # 8:15 PM ET Thursday, 1 PM ET Sunday (far enough out that the clock never locks anyone)


def _line(opp, kickoff, implied, home=True):
    return {"opp": opp, "home": home, "spread": -4.0 if home else 4.0, "total": 51.0, "implied": implied, "kickoff": kickoff,
            "venue": "Somewhere", "indoor": False, "state": "pre"}


def _packet_with_lines(monkeypatch):
    """The synthetic league with the star's team on Thursday night (against a team nobody in the demo plays for) and
    the other two teams playing each other on Sunday, plus a usage row for the star."""
    monkeypatch.setattr("ff.packet.fantasycalc.by_espn_id", lambda **kw: {})
    snap = make_snapshot()
    base = analyze_league(snap, FakeXW(), {}, {}, {}, {}, overrides=None, sims=100)
    star = base["roster"][0]
    others = [t for t in ("GB", "KC", "DAL") if t != star["team"]]
    lines = {star["team"]: _line("BUF", THU, 27.5), others[0]: _line(others[1], SUN, 21.0),
             others[1]: _line(others[0], SUN, 23.5, home=False)}
    usage = {f"g{star['espn_id']}": {"flags": [], "ppg": 14.0, "xfp_pg": 13.0, "tgt_share": 0.262, "tgt_pg": 8.1, "carry_pg": 0.0}}
    blk = analyze_league(snap, FakeXW(), {}, {}, {}, usage, overrides=None, sims=100, lines=lines)
    blk["name"] = "L9"
    return {"version": 3, "generated": "2026-09-10T07:00:00", "season": 2026,
            "shared": {"injury_watchlist": [], "exposure": {}, "trending_adds": [], "usage_error": None, "unmatched_ids": []},
            "leagues": [blk]}, star


def test_packet_carries_the_game_and_the_kickoff_day(monkeypatch):
    p, star = _packet_with_lines(monkeypatch)
    lg = p["leagues"][0]
    me = next(q for q in lg["roster"] if q["espn_id"] == star["espn_id"])
    assert me["odds_line"]["implied"] == 27.5 and me["odds_line"]["day"] == "Thu" and me["odds_line"]["home"] is True
    assert all((q["odds_line"] or {}).get("day") in ("Thu", "Sun") for q in lg["roster"])
    assert all(w.get("kickoff_day") in ("Thu", "Sun") for w in lg["waivers"])  # the wire is on the same clock


def test_the_card_says_set_by_thu_and_the_number_behind_the_accept_word(monkeypatch):
    p, star = _packet_with_lines(monkeypatch)
    lg = p["leagues"][0]
    rows = {x["id"]: x for x in report.todos(lg)}
    assert f"{star['name']}: bench → QB, set by Thu" in rows["lineup"]["moves"]  # the demo benches the star; he plays Thursday
    assert not any("set by" in m for m in rows["lineup"]["moves"] if star["name"] not in m)
    trade_rows = [x for x in rows.values() if x["kind"] == "trade"]
    assert trade_rows and all(f"({x['p_accept']:.0%})" in x["text"] for x in trade_rows)
    html = render_email(p)
    assert escape(f"{star['name']}: bench → QB, set by Thu").replace(" → ", "") in html.replace(' <span style="color:#6b7280">→</span> ', "")
    assert "%)" in html
    md = report.render(p)
    assert "set by Thu" in md and "%)" in md


def test_the_detail_shows_opponent_implied_total_and_usage_next_to_the_projection(monkeypatch):
    p, star = _packet_with_lines(monkeypatch)
    opp = p["leagues"][0]["roster"][0]["odds_line"]["opp"]
    html = render_email(p, full=True)
    assert f"vs {opp} 27.5 Thu" in html and "tgt 26%" in html
    md = report.render(p)
    assert f"| {star['name']} | QB | " in md and f"| vs {opp} 27.5 Thu | tgt 26% |" in md
    assert f"- QB: {star['name']} (vs {opp} 27.5 Thu)" in md
    # no lines, no usage: nothing invented, the rows read as before
    bare = _packet(monkeypatch)
    plain = render_email(bare, full=True)
    assert "set by" not in plain and " 27.5 " not in plain and "tgt " not in plain and "mkt " not in plain
    assert "set by" not in report.render(bare)
