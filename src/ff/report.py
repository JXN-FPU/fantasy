"""Render a DecisionPacket to markdown. Works with no LLM."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .ledger import RISKY_TO_SIT, Spots, bench, drop_candidate, drop_cost, drop_note, drop_order, handcuff_for, market_value, sit_note, weeks_left
from .model import waivers as waivers_model

HOME_TZ = ZoneInfo("America/Denver")   # Dustin's clock, for the times ESPN stamps on a trade


def slug(text: str) -> str:
    """Stable, typeable id fragment: 'Jameson Williams' -> 'jameson-williams'."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(text).lower())).strip("-")


def _flags(p: dict) -> str:
    f = [x for x in p.get("flags", []) if not x.startswith("fp:")]
    g = p["sources"].get("grade")
    if g:
        f.append(g)
    if (p.get("weeks_out") or 0) >= 1:
        f.append(f"out ~{p['weeks_out']:.0f}w" + (f", back wk {p['return_week']}" if p.get("return_week") else " (season)"))
    return ", ".join(f)


NON_STARTER = {"BE", "IR", "", "FA"}

# Kickoff days in the order the moves fall due; a day the packet does not know reads as Sunday.
DAY_ORDER = {"Thu": 0, "Fri": 1, "Sat": 2, "Sun": 3, "Mon": 4}


def game_note(p: dict) -> str:
    """"vs KC 27.5 Thu" / "@DET 21.0 Sun": the opponent, Vegas implied team total and kickoff day from `odds_line`
    (the packet's copy of the scoreboard line); empty when the packet has no line for his team."""
    ln = p.get("odds_line") or {}
    if not ln.get("opp"):
        return ""
    out = f"{'@' if ln.get('home') is False else 'vs '}{ln['opp']}"
    if ln.get("implied") is not None:
        out += f" {ln['implied']:.1f}"
    if ln.get("day"):
        out += f" {ln['day']}"
    return out


def usage_note(p: dict) -> str:
    """The one usage figure worth a cell: target share for a receiver, carries a game for a back (`model/usage.py`,
    season to date), targets a game when the share column is blank, and the 30-day market move when nflverse has
    nothing on him. Empty when there is no number; nothing is invented."""
    u = p.get("usage") or {}
    if p.get("pos") == "RB" and (u.get("carry_pg") or 0) >= 1:
        return f"{u['carry_pg']:.0f} car/g"
    if u.get("tgt_share") is not None:
        return f"tgt {u['tgt_share']:.0%}"
    if (u.get("tgt_pg") or 0) >= 1:
        return f"{u['tgt_pg']:.0f} tgt/g"
    trend = (p.get("market") or {}).get("trend_30d")
    return f"mkt {trend:+.0f}/30d" if trend else ""


def due_note(day: str | None) -> str:
    """", set by Thu" on a move for a player whose game is before Sunday, " (Mon)" for a Monday one, nothing for
    Sunday or an unknown day: the lineup locks player by player at kickoff, so a Thursday move is today's."""
    if day in ("Thu", "Fri", "Sat"):
        return f", set by {day}"
    return " (Mon)" if day == "Mon" else ""


def _lineup_changes(lg: dict) -> list[str]:
    """Explicit moves to get from the current ESPN slots to the recommended lineup, as an ordered chain:
    'X: bench -> FLEX' or 'Y: WR -> FLEX', with slots freed in an order that works in the app. Inside each group the
    earliest kickoff goes first and a Thursday player's move says so (`due_note`); the groups keep the app order."""
    cur = {p["name"]: p["slot"] for p in lg["roster"]}
    day = {p["name"]: (p.get("odds_line") or {}).get("day") for p in lg["roster"]}
    rec = {n: s for s, names in lg["lineup_win"]["slots"].items() for n in names if not n.startswith("(")}
    by_day = lambda n: DAY_ORDER.get(day.get(n), DAY_ORDER["Sun"])  # noqa: E731
    moves = []
    # starters who are dropped entirely
    for n in sorted((n for n, s in cur.items() if s not in NON_STARTER and n not in rec), key=by_day):
        moves.append(f"{n}: {cur[n]} → bench{due_note(day.get(n))}")
    # starters changing slot
    for n in sorted((n for n, s in rec.items() if cur.get(n) not in NON_STARTER and cur.get(n) != s), key=by_day):
        moves.append(f"{n}: {cur[n]} → {rec[n]}{due_note(day.get(n))}")
    # bench players coming in
    for n in sorted((n for n in rec if cur.get(n) in NON_STARTER), key=by_day):
        moves.append(f"{n}: bench → {rec[n]}{due_note(day.get(n))}")
    empties = [s for s, names in lg["lineup_win"]["slots"].items() if any(n.startswith("(") for n in names)]
    if empties:
        moves.append("EMPTY SLOT: " + ", ".join(empties) + " (no healthy player; pick one up)")
    return moves


# The ledger lives in `ff.ledger`; these names stay importable from here (tests and CLAUDE.md know them by these names).
_Spots = Spots
_drop_cost, _drop_order, _drop_candidate, _drop_note = drop_cost, drop_order, drop_candidate, drop_note
_bench, _sit_note, _market_value, _weeks_left, _handcuff_for = bench, sit_note, market_value, weeks_left, handcuff_for


# Below this many starters still to play, naming them is the point (Monday morning: two guys decide the week).
# Above it the names are a roster dump — the count is the whole signal.
NAME_LEFT_AT = 3


def phase_line(lg: dict) -> str | None:
    """One plain sentence about where the week stands, or None before kickoff."""
    ws = lg.get("week_state") or {}
    ph = ws.get("phase", "pre")
    if ph == "pre":
        return None
    me, op = ws.get("espn_my_score", ws.get("my_points")), ws.get("espn_opp_score", ws.get("opp_points"))
    if ph == "final":
        res = "W" if (me or 0) > (op or 0) else ("L" if (me or 0) < (op or 0) else "T")
        return f"Week {lg['week']} final: {res} {me}–{op}"
    left = ws.get("my_left") or []
    ol = ws.get("opp_left") or []
    who = f" ({', '.join(left)})" if 0 < len(left) <= NAME_LEFT_AT else ""
    return f"Week {lg['week']} in progress: you {me} – {op} · {len(left)} starters left{who} vs their {len(ol)}"


def sendable(t: dict) -> bool:
    """Worth putting in front of a rival: helps them too, and I am not overpaying at market value."""
    return bool(t.get("sendable", t["their_delta_ppw"] >= 0))


def push_line(t: dict) -> str:
    """Why this trade is at the top and how to make it stop: the day count and the reason (Claude's note or the math)."""
    p = t.get("pushed") or {}
    days = int(p.get("days") or 1)
    why = p.get("note") or f"+{t['my_delta_ppw']:.1f} pts/wk is a big lineup gain by the math"
    asked = f"asked {days} mornings running" if days > 1 else "first ask"
    return f"— too good to let slide ({why}); {asked}. Send it, or skip it with a reason and it stops"


# Trade rows on the card. Two: a third is where the laughed-at packages used to live.
TRADE_ROWS = 2


def trade_tag(t: dict) -> str:
    return "worth sending" if sendable(t) else "a reach, send only if bored"


VERDICT_WORD = {"accept": "ACCEPT", "decline": "DECLINE", "counter": "COUNTER"}


def executes_note(dl: dict, lead: str) -> str:
    """"; if he accepts today it executes Wed 12:05 PM" from the league's deadlines block (the trade review period),
    with the kickoff caveat when that clock is past the next game: a player whose game has started by then is
    locked and moves for next week only. Empty without a review period."""
    w = dl.get("offer_executes_word")
    if not w:
        return ""
    out = f"; {lead} {w}"
    if dl.get("offer_after_kickoff"):
        out += f", after the {dl.get('next_kickoff_word') or 'next'} kickoff: a player whose game has started by then is locked until next week"
    return out


def incoming_line(t: dict) -> str:
    """One sentence on an offer someone sent me: what moves, what it does for each side, when it expires, and when
    it executes if accepted now (the review period)."""
    td = f", title odds {t['my_title_delta']:+.1f}" if t.get("my_title_delta") is not None else ""
    left = f"; expires in {t['hours_left']:.0f}h" if t.get("hours_left") is not None else ""
    cut = ""
    if t.get("drops"):
        cut = "; accepting means dropping " + ", ".join(f"{d['name']} ({d['cap']}-{d['pos']} cap)" for d in t["drops"])
    counter = ""
    if t.get("verdict") == "counter" and t.get("counter"):
        counter = f"; counter with your {', '.join(t['counter']['give'])} for {', '.join(t['counter']['get'])} ({t['counter']['my_delta_ppw']:+.1f} for you, fair on his chart)"
    ex = ""
    if t.get("executes"):
        ex = f"; accept now and it executes {t['executes']}"
        if t.get("executes_after_kickoff"):
            ex += ", after the next kickoff: a player whose game has started by then is locked until next week"
    return (f"{t['rival']} offers {', '.join(t['get']) or 'nothing'} for your {', '.join(t['give']) or 'nothing'}: "
            f"{t['my_delta_ppw']:+.1f} pts/wk for you, {t['their_delta_ppw']:+.1f} for them{td}{left}{cut}{counter}{ex}")


# Slots the flex cannot cover, so the last healthy body at the position is the whole slot.
COVER_POS = ("QB", "TE", "K", "D/ST")
# The model's why-vocabulary is first person ("leaves me no backup QB"); the checklist is addressed to Dustin.
TO_DUSTIN = (("leaves me ", "leaves you "), ("market says I give", "market says you give"), ("for me", "for you"),
             ("I get the best player", "you get the best player"))


def _to_dustin(s: str) -> str:
    for a, b in TO_DUSTIN:
        s = s.replace(a, b)
    return s


def _cover_items(lg: dict, spots: Spots, added: set[str]) -> list[dict]:
    """A starter at a slot the flex cannot cover is in real doubt and nothing healthy sits behind him.

    The optimizer never proposes this: a backup kicker is worth ~nothing in expectation, so he loses every ranking
    we have right up to the Sunday his starter is scratched and the slot scores zero. It is still a button to press.

    The button has to work: the body named is a free agent when there is one (a player still on waivers clears on
    waiver day, not before kickoff; he is the fallback, worded as the claim he is), and the add spends a roster spot
    on the ledger like every other pickup, so the row names the drop (B7). A row above that already adds a body at
    the position is the cover; the row is skipped then.
    """
    slots = lg["settings"]["lineup_slots"]
    rec = lg["lineup_win"]["slots"]
    by_name = {p["name"]: p for p in lg["roster"]}
    starters = {n for ns in rec.values() for n in ns}
    pos_added = {w["pos"] for w in lg["waivers"] if w["name"] in added}
    out = []
    for pos in COVER_POS:
        if not slots.get(pos):
            continue
        risky = [by_name[n] for n in rec.get(pos, []) if n in by_name and not by_name[n].get("locked")
                 and (by_name[n]["p_zero"] >= RISKY_TO_SIT or by_name[n]["bye"])]
        if not risky:
            continue
        if any(p["pos"] == pos and p["name"] not in starters and p["mu_ros"] > 0 and p["p_zero"] < RISKY_TO_SIT
               for p in lg["roster"]):
            continue  # a healthy body on the bench already covers it
        if pos in pos_added:
            continue  # a waiver row above already brings one in
        who = risky[0]
        pool = [w for w in lg["waivers"] if w["pos"] == pos and w["name"] not in added]
        fa = next((w for w in pool if not w.get("on_waivers")), pool[0] if pool else None)
        risk = "on bye" if who["bye"] else f"{who['p_zero']:.0%} to sit"
        _, suffix = spots.take(adding=pos, lands_ts=(fa or {}).get("lands_ts"))
        if fa is None:
            plan = "grab a startable one off the wire before kickoff"
        elif fa.get("on_waivers"):
            plan = f"{_add_verb(lg, fa)} {fa['name']} ({fa['team']}) now, nobody at {pos} is a free agent and the claim lands {fa.get('lands') or 'on waiver day'}"
        else:
            plan = f"add {fa['name']} ({fa['team']}) before kickoff"
        if fa is not None:
            added.add(fa["name"])
        out.append({"kind": "cover", "id": f"cover:{slug(pos)}", "label": f"Cover {pos}",
                    "text": f"{who['name']} is {risk} and he is your only {pos}; {plan}{suffix}"})
    return out


def _add_verb(lg: dict, w: dict) -> str:
    """"claim" for a player still on waivers (the click is a claim that processes on waiver day, and in a priority
    league it goes to the highest priority), "add" for a free agent who is yours the moment you click."""
    if w.get("on_waivers"):
        pri = lg.get("waiver_rank")
        faab = lg.get("faab_remaining") is not None
        lands = f", lands {w['lands']}" if w.get("lands") else ""
        return "claim" + (f" (waivers, bid ${w['bid']}{lands})" if faab and w.get("bid") else (f" (waivers, you are priority #{pri}{lands})" if pri else f" (waivers{lands})"))
    return "add"


def _claim_note(lg: dict, w: dict) -> str:
    if not w.get("on_waivers"):
        return ""
    return f" (he is on waivers, so the claim lands {w['lands']})" if w.get("lands") else " (he is on waivers, so the claim lands on waiver day)"


INJURY_LABEL = {"ir": "IR", "hold": "Holding", "drop": "Drop", "trade": "Hurt (trade)", "activate": "Activate"}
# Verdicts that move a player off the roster or out the door: Claude has to rule on them, like trade rows.
INJURY_NEEDS_RULING = ("drop", "trade")


def _weeks(r: dict) -> str:
    if r.get("return_week") is None and (r.get("avail_ros") or 0) == 0:
        return "the season"
    return f"~{r['weeks_out']:.0f} wk{'s' if r['weeks_out'] >= 1.5 else ''}"


def _stash_note(r: dict, drop: str | None) -> str:
    """", added when he was stashed Oct 3" when the drop an activation takes is the body that stash brought in."""
    st = r.get("stash") or {}
    if not drop or drop not in (st.get("added") or []):
        return ""
    try:
        when = date.fromisoformat(st["stashed"]).strftime("%b %-d")
    except (KeyError, TypeError, ValueError):
        return ", added when he was stashed"
    return f", added when he was stashed {when}"


def _injury_items(lg: dict, spots: Spots | None = None) -> list[dict]:
    """One row per hurt player, the verb decided by `model.injuries`; this only puts words on the numbers.

    Rows lead with the click (move to IR, drop X and add Y) so the checklist reads as a sequence. A hold is not a
    click, so it comes back as kind `hold` and the renderers print it below the list, not in it. An activation
    needs a bench spot, so it draws on the ledger; when the drop it takes is a player who has his own Drop row, that
    row folds into this one (one click, one drop). The ledger counts a drop or an add on the row that prints it."""
    spots = spots or Spots(lg, lg.get("open_spots") or 0, set())
    drop_rows = {r["name"]: r for r in lg.get("injuries") or [] if r["verdict"] == "drop"}
    # Every activation draws its spot before any row prints. `injuries.decide` orders rows by `mu_ros_active`, so the
    # Drop row an activation folds in can come first; folding on the fly then printed "drop Dart" on both rows.
    taken = {r["name"]: spots.take(for_whom=r["name"]) for r in lg.get("injuries") or [] if r["verdict"] == "activate"}
    folded = {drop for drop, _ in taken.values() if drop in drop_rows}
    out = []
    for r in lg.get("injuries") or []:
        if r["name"] in folded:
            continue
        who, v = f"{r['name']} ({r['pos']})", r["verdict"]
        if v == "hold" and r["weeks_out"] < 2:
            continue  # out one game and worth keeping: the lineup row already handles him, this would be the injury report
        back = f", back wk {r['return_week']}" if r.get("return_week") else ""
        add = r.get("add")
        kind = "injury"
        if v == "ir":
            text = f"move {who} to IR (out {_weeks(r)}{back})"
            if r.get("ir_occupant"):
                text += f" in place of {r['ir_occupant']}"
            elif add:
                text += f", then add {add['name']} ({add['pos']}, {add['why']})"
                spots.note(add_pos=add["pos"])
        elif v == "activate":
            st = (r.get("espn_status") or "active").replace("_", " ").lower()
            text = f"move {who} off IR, ESPN lists him {st} so the slot has to be cleared"
            drop, suffix = taken[r["name"]]
            # The drop is the body the stash brought in: say so, so a round trip reads as one (Daniels, Oct 3 to 7).
            came_in = _stash_note(r, drop)
            if drop in folded:
                d = drop_rows[drop]
                text += f"; drop {drop} ({d['pos']}, out {_weeks(d)}, worth ~{d['hold_value']:.0f} pts the rest of the way{came_in}) to make room"
            else:
                text += suffix + (f"{came_in} to make room" if drop else "")
        elif v == "drop":
            text = f"drop {who}: out {_weeks(r)}{back}, worth ~{r['hold_value']:.0f} pts the rest of the way"
            spots.note(drop=r["name"], add_pos=add["pos"] if add else None)
            if add:
                text += f"; add {add['name']} ({add['pos']}, {add['why']})"
        elif v == "trade":
            t = r["trades"][0]
            text = f"the offer to {t['rival']} for {', '.join(t['get'])} ships {who} (out {_weeks(r)}{back}); else hold"
        else:
            kind = "hold"
            games = f"{r['back_eff']:.0f} game{'s' if r['back_eff'] >= 1.5 else ''}"
            text = f"{who} out {_weeks(r)}{back}, {games} at ~{r['mu_ros_active']:.0f}/g on return"
            if r.get("why"):
                text += f"; {r['why'][0]}"
        out.append({"kind": kind, "id": f"injury:{slug(r['name'])}", "label": INJURY_LABEL[v], "verdict": v, "text": text,
                    "trade_ids": [f"trade:{slug('-'.join(t['get']))}" for t in r.get("trades") or []]})
    return out


def _offer_items(lg: dict) -> list[dict]:
    """One row per offer someone sent me, with the verdict in the label: the first thing to answer."""
    return [{"kind": "trade_in", "id": f"offer:{slug('-'.join(t['get']) or t['rival'] or 'offer')}",
             "label": f"Incoming offer ({VERDICT_WORD[t['verdict']]})", "verdict": t["verdict"], "text": incoming_line(t)}
            for t in lg.get("incoming_trades") or []]


def _pickup_move(lg: dict, w: dict) -> str:
    """"Keon Coleman: free agent → WR over Wan'Dale Robinson once the add above is through": a pickup who starts
    this week is a lineup move too, so it rides on the lineup row with the rest, after the add, and is worded so
    the lineup above it still stands when he was gone by the time Dustin clicked (Oct 10: he was)."""
    slot = w.get("week_slot") or w["pos"]
    by_name = {p["name"]: p for p in lg["roster"]}
    names = [n for n in lg["lineup_win"]["slots"].get(slot) or [] if n in by_name and not by_name[n].get("locked")]
    over = min(names, key=lambda n: (1 - (by_name[n].get("p_zero") or 0.0)) * by_name[n]["mu"], default=None)
    return (f"{w['name']}: free agent → {slot}" + (f" over {over}" if over else "") + " once the add above is through"
            + due_note(w.get("kickoff_day")))


def _lineup_item(lg: dict, pickups: list[dict] = ()) -> dict:
    """The lineup row: the moves in app order before kickoff, only the unplayed slots mid-week, a pointer to next
    week once the week is final. `pickups` are the free agents the waiver rows above add who start this week; their
    move prints last, conditional on the add (`_pickup_move`), so the lineup is set once, after the roster moves."""
    ph = (lg.get("week_state") or {}).get("phase", "pre")
    ch = (_lineup_changes(lg) + [_pickup_move(lg, w) for w in pickups]) if ph != "final" else []
    # a player an accepted trade brings in is on the bench the morning it processes; his move waits for that
    arriving = {p["name"] for p in lg.get("roster") or [] if p.get("pending_trade") == "in"}
    ch = [f"{c} (once the accepted trade processes)" if c.split(":")[0] in arriving else c for c in ch]
    if ph == "final":
        return {"kind": "lineup", "id": "lineup", "label": "Lineup", "moves": [],
                "text": f"week {lg['week']} is over; set next week's lineup once ESPN rolls to week {lg['week'] + 1} (Tuesday)"}
    if ch:
        return {"kind": "lineup", "id": "lineup", "label": "Lineup (in order)" if ph == "pre" else "Lineup (only unplayed slots)",
                "text": " · ".join(ch), "moves": ch}
    return {"kind": "lineup", "id": "lineup", "label": "Lineup", "text": "leave as is" if ph == "pre" else "nothing left to change", "moves": []}


def _waiver_items(lg: dict, spots: Spots, added: set[str]) -> list[dict]:
    """At most one pickup who starts this week and one who upgrades a slot for the season; each spends a spot on the
    ledger and goes into `added` so no row below brings him in again. A claim in a rolling-priority league also says
    whether the priority it spends is worth it (`_priority_note`). The wire moves between the sync and the click
    (Oct 10: the pickup was on another roster by noon), so each row names the next body on the list as the fallback.
    A free agent who starts this week comes back on the row as `pickup`, for the lineup row to fold in; a claim
    lands on waiver day, so his start is next card's business."""
    starts = [w for w in lg["waivers"] if not w["streamer"] and w.get("delta_week", 0) >= 1.5 and w["name"] not in added]
    ups = [w for w in lg["waivers"] if not w["streamer"] and w["delta_over_starter"] >= waivers_model.CLAIM_WORTHY_PPW and w not in starts and w["name"] not in added]
    out = []
    for w in starts[:1]:
        _, suffix = spots.take(adding=w["pos"], lands_ts=w.get("lands_ts"))
        added.add(w["name"])
        nxt = next((x for x in starts if x is not w), None)
        fallback = f"; if he is gone, {nxt['name']} ({nxt['pos']}, +{nxt['delta_week']:.0f} this week)" if nxt else ""
        out.append({"kind": "waiver", "id": f"waiver:{slug(w['name'])}", "label": "Waiver",
                    "text": f"{_add_verb(lg, w)} {w['name']} ({w['pos']}) and start him at {w['week_slot']} (+{w['delta_week']:.0f} pts this week)"
                            f"{due_note(w.get('kickoff_day'))}{_priority_note(lg, w)}{suffix}{fallback}",
                    "pickup": None if w.get("on_waivers") else w})
    for w in ups[:1]:
        _, suffix = spots.take(adding=w["pos"], lands_ts=w.get("lands_ts"))
        added.add(w["name"])
        nxt = next((x for x in ups if x is not w), None)
        fallback = f"; if he is gone, {nxt['name']} ({nxt['pos']}, +{nxt['delta_over_starter']:.1f}/wk)" if nxt else ""
        out.append({"kind": "waiver_up", "id": f"waiver:{slug(w['name'])}", "label": "Waiver (upgrade)",
                    "text": f"{_add_verb(lg, w)} {w['name']} ({w['pos']}), +{w['delta_over_starter']:.1f}/wk over your {w['slot']}"
                            f"{due_note(w.get('kickoff_day'))}{_priority_note(lg, w)}{suffix}{fallback}"})
    return out


def _priority_note(lg: dict, w: dict) -> str:
    """"; worth it: +2.1/wk now vs ~0.7/wk of future claims given up" or "; pass: you'd give up priority #2 for
    +0.3/wk (~0.7/wk of future claims)" on a claim in a rolling-priority league (`model.waivers.priority_cost`, the
    wire's claim-worthy upgrades as the sample of future claims). Empty for a free agent, a FAAB league, a league
    whose order resets weekly, or when the order or my rank is unknown."""
    if not w.get("on_waivers") or lg.get("faab_remaining") is not None:
        return ""
    s = lg.get("settings") or {}
    deltas = [x["delta_over_starter"] for x in lg["waivers"] if not x["streamer"] and x["delta_over_starter"] >= waivers_model.CLAIM_WORTHY_PPW]
    cost = waivers_model.priority_cost(lg.get("waiver_rank"), s.get("team_count"), deltas, weeks_left(lg), s.get("waiver_order"))
    if cost is None:
        return ""
    gain = waivers_model.claim_gain(w["delta_over_starter"], w.get("delta_week") or 0.0, weeks_left(lg))
    if waivers_model.claim_worth_it(gain, cost):
        return f"; worth it: +{gain:.1f}/wk now vs ~{cost:.1f}/wk of future claims given up"
    return f"; pass: you'd give up priority #{lg['waiver_rank']} for +{gain:.1f}/wk (~{cost:.1f}/wk of future claims)"


def _stream_items(lg: dict) -> list[dict]:
    """The one streamer (K, D/ST, a bye-week QB) worth swapping in this week; a swap spends no spot."""
    return [{"kind": "stream", "id": f"stream:{slug(w['name'])}", "label": "Stream",
             "text": f"swap in {w['name']} at {w['pos']} (+{w['delta_over_starter']:.1f} this week){due_note(w.get('kickoff_day'))}" + _claim_note(lg, w)}
            for w in [w for w in lg["waivers"] if w["streamer"] and w["delta_over_starter"] >= 1.5][:1]]


def _open_spot_items(lg: dict, spots: Spots, added: set[str]) -> list[dict]:
    """Bench spots still open after the rows above: name who fills each, never someone the card already adds."""
    out = []
    for a in lg.get("open_spot_adds") or []:
        if spots.open <= 0:
            break
        if a["name"] in added:
            continue
        spots.fill(a["pos"])
        added.add(a["name"])
        w = next((w for w in lg["waivers"] if w["name"] == a["name"]), None)
        out.append({"kind": "waiver", "id": f"open:{slug(a['name'])}", "label": "Open spot",
                    "text": f"{_add_verb(lg, w) if w else 'add'} {a['name']} ({a['pos']}, {a['why']}) to the open bench spot"})
    return out


def _sent_items(lg: dict, spots: Spots | None = None) -> list[dict]:
    """The deadline, once it has passed, the trades accepted and waiting on ESPN's clock, and the offers I already
    sent. Those are the reason half the "why didn't it know that" moments happen: without them the card re-proposes
    a deal sitting in the rival's inbox with a day left. `spots` is the ledger after the rows above: the spots an
    accepted trade frees are listed less the ones a claim landing after it already took."""
    out = []
    if lg.get("trades_closed"):
        out.append({"kind": "sent", "id": "deadline", "label": "Trades",
                    "text": f"the trade deadline passed ({(lg.get('trade_deadline_iso') or '')[:10]}); the rest of the way is waivers only"})
    spots_said = False
    for t in lg.get("outgoing_trades") or []:
        if t.get("accepted"):
            give, get = ", ".join(t["give"]) or "nothing", ", ".join(t["get"]) or "nothing"
            text = (f"your {give} for {get} with {t['rival'] or 'them'} is accepted{processes_note(t)}; this card already "
                    f"counts it ({get} on your roster for Sunday, {give} off it)")
            n_all = int(lg.get("trade_spots_after") or 0)
            n = spots.after_open if spots is not None else n_all
            if n_all and not spots_said:
                spots_said = True
                adds = (lg.get("trade_adds_after") or [])[:n]
                taken = n_all - n
                text += f"; it frees {n_all} bench spot{'s' if n_all != 1 else ''} when it processes, not before"
                if taken:
                    text += f" ({taken} of them {'goes' if taken == 1 else 'go'} to the claim above, which lands after it)"
                dl = lg.get("deadlines") or {}
                if dl.get("waiver_run_ts") and t.get("process_ts") and dl["waiver_run_ts"] < t["process_ts"]:
                    text += f"; the waiver run ({dl.get('waiver_run_word')}) comes first, so a claim placed today still needs a drop"
                if adds:
                    text += ": then add " + ", ".join(f"{a['name']} ({a['pos']}, {a['why']})" for a in adds)
            out.append({"kind": "sent", "id": f"sent:{slug('-'.join(t['get']) or t['rival'] or 'sent')}", "label": "Accepted", "text": text})
            continue
        left = f", {t['hours_left']:.0f}h left" if t.get("hours_left") is not None else ""
        out.append({"kind": "sent", "id": f"sent:{slug('-'.join(t['get']) or t['rival'] or 'sent')}", "label": "Already sent",
                    "text": f"your {', '.join(t['give']) or 'nothing'} for {', '.join(t['get']) or 'nothing'} is still "
                            f"waiting on {t['rival'] or 'them'}{left}"})
    return out


def processes_note(t: dict) -> str:
    """When ESPN executes an accepted trade: ", processes Wed 8:05 AM" in Dustin's clock (`HOME_TZ`) from `process_ts`
    on the row, `processes_iso` (machine-local) as the fallback; empty when the clock is unknown."""
    ms = t.get("process_ts")
    if ms:
        dt = datetime.fromtimestamp(ms / 1000, tz=HOME_TZ)
        return f", processes {dt.strftime('%a %I:%M %p').replace(' 0', ' ')}"
    iso = t.get("processes_iso")
    return f", processes {iso}" if iso else ""


def _pushed(lg: dict) -> list[dict]:
    """The one package to keep at the top until Dustin sends or skips it, and the trades list cleaned so the rulings
    memory only sees that one.

    One push at a time: a remembered one first (Claude's, or the math's from an earlier morning), else the biggest
    gain the math flags today. Two pushes that ship the same player would be alternatives, not a to-do list. A math
    push is only as good as this morning's math: once the row stops being `sendable` (a rival move left me overpaying
    at market, or the package no longer helps him) the memory does not keep it at "do this one". Claude's push
    carries his note as the reason and stays until he sends or skips it."""
    remembered = [t for t in lg["trades"] if t.get("pushed") and ((t["pushed"] or {}).get("source") == "claude" or sendable(t))]
    flagged = sorted([t for t in lg["trades"] if t.get("must_try")], key=lambda t: -t["my_delta_ppw"])
    pushed = (remembered or flagged)[:1]
    for t in lg["trades"]:
        if t not in pushed:
            t["must_try"] = False  # the card's word is final: rulings memory only records the row that was pushed
            t.pop("pushed", None)
    return pushed


# Depth and market warnings that ride along with a trade row: the card is the whole HTML email, so a caveat that only
# reached the detail tables would never be seen on a phone.
TRADE_WARN_PREFIXES = ("leaves me", "leaves them", "market says I", "they start", "they have to cut a body",
                       "he reads it as a lowball", "I get the best player", "they'd have to stream")


def accept_note(t: dict) -> str:
    """", coin flip, p(accept) 0.42": the bucket word and the number behind it (`model/acceptance.py`); the word alone
    on an older packet, nothing when the scan did not score it."""
    word = f", {t['accept_word']}" if t.get("accept_word") else ""
    return word + (f", p(accept) {t['p_accept']:.2f}" if t.get("p_accept") is not None else "")


def _trade_item(t: dict, spots: Spots, push: bool, lg: dict | None = None) -> dict:
    """One package to go send: the offer, what it does for each side, the cut ESPN's position cap forces in the trade
    screen (without it the paste goes out and the screen answers "Too many players with default position WR"), what
    he is left with where I ask (so the paste cannot tell a one-QB team it is set at QB), the fallback, the push."""
    warn = [_to_dustin(w) for w in t["why"] if w.startswith(TRADE_WARN_PREFIXES)]
    them = "neutral for them" if abs(t["their_delta_ppw"]) < 0.05 else f"{t['their_delta_ppw']:+.1f} for them"
    drops, cut = spots.cut(t)
    odds = accept_note(t)
    text = (f"offer {t['rival'] or 'them'} your {', '.join(t['give'])} for {', '.join(t['get'])} "
            f"(+{t['my_delta_ppw']:.1f} pts/wk for you, {them}{odds}){cut}")
    if t.get("after_line"):
        text += f"; {t['after_line']}"
    if t.get("fallback"):
        text += f"; {t['fallback']['text']}"
    text += executes_note((lg or {}).get("deadlines") or {}, "if he accepts today it executes")
    if push:
        text += " " + push_line(t)
    thin_after = [r["pos"] for r in t.get("rival_after") or [] if any(st["wire"] for st in r["starters"]) or not r["starters"]]
    return {"kind": "trade", "id": f"trade:{slug('-'.join(t['get']))}", "label": "Trade (do this one)" if push else f"Trade ({trade_tag(t)})",
            "worth": sendable(t), "push": push, "rival": t.get("rival"), "give": list(t["give"]), "get": list(t["get"]), "warn": warn,
            "drops": drops, "text": text, "thin_after": thin_after, "p_accept": t.get("p_accept")}


def _trade_items(lg: dict, spots: Spots, pushed: list[dict]) -> list[dict]:
    """The pushed package first, then the packages that help both sides, `TRADE_ROWS` rows in all unless the push
    needs more. No "reach" row: an offer only he would decline is not a thing to do today, and printing one anyway
    taught the card to nag about packages nobody takes."""
    worth = pushed + [t for t in lg["trades"] if sendable(t) and t not in pushed][:max(TRADE_ROWS - len(pushed), 0)]
    return [_trade_item(t, spots, bool(t.get("pushed") or t.get("must_try")), lg) for t in worth]


def _ledger(lg: dict) -> Spots:
    """The league's roster-spot ledger before any row has spent a spot. Players an injury row already moves (to IR,
    out in a trade) are not drop candidates for anyone else."""
    reserved = {r["name"] for r in lg.get("injuries") or [] if r["verdict"] in ("ir", "trade")}
    n_open = lg["open_spots"] if lg.get("open_spots") is not None else len(lg.get("open_spot_adds") or [])
    return Spots(lg, n_open, reserved)


def todos(lg: dict) -> list[dict]:
    """The literal things to do today in one league, in the order Dustin clicks through ESPN: answer an offer, the
    roster moves (IR, pickups with their drop, the drops with their add), then the lineup once the roster is what it
    will be, then trades to go send. Each item: {id, kind, label, text, moves?}. kind ∈ trade_in | lineup | injury |
    waiver | waiver_up | stream | cover | sent | trade | hold. Holds come last and the renderers print them as a
    footnote, not a step. Shared by both renderers.

    The lineup comes after the adds because an add changes it: the card used to say "set it this way" and then,
    two rows down, "add X and start him at WR", so the lineup was set twice and the first setting was wrong the
    moment the add went through. Now the pickup's start is a move on the lineup row, conditional on the add.

    This is only the order and the push selection: each row kind has its own builder above, and the builders share
    one ledger (`ff.ledger.Spots`) so an activation and a pickup never spend the same drop.

    `id` is stable for a given packet, so reads.json can rule on one row by name instead of arguing with the
    whole card in prose. Player names make the ids readable; `_unique_ids` keeps them unique within a league
    (two packages for the same player read `trade:<get>-for-<give>`, an Open-spot row is `open:<name>`).
    """
    out = _offer_items(lg)
    spots = _ledger(lg)
    inj = _injury_items(lg, spots)  # counts each drop and add on the ledger as its row prints it (a folded Drop row's add never does)
    # A Drop row spends its spot on its own add, so he is not the drop for a pickup too (the card once cut one body
    # for two adds). An activation may still have folded him in above; that spot is spent either way.
    spots.reserved |= {r["name"] for r in lg.get("injuries") or [] if r["verdict"] == "drop" and r.get("add")}
    out += [i for i in inj if i["verdict"] in ("ir", "activate")]
    added = {r["add"]["name"] for r in lg.get("injuries") or [] if r.get("add")}
    wv = _waiver_items(lg, spots, added)
    out += wv
    out += _stream_items(lg)
    out += _cover_items(lg, spots, added)
    out += [i for i in inj if i["verdict"] == "drop"]
    out += _open_spot_items(lg, spots, added)
    out += [_lineup_item(lg, [i["pickup"] for i in wv if i.get("pickup")])]
    out += _sent_items(lg, spots)
    out += _trade_items(lg, spots, _pushed(lg))
    out += [i for i in inj if i["verdict"] == "trade"]
    out += [i for i in inj if i["kind"] == "hold"]
    return _unique_ids(out)


def _unique_ids(out: list[dict]) -> list[dict]:
    """One id per row, in place. `trade:<get>` says nothing about what I send, so two packages for the same player
    would share it and a `skip` in reads.json would strike both; those grow `-for-<give>`. Anything else still shared
    (two bodies whose names slug alike) gets a counter. A lone row keeps its short id, which is what reads.json and the
    rulings memory already use. Injury rows name the trade rows that ship the player by id, so they follow the rename.
    """
    shared = {k for k, n in Counter(x["id"] for x in out).items() if n > 1}
    for x in out:
        if x["id"] in shared and x["kind"] == "trade":
            x["id"] = f"{x['id']}-for-{slug('-'.join(x['give'])) or 'nothing'}"
    seen: Counter = Counter()
    for x in out:
        seen[x["id"]] += 1
        if seen[x["id"]] > 1:
            x["id"] = f"{x['id']}-{seen[x['id']]}"
    by_get: dict[str, list[str]] = {}
    for x in out:
        if x["kind"] == "trade":
            by_get.setdefault(f"trade:{slug('-'.join(x['get']))}", []).append(x["id"])
    for x in out:
        if x.get("trade_ids"):
            x["trade_ids"] = [i for tid in x["trade_ids"] for i in by_get.get(tid) or [tid]]
    return out


RULINGS = ("do", "skip", "amend", "push")  # push: do, and keep it at the top every morning until sent or skipped


def apply_reads(items: list[dict], r: dict | None) -> list[dict]:
    """Merge Claude's per-row verdicts from reads.json into the checklist.

    The card is the model's math and the read is judgment, and they are allowed to disagree — but the email has to
    end with one answer per row, not a card saying do it and a paragraph underneath saying don't. `skip` strikes the
    row and prints the reason on it; `amend` keeps it with the correction attached.
    """
    ruling = (r or {}).get("items") or {}
    for it in items:
        v = ruling.get(it["id"])
        if isinstance(v, str):
            v = {"verdict": v}
        v = v or {}
        it["ruling"] = (v.get("verdict") or "").lower() or None
        it["ruling_note"] = v.get("note")
    # Trades that ship the same player are alternatives, not a to-do list, and the card has to say so. Computed
    # after the rulings because a skipped row is no longer an alternative to anything: if two of three are struck,
    # the survivor is just the move.
    committed: dict[str, None] = {}
    for it in items:
        if it["kind"] != "trade" or it.get("ruling") == "skip":
            continue
        dup = next((g for g in it.get("give") or [] if g in committed), None)
        if dup:
            it["warn"] = [*it.get("warn", []), f"ships the same {dup} as the offer above, so these are alternatives, pick one"]
        committed.update(dict.fromkeys(it.get("give") or []))
    return items


# Sit risk worth a line in the card. An untouched Questionable sits at exactly 0.30, so anything at or below that is
# a designation rather than a decision, and listing them all just reprints the injury report beside the checklist.
# Above it means the research (or a bye) actually moved the number.
SIT_RISK_SHOWN = 0.35


def why_parts(lg: dict) -> list[str]:
    """Short reasons behind the checklist: the starters actually in doubt, and the game script when it moved a slot."""
    lw = lg["lineup_win"]
    starters = {n for names in lw["slots"].values() for n in names}
    flagged = [p for p in lg["roster"] if p["name"] in starters and not p.get("locked") and (p["p_zero"] >= SIT_RISK_SHOWN or p["bye"])]
    flagged.sort(key=lambda p: -p["p_zero"])
    why = []
    if flagged:
        why.append("; ".join(f"{p['name']} " + ("on bye" if p["bye"] else f"{p['p_zero']:.0%} to sit") for p in flagged[:2]))
    ph = (lg.get("week_state") or {}).get("phase", "pre")
    # P(win) is already a badge at the top of the card. The game script only earns a line when it actually changed
    # something: that is exactly when the P(win) lineup differs from the plain highest-points one.
    if lg.get("lineup_diff") and lw.get("p_win") is not None and ph != "final":
        why.append(game_script(lw["p_win"]) + ", so this is not the highest-points lineup" + win_gain_note(lw))
    return why


def game_script(p_win: float) -> str:
    return "underdog, favor upside" if p_win < 0.42 else ("favorite, play it safe" if p_win > 0.58 else "coin flip")


def win_gain_note(lw: dict) -> str:
    """The P(win) the recommended lineup buys over the highest-points one, as ' (+2.1 pp P(win))'; empty when the
    packet has no number (an older packet, or no opponent)."""
    g = lw.get("win_gain")
    return f" (+{g * 100:.1f} pp P(win))" if g else ""


def watchlist(packet: dict) -> list[dict]:
    """Cross-league injury watch, one row per player instead of one per (player, league).

    Only two kinds of row survive: someone in this week's lineup, and someone the research actually turned up
    something on. A bare designation on a bench player is the injury report, not a briefing.
    """
    starters = {lg["name"]: {n for ns in lg["lineup_win"]["slots"].values() for n in ns} for lg in packet["leagues"]}
    rows: dict[str, dict] = {}
    for w in packet["shared"]["injury_watchlist"]:
        if w.get("locked"):
            continue  # already played this week; nothing to decide
        starting = w["name"] in starters.get(w["league"], set())
        if not starting and not w.get("override_note"):
            continue
        r = rows.get(w["name"])
        if r is None:
            r = rows[w["name"]] = {**w, "leagues": []}
            r["starting"] = False
        r["leagues"].append(w["league"])
        r["starting"] = r["starting"] or starting
        r["override_note"] = r.get("override_note") or w.get("override_note")
        r["p_zero"] = max(r.get("p_zero") or 0.0, w.get("p_zero") or 0.0)
    return sorted(rows.values(), key=lambda r: (not r["starting"], -(r["p_zero"] or 0.0), r["name"]))


def injured_list(packet: dict) -> list[dict]:
    """Cross-league return timelines, one row per player, longest absence first."""
    rows: dict[str, dict] = {}
    for w in packet["shared"].get("injured") or []:
        r = rows.get(w["name"])
        if r is None:
            r = rows[w["name"]] = {**w, "leagues": []}
        r["leagues"].append(w["league"])
        r["override_note"] = r.get("override_note") or w.get("override_note")
        if (w.get("weeks_out") or 0) > (r.get("weeks_out") or 0):
            r["weeks_out"], r["return_week"] = w["weeks_out"], w.get("return_week")  # the longer absence sets the date
    return sorted(rows.values(), key=lambda r: (-(r["weeks_out"] or 0), r["name"]))


def watch_line(w: dict) -> str:
    """One watchlist entry with the override note (Claude's research) if there is one."""
    s = f"{w['name']} {w['status'].title()} ({', '.join(w.get('leagues') or [w['league']])})"
    return s + (f" — {w['override_note']}" if w.get("override_note") else "")


def injured_line(w: dict) -> str:
    when = "the season" if w.get("return_week") is None and (w.get("weeks_out") or 0) >= 4 else f"~{w['weeks_out']:.0f} wk{'s' if w['weeks_out'] >= 1.5 else ''}"
    back = f", back wk {w['return_week']}" if w.get("return_week") else ""
    s = f"{w['name']} out {when}{back} ({', '.join(w.get('leagues') or [w['league']])})"
    return s + (f" — {w['override_note']}" if w.get("override_note") else "")


AI_TELLS = [
    ("—", "em dash; use a comma or a period"),
    (r"\bnot (?:just |only |merely )?\w[\w' ]{0,30}, but\b", "'not X, but Y' reframe"),
    (r"\b(worth noting|that said|ultimately|it's important to|in today's|game-changer|leverage|robust|seamless|delve)\b", "stock AI phrase"),
    (r"\b(projection|projected|model|points per week|ppw|Claude)\b", "paste message mentions the model or numbers"),
]


def voice_lint(reads: dict) -> list[str]:
    """Flag the well-known AI-writing tells in Claude's prose so the routine can rewrite before sending.
    The last pattern only applies to the paste message (a real person receives it)."""
    import re
    out = []
    for lg, r in reads.items():
        for field in ("read", "paste", "reply"):
            txt = (r or {}).get(field) or ""
            if not txt:
                continue
            for pat, why in AI_TELLS[:3] + (AI_TELLS[3:] if field in ("paste", "reply") else []):
                if re.search(pat, txt, flags=re.I):
                    out.append(f"{lg}.{field}: {why}")
            if field in ("paste", "reply") and len(txt) > 280:
                out.append(f"{lg}.{field}: too long for a chat message ({len(txt)} chars)")
    return out


def hold_line(x: dict) -> str:
    """A hurt player kept on the bench: not a step, a footnote under the list, with Claude's note if there is one."""
    line = x["text"]
    if x.get("ruling") == "skip":
        line = f"~~{line}~~"
    if x.get("ruling_note"):
        line += f" ({x['ruling_note']})"
    return line


def item_line(x: dict) -> str:
    """One checklist row for the markdown body, with Claude's ruling folded into it rather than argued below it."""
    label, text = x["label"], x["text"]
    if x.get("ruling") == "skip":
        label, text = f"~~{label}~~", f"~~{text}~~"
    line = f"**{label}:** {text}"
    if x.get("warn") and x.get("ruling") != "skip":
        line += f" — {'; '.join(x['warn'])}"
    if x.get("ruling_note"):
        line += f" — **{'skip' if x.get('ruling') == 'skip' else 'note'}:** {x['ruling_note']}"
    return line


def read_lint(packet: dict, reads: dict | None) -> list[str]:
    """Check reads.json against the checklist it is ruling on: typo'd ids silently do nothing, and a trade row with
    no ruling is the old failure mode — three offers on the card and a paragraph that only picks one."""
    reads = reads or {}
    out = []
    ids_by_league = {lg["name"]: {x["id"]: x for x in todos(lg)} for lg in packet["leagues"]}
    for name in reads:
        if name not in ids_by_league:
            out.append(f"{name}: no league by that name in the packet ({', '.join(ids_by_league) or 'none'})")
    for name, known in ids_by_league.items():
        ruled = (reads.get(name) or {}).get("items") or {}
        for key, v in ruled.items():
            if key not in known:
                out.append(f"{name}.items['{key}']: no such row; ids are {', '.join(known)}")
                continue
            verdict = (v if isinstance(v, str) else (v or {}).get("verdict") or "").lower()
            if verdict not in RULINGS:
                out.append(f"{name}.items['{key}']: verdict must be one of {', '.join(RULINGS)}, got '{verdict}'")
            elif verdict != "do" and not (isinstance(v, dict) and v.get("note")):
                out.append(f"{name}.items['{key}']: '{verdict}' needs a note saying why")
            elif verdict == "push" and known[key]["kind"] != "trade":
                out.append(f"{name}.items['{key}']: 'push' is for trade rows only")
        # A paste that tells him he is "set at QB" when the row says the wire starts for him at QB after the trade
        # is the 2026-10-07 failure: the prose contradicted the math on a fact the rival knows better than anyone.
        paste = ((reads.get(name) or {}).get("paste") or "").lower()
        for key, x in known.items():
            if x["kind"] == "trade" and paste and ruled.get(key) is not None and (ruled[key] if isinstance(ruled[key], str) else (ruled[key] or {}).get("verdict", "")).lower() != "skip":
                for pos in x.get("thin_after") or []:
                    if any(phrase in paste for phrase in (f"set at {pos.lower()}", f"deep at {pos.lower()}", f"{pos.lower()} depth", f"spare {pos.lower()}")):
                        out.append(f"{name}.paste: says he is set at {pos}, but the row says the wire starts for him at {pos} after the trade")
        for key, x in known.items():
            if x["kind"] == "trade" and key not in ruled:
                out.append(f"{name}.items['{key}']: trade row has no ruling (do / skip / amend)")
            if x["kind"] == "injury" and x.get("verdict") in INJURY_NEEDS_RULING and key not in ruled:
                out.append(f"{name}.items['{key}']: {x['verdict']} verdict on a hurt player has no ruling (do / skip / amend)")
    return out


def action_card(packet: dict, reads: dict | None = None, show_ids: bool | None = None) -> str:
    """Checklist-first: literal things to do today per league, then a one-line why and Claude's read.

    `show_ids` prints each row's reads.json key. It defaults to on exactly when there are no reads yet, which is the
    draft the routine reads before writing its rulings; the copy that reaches Dustin never carries them.
    """
    L = [f"# FF briefing — {packet['generated'][:10]}", ""]
    sh = packet["shared"]
    show_ids = (not reads) if show_ids is None else show_ids
    reads = reads or {}
    for lg in packet["leagues"]:
        me = lg["odds"].get(str(lg["my_team_id"])) or {}
        opp = lg["opponent"]; lw = lg["lineup_win"]
        pw = f"{lw['p_win']:.0%}" if lw.get("p_win") is not None else "?"
        final = (lg.get("week_state") or {}).get("phase") == "final"
        L.append(f"## {lg['league_name']}")
        L.append(f"_{lg['my_record']} · vs {opp.get('name')}{'' if final else ' · win ' + pw} · playoffs {me.get('playoff_pct', '?')}% · title {me.get('title_pct', '?')}%_")
        pl = phase_line(lg)
        if pl:
            L.append(f"**{pl}**")
        L.append("")
        r = reads.get(lg["name"]) or {}
        rows = apply_reads(todos(lg), r)
        for x in (x for x in rows if x["kind"] != "hold"):
            L.append(f"- {item_line(x)}" + (f"  `[{x['id']}]`" if show_ids else ""))
        holds = [x for x in rows if x["kind"] == "hold"]
        if holds:
            L.append("")
            L.append("**Holding:** " + "; ".join(hold_line(x) + (f"  `[{x['id']}]`" if show_ids else "") for x in holds))
        L.append("")
        why = why_parts(lg)
        if why:
            L.append("_Why: " + " · ".join(why) + "_")
        if r.get("read"):
            L.append(f"**Claude's read:** {r['read']}")
        if r.get("paste"):
            L.append(f"**Paste to {r.get('paste_to') or 'the rival'}:** \"{r['paste']}\"")
        if r.get("reply"):
            L.append(f"**Reply to {r.get('reply_to') or 'the offer'}:** \"{r['reply']}\"")
        L.append("")
    watch = watchlist(packet)
    if watch:
        L.append("**Watch:** " + "; ".join(watch_line(w) for w in watch))
    hurt = injured_list(packet)
    if hurt:
        L.append("**Out:** " + "; ".join(injured_line(w) for w in hurt))
    if sh["exposure"]:
        L.append("**Exposure:** " + ", ".join(f"{k} ({len(v)}x)" for k, v in sh["exposure"].items()))
    return "\n".join(line if (not line or line.startswith("#") or line.startswith("- ")) else line + "  " for line in L)


def offer_rows(lg: dict, r: dict | None) -> list[dict]:
    """The incoming-offer rows of one league with Claude's rulings folded in, in `incoming_trades` order: the same
    `offer:` ids and `skip` / `amend` / `do` mechanics as the briefing card, so the alert email ends with one answer per
    offer instead of a verdict badge and a read underneath arguing with it."""
    return [x for x in apply_reads(todos(lg), r) if x["kind"] == "trade_in"]


def offer_card(packet: dict, reads: dict | None = None, show_ids: bool | None = None) -> str:
    """Just the incoming offers (for the alert email): verdict line, why, and Claude's reply, per league with an offer.
    `show_ids` prints each row's reads.json key, on by default exactly when there are no reads yet (the draft the
    trade-offer routine reads before ruling)."""
    show_ids = (not reads) if show_ids is None else show_ids
    reads = reads or {}
    L = [f"# FF trade offer — {packet['generated'][:10]}", ""]
    for lg in packet["leagues"]:
        if not lg.get("incoming_trades"):
            continue
        L.append(f"## {lg['league_name']}")
        L.append("")
        r = reads.get(lg["name"]) or {}
        for t, x in zip(lg["incoming_trades"], offer_rows(lg, r)):
            why = f". {'; '.join(t['why']) or 'even swap on paper'}"
            x = {**x, "label": VERDICT_WORD[t["verdict"]], "text": x["text"] + why}
            L.append(f"- {item_line(x)}" + (f"  `[{x['id']}]`" if show_ids else ""))
        L.append("")
        if r.get("read"):
            L.append(f"**Claude's read:** {r['read']}")
        if r.get("reply"):
            L.append(f"**Reply to {r.get('reply_to') or 'the offer'}:** \"{r['reply']}\"")
        L.append("")
    if len(L) == 2:
        L.append("No incoming offers pending.")
    return "\n".join(line if (not line or line.startswith("#") or line.startswith("- ")) else line + "  " for line in L)


def render(packet: dict, detail: bool = True, reads: dict | None = None, only_incoming: bool = False) -> str:
    """Action card first; full tables after a divider (omit with detail=False). only_incoming: just the offer card."""
    if only_incoming:
        return offer_card(packet, reads)
    head = action_card(packet, reads)
    if not detail:
        return head
    return head + "\n\n---\n\n_Full detail below._\n\n" + render_detail(packet)


def render_detail(packet: dict) -> str:
    """The full tables after the card: the shared blocks, then one section per league."""
    L = _detail_shared(packet)
    for lg in packet["leagues"]:
        L += _detail_matchup(lg) + _detail_roster(lg) + _detail_trades(lg) + _detail_odds(lg)
    return "\n".join(L)


def _detail_shared(packet: dict) -> list[str]:
    L = []
    sh = packet["shared"]
    L.append(f"# Detail — {packet['generated'][:10]}\n")
    if sh["injury_watchlist"]:
        L.append("## Injury watchlist (ambiguous designations on my rosters)")
        for w in sh["injury_watchlist"]:
            risk = "already played this week" if w.get("locked") else f"{w['p_zero']:.0%} chance to sit"
            L.append(f"- **{w['name']}** ({w['pos']}, {w['team']}) — {w['status']} in *{w['league']}*; {risk}" + (f"; {w['notes']}" if w.get("notes") else "") + (f"; {w['override_note']}" if w.get("override_note") else ""))
        L.append("")
    if sh["exposure"]:
        L.append("## Exposure (held in 2+ leagues)")
        L.append(", ".join(f"{k} ({', '.join(v)})" for k, v in sh["exposure"].items()))
        L.append("")
    if sh.get("usage_error"):
        L.append(f"> usage metrics unavailable: {sh['usage_error']}\n")
    if sh.get("pending_trades_error"):
        L.append(f"> could not read pending trades: {sh['pending_trades_error']}\n")
    return L


def playoff_weight_note(lg: dict) -> str:
    """" · playoff weeks weighted ×1.4" from the packet's `playoff_weight` (`season.week_weight`), or nothing when
    no playoff week is left or the weight is a plain 1."""
    pw = lg.get("playoff_weight") or {}
    w = pw.get("weight")
    if w is None or not pw.get("weeks") or abs(w - 1.0) < 0.05:
        return ""
    wks = pw["weeks"]
    span = f"week {wks[0]}" if len(wks) == 1 else f"weeks {wks[0]}-{wks[-1]}"
    return f" · playoff {span} weighted ×{w:.1f}"


def clocks_line(lg: dict) -> str:
    """" · waivers run Wed 1:00 AM · a trade executes 24h after acceptance": the league's clocks from the deadlines
    block, for the header of both renderers. Empty when the packet predates the block."""
    dl = lg.get("deadlines") or {}
    parts = []
    if dl.get("waiver_run_word"):
        parts.append(f"waivers run {dl['waiver_run_word']}")
    if dl.get("trade_review_hours") is not None:
        parts.append(f"a trade executes {dl['trade_review_hours']}h after acceptance")
    return "".join(f" · {p}" for p in parts)


def _detail_matchup(lg: dict) -> list[str]:
    """The league header, the odds line, the matchup and the recommended lineup."""
    L = [f"\n---\n# {lg['league_name']} (`{lg['name']}`) — Week {lg['week']}"]
    acq = f"FAAB left ${lg['faab_remaining']}" if lg.get("faab_remaining") is not None else f"waiver priority #{lg.get('waiver_rank')}"
    L.append(f"Record {lg['my_record']} · {acq} · weeks remaining {lg['weeks_remaining']}{clocks_line(lg)}  ")
    me = lg["odds"].get(str(lg["my_team_id"])) or {}
    if me:
        L.append(f"Playoff odds **{me['playoff_pct']}%** · title odds **{me['title_pct']}%** · expected wins {me['exp_wins']}{playoff_weight_note(lg)}")
    opp = lg["opponent"]
    if opp.get("name"):
        theirs = "their set lineup" if opp.get("lineup") == "set" else "their proj"
        L.append(f"\n## Matchup vs {opp['name']} ({theirs} {opp['mu']} ± {opp['sd']})")
    lw, le = lg["lineup_win"], lg["lineup_ev"]
    L.append(f"Recommended lineup (max P(win){' = ' + str(lw['p_win']) if lw.get('p_win') is not None else ''}), proj {lw['mu']} ± {lw['sd']}; current ESPN lineup proj {lg['current_lineup_mu']}")
    L.append("")
    by_name = {p["name"]: p for p in lg["roster"]}
    for slot, names in lw["slots"].items():
        L.append(f"- {slot}: " + ", ".join(n + (f" ({game_note(by_name[n])})" if n in by_name and game_note(by_name[n]) else "") for n in names))
    if lg["lineup_diff"]:
        L.append(f"Differs from the pure-points lineup ({le['mu']}){win_gain_note(lw)}: " + "; ".join(f"{d['name']} in {d['in']} lineup ({d['ev']} ± {d['sd']})" for d in lg["lineup_diff"]))
    L.append("")
    L.append(f"Bench: {', '.join(lw['bench'])}")
    return L


def _detail_roster(lg: dict) -> list[str]:
    """The roster table, the waiver table and the handcuffs."""
    L = ["\n## My roster (this week μ · ROS/g · game · usage · flags)", "| player | pos | wk μ | ROS/g | game | usage | flags |", "|---|---|---|---|---|---|---|"]
    for p in lg["roster"]:
        L.append(f"| {p['name']} | {p['pos']} | {p['mu']} | {p['mu_ros']} | {game_note(p)} | {usage_note(p)} | {_flags(p)} |")

    L.append("\n## Waivers")
    if lg["waivers"]:
        faab = lg.get("faab_remaining") is not None
        L.append("| target | pos | wk μ | ROS/g | vs my starter | " + ("bid | " if faab else "") + "why |")
        L.append("|---|---|---|---|---|" + ("---|" if faab else "") + "---|")
        for w in lg["waivers"][:6]:
            d = (f"+{w['delta_over_starter']:.1f} at {w['slot']}" if w['delta_over_starter'] > 0 else f"depth ({w['delta_over_starter']:.1f} vs {w['slot']})") if w['slot'] else "depth"
            L.append(f"| {w['name']} | {w['pos']} | {w['mu_week']} | {w['mu_ros']} | {d} | " + (f"${w['bid']} | " if faab else "") + f"{'; '.join(w['why'][:4])} |")
    else:
        L.append("Nothing worth a claim.")
    if lg["handcuffs"]:
        L.append("")
        L.append("Handcuffs: " + "; ".join(f"{h['handcuff']} for {h['starter']} ({'FA' if h['owner_team_id'] is None else 'owned'})" for h in lg["handcuffs"]))
    return L


def _detail_trades(lg: dict) -> list[str]:
    """Incoming offers, my open offers and the scan's candidates."""
    L = ["\n## Incoming offers"]
    if lg.get("incoming_trades"):
        for t in lg["incoming_trades"]:
            mk = f" · market {t['market_get']} for {t['market_give']}" if t.get("market_give") and t.get("market_get") else ""
            L.append(f"- **{VERDICT_WORD[t['verdict']]}** — {incoming_line(t)}{mk}. {'; '.join(t['why']) or 'even swap on paper'}")
    else:
        L.append("None pending.")
    if lg.get("outgoing_trades"):
        L.append("Your open offers: " + "; ".join(
            f"{', '.join(t['give'])} for {', '.join(t['get'])} with {t['rival']} (accepted{processes_note(t)})" if t.get("accepted")
            else f"{', '.join(t['give'])} for {', '.join(t['get'])} to {t['rival']}" for t in lg["outgoing_trades"]))

    L.append("\n## Trade candidates")
    if lg["trades"]:
        for t in lg["trades"][:3]:
            td = f" · title odds me {t.get('my_title_delta', '?'):+} / them {t.get('their_title_delta', '?'):+}" if "my_title_delta" in t else ""
            pa = f" · p(accept) {t['p_accept']:.2f} ({t.get('accept_word') or 'not a row'}: {'; '.join(t.get('accept_why') or [])})" if t.get("p_accept") is not None else ""
            after = f" · {t['after_line']}" if t.get("after_line") else ""
            L.append(f"- **Give {', '.join(t['give'])} → get {', '.join(t['get'])}** from {t['rival']}: me {t['my_delta_ppw']:+.1f} ppw, them {t['their_delta_ppw']:+.1f} ppw{td}{pa}{after}. {'; '.join(t['why'])}")
    else:
        L.append("No mutually beneficial package found this week.")
    return L


def _detail_odds(lg: dict) -> list[str]:
    L = ["\n## League odds", "| team | record | playoff % | title % | exp wins |", "|---|---|---|---|---|"]
    for _tid, o in sorted(lg["odds"].items(), key=lambda kv: -kv[1]["title_pct"]):
        L.append(f"| {'**' if o['is_me'] else ''}{o['name']}{'**' if o['is_me'] else ''} | {o['record']} | {o['playoff_pct']} | {o['title_pct']} | {o['exp_wins']} |")
    L.append("\nRival needs: " + "; ".join(f"{lg['odds'].get(tid, {}).get('name', tid)}: " + ", ".join(f"{pos} {v}" for pos, v in n.items() if v) for tid, n in lg["rival_needs"].items()))
    return L
