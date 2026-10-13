"""Parse ESPN's raw mPendingTransactions shape (captured 2026-09-16) into give/get from my point of view."""
from ff.sources.espn import pending_trades

RAW = {"pendingTransactions": [
    {"bidAmount": 0, "executionType": "EXECUTE", "expirationDate": 1789696354383, "id": "b1a22a82", "isPending": True,
     "items": [
         {"fromLineupSlotId": 20, "fromTeamId": 4, "playerId": 4241985, "toLineupSlotId": -1, "toTeamId": 16, "type": "TRADE"},
         {"fromLineupSlotId": 6, "fromTeamId": 4, "playerId": 3040151, "toLineupSlotId": -1, "toTeamId": 16, "type": "TRADE"},
         {"fromLineupSlotId": 6, "fromTeamId": 16, "playerId": 4361307, "toLineupSlotId": -1, "toTeamId": 4, "type": "TRADE"}],
     "proposedDate": 1789523554358, "scoringPeriodId": 2, "status": "PENDING", "teamActions": {"4": "ACCEPTED"}, "teamId": 4, "type": "TRADE_PROPOSAL"},
    # a proposal between two other teams: ignored
    {"id": "other", "items": [{"fromTeamId": 1, "toTeamId": 2, "playerId": 1, "type": "TRADE"}], "teamId": 1, "type": "TRADE_PROPOSAL", "status": "PENDING"},
    # a pending waiver claim: ignored
    {"id": "w", "items": [{"fromTeamId": -1, "toTeamId": 16, "playerId": 5, "type": "ADD"}], "teamId": 16, "type": "WAIVER", "status": "PENDING"},
]}


class FakeReq:
    def league_get(self, params=None, headers=None):
        assert params == {"view": "mPendingTransactions"}
        return RAW


class FakeLeague:
    espn_request = FakeReq()


def test_incoming_from_my_side():
    out = pending_trades(FakeLeague(), 16)
    assert len(out) == 1
    t = out[0]
    assert t["direction"] == "incoming" and t["rival_team_id"] == 4 and t["proposer_team_id"] == 4
    assert t["get"] == [4241985, 3040151] and t["give"] == [4361307]
    assert t["proposed_ts"] == 1789523554358 and t["expires_ts"] == 1789696354383 and t["id"] == "b1a22a82"


def test_outgoing_from_proposer_side():
    t = pending_trades(FakeLeague(), 4)[0]
    assert t["direction"] == "outgoing" and t["rival_team_id"] == 16
    assert t["give"] == [4241985, 3040151] and t["get"] == [4361307]


def test_empty_and_unknown_team():
    class Empty:
        class espn_request:
            @staticmethod
            def league_get(params=None, headers=None):
                return {"status": {}}
    assert pending_trades(Empty(), 16) == []
    assert pending_trades(FakeLeague(), None) == []


def test_position_limits_come_from_default_position_ids_and_skip_unlimited():
    from ff.sources.espn import position_limits
    raw = {"positionLimits": {"0": 0, "1": 4, "2": 6, "3": 6, "4": 3, "5": 3, "6": -1, "16": 3, "17": -1}}
    assert position_limits(raw) == {"QB": 4, "RB": 6, "WR": 6, "TE": 3, "K": 3, "D/ST": 3}
    assert position_limits({}) == {}


# ---- the history view: how ESPN closed a proposal (captured 2026-10-08, ids shortened, player ids redacted) ----
# The proposal itself stays PENDING in mTransactions2 forever; the terminal state is a separate record pointing back
# at it through relatedTransactionId. A decline is a TRADE_DECLINE from the rival plus a CANCELED copy of the proposal
# carrying his member id (same millisecond). An expiry is a CANCELED copy whose member id is no team's owner (ESPN's
# job, 51 s after expirationDate). A withdrawal by me (never observed live: this code never writes) is the same copy
# with my member id and no decline.
ME, RIVAL9, RIVAL3 = "{ME-SWID}", "{OWNER-OF-9}", "{OWNER-OF-3}"
HISTORY = {"transactions": [
    {"id": "9d6cd924", "type": "TRADE_PROPOSAL", "executionType": "EXECUTE", "status": "PENDING", "teamId": 3, "memberId": ME,
     "proposedDate": 1791295014000, "expirationDate": 1791467814032, "isPending": True,
     "items": [{"fromTeamId": 3, "toTeamId": 9, "playerId": 1, "type": "TRADE"}, {"fromTeamId": 9, "toTeamId": 3, "playerId": 2, "type": "TRADE"}]},
    {"id": "8194e95a", "type": "TRADE_DECLINE", "executionType": "EXECUTE", "status": "EXECUTED", "teamId": 9, "memberId": RIVAL9,
     "proposedDate": 1791312512869, "relatedTransactionId": "9d6cd924", "isPending": False, "items": []},
    {"id": "108212c1", "type": "TRADE_PROPOSAL", "executionType": "CANCEL", "status": "CANCELED", "teamId": 3, "memberId": RIVAL9,
     "proposedDate": 1791312512869, "expirationDate": 1791467814032, "relatedTransactionId": "9d6cd924", "isPending": True,
     "items": [{"fromTeamId": 3, "toTeamId": 9, "playerId": 1, "type": "TRADE"}, {"fromTeamId": 9, "toTeamId": 3, "playerId": 2, "type": "TRADE"}]},
    # lapsed: ESPN's job cancels it under a member id that owns no team
    {"id": "106016a8", "type": "TRADE_PROPOSAL", "executionType": "EXECUTE", "status": "PENDING", "teamId": 3, "memberId": ME,
     "proposedDate": 1791295221749, "expirationDate": 1791468021772, "isPending": True,
     "items": [{"fromTeamId": 3, "toTeamId": 4, "playerId": 3, "type": "TRADE"}]},
    {"id": "bf550d96", "type": "TRADE_PROPOSAL", "executionType": "CANCEL", "status": "CANCELED", "teamId": 3, "memberId": "{NOBODYS-OWNER}",
     "proposedDate": 1791468072581, "expirationDate": 1791468021772, "relatedTransactionId": "106016a8", "isPending": True,
     "items": [{"fromTeamId": 3, "toTeamId": 4, "playerId": 3, "type": "TRADE"}]},
    # withdrawn in the app by me: the cancel copy carries my member id and there is no TRADE_DECLINE
    {"id": "0270d39d", "type": "TRADE_PROPOSAL", "executionType": "EXECUTE", "status": "PENDING", "teamId": 3, "memberId": ME,
     "proposedDate": 1791295357661, "expirationDate": 1791468157691, "isPending": True,
     "items": [{"fromTeamId": 3, "toTeamId": 5, "playerId": 4, "type": "TRADE"}]},
    {"id": "451b9f04", "type": "TRADE_PROPOSAL", "executionType": "CANCEL", "status": "CANCELED", "teamId": 3, "memberId": ME,
     "proposedDate": 1791295571923, "expirationDate": 1791468157691, "relatedTransactionId": "0270d39d", "isPending": True,
     "items": [{"fromTeamId": 3, "toTeamId": 5, "playerId": 4, "type": "TRADE"}]},
    # the decline record can come after the cancel copy in the list; it still wins
    {"id": "c1", "type": "TRADE_PROPOSAL", "executionType": "CANCEL", "status": "CANCELED", "teamId": 3, "memberId": RIVAL3,
     "proposedDate": 1791300000000, "relatedTransactionId": "p1", "isPending": True, "items": []},
    {"id": "d1", "type": "TRADE_DECLINE", "executionType": "EXECUTE", "status": "EXECUTED", "teamId": 5, "memberId": RIVAL3,
     "proposedDate": 1791300000000, "relatedTransactionId": "p1", "isPending": False, "items": []},
    # still open, and noise: a waiver claim, a roster move
    {"id": "37d622ff", "type": "TRADE_PROPOSAL", "executionType": "EXECUTE", "status": "PENDING", "teamId": 3, "memberId": ME, "items": []},
    {"id": "w", "type": "WAIVER", "status": "EXECUTED", "teamId": 3, "memberId": ME, "items": [{"type": "ADD", "playerId": 7}]},
    {"id": "r", "type": "ROSTER", "status": "EXECUTED", "teamId": 9, "memberId": RIVAL9, "items": []},
]}


class _Team:
    def __init__(self, team_id, owners):
        self.team_id, self.owners = team_id, owners


class HistoryLeague:
    teams = [_Team(3, [{"id": ME, "displayName": "me"}]), _Team(9, [{"id": RIVAL9}]), _Team(5, [{"id": RIVAL3}]), _Team(4, [])]

    class espn_request:
        @staticmethod
        def league_get(params=None, headers=None):
            assert params == {"view": "mTransactions2"}
            return HISTORY


def test_the_history_view_says_who_closed_each_proposal_and_how():
    from ff.sources.espn import trade_resolutions
    res = trade_resolutions(HistoryLeague(), 3)
    assert res["9d6cd924"] == {"status": "declined", "by": 9, "ts": 1791312512869}
    assert res["106016a8"] == {"status": "expired", "by": None, "ts": 1791468072581}
    assert res["0270d39d"] == {"status": "withdrawn", "by": 3, "ts": 1791295571923}
    assert res["p1"]["status"] == "declined"
    assert "37d622ff" not in res and "w" not in res and "r" not in res
    assert trade_resolutions(HistoryLeague(), None) == {}


# ---- an accepted trade in its review period (captured 2026-10-13: the Hubbard trade, accepted Tuesday 11:50 AM Denver,
# processing Wednesday 8:05 AM). mPendingTransactions keeps the record under the same id with its type turned to
# TRADE_ACCEPT, both teams ACCEPTED and processDate set; it used to be dropped as not-a-proposal, so the offer log
# closed the deal as his decline while the ESPN app said "Accepted".
ACCEPTED_RAW = {"pendingTransactions": [
    {"id": "92378dd4", "type": "TRADE_ACCEPT", "status": "PENDING", "executionType": "EXECUTE", "teamId": 3, "memberId": ME,
     "proposedDate": 1791824686897, "expirationDate": 1791997486924, "processDate": 1791986732413, "relatedTransactionId": None,
     "scoringPeriodId": 5, "teamActions": {"3": "ACCEPTED", "4": "ACCEPTED"}, "isPending": True,
     "items": [{"type": "TRADE", "playerId": 1, "fromTeamId": 3, "toTeamId": 4}, {"type": "TRADE", "playerId": 2, "fromTeamId": 3, "toTeamId": 4},
               {"type": "TRADE", "playerId": 3, "fromTeamId": 3, "toTeamId": 4}, {"type": "TRADE", "playerId": 4, "fromTeamId": 4, "toTeamId": 3}]},
]}


class AcceptedLeague:
    class espn_request:
        @staticmethod
        def league_get(params=None, headers=None):
            return ACCEPTED_RAW


def test_an_accepted_trade_in_its_review_period_is_still_pending_and_says_so():
    out = pending_trades(AcceptedLeague(), 3)
    assert len(out) == 1
    t = out[0]
    assert t["accepted"] is True and t["process_ts"] == 1791986732413 and t["status"] == "PENDING"
    assert t["direction"] == "outgoing" and t["rival_team_id"] == 4 and t["give"] == [1, 2, 3] and t["get"] == [4]
    # the rival's side of the same record: incoming, accepted, same clock
    r = pending_trades(AcceptedLeague(), 4)[0]
    assert r["direction"] == "incoming" and r["accepted"] is True and r["process_ts"] == 1791986732413
    # a plain proposal carries no process clock
    assert pending_trades(FakeLeague(), 16)[0]["accepted"] is False and pending_trades(FakeLeague(), 16)[0]["process_ts"] is None


def test_the_history_view_reads_his_acceptance_and_it_beats_a_cancel_copy():
    """mTransactions2 2026-10-13: a TRADE_ACCEPT under the rival's member id, relatedTransactionId the proposal, its
    items the drop his roster needed. A cancel copy under his id in the same view (ESPN writes one on some paths)
    must not turn the acceptance into a decline."""
    from ff.sources.espn import trade_resolutions
    hist = {"transactions": [
        {"id": "f65a9f1f", "type": "TRADE_ACCEPT", "status": None, "executionType": "EXECUTE", "teamId": 9, "memberId": RIVAL9,
         "proposedDate": 1791900332381, "relatedTransactionId": "92378dd4", "isPending": False,
         "items": [{"type": "DROP", "playerId": -16006, "fromTeamId": 9, "toTeamId": 0}]},
        {"id": "c9", "type": "TRADE_PROPOSAL", "executionType": "CANCEL", "status": "CANCELED", "teamId": 3, "memberId": RIVAL9,
         "proposedDate": 1791900332381, "relatedTransactionId": "92378dd4", "isPending": True, "items": []},
        {"id": "d9", "type": "TRADE_DECLINE", "executionType": "EXECUTE", "status": "EXECUTED", "teamId": 9, "memberId": RIVAL9,
         "proposedDate": 1791900332381, "relatedTransactionId": "92378dd4", "isPending": False, "items": []},
    ]}

    class L(HistoryLeague):
        class espn_request:
            @staticmethod
            def league_get(params=None, headers=None):
                return hist
    assert trade_resolutions(L(), 3)["92378dd4"] == {"status": "accepted", "by": 9, "ts": 1791900332381}


def test_the_pending_view_carries_espns_own_waiver_run_and_the_history_the_last_drop_of_each_player():
    """2026-10-13: every pending claim carried processDate = Wednesday 03:00 ET, the run itself. The drops in the
    history are the clock a dropped player's waiver period runs from (a cancelled claim's drop never happened)."""
    from ff.sources.espn import recent_drops, waiver_run_ts
    pend = {"pendingTransactions": [
        {"id": "w1", "type": "WAIVER", "status": "PENDING", "processDate": 1792047600000, "proposedDate": 1791983040000, "items": [{"type": "ADD", "playerId": 1}]},
        {"id": "w2", "type": "WAIVER", "status": "PENDING", "processDate": 1792134000000, "items": []},
        {"id": "t", "type": "TRADE_PROPOSAL", "status": "PENDING", "processDate": 1791986732413, "items": []},
    ]}
    hist = {"transactions": [
        {"id": "r1", "type": "ROSTER", "status": "EXECUTED", "proposedDate": 1791906884961, "items": [{"type": "DROP", "playerId": 4429160}, {"type": "ADD", "playerId": 7}]},
        {"id": "r2", "type": "ROSTER", "status": "EXECUTED", "proposedDate": 1791800000000, "processDate": 1791810000000, "items": [{"type": "DROP", "playerId": 4429160}]},
        {"id": "ta", "type": "TRADE_ACCEPT", "status": None, "proposedDate": 1791900332381, "items": [{"type": "DROP", "playerId": -16006}]},
        {"id": "c", "type": "WAIVER", "status": "CANCELED", "executionType": "CANCEL", "proposedDate": 1791950000000, "items": [{"type": "DROP", "playerId": 5}]},
        {"id": "p", "type": "WAIVER", "status": "PENDING", "proposedDate": 1791960000000, "items": [{"type": "DROP", "playerId": 6}]},
    ]}

    class L:
        class espn_request:
            @staticmethod
            def league_get(params=None, headers=None):
                return pend if params == {"view": "mPendingTransactions"} else hist
    assert waiver_run_ts(L()) == 1792047600000
    assert recent_drops(L()) == {"4429160": 1791906884961, "-16006": 1791900332381}

    class Empty:
        class espn_request:
            @staticmethod
            def league_get(params=None, headers=None):
                return {}
    assert waiver_run_ts(Empty()) is None and recent_drops(Empty()) == {}
