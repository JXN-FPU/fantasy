"""DecisionPacket -> phone-first HTML email.

Rendered straight from the packet (no markdown round-trip) so lists, tables and spacing are deterministic. Constraints:
Gmail strips <style>, so everything is inline; table-based layout; cellpadding/bgcolor/align attributes instead of per-cell
styles to keep the document small enough to paste as a tool argument (well under 20 KB); no images or web fonts; colors chosen to
survive Gmail's dark-mode inversion (no pure-black text, badges are tinted backgrounds with dark text).

Claude's prose comes in via `reads`: {"<league name>": {"read": "...", "paste": "...", "paste_to": "...",
"reply": "...", "reply_to": "..."}} (`reply` answers an incoming offer).
"""
from __future__ import annotations

import re
from datetime import datetime
from html import escape as _e

from . import report

# palette: (background, text)
TONES = {
    "good": ("#d7f5e0", "#0b6b3a"),
    "warn": ("#fff1c2", "#7a5400"),
    "bad": ("#fde2e2", "#9b1c1c"),
    "info": ("#e6ecff", "#2b3f9e"),
    "move": ("#eee4ff", "#5b2ea6"),
    "grey": ("#e9ecef", "#4b5563"),
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
TEXT, MUTED, BORDER, PAGE, CARD, ZEBRA = "#1f2933", "#6b7280", "#e3e6ea", "#eef0f3", "#ffffff", "#f6f7f9"
LABELS = {"waiver": ("WAIVER", "info"), "waiver_up": ("WAIVER", "info"), "stream": ("STREAM", "info"),
          "lineup": ("LINEUP", "move"), "trade": ("TRADE", "good"), "trade_in": ("OFFER", "warn"),
          "sent": ("SENT", "grey"), "cover": ("COVER", "bad"), "injury": ("HURT", "warn"), "hold": ("HOLD", "warn")}
INJURY_BADGE = {"ir": ("IR", "info"), "hold": ("HOLD", "warn"), "drop": ("DROP", "bad"), "trade": ("TRADE", "warn"), "activate": ("ACTIVATE", "info")}
VERDICT_TONE = {"accept": "good", "decline": "bad", "counter": "warn"}


def _badge(text: str, tone: str = "grey") -> str:
    bg, fg = TONES[tone]
    return f'<span style="padding:1px 7px;border-radius:9px;white-space:nowrap;font-size:11px;font-weight:600;background:{bg};color:{fg}">{_e(text)}</span>'


def _muted(text: str, size: int = 13) -> str:
    return f'<div style="color:{MUTED};font-size:{size}px;margin-top:4px">{text}</div>'


def _h(text: str, size: int = 15) -> str:
    return f'<div style="font-size:{size}px;font-weight:700;margin:14px 0 6px">{_e(text)}</div>'


def _table(headers: list[str], rows: list[list[str]], align: str = "", size: int = 13, hilite: int | None = None) -> str:
    """Compact table: cellpadding + bgcolor zebra, per-cell markup is just <td> or <td align=right>.
    `align` is one char per column: 'l' or 'r'. `hilite` = row index to tint."""
    align = align or "l" * len(headers)
    th = "".join(f'<th align="{"right" if a == "r" else "left"}" style="color:{MUTED};font-weight:600;font-size:11px;'
                 f'text-transform:uppercase;border-bottom:1px solid {BORDER}">{_e(h)}</th>' for h, a in zip(headers, align))
    body = []
    for i, r in enumerate(rows):
        bg = TONES["warn"][0] if i == hilite else (ZEBRA if i % 2 else CARD)
        body.append(f'<tr bgcolor="{bg}">' + "".join(f'<td{" align=right" if a == "r" else ""}>{c}</td>' for c, a in zip(r, align)) + "</tr>")
    return (f'<table width="100%" cellpadding="5" cellspacing="0" border="0" style="border-collapse:collapse;font-size:{size}px;margin:6px 0">'
            f"<tr>{th}</tr>{''.join(body)}</table>")


def _card(title: str, sub: str, body: str, accent: str = BORDER) -> str:
    return (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 14px"><tr><td bgcolor="{CARD}" '
            f'style="border:1px solid {BORDER};border-top:4px solid {accent};border-radius:8px;padding:14px 16px">'
            f'<div style="font-size:18px;font-weight:700">{_e(title)}</div>{sub}{body}</td></tr></table>')


def _callout(label: str, text: str, tone: str = "info", quote: bool = False) -> str:
    bg, fg = TONES[tone]
    inner = f'<div style="font-size:14px;margin-top:3px{";font-style:italic" if quote else ""}">{_e(text)}</div>'
    return (f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:10px 0 0"><tr><td bgcolor="{bg}" '
            f'style="border-left:4px solid {fg};border-radius:0 6px 6px 0;padding:8px 12px">'
            f'<div style="font-size:11px;font-weight:700;color:{fg};text-transform:uppercase">{_e(label)}</div>{inner}</td></tr></table>')


# ---------- player status wording (model jargon -> plain words) ----------

def _status(p: dict) -> tuple[str, str] | None:
    """(label, tone) for a player's availability, or None when healthy."""
    s = p["sources"]
    st = (s.get("espn_status") or "").upper()
    sl = (s.get("sleeper_status") or "").upper()
    if p.get("bye"):
        return "BYE", "grey"
    if "OUT" in (st, sl) or st in ("INJURY_RESERVE", "IR", "SUSPENSION") or p["p_zero"] >= 0.95:
        return "OUT", "bad"
    if "DOUBTFUL" in (st, sl) or p["p_zero"] >= 0.6:
        return "DOUBTFUL", "warn"
    if "QUESTIONABLE" in (st, sl) or st == "DAY_TO_DAY" or p["p_zero"] >= 0.15:
        return "QUESTIONABLE", "warn"
    if sl in ("NA", "PUP", "SUS"):
        return sl, "grey"
    return None


SHORT = {"QUESTIONABLE": "Q", "DOUBTFUL": "D"}


def _status_cell(p: dict) -> str:
    bits = []
    st = _status(p)
    if p.get("locked"):
        bits.append(f'<span style="color:{MUTED};font-size:11px">played</span>')
    elif st:
        bits.append(_badge(SHORT.get(st[0], st[0]), st[1]))
    g = p["sources"].get("grade")
    if g:
        tone = "good" if g.startswith("A") else ("info" if g.startswith("B") else ("warn" if g.startswith("C") else "grey"))
        bits.append(_badge(g, tone))
    notes = []
    if (p.get("weeks_out") or 0) >= 1:
        notes.append(f"back wk {p['return_week']}" if p.get("return_week") else "out for the season")
    if p["sources"].get("sleeper_notes") and st:
        notes.append(str(p["sources"]["sleeper_notes"]))
    if p["sources"].get("override_note"):
        notes.append(str(p["sources"]["override_note"]))
    elif any(f.startswith("llm:") for f in p.get("flags", [])):
        notes.append("adjusted from news")
    out = " ".join(bits)
    if notes:
        out += f'<div style="color:{MUTED};font-size:11px">{_e("; ".join(notes))}</div>'
    return out


def _why_clean(why: list[str]) -> str:
    """Waiver 'why' bullets without the fp:/llm: tokens (the grade shows as a badge instead)."""
    return " · ".join(w for w in why if not (w.startswith("fp:") or w.startswith("llm:")))


def _pct(x) -> str:
    return "?" if x is None else f"{x:.0f}%"


# ---------- sections ----------

def _stat_row(lg: dict) -> str:
    me = lg["odds"].get(str(lg["my_team_id"])) or {}
    lw = lg["lineup_win"]; opp = lg["opponent"]
    pw = lw.get("p_win")
    tone = "grey" if pw is None else ("good" if pw > 0.58 else ("warn" if pw < 0.42 else "grey"))
    final = (lg.get("week_state") or {}).get("phase") == "final"
    cells = [
        f"<b>{_e(lg['my_record'])}</b>",
        f"vs {_e(opp.get('name') or 'TBD')}",
        *([] if final else [f"Win {_badge(_pct(pw * 100 if pw is not None else None), tone)}"]),
        # Title odds are two decimal places of noise in September and they are in the detail tables either way.
        f"Playoffs <b>{_pct(me.get('playoff_pct'))}</b>",
        # "playoff weeks 15-17 weighted ×1.4": the weight the season pricing gives those weeks (`season.week_weight`)
        *([note.strip(" ·")] if (note := report.playoff_weight_note(lg)) else []),
    ]
    return f'<div style="color:{MUTED};font-size:13px;margin-top:3px">{" &nbsp;·&nbsp; ".join(cells)}</div>'


def _todo_rows(lg: dict, r: dict) -> tuple[str, bool]:
    """The checklist. Returns (html, paste_attached): Claude's paste message rides on the trade row it belongs to,
    so three trade ideas can never leave you guessing which one the message is for."""
    paste, paste_to = r.get("paste"), r.get("paste_to") or ""
    attached = False
    rows, holds = [], []
    for t in report.apply_reads(report.todos(lg), r):
        if t["kind"] == "hold":
            holds.append(t)
            continue
        label, tone = LABELS[t["kind"]]
        if t["kind"] == "trade":
            tone = "good" if t.get("worth") else "grey"
            label = "TRADE" if t.get("worth") else "TRADE (reach)"
            if t.get("push"):
                label, tone = "TRADE (DO IT)", "warn"
        if t["kind"] == "trade_in":
            tone = VERDICT_TONE[t["verdict"]]
            label = report.VERDICT_WORD[t["verdict"]] + " OFFER"
        if t["kind"] == "injury":
            label, tone = INJURY_BADGE[t["verdict"]]
        skip = t.get("ruling") == "skip"
        if skip:
            label, tone = "SKIP", "grey"
        if t["kind"] == "lineup" and t.get("moves"):
            body = "<b>In this order:</b><br>" + "<br>".join(
                _e(m).replace(" → ", f' <span style="color:{MUTED}">→</span> ') for m in t["moves"])
        else:
            body = _e(t["text"])
        if skip:
            body = f'<span style="text-decoration:line-through;color:{MUTED}">{body}</span>'
        elif t.get("warn"):
            body += f'<div style="color:{TONES["bad"][1]};font-size:11px;margin-top:2px">{_e("; ".join(t["warn"]))}</div>'
        if t.get("ruling_note"):
            fg = MUTED if skip else TONES["warn"][1]
            body += f'<div style="color:{fg};font-size:12px;margin-top:3px">{_e(t["ruling_note"])}</div>'
        if t["kind"] == "trade" and not skip and paste and not attached and paste_to in (t.get("rival") or "", ""):
            body += _callout(f"Paste to {t.get('rival') or paste_to or 'the rival'}", paste, "good", quote=True)
            attached = True
        step = f'<span style="display:inline-block;min-width:14px;color:{MUTED};font-size:12px;font-weight:700">{len(rows) + 1}</span> '
        rows.append(f'<tr><td valign="top" width="1" style="padding:6px 8px 6px 0;white-space:nowrap">{step}{_badge(label, tone)}</td>'
                    f'<td valign="top" style="padding:6px 0;font-size:14px;border-bottom:1px solid {BORDER}">{body}</td></tr>')
    html = f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px">{"".join(rows)}</table>'
    if holds:
        # Not steps: the hurt players staying put, one muted line under the list so the sequence above stays clean.
        html += _muted("Holding: " + "; ".join(_hold(h) for h in holds))
    return html, attached


def _hold(h: dict) -> str:
    """One hold in the footnote. A skip is the only ruling a hold can carry, and `~~` is how `report.hold_line` marks it;
    here the strike is the same line-through the checklist rows use, with Claude's note after it, not stripped (B8)."""
    text = _e(h["text"])
    if h.get("ruling") == "skip":
        text = f'<span style="text-decoration:line-through">{text}</span>'
    if h.get("ruling_note"):
        text += f" ({_e(h['ruling_note'])})"
    return text


def _phase(lg: dict) -> str:
    pl = report.phase_line(lg)
    if not pl:
        return ""
    ph = lg["week_state"]["phase"]
    tone = "grey" if ph == "final" else "warn"
    return f'<div style="margin-top:6px">{_badge("FINAL" if ph == "final" else "IN PROGRESS", tone)} <span style="font-size:13px">{_e(pl.split(": ", 1)[1])}</span></div>'


def _league_action(lg: dict, r: dict) -> str:
    rows, pasted = _todo_rows(lg, r)
    body = _phase(lg) + rows
    why = report.why_parts(lg)
    if why:
        body += _muted("Why: " + _e(" · ".join(why)))
    if r.get("read"):
        body += _callout("Claude's read", r["read"], "info")
    if r.get("paste") and not pasted:
        body += _callout(f"Paste to {r.get('paste_to') or 'the rival'}", r["paste"], "good", quote=True)
    if r.get("reply"):
        body += _callout(f"Reply to {r.get('reply_to') or 'the offer'}", r["reply"], "warn", quote=True)
    pw = lg["lineup_win"].get("p_win")
    accent = TONES["good"][1] if pw is not None and pw > 0.58 else (TONES["warn"][1] if pw is not None and pw < 0.42 else "#94a3b8")
    return _card(lg["league_name"], _stat_row(lg), body, accent)


def _shared(packet: dict) -> str:
    sh = packet["shared"]
    parts = []
    watch = report.watchlist(packet)
    if watch:
        items = []
        for w in watch:
            tone = "bad" if w["status"] == "OUT" else "warn"
            where = ", ".join(w.get("leagues") or [w["league"]])
            s = (f"{_badge(w['status'], tone)} <b>{_e(w['name'])}</b> "
                 f"<span style='color:{MUTED}'>({_e(w['pos'])}, {_e(where)}{'' if w['starting'] else ', bench'})</span>")
            if w.get("override_note"):
                s += f" — {_e(w['override_note'])}"
            items.append(s)
        parts.append(_h("Watch") + "<div style='font-size:13px;line-height:1.7'>" + "<br>".join(items) + "</div>")
    hurt = report.injured_list(packet)
    if hurt:
        items = []
        for w in hurt:
            where = ", ".join(w.get("leagues") or [w["league"]])
            when = "SEASON" if w.get("return_week") is None and (w.get("weeks_out") or 0) >= 4 else f"~{w['weeks_out']:.0f} WK"
            s = f"{_badge(when, 'bad')} <b>{_e(w['name'])}</b> <span style='color:{MUTED}'>({_e(w['pos'])}, {_e(where)}" \
                + (f", back wk {w['return_week']}" if w.get("return_week") else "") + ")</span>"
            if w.get("override_note"):
                s += f" — {_e(w['override_note'])}"
            items.append(s)
        parts.append(_h("Out") + "<div style='font-size:13px;line-height:1.7'>" + "<br>".join(items) + "</div>")
    if sh["exposure"]:
        parts.append(_h("Exposure") + _muted(", ".join(f"<b>{_e(k)}</b> ({', '.join(map(_e, v))})" for k, v in sh["exposure"].items())))
    if not parts:
        return ""
    return _card("Across leagues", "", "".join(parts), "#94a3b8")


def _lineup_table(lg: dict) -> str:
    by_name = {p["name"]: p for p in lg["roster"]}
    rows = []
    for slot, names in lg["lineup_win"]["slots"].items():
        cells = []
        for n in names:
            if n.startswith("("):
                cells.append(_badge("EMPTY", "bad") + " " + _e(n))
                continue
            p = by_name.get(n)
            st = _status(p) if p else None
            if p and p.get("locked"):
                cells.append(_e(n) + f" <span style='color:{MUTED}'>{p.get('actual') or 0:.1f} pts, played</span>")
                continue
            game = f" · {_e(report.game_note(p))}" if p and report.game_note(p) else ""
            cells.append(_e(n) + (f" {_badge(SHORT.get(st[0], st[0]), st[1])}" if st else "") + (f" <span style='color:{MUTED}'>{p['mu']:.1f}{game}</span>" if p else ""))
        rows.append([f"<b>{_e(slot)}</b>", ", ".join(cells)])
    return _table(["Slot", "Start"], rows, "ll")


def _player_cell(p: dict) -> str:
    """The name, and under it the game and the usage figure when the packet has them (D7): one muted line, not two
    more columns, so the table still fits a phone."""
    sub = " · ".join(x for x in (report.game_note(p), report.usage_note(p)) if x)
    return _e(p["name"]) + (f"<div style='color:{MUTED};font-size:11px'>{_e(sub)}</div>" if sub else "")


def _offers_table(lg: dict, rulings: list[dict] | None = None) -> str:
    """Incoming offers with a verdict badge, then my own open offers, one row each. `rulings` (from `report.offer_rows`,
    one per incoming offer in order) folds Claude's ruling in: a `skip` strikes the verdict and the note says what to
    do instead, an `amend` keeps it with the note attached."""
    rows = []
    for i, t in enumerate(lg.get("incoming_trades") or []):
        x = (rulings or [])[i] if rulings and i < len(rulings) else {}
        skip = x.get("ruling") == "skip"
        mk = f" · market {t['market_get']} for {t['market_give']}" if t.get("market_give") and t.get("market_get") else ""
        td = f" · title odds you {t['my_title_delta']:+.1f} / them {t['their_title_delta']:+.1f}" if t.get("my_title_delta") is not None else ""
        left = f" · expires in {t['hours_left']:.0f}h" if t.get("hours_left") is not None else ""
        body = (f"{_e(t['rival'] or 'Someone')} gives <b>{_e(', '.join(t['get']) or 'nothing')}</b> for your <b>{_e(', '.join(t['give']) or 'nothing')}</b>"
                f"<div style='color:{MUTED};font-size:11px'>you {t['my_delta_ppw']:+.1f} ppw / them {t['their_delta_ppw']:+.1f}{td}{mk}{left} · {_e('; '.join(t['why']) or 'even swap on paper')}</div>")
        if skip:
            body = f'<span style="text-decoration:line-through;color:{MUTED}">{body}</span>'
        if x.get("ruling_note"):
            body += f'<div style="color:{MUTED if skip else TONES["warn"][1]};font-size:12px;margin-top:3px">{_e(x["ruling_note"])}</div>'
        rows.append([_badge("SKIP" if skip else report.VERDICT_WORD[t["verdict"]], "grey" if skip else VERDICT_TONE[t["verdict"]]), body])
    for t in lg.get("outgoing_trades") or []:
        if t.get("accepted"):
            rows.append([_badge("ACCEPTED", "good"), f"Your <b>{_e(', '.join(t['give']))}</b> for <b>{_e(', '.join(t['get']))}</b> with "
                                                     f"{_e(t['rival'] or '?')}, accepted{_e(report.processes_note(t))}"])
            continue
        rows.append([_badge("SENT", "grey"), f"Your <b>{_e(', '.join(t['give']))}</b> for <b>{_e(', '.join(t['get']))}</b> to {_e(t['rival'] or '?')}, waiting on them"])
    return _table(["", "Offer"], rows, "ll", 13)


def _league_detail(lg: dict) -> str:
    lw, opp = lg["lineup_win"], lg["opponent"]
    me = lg["odds"].get(str(lg["my_team_id"])) or {}
    acq = f"FAAB left ${lg['faab_remaining']}" if lg.get("faab_remaining") is not None else f"waiver priority #{lg.get('waiver_rank')}"
    sub = _muted(f"Week {lg['week']} · {_e(acq)} · {lg['weeks_remaining']} weeks left · expected wins {me.get('exp_wins', '?')}")
    body = _h("Matchup")
    pw = f" · P(win) <b>{lw['p_win']:.0%}</b>" if lw.get("p_win") is not None else ""
    body += _muted(f"You <b>{lw['mu']:.1f}</b> ± {lw['sd']:.0f} vs {_e(opp.get('name') or 'TBD')} <b>{opp.get('mu', '?')}</b> ± {opp.get('sd', '?')}{pw}"
                   f" · current ESPN lineup {lg['current_lineup_mu']}")
    body += _lineup_table(lg)
    if lg["lineup_diff"]:
        body += _muted(f"Pure-points lineup differs{_e(report.win_gain_note(lw))}: " + _e("; ".join(f"{d['name']} in {d['in']} lineup ({d['ev']} ± {d['sd']})" for d in lg["lineup_diff"])), 12)
    body += _muted("Bench: " + _e(", ".join(lw["bench"])), 12)

    body += _h("Roster") + _muted("Wk = projected points this week · ROS = rest-of-season per game · Q/D = questionable/doubtful · "
                                  "under the name: opponent, Vegas implied total, kickoff day, usage (target share, carries/g) or the 30-day market move", 11)
    rows = [[_player_cell(p), _e(p["pos"]), f"{p['mu']:.1f}", f"{p['mu_ros']:.1f}", _status_cell(p)] for p in lg["roster"]]
    body += _table(["Player", "Pos", "Wk", "ROS", "Status"], rows, "llrrl", 12)

    body += _h("Waivers")
    if lg["waivers"]:
        faab = lg.get("faab_remaining") is not None
        rows = []
        for w in lg["waivers"][:6]:
            d = (f"+{w['delta_over_starter']:.1f} at {w['slot']}" if w["delta_over_starter"] > 0 else f"{w['delta_over_starter']:.1f} vs {w['slot']}") if w["slot"] else "depth"
            name = f"<b>{_e(w['name'])}</b>" + (f"<div style='color:{MUTED};font-size:11px'>{_e(_why_clean(w['why']))}</div>" if _why_clean(w["why"]) else "")
            r = [name, _e(w["pos"]), f"{w['mu_week']:.1f}", f"{w['mu_ros']:.1f}", _e(d)]
            if faab:
                r.append(f"${w['bid']}")
            rows.append(r)
        body += _table(["Target", "Pos", "Wk", "ROS", "vs starter"] + (["Bid"] if faab else []), rows, "llrrl" + ("r" if faab else ""), 12)
    else:
        body += _muted("Nothing worth a claim.")
    if lg["handcuffs"]:
        body += _muted("Handcuffs: " + _e("; ".join(f"{h['handcuff']} for {h['starter']} ({'FA' if h['owner_team_id'] is None else 'owned'})" for h in lg["handcuffs"])), 12)

    if lg.get("incoming_trades") or lg.get("outgoing_trades"):
        body += _h("Offers on the table") + _offers_table(lg)

    body += _h("Trades")
    if lg["trades"]:
        rows = []
        for t in lg["trades"][:3]:
            worth = t.get("sendable", t["their_delta_ppw"] >= 0)
            td = f" · title odds you {t['my_title_delta']:+.1f} / them {t['their_title_delta']:+.1f}" if "my_title_delta" in t else ""
            odds = report.accept_note(t).replace(", ", " · ", 1).replace(", p(accept)", " · p(accept)")
            after = f" · {t['after_line']}" if t.get("after_line") else ""
            rows.append([_badge("SEND" if worth else "REACH", "good" if worth else "grey"),
                         f"Give <b>{_e(', '.join(t['give']))}</b> → get <b>{_e(', '.join(t['get']))}</b> from {_e(t['rival'])}"
                         f"<div style='color:{MUTED};font-size:11px'>you {t['my_delta_ppw']:+.1f} ppw / them {t['their_delta_ppw']:+.1f}{td}{odds}{after} · {_e('; '.join(t['why']))}</div>"])
        body += _table(["", "Offer"], rows, "ll", 13)
    else:
        body += _muted("No mutually beneficial package found this week.")

    body += _h("League odds")
    ranked = sorted(lg["odds"].values(), key=lambda o: -o["title_pct"])
    show = [o for i, o in enumerate(ranked) if i < 5 or o["is_me"]]
    hil = next((i for i, o in enumerate(show) if o["is_me"]), None)
    rows = [[(f"<b>{_e(o['name'])}</b>" if o["is_me"] else _e(o["name"])), _e(o["record"]), f"{o['playoff_pct']:.0f}%", f"{o['title_pct']:.0f}%", f"{o['exp_wins']:.1f}"] for o in show]
    body += _table(["Team", "Rec", "Playoffs", "Title", "Exp W"], rows, "llrrr", 12, hil)
    if len(ranked) > len(show):
        body += _muted(f"…and {len(ranked) - len(show)} more (full table in the plain-text version)", 11)
    needs = [(lg["odds"].get(tid, {}).get("name", tid), [pos for pos, v in n.items() if v]) for tid, n in lg["rival_needs"].items()]
    needs = [(nm, ps) for nm, ps in needs if ps][:6]
    if needs:
        body += _muted("Rival needs: " + " · ".join(f"<b>{_e(nm)}</b> {_e(', '.join(ps))}" for nm, ps in needs), 12)
    return _card(lg["league_name"], sub, body, "#94a3b8")


def render_offer_email(packet: dict, reads: dict | None = None) -> str:
    """Short alert email: one card per league with an incoming offer (verdict table + Claude's read + reply)."""
    reads = reads or {}
    cards = []
    for lg in packet["leagues"]:
        if not lg.get("incoming_trades"):
            continue
        r = reads.get(lg["name"]) or {}
        body = _offers_table({**lg, "outgoing_trades": []}, report.offer_rows(lg, r))
        if r.get("read"):
            body += _callout("Claude's read", r["read"], "info")
        if r.get("reply"):
            body += _callout(f"Reply to {r.get('reply_to') or 'the offer'}", r["reply"], "warn", quote=True)
        t0 = lg["incoming_trades"][0]
        cards.append(_card(lg["league_name"], _muted(f"From {_e(t0['rival'] or '?')} · week {lg['week']}"), body, TONES[VERDICT_TONE[t0["verdict"]]][1]))
    if not cards:
        cards.append(_card("No incoming offers", "", _muted("Nothing pending on ESPN right now."), "#94a3b8"))
    head = (f'<div style="font-size:22px;font-weight:800;margin:4px 0 2px">FF trade offer</div>'
            f'<div style="color:{MUTED};font-size:13px;margin-bottom:14px">{_e(packet["generated"][:16].replace("T", " "))} · you accept or decline in the ESPN app</div>')
    return _page(head + "".join(cards))


def _wrap(html: str) -> str:
    """Newline after every closing </tr>, </table> and </div> so the file can be read (and re-typed) in chunks
    without a chunk boundary ever splitting a tag. HTML ignores the whitespace."""
    return re.sub(r"(</(?:tr|table|div)>)", r"\1\n", html)


def _page(inner: str) -> str:
    return _wrap(
        '<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark"></head>'
        f'<body style="margin:0;padding:0;background:{PAGE}">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAGE}"><tr><td align="center" style="padding:12px 8px">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;font-family:{FONT};color:{TEXT};line-height:1.45">'
        f"<tr><td>{inner}</td></tr></table></td></tr></table></body></html>"
    )


def render_email(packet: dict, reads: dict | None = None, only_incoming: bool = False, full: bool = False) -> str:
    """Action cards only by default (small enough to transcribe into a mail tool); `full` appends the detail appendix,
    which otherwise lives only in the plain-text/markdown part (`report.render`)."""
    reads = reads or {}
    if only_incoming:
        return render_offer_email(packet, reads)
    try:
        gen = datetime.fromisoformat(packet["generated"])
        when = gen.strftime("%a %b %-d")
    except ValueError:
        gen, when = None, packet["generated"][:10]
    week = packet["leagues"][0]["week"] if packet["leagues"] else "?"
    head = (f'<div style="font-size:22px;font-weight:800;margin:4px 0 2px">FF briefing</div>'
            f'<div style="color:{MUTED};font-size:13px;margin-bottom:14px">Week {week} · {_e(when)}</div>')
    actions = "".join(_league_action(lg, reads.get(lg["name"]) or {}) for lg in packet["leagues"])
    shared = _shared(packet)
    body = head + actions + shared
    if full:
        body += (f'<div style="margin:22px 0 12px;border-top:2px dashed #c4c9d0"></div>'
                 f'<div style="font-size:12px;font-weight:700;color:{MUTED};text-transform:uppercase;margin-bottom:10px">Full detail</div>')
        body += "".join(_league_detail(lg) for lg in packet["leagues"])
    else:
        body += _muted("Every roster, waiver and odds table is in the plain-text version of this email.", 11)
    if packet["shared"].get("usage_error"):
        body += _muted("Usage metrics unavailable this run.", 11)
    if packet["shared"].get("pending_trades_error"):
        body += _muted("Could not read pending trades from ESPN this run; check the app for offers.", 11)
    body += _muted(f"Generated {_e(packet['generated'][:16].replace('T', ' '))} · numbers from ff, read from Claude · read-only against ESPN", 11)
    return _page(body)
