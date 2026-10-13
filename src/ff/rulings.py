"""Memory for Claude's `skip` rulings on trade rows, so a package Dustin passed on does not come back every morning.

The scan re-derives the same offers daily from the same rosters; the only state it had was ESPN's pending list.
`ff render-email` records each skipped trade row here; `packet.build` drops candidates skipped in the last
`SKIP_DAYS`. The file lives under data/projlog, which the routine already pushes to the `projlog` branch and
`scripts/cloud_setup.sh` restores on the next clone. A rival roster change that makes the package different (a new
`get` set) is a new row and is not skipped.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from .config import DATA_DIR

SKIPS_PATH = DATA_DIR / "projlog" / "skipped_trades.json"
PUSHES_PATH = DATA_DIR / "projlog" / "pushed_trades.json"
SKIP_DAYS = 14


def skip_key(league: str, get: list[str]) -> str:
    return f"{league}|" + "+".join(sorted(get))


def _load(path: Path) -> dict[str, dict]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def load_skips(path: Path = SKIPS_PATH) -> dict[str, dict]:
    return _load(path)


def load_pushes(path: Path = PUSHES_PATH) -> dict[str, dict]:
    return _load(path)


def record_skips(packet: dict, reads: dict, path: Path = SKIPS_PATH, today: date | None = None) -> list[str]:
    """Write every `skip` ruling on a trade row into the file; returns the keys added."""
    from .report import apply_reads, todos  # local import: report imports nothing from here, keep it that way
    today = today or date.today()
    skips = load_skips(path)
    added = []
    for lg in packet.get("leagues") or []:
        r = (reads or {}).get(lg["name"]) or {}
        for row in apply_reads(todos(lg), r):
            if row["kind"] != "trade" or row.get("ruling") != "skip":
                continue
            key = skip_key(lg["name"], row.get("get") or [])
            skips[key] = {"date": today.isoformat(), "league": lg["name"], "rival": row.get("rival"),
                          "give": row.get("give") or [], "get": row.get("get") or [], "note": row.get("ruling_note")}
            added.append(key)
    if added:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(skips, indent=1))
    return added


def drop_recently_skipped(cands: list[dict], skips: dict[str, dict], league: str, today: date | None = None,
                          days: int = SKIP_DAYS) -> list[dict]:
    today = today or date.today()
    out = []
    for c in cands:
        s = skips.get(skip_key(league, c.get("get") or []))
        if s:
            try:
                when = date.fromisoformat(s.get("date", ""))
            except ValueError:
                when = today
            if today - when <= timedelta(days=days):
                continue
        out.append(c)
    return out


# ---------- pushes: "this one is really good, keep asking until he sends it or says no" ----------

def record_pushes(packet: dict, reads: dict, path: Path = PUSHES_PATH, today: date | None = None) -> list[str]:
    """Open, close or keep every push. A trade row is pushed when the math flags it (`must_try`) or Claude rules
    `push` on it. A push closes when the package shows up among Dustin's own pending offers (sent), or when he
    rules `skip` on it (which the skip memory also records). A closed push stays closed for SKIP_DAYS however often
    the math re-flags the package (a declined offer comes straight back from the scan); only Claude ruling `push`
    again reopens it. Returns "<key>: opened|sent|skipped" lines."""
    from .report import apply_reads, todos
    today = today or date.today()
    pushes = _load(path)
    events = []
    for lg in packet.get("leagues") or []:
        r = (reads or {}).get(lg["name"]) or {}
        sent_keys = {skip_key(lg["name"], t.get("get") or []) for t in lg.get("outgoing_trades") or []}
        for key in sent_keys:
            if pushes.get(key, {}).get("status") == "open":
                pushes[key]["status"], pushes[key]["closed"] = "sent", today.isoformat()
                events.append(f"{key}: sent")
        for row in apply_reads(todos(lg), r):
            if row["kind"] != "trade":
                continue
            key = skip_key(lg["name"], row.get("get") or [])
            cur = pushes.get(key)
            if row.get("ruling") == "skip":
                if cur and cur.get("status") == "open":
                    cur["status"], cur["closed"], cur["skip_note"] = "skipped", today.isoformat(), row.get("ruling_note")
                    events.append(f"{key}: skipped")
                continue
            if row.get("ruling") == "push" or row.get("push"):  # `push` on the row: the math flagged it, or it is remembered
                if cur and cur.get("status") in ("sent", "skipped") and row.get("ruling") != "push" \
                        and _closed_within(cur, today, SKIP_DAYS):
                    # Sent (and then declined or expired: the offer left `pending_trades` and the scan re-derived the
                    # same must_try row) or skipped with a reason: the math alone does not reopen it for SKIP_DAYS.
                    # Only Claude ruling `push` again does; the day he says so is a new ask with his note as the reason.
                    continue
                if cur and cur.get("status") == "open":
                    cur["last"] = today.isoformat()
                    if row.get("ruling") == "push" and row.get("ruling_note"):
                        cur["note"], cur["source"] = row["ruling_note"], "claude"
                    continue
                pushes[key] = {"status": "open", "since": today.isoformat(), "last": today.isoformat(), "league": lg["name"],
                               "rival": row.get("rival"), "give": row.get("give") or [], "get": row.get("get") or [],
                               "source": "claude" if row.get("ruling") == "push" else "math",
                               "note": row.get("ruling_note") if row.get("ruling") == "push" else None}
                events.append(f"{key}: opened")
    if events:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(pushes, indent=1))
    return events


def _closed_within(push: dict, today: date, days: int) -> bool:
    try:
        return today - date.fromisoformat(push.get("closed") or "") <= timedelta(days=days)
    except ValueError:
        return True  # an unreadable close date keeps it closed: the memory errs toward not nagging


def mark_pushed(cands: list[dict], pushes: dict[str, dict], league: str, today: date | None = None) -> list[dict]:
    """Attach the open push (since, days asked, note) to the scan candidates that match one. A push the scan no
    longer produces (rosters changed) simply stops showing; nothing here invents a trade."""
    today = today or date.today()
    for c in cands:
        p = pushes.get(skip_key(league, c.get("get") or []))
        if p and p.get("status") == "open":
            try:
                since = date.fromisoformat(p.get("since", ""))
            except ValueError:
                since = today
            c["pushed"] = {"since": since.isoformat(), "days": (today - since).days + 1, "note": p.get("note"), "source": p.get("source")}
    return cands


def record_rulings(packet: dict, reads: dict, skips_path: Path = SKIPS_PATH, pushes_path: Path = PUSHES_PATH,
                   today: date | None = None) -> list[str]:
    """Everything `ff render-email` remembers from one morning's reads."""
    return [f"skip {k}" for k in record_skips(packet, reads, skips_path, today)] + \
           [f"push {e}" for e in record_pushes(packet, reads, pushes_path, today)]


# ---------- outcomes: what ESPN did with the offers I actually sent ----------
# The first real acceptance data this repo has. Every offer of mine that shows in `outgoing_trades` is opened here the
# morning it is seen and closed the morning it is gone: accepted when the players I asked for are on my roster and the
# ones I offered are not; countered when he has a counter pending or a different package with him went through;
# withdrawn when a piece I offered left my roster with nothing coming back (ESPN voids the proposal, his answer was
# never given); expired when its expiry passed before the run; declined otherwise. The scan reads it for a per-rival
# prior (accepted / declined / expired: the offers he actually had in front of him until the end) and a recency factor
# (declined only). ESPN's history view (`trade_resolutions` on the league block, from mTransactions2: a TRADE_DECLINE
# from him, or a CANCEL copy of the proposal naming who cancelled it) is asked first for the three it can tell apart,
# so an offer I withdrew in the app with my roster unchanged reads withdrawn, not declined; the roster and the clock
# are the fallback when that view could not be read.

OUTCOMES_PATH = DATA_DIR / "projlog" / "offer_outcomes.json"
RECENT_DAYS = 7
CLOSED = ("accepted", "declined", "expired")   # count toward the prior


def load_outcomes(path: Path = OUTCOMES_PATH) -> dict[str, dict]:
    return _load(path)


def _expired(o: dict, now: datetime) -> bool:
    """The offer's expiry is before `now`. `expires_ts` is ESPN's clock (ms, UTC); older entries have only `expires`,
    the naive machine-local string `packet._ms_iso` wrote, compared in the same machine-local time."""
    ts = o.get("expires_ts")
    if ts:
        return ts / 1000 <= now.timestamp()
    try:
        return datetime.fromisoformat(o.get("expires") or "") <= now.astimezone().replace(tzinfo=None)
    except ValueError:
        return False


def _countered(o: dict, incoming: list[dict]) -> bool:
    """He has an offer to me pending that he proposed after mine: ESPN's counter declines the original and opens his."""
    rid = o.get("rival_team_id")
    if rid is None:
        return False
    since = o.get("proposed_ts") or 0
    return any(str(t.get("rival_team_id")) == str(rid) and (t.get("proposed_ts") or 0) >= since for t in incoming)


def record_outcomes(packet: dict, path: Path = OUTCOMES_PATH, today: date | None = None,
                    now: datetime | None = None) -> list[str]:
    """Open an entry for every offer of mine pending on ESPN; close the ones that are not pending any more.
    `now` is the run's clock (aware); `today` defaults to its local date. A league whose pending list could not be
    read closes nothing (an empty list there means nothing)."""
    if now is None:
        now = datetime.combine(today, time(12), tzinfo=UTC) if today else datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    today = today or now.astimezone().date()
    outs = load_outcomes(path)
    events = []
    for lg in packet.get("leagues") or []:
        league = lg["name"]
        pending = {str(t["id"]): t for t in lg.get("outgoing_trades") or [] if t.get("id") is not None}
        for oid, t in pending.items():
            key = f"{league}|{oid}"
            if key not in outs:
                outs[key] = {"status": "open", "league": league, "rival": t.get("rival"), "rival_team_id": t.get("rival_team_id"),
                             "give": list(t.get("give") or []), "get": list(t.get("get") or []),
                             "opened": today.isoformat(), "expires": t.get("expires_iso"),
                             "expires_ts": t.get("expires_ts"), "proposed_ts": t.get("proposed_ts")}
                events.append(f"{key}: opened")
        if lg.get("pending_trades_error"):
            continue
        roster_names = {p["name"] for p in lg.get("roster") or []}
        incoming = lg.get("incoming_trades") or []
        resolved = lg.get("trade_resolutions") or {}
        for key, o in outs.items():
            oid = key.split("|", 1)[1]
            if o.get("league") != league or o.get("status") != "open" or oid in pending:
                continue
            got = [n for n in o.get("get") or [] if n in roster_names]
            kept = [n for n in o.get("give") or [] if n in roster_names]
            espn_says = (resolved.get(oid) or {}).get("status")
            if (got and not kept) or espn_says == "accepted":
                status = "accepted"       # the players moved, or ESPN's history has his TRADE_ACCEPT (the review period ran)
            elif got or _countered(o, incoming):
                status = "countered"      # a different package with him went through, or his counter is on the table
            elif espn_says in ("declined", "withdrawn", "expired"):
                status = espn_says        # ESPN's history says who closed it (B10); his decline before the expiry is still his
            elif _expired(o, now):
                status = "expired"
            elif len(kept) < len(o.get("give") or []):
                status = "withdrawn"      # a piece I offered left my roster first; the proposal died by my hand
            else:
                status = "declined"
            o["status"], o["closed"] = status, today.isoformat()
            events.append(f"{key}: {status}")
    if events:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(outs, indent=1))
    return events


def history(outs: dict[str, dict], league: str, today: date | None = None) -> dict[int, dict]:
    """Per rival team id, what his answers so far say: `recent_decline` (a no in the last RECENT_DAYS; an expiry, a
    counter or a withdrawal is not one) and `prior`, his acceptance rate over the offers he had in front of him
    (`CLOSED`) with a half-count of smoothing, only once there are two of those."""
    today = today or date.today()
    tally: dict[int, dict] = {}
    for o in outs.values():
        if o.get("league") != league or o.get("status") not in CLOSED or o.get("rival_team_id") is None:
            continue
        rid = int(o["rival_team_id"])
        t = tally.setdefault(rid, {"n": 0, "accepted": 0, "recent_decline": False})
        t["n"] += 1
        t["accepted"] += o["status"] == "accepted"
        try:
            closed = date.fromisoformat(o.get("closed", ""))
        except ValueError:
            closed = today
        if o["status"] == "declined" and today - closed <= timedelta(days=RECENT_DAYS):
            t["recent_decline"] = True
    return {rid: {"n": t["n"], "recent_decline": t["recent_decline"],
                  "prior": round((t["accepted"] + 0.5) / (t["n"] + 1), 3) if t["n"] >= 2 else None} for rid, t in tally.items()}


# ---------- IR slot moves: who sat in the slot each morning, so a bouncing ESPN tag does not churn the bench ----------
# Jayden Daniels, Sep 30 to Oct 7: Questionable (card: activate, drop Jacobs), Out again Saturday (card: stash him, add
# McGowan), Questionable Wednesday (card: activate, drop McGowan). Three clicks, a four-day rental, nothing gained. The
# stash rule had no memory that he had just left the slot; this is that memory. `ff packet` notes each league's roster
# before the injury rule runs, so the morning he comes off IR already counts.

IR_MOVES_PATH = DATA_DIR / "projlog" / "ir_moves.json"


def load_ir_moves(path: Path = IR_MOVES_PATH) -> dict:
    d = _load(path)
    return d if "moves" in d else {"rosters": {}, "moves": {}}


def save_ir_moves(moves: dict, path: Path = IR_MOVES_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(moves, indent=1))


def note_ir_roster(moves: dict, league: str, roster: list[dict], today: date | None = None) -> list[str]:
    """Diff one league's roster (`espn_id`, `name`, `slot`) against the last run and stamp the IR moves in place.

    A player in the slot who was not: `stashed` today, with `added` = the names that joined the roster the same run
    (the body the stash made room for, whoever Dustin actually picked). A player out of the slot but still rostered:
    `left` today. The first run for a league only seeds the roster. Returns "<league>|<id>: stashed|left" lines."""
    today = today or date.today()
    prev = moves.setdefault("rosters", {}).get(league)
    cur_names = {str(p["espn_id"]): p["name"] for p in roster}
    cur_ir = {str(p["espn_id"]) for p in roster if p.get("slot") == "IR"}
    events = []
    if prev is not None:
        prev_ids, prev_ir = set(prev.get("names") or {}), set(prev.get("ir") or [])
        joined = sorted(cur_names[i] for i in cur_names if i not in prev_ids)
        for pid in sorted(cur_ir - prev_ir):
            m = moves.setdefault("moves", {}).setdefault(f"{league}|{pid}", {})
            m.update({"name": cur_names[pid], "league": league, "stashed": today.isoformat(), "added": joined})
            events.append(f"{league}|{pid}: stashed")
        for pid in sorted(prev_ir - cur_ir):
            if pid in cur_names:  # still mine, just not in the slot: an activation, not a drop
                m = moves.setdefault("moves", {}).setdefault(f"{league}|{pid}", {})
                m.update({"name": cur_names[pid], "league": league, "left": today.isoformat()})
                events.append(f"{league}|{pid}: left")
    moves["rosters"][league] = {"date": today.isoformat(), "names": cur_names, "ir": sorted(cur_ir)}
    return events


def ir_memory(moves: dict, league: str, today: date | None = None) -> dict[int, dict]:
    """What the injury rule needs per player: `left_days` since he last came off IR (None if never), the date he
    was last `stashed` and the names `added` that morning."""
    today = today or date.today()
    out: dict[int, dict] = {}
    for key, m in (moves.get("moves") or {}).items():
        lg, _, pid = key.partition("|")
        if lg != league or not pid.isdigit():
            continue
        left_days = None
        if m.get("left"):
            try:
                left_days = (today - date.fromisoformat(m["left"])).days
            except ValueError:
                left_days = None
        out[int(pid)] = {"left_days": left_days, "stashed": m.get("stashed"), "added": list(m.get("added") or [])}
    return out
