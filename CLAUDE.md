# ff — fantasy football co-manager

Three ESPN redraft leagues (`leagues.toml`, untracked; copy from `leagues.example.toml`). **Read-only against ESPN**: never propose or build lineup/waiver/trade
writes or ESPN chat posting. Dustin taps the buttons.

## Boundary rule
Code owns state and math. Claude owns judgment over unstructured text and prose.
Claude returns *parameters and prose*, never *decisions or state*. Every number in the briefing comes from `ff`.

## Morning routine (`/briefing` skill encodes this)
1. `uv run ff doctor` — stop and tell Dustin if cookies are dead.
2. `uv run ff sync && uv run ff packet` → `data/packets/<date>.json`.
3. Read the packet. `shared.injury_watchlist` lists players with ambiguous designations across all my rosters;
   `shared.injured` lists the ones out a week or more (or on IR) whose return timeline needs a number.
   WebSearch each one (beat writers, practice reports). Also skim for narrative shifts on my players and top waiver targets
   (committee → bellcow, QB change, coach comments).
4. Write `overrides.json`: `{"<espn_id>": {"p_zero": 0.15, "mu_mult": 1.1, "weeks_out": 4, "ros_mult": 0.9, "note": "..."}}` only where
   news changes the picture. `p_zero`/`mu_mult` are this week; `weeks_out` (games, or `"season"`) and `ros_mult` are the rest of the
   season and decide the hold / IR / drop / trade row for a hurt player. The `note` is shown next to the player in the email.
5. `uv run ff briefing --overrides overrides.json` → markdown + packet path. Then write `reads.json`
   (`{"L1": {"read": "...", "items": {"<row id>": {"verdict": "do|skip|amend", "note": "..."}}, "paste": "...", "paste_to": "...",
   "reply": "...", "reply_to": "..."}}`): rule on rows in `items` (the ids print in backticks at the end of each briefing.md
   checklist line) rather than arguing with the card in prose, every `trade` row needs a verdict, and `read` carries only
   what the research adds; `reply` only when `incoming_trades` has an offer.
   Then `uv run ff render-email --packet <path> --reads reads.json --out briefing.html --md briefing.md`. Never hand-edit the HTML;
   `src/ff/email_html.py` owns the layout.
6. Email it to Dustin via the Gmail connector (subject: `FF briefing — Week N — <date>`).

Tuesday = waivers emphasis (bids due before Wednesday processing). Sunday morning = final lineup + inactives check.

## Commands
`ff setup-check | doctor | sync | roster | lineup | waivers | trades | incoming | odds | packet | briefing | render-email` (all accept `--league <name>`).
`ff incoming` = offers other managers sent me, with an accept/decline/counter verdict (`--json --new-since 40m` is the poller path).

## Layout
`src/ff/sources/*` data pulls (cached in `data/cache/`), `src/ff/model/*` math (`injuries.py` is the hold / IR / drop / trade
rule; `mu_ros` is availability-weighted, `mu_ros_active` is per game played; `season.py` prices a roster week by week with
byes, return dates and the wire as the fallback body, each week weighted by `week_weight` (1 in the regular season; in the
league's playoff weeks, my playoff odds × `TITLE_RUN_MULT`, so a 0% team weights them at zero and a lock above a regular week;
`injuries.week_weights` is the same number and `playoff_weight` in the league block is what the card prints); `acceptance.py` is the would-he-say-yes scorecard behind every trade
row, `trades.py` the scan and the hard rules), `packet.py` builds the DecisionPacket,
`ledger.py` is the roster-spot ledger behind the checklist (`Spots` hands out open spots and drops, counts bodies against
ESPN's position caps; `drop_order` / `drop_note` rank and explain a cut), `report.py` renders markdown (plain-text body)
and owns the checklist itself (one `_*_items` builder per row kind over the shared ledger, `todos` is the order, the push
selection and the ids, `apply_reads` folds Claude's per-row verdicts in, `read_lint` catches a typo'd id or an unruled trade),
`email_html.py` renders the HTML email from the packet. Game context on a row is a packet field put into words by one
`report` helper both renderers call (`game_note` reads `odds_line`, which `packet.enrich` fills with the opponent, implied total
and Eastern kickoff `day`; `usage_note` reads `usage` then `market.trend_30d`; `due_note` says "set by Thu" from a kickoff day,
on roster players' `odds_line.day` and waiver rows' `kickoff_day`; `accept_note` prints the bucket word with `p_accept`). Tests: `uv run pytest`.
`demo.py` is the synthetic league (fictional, seeded names); `scripts/screenshots.sh` regenerates `examples/` including the README images.
The Gmail doorbell is Apps Script, so pytest cannot see it: `node scripts/gmail_trade_doorbell.test.js` runs it against a stubbed
Apps Script runtime (retries, escalation, no-double-fire). Run it after editing the `.gs`, before pasting into the editor.

## Data gotchas
- nflreadpy installed from git (PyPI lags). No 2026 snap counts yet.
- FantasyPros via dynastyprocess/data mirror; do not scrape fantasypros.com or keeptradecut.com.
- Player ID joins via `db_playerids.csv` + Sleeper; check `ff doctor` for unmatched names.

## Operations (for a fresh session responding to a morning email)
Trigger ids, env id, routine URLs and the recipient address live in `ops.local.md` (untracked) and in memory; nothing personal is tracked.
- Routine "FF daily briefing": cron `CRON_TZ=America/Denver 59 5 * * *` (5:59 AM Denver year-round; it was `0 12 * * *` UTC
  until 2026-10-08, which drifts an hour at each DST change), cloud env `fantasy` (network Full, env vars
  ESPN_S2/SWID/SEASON/HEALTHCHECK_URL/BRIEFING_TO/LEAGUES_TOML), model `claude-opus-5`. `scripts/cloud_setup.sh` writes `.env` and
  `leagues.toml` from those env vars. The briefing is emailed to `$BRIEFING_TO`. A second routine, "FF Sunday lineup check"
  (`CRON_TZ=America/Denver 45 9 * * 0`, same env and prompt with a preface: inactives are out, research only my questionable
  starters, email the final lineups as `FF Sunday check — Week N — <date>`, skip waivers, trades and the MLB step 0), runs
  after the 11:30 ET inactives. The routines UI stores a plain cron in UTC; a timezone needs the **Custom** trigger with the
  `CRON_TZ=` prefix typed in, which the UI accepts and displays in local time.
- Incoming trade offers: every pending offer shows in the morning briefing (`incoming_trades` per league, first checklist item).
  Primary alert path: a Google Apps Script doorbell (`scripts/gmail_trade_doorbell.gs`, setup in `scripts/gmail_trade_doorbell.md`)
  runs every minute in Dustin's Gmail, finds ESPN's "Trade Proposal" emails of the last 2 hours, fires the "FF trade offer"
  routine's API trigger (`/trade-offer` skill, emails `FF trade offer — <league> — <date>`) once per message id it has not
  fired for (`ff_fired_message_ids` in Script Properties, pruned after 3 days) and labels the thread `ff-alerted`. The label
  is a marker only: ESPN's subject is identical every time, so proposals thread together and the id record, not the label,
  decides what is new. The trigger id and fire token live only in the script's Script Properties (nothing in the repo).
  Backstop: `.github/workflows/trade-poll.yml` runs `ff incoming --new-since 6h` in season, dedupes by offer id via
  `actions/cache`, and fires the same routine. The cron asks for hourly but GitHub delivers roughly every 3h on a quiet
  repo, so the window is deliberately wider than the cadence; overlap is free because ids are deduped. Whether a
  scheduled run polls is `scripts/trade_poll_gate.py` (stdlib, Denver time, `tests/test_trade_poll_gate.py`): Sep-Jan,
  every weekday, every hour but 1-5 AM Denver, so an evening offer is seen within the cadence instead of waiting for
  the briefing (the gate skipped Monday and UTC 0-11, every Denver evening, until 2026-10-08). `python3
  scripts/trade_poll_gate.py --now <ISO>` answers "would it have polled then".
  GitHub needs secrets `ESPN_S2`, `SWID`, `FF_ROUTINE_FIRE_TOKEN` and variables `FF_TRADE_ROUTINE_ID`, `SEASON`, `LEAGUES_TOML`.
- MLB pregame alerts (baseball, same env): step 0 of the briefing skill runs `python3 scripts/mlb_pregame.py`, which reads
  MLB's schedule and prints one one-shot spec (name, `run_once_at` = first pitch minus `LEAD_MIN`, prompt) per first pitch
  among today's games that matter (postseason, or `WATCH_TEAMS` through `REGULAR_SEASON_LAST_DAY`); the briefing session
  creates each with `create_trigger` (fresh session, push notification) and the one-shot's reply is the push. The prompt
  is `.claude/skills/mlb-pregame/SKILL.md` inlined: a session a routine spawns has no repo checkout and no trigger tools,
  which is why this rides the briefing routine (created in the UI with the repo and the Claude Code Remote connector)
  instead of a routine of its own, and why there is no token, state file or GitHub cron (the old workflow ran hours late
  and never had a fire token). `python3 scripts/mlb_pregame.py --text` shows today's plan.
- Debug a run: `RemoteTrigger list_runs` (trigger_id in ops.local.md) → `get_run_log` on the newest session. Re-run: `RemoteTrigger run`.
- Reproduce locally: `uv run ff doctor && uv run ff sync && uv run ff briefing --short --sims 500`. Local `.env` has the same cookies.
  No credentials at all: `uv run ff briefing --demo` (synthetic league from `src/ff/demo.py`).
- Code changes take effect on the next cloud run only after `git push` to main (the VM clones fresh each time).
- Projection logs: the routine pushes `data/projlog/` to the `projlog` branch, never main, through
  `scripts/projlog_push.sh "<message>"` (step 9 of the briefing skill): it fetches the remote tip, builds the commit in a
  temporary index (`data/projlog` additions and edits only, never a deletion), parents it on that tip and pushes a plain
  fast-forward, so the checkout, main and the branch's history are never touched; a rejected push refetches and rebuilds,
  up to three times. `scripts/projlog_push.sh --restore` is the other direction (`git archive` of the tip's
  `data/projlog` into the working tree as untracked files; cloud_setup.sh runs it when the directory is missing), and
  is also how to get the logs locally before `ff accuracy`. The log (`src/ff/projlog.py`) keeps the
  pre-clock projection (`mu_pre`) and ESPN's actual in league scoring (`actual` once his game is `post`, `actual_prev`
  in the next week's first log for Monday night); the scorer takes each player's last pre-kickoff row per league, so K
  and D/ST are scored and half-PPR is scored as half-PPR. No nflverse, no crosswalk; it runs offline.
  `uv run ff accuracy --fit` (`projlog.sigma_fit`) fits `BASE_SIGMA` / `CV_FLOOR` per position to the blend residuals
  in the optimizer's own shape (`max(0.6 × BASE_SIGMA, CV_FLOOR × mu)`, Gaussian NLL on a grid, deterministic) and
  prints them next to the constants in `model/projections.py`; it writes nothing, and a position under
  `projlog.FIT_MIN_N` (150) scored forecasts reads `keep prior`. TODO D2 says when each position gets there.
- Skipped trade rows are remembered: `ff render-email --reads` writes each `skip` on a `trade:` row to
  `data/projlog/skipped_trades.json` (`src/ff/rulings.py`), the packet drops that package for 14 days, and the file rides
  the `projlog` branch (cloud_setup.sh restores it). A `do`/`amend` is not remembered.
- Offer outcomes: every offer of mine in `outgoing_trades` is opened in `data/projlog/offer_outcomes.json` the morning it
  shows and closed the morning it is gone (`src/ff/rulings.py` `record_outcomes`, run by `ff packet`): `accepted` when the
  players I asked for are on my roster and mine are gone, `countered` when he has a counter pending or a different
  package with him went through, `expired` when ESPN's expiry (`expires_ts`, compared to the run's clock) passed,
  `withdrawn` when a piece I offered left my roster with nothing back (ESPN voids the proposal), `declined` otherwise.
  The scan reads it per rival (`history`): a `declined` in the last 7 days is a factor on every package to him, and after
  two answers (accepted / declined / expired) his rate is a prior; a counter or a withdrawal is not an answer.
  Rides the `projlog` branch. `ff trades --explain` prints the scorecard behind each row (p(accept), its factors, what he
  is left with, the fallback package for when he says no).
- Pushed trades: the one package the scan marks `must_try` (sendable and ≥ +2.0 ppw for me) or that Claude rules `push` on
  (with a note) is recorded in `data/projlog/pushed_trades.json` by `ff render-email` and comes back at the top of the card
  every morning ("asked N mornings running") until it shows up in my pending offers (sent), Claude rules `skip` on it, or
  the scan stops producing it. A math push also drops the morning its row is no longer `sendable` (`report.todos`); a
  Claude push stays whatever the math says. One push per league at a time; the packet only reads the memory.
- IR slot moves: `ff packet` notes who sits in each league's IR slot in `data/projlog/ir_moves.json` (`src/ff/rulings.py`
  `note_ir_roster`, before the injury rule runs, so the morning he comes off IR already counts) and stamps `left` when a
  player comes off the slot and `stashed` + `added` (the names that joined the roster that run) when one goes on. A player
  who left the slot in the last `IR_REENTRY_DAYS` (10) is not stashed again unless he is out `IR_REENTRY_WEEKS` (3) or more;
  the hold row says the tag is bouncing. The activate row names the drop as "added when he was stashed <date>" when it is
  the body that stash brought in. Rides the `projlog` branch.
- Checklist ledger: `ledger.Spots` (re-exported as `report._Spots`) hands out roster spots (open bench spot first, then the cheapest drop not already
  named), so an activation and a pickup never spend the same drop. It also counts bodies per position against ESPN's
  caps (`settings.position_limits`): a trade row that brings in a WR at the cap says which WR to drop in the trade
  screen, and skips that when a pickup above it already dropped one. Waiver rows say `claim` (still on waivers, with the
  priority) or `add` (free agent) from `waiver_status` in the snapshot; in a rolling-priority league (`settings.waiver_order`
  from ESPN's `waiverOrderReset`) a claim row also prices the priority it spends (`model/waivers.py` `priority_cost`: the
  value of holding #N less that of holding last, over the wire's claim-worthy upgrades with `RIVAL_CLAIM_P` per team ahead,
  times `(weeks-1)/(2*weeks)`) and says "worth it" or "pass"; a weekly reset to reverse standings costs nothing and prints nothing.
- Questionable sit-risk is day-aware (`QUESTIONABLE_BY_WEEKDAY`: 15% Mon–Wed, 20% Thu, 30% from Fri); a `p_zero` override wins.
  An IR stash is activated only when ESPN's designation leaves the IR-eligible set, never from the guessed return week.
- Do not add ESPN writes. Do not commit briefing.md/html, overrides.json, reads.json, data/cache, data/packets, leagues.toml, ops.local.md.

## Known failure modes
| symptom | cause | fix |
|---|---|---|
| "FF briefing FAILED" email or `ESPNAccessDenied` / `AUTH_MISSING_CREDENTIALS` | ESPN cookies expired (~yearly, silent) | Dustin re-copies `espn_s2` + `SWID` (scripts/setup_cookies.md) into local `.env` AND the cloud env vars |
| No email and healthchecks.io alert | routine never ran / VM failed | `RemoteTrigger get` to check enabled + next_run_at; `run` manually; check run log |
| `uv sync` network timeout in cloud | flaky PyPI download | cloud_setup.sh retries with UV_HTTP_TIMEOUT=300; re-run if it still fails |
| "usage metrics unavailable: Read timed out" | nflverse GitHub release download slow | NFLREADPY_TIMEOUT=120 is set in cloud_setup.sh; harmless, usage columns just blank |
| Lineup shows a slot as EMPTY | no healthy eligible player | expected; the checklist tells Dustin to pick one up |
| Monday email suggests moving players who played Sunday | `model/clock.py` locks players by kickoff/points; check `lines` (vegas scoreboard cache) has kickoffs and `actual_week` is populated | `ff sync --force`; look at `week_state` in the packet |
| Sleeper projections missing for many players | crosswalk join | `Crosswalk.sleeper_id()`; check `shared.unmatched_ids` in the packet |
| Agent sent >1 email or investigated git/Gmail history | skill scope drift | SKILL.md step 10 forbids it; tighten wording if it recurs |
| Card says "activate" on a stash still listed Out, or names one drop on two rows (a Drop row that adds X, then a waiver row that drops the same player for Y) | old checklist logic (fixed 2026-09-23; the Drop-row case 2026-10-08, B1; the activate row printed after the Drop row it should have folded in, B2) | activation keys off `ir_eligible`; drops come from `report._Spots`; `_injury_items` takes every activation's drop before any row prints, so the folded Drop row is skipped whatever order `injuries.decide` put them in, and a Drop row with its own add is `reserved` once the injury rows are built |
| Card says activate, then "move to IR" three days later, then activate again (Daniels, Sep 30 to Oct 7: a four-day McGowan rental) | ESPN's tag bounced Questionable → Out → Questionable and the stash rule had no memory of the activation | fixed 2026-10-07: `ir_moves.json` remembers the exit; a short Out inside `IR_REENTRY_DAYS` holds instead. If it still churns, check the projlog branch restored the file and that `weeks_out` in overrides.json is not inflating a one-week Out |
| Same trade package returns the morning after a `skip` | `data/projlog/skipped_trades.json` missing (projlog branch not restored) | check cloud_setup.sh fetched `origin/projlog`; the key is `<league>|<get names>` |
| Yesterday's rulings are gone this morning (a skipped package is back, a push restarts at "asked 1 morning", an offer outcome or IR move never closed) although the run log says the projlog was pushed, or the log says the push was rejected non-fast-forward | step 9 used to `git checkout -B projlog FETCH_HEAD` over the restored untracked files: with `data/projlog/` ignored (HEAD on main) git silently overwrote them with yesterday's copy before the commit, and when it refused instead the fallback branched off main and the push was rejected | fixed 2026-10-08 (C1): `scripts/projlog_push.sh` commits from a temporary index onto the fetched tip and never checks out a branch (`tests/test_projlog_push.py` runs it against a scratch remote). If a ruling still goes missing, the run log shows the script's one line ("pushed", "nothing to commit", or the failure) |
| Email card says do it and Claude's read says don't (the morning checklist, or the trade-offer alert's verdict badge with a read underneath disagreeing) | a row was argued with in prose instead of ruled on | `items` in reads.json: `skip` strikes the row, `amend` corrects it; `ff render-email` warns about unruled trade rows. Since 2026-10-08 (D6) the `--only-incoming` alert folds a ruling on the `offer:` row in the same way, and `ff incoming --json` prints that id as `row_id` |
| Claude's `p_zero` for a Questionable starter is below the default, or the research step burns its searches on wind | the skill prompt quoted a sit-risk number of its own (0.25 for a limited Friday against the code's 0.30) and never said the code discounts wind | fixed 2026-10-08 (D6): `tests/test_skill_prompts.py` holds both SKILL.md files to `QUESTIONABLE_BY_WEEKDAY`, `P_ZERO` and `WIND_MPH_START`, and to naming every overrides.json key; change the constant and the prompt line together |
| Cloud Bash killed a long command | 120 s default timeout | run `ff` steps with timeout 600000, never `&` |
| Email says hold a player who is done for the year, or trade/drop one who is back Sunday | `weeks_out` still at its designation default (IR = 4, Out = 1) | the player is in `shared.injured`; write `weeks_out` (or `"season"`) in overrides.json and re-run step 5 |
| Card says drop a hurt starter (or hold a backup who is plainly dead weight) | `mu_ros_active` is off: ESPN docked his season total for the injury, or his healthy level is not what ESPN thinks | `shared.injured` shows `mu_ros_active`; write `ros_mult` in overrides.json; the drop rule fires when he is below replacement on return |
| Card says move a player to IR and the app refuses | league's IR eligibility is stricter than `IR_ELIGIBLE` in `model/injuries.py` | `scripts/probe_injuries.py` shows what ESPN says; narrow the constant |
| Briefing says "could not read pending trades" | ESPN changed `mPendingTransactions` or cookies half-dead | check `pending_trades_error` in the packet; `ff incoming --force` locally |
| trade-poll workflow red | cookies dead in GitHub secrets, or fire token revoked | update repo secrets; `gh workflow run trade-poll.yml -f window=48h` to test |
| "Summary of failures for Google Apps Script" naming one or two runs | transient Google backend fault. Until 2026-10-12 a single run could still raise it: the lock call, the failure-counter write in the catch block and the lock release threw the same server error outside the swallow, and a hung Gmail search was retried for 8 minutes (Oct 11, 1:54 AM) | fixed 2026-10-12: each of those is guarded and `RETRY_BUDGET_MS` stops retries 2 minutes into a run. Paste the current `.gs` into the editor if the notice names a run after that date; otherwise ignore, the doorbell retries in-run and re-runs the next minute, and Apps Script only reports it after 5 consecutive failed runs |
| Email "FF trade doorbell — cannot reach the trade routine" | 3 fires in a row rejected: token rotated (401/403) or routine id wrong (404) | fix the Script Property; `fireTestOffer` in the Apps Script editor re-checks the path |
| Trade offer email never arrives but the offer is in ESPN | doorbell silent (Apps Script trigger gone, Script Properties cleared, token rotated) and poller missed it or routine disabled | Apps Script Executions log; confirm Script Properties `FF_TRADE_ROUTINE_ID` + `FF_ROUTINE_FIRE_TOKEN` are still set and the 1-minute trigger exists; `RemoteTrigger get` on the trade routine; it still appears in the next morning briefing |
| Trade screen says "Too many players with default position WR (maximum 6)" on a trade the card proposed | the league caps rostered players per position (`position_limits` in the settings, from ESPN's `positionLimits`); the scan used to ignore it | fixed 2026-09-24: the trade row now names the drop ESPN wants in the trade screen (`drops` on the candidate, cheapest body at that position, IR stash never). If ESPN does not actually demand the drop the card names, the IR occupant should stop counting: see `over_cap` in `model/trades.py` |
| Trade row reads "drop X in the trade screen (); ESPN caps WR at 3 and there is no obvious WR to drop, your call" (empty parentheses, then a denial right after naming him) | a 2-for-1 pushed the position two over the cap and the bench had one body to cut; `report._Spots.cut` filed the position as `stuck` and dropped its cap note | fixed 2026-10-08 (B11): the note names every position a cut was found for and the row says "and one more WR has to go, your call"; a position with nobody to cut still gets the no-obvious-drop sentence |
| Rival laughs at a 2-for-1 the card called "neutral for them" (his backup QB, the market's QB3, for a QB nobody wants plus a mid RB) | lineup math prices a rival's bench at zero, and the market ratio summed the package so the throw-in rode in on the other piece's value | fixed 2026-10-06: `throw_ins` in `model/trades.py` drops a package with a piece he would neither start nor price (`THROW_IN_FRAC`); the row also warns when I ask for a player he starts today, and when a 2-for-1 forces a cut on his full roster. Same rule discounts throw-ins in offers I receive |
| Card proposes two mid pieces for a top-10 player, or asks a one-QB team for its QB and the paste says "you're set at QB" | the scan sums market value linearly, rewards me getting the best player (`consol`), never depth-checks the rival, and counts his IR bodies as cover | `docs/plans/trade-acceptance.md` (evidence: 17 of 19 skips on the projlog branch were that shape). Since 2026-10-07 `model/trades.py` taxes the side sending more bodies (`CONSOLIDATION_TAX`, `fairness`), refuses a star ask that does not send a top-24 piece back with the premium (`is_star`, `STAR_PREMIUM`), asks for a QB only from a roster with two startable ones (`startable_qbs`), depth-checks the rival, prices both rosters week by week with the wire as the fallback (`model/season.py`), ranks by `p_accept` × my gain (`model/acceptance.py`; the row says "coin flip" or "he'd likely take it" and what he is left with at the slot), shows two rows and no "reach". If a row still looks like a lowball, `ff trades` prints `p(accept)` with the factors; tune the factor in `acceptance.score`, not the row |
| Hold, trade and drop rows value every player more each week (an injured player's hold row most of all), playoff and title odds drift up, a 14-a-game WR reads 19 a game in October | `mu_ros_active` divided ESPN's full-season total by the weeks left, so the inflation was `17 / weeks_remaining` | fixed 2026-10-08 (TODO A1): the per-game base is season-to-date points per game with `proj_season / 17` as a prior that fades out by game six (`season_pg` in `model/projections.py`), shrunk toward the matchup-neutral weekly blend; `espn_pg` (the rival's view) is the same arithmetic on ESPN's numbers. If a healthy starter's ROS/g is still far from his weekly projection, check `games_played` and `ytd_pg` in his `sources` |
| Trade and offer rows show a title-odds delta that an empty trade would also show (a null offer read -1.5 to +0.9 title points; the number moved between mornings with no roster change) | the re-sim ran on its own seed and draw count (seed 11 at 1500) against a baseline at seed 7, so the delta was seed noise | fixed 2026-10-08 (TODO A3): `title_deltas` in `packet.py` re-sims with the baseline's seed and `sims` (common random numbers); a null trade reads 0.0 exactly. If a delta still looks like noise, check both sims run at the same `n` |
| `ff accuracy` shows a blend MAE under 1, or no K / D/ST rows, or a week missing | logs before 2026-10-08 scored Monday's banked points against themselves (fixed, TODO A2); a week with no Monday log and no log the Tuesday after has no truth (the routine skipped both days) | the harness scores a forecast only when a `post` row or the next week's `actual_prev` exists for it; `ff accuracy` with the projlog branch restored lists what it could score, through the newest log's week (`--week N` overrides it; it read Sleeper's clock until 2026-10-08, C8, which scored a half-played week or skipped a finished one); nothing to fix for a skipped week, the row is simply absent |
| Sunday email's P(win) and the rival's projection ignore that he benched a starter or left a slot empty (an empty flex and a 12-point RB on the bench read the same as his best lineup) | `opponent.mu` was the EV optimizer run over his whole roster, whatever his slots said | fixed 2026-10-08 (TODO A4): Sat/Sun/Mon (`LINEUP_SET_DAYS` in `packet.py`), or any day every starter slot holds a non-bye player, `opponent.mu` sums the players in his starter slots (`lineup.as_set`); `opponent.lineup` in the packet says `set` or `projected`, and the markdown header says "their set lineup". If a Sunday rival still reads too strong, check the snapshot's `slot` values and that the run passes `now` |
| No MLB pregame push before a game | the briefing run failed before step 0, or skipped it; or the one-shot was never created (`list_triggers` with recurring false shows `MLB pregame: ...` rows) | the briefing run log; `python3 scripts/mlb_pregame.py --text` locally shows what it should have scheduled; create the missing one-shot by hand from that output |
| Trade row says "he turned one down this week" the morning after an offer merely lapsed overnight, after I accepted his counter, or after I dropped the piece I had offered | `record_outcomes` compared the expiry date only, in UTC (a 9 PM Denver lapse is the next UTC day), and closed everything that was not an acceptance as `declined`; `history` then counted `expired` as a fresh no | fixed 2026-10-08 (B5): the close compares `expires_ts` to the run's clock and separates `countered`, `withdrawn`, `expired` and `declined`; only `declined` sets `recent_decline`. If the factor still shows, `offer_outcomes.json` on the projlog branch has the status and `closed` date per offer |
| Waiver or activate row names a rookie RB the market prices like a starter as the drop, ahead of a WR4 nobody would trade for | the drop order was `mu_ros` alone; a player the market would pay for is trade bait, not a cut | fixed 2026-10-08 (B6): `model/injuries.py` `drop_cost` (B6b moved it there) adds `MARKET_DROP_PPW` per 1000 of FantasyCalc redraft value (trend at half weight) and the row prints both players' market and ROS/wk side by side. A big market number is a thumb on the scale, not a veto; if the row still cuts the wrong body, the two numbers are on it |
| Hold row says "X is the cheaper drop" while a waiver row above it drops the player being held; or the drop named is my RB1's backup, or a healthy body when another is on bye this week | the Drop row (`injuries.decide` `cheapest`) ranked by `mu_ros` alone and the ledger by `report._drop_cost`, and neither knew about handcuffs, byes or this week's sit risk | fixed 2026-10-08 (B6b): both rank by `model/injuries.py` `drop_cost`: `mu_ros` + market + the handcuff's `est_value` (from `lg["handcuffs"]`) − the one week in `weeks_remaining` a bye or a `p_zero` not already netted out of `mu_ros` gives nothing (an Out is not counted twice). The row says "on bye this week" / "90% to sit this week" or "handcuff for RB1, not a cut". If the two rows still disagree, check `handcuffs` and `weeks_remaining` in the packet |
| Cover row says "add Backup K before kickoff" and the app says he is on waivers (the claim lands Wednesday), or the add has no spot and the row names no drop | `_cover_items` took the first waiver at the position whatever `on_waivers` said and never touched the roster-spot ledger | fixed 2026-10-08 (B7): the row names a free agent first, words a waivers-only fallback as the claim it is, takes its spot from `report._Spots` (open spot, else the named drop) and steps aside when a waiver row above already adds a body at the position |
| Lineup row benches a higher-points player and the why line says "favor upside" / "play it safe" for a P(win) edge that is noise (0.3pp) | the P(win) search took any gain over the highest-points lineup, and sigma is a positional guess, not a fitted number | fixed 2026-10-08 (TODO A5): `MIN_WIN_GAIN` in `model/lineup.py` (1.5pp) keeps the highest-points lineup under the floor; `lineup_win.win_gain` in the packet is the gain and the why line prints it ("+2.1 pp P(win)"). If a deviation still looks thin, the number is on the row; the floor comes down once variance is fitted (TODO D2) |
| "Trade (do this one)" row with "too good to let slide (+N pts/wk ... by the math)" on a package the same card would otherwise call a reach, or that no longer helps the rival | `pushed_trades.json` remembered a math push and `report.todos` never re-checked `sendable` on it | fixed 2026-10-08 (TODO B3): a remembered push whose `source` is `math` is dropped when the row is no longer `sendable` (the memory stays open and resumes if the math comes back); a `claude` push keeps its note and its place. If a stale push persists, check `source` on the row in `pushed_trades.json` |
| Trade card says "do this one ... first ask" on the package Dustin sent two days ago and the rival just declined, then counts "asked N mornings running" from there | `record_pushes` in `rulings.py` only refused to reopen a `sent`/`skipped` push closed the same morning; once the declined offer left `pending_trades` the scan re-derived the `must_try` row and opened a new push | fixed 2026-10-08 (B4): a closed push stays closed for `SKIP_DAYS` unless Claude rules `push` on the row again (with a note). If it still nags, check `pushed_trades.json` came back from the projlog branch and has a `closed` date |
| The Holding footnote in the email shows a hold Claude ruled `skip` on as a plain hold (the markdown strikes it, the HTML does not) | `email_html.py` stripped the `~~` markers from `report.hold_line` instead of rendering the ruling | fixed 2026-10-08 (B8): `_hold` in `email_html.py` renders the skip as the same line-through the checklist rows use, with the note after it |
| ESPN proposal email has no `ff-alerted` label an hour later | doorbell never ran or the fire returned non-2xx | same as above; `resetDoorbellState` in the Apps Script editor makes it fire again for a message already on record |
| Second trade offer of the day never got a doorbell email while the first did (both still in the morning briefing) | the doorbell searched `-label:ff-alerted` and labels are per thread; ESPN's identical subject threaded the second proposal under the handled first one, so it was never seen | fixed 2026-10-08 (C2): the search no longer excludes the label and dedupe is per message id (`ff_fired_message_ids`, 3-day TTL). If it recurs, the Executions log should show "Fired routine ... (N new messages)" for the thread; paste the current `.gs` into the editor if it does not |
| Trade row says "he turned one down this week" the morning after I cancelled my own offer in the app (roster unchanged, offer not yet expired) | the pending view has no terminal status, so `record_outcomes` read anything not accepted, countered, expired or voided as his decline | fixed 2026-10-08 (B10): `espn.trade_resolutions` reads `mTransactions2`, where a `TRADE_DECLINE` or a `CANCELED` copy of the proposal (`relatedTransactionId`) names who closed it (his member id: declined; mine: withdrawn; no team's: expired); `trade_resolutions` on the league block, roster and clock remain the fallback. If a cancel still reads declined, `trade_resolutions_error` in the snapshot says whether the view was read, and `by` on the entry says whose member id ESPN put on the cancel |
| Claim row says "pass: you'd give up priority #N for +0.3/wk" on a player I obviously want (a one-week fill-in, or the wire is stacked) | `_priority_note` in `report.py` compares `claim_gain` (the ROS upgrade, or a one-week fill-in's points spread over the weeks left) with `model/waivers.py` `priority_cost`: holding #N vs holding last over today's claim-worthy wire (`delta_over_starter >= CLAIM_WORTHY_PPW`), `RIVAL_CLAIM_P` (0.35) per team ahead, discounted `(weeks-1)/(2*weeks)` | the two numbers are on the row; `skip`/`amend` it in reads.json when the research says otherwise (a short-week fill-in with a bye crunch, a wire the card overrates). A league that resets priority weekly must read `waiver_order: reset` in the packet settings; if it says `rolling` and ESPN says "Waiver Order Reset: Yes", the `waiverOrderReset` mapping in `sources/espn.py` `waiver_order` is backwards |
| Monday or Tuesday email prices players off next week's (or last week's) Sleeper numbers: a Monday-night starter's projection does not match ESPN's week, the Sleeper source in the packet shows `week` one off the league's | `packet.build` fetched Sleeper projections for `sleeper.state().week`, Sleeper's own clock, while ESPN's `current_week` rolls Tuesday and `apply_clock` / `week_state` price the league's week | fixed 2026-10-08 (C6): `_sleeper_projections` in `packet.py` fetches per league week (`sl_proj_by_week`, like the vegas lines) and never reads `sleeper.state()`. If a Sleeper number still looks like the wrong week, check `week` in the snapshot and the `sleeper_proj_<season>_<week>` cache key |
| Card says move X to IR in place of Y and the app says Y is locked (Oct 10: McMillan, whose game kicked off Thursday); or names a drop ESPN refuses with "player is locked" | ESPN locks a player from kickoff until the week rolls on Tuesday: no drop, no move into or out of the IR slot. `model/clock.py` set `locked` and the IR rule, the Drop row and the ledger never read it | fixed 2026-10-10: `LOCKED_WHY` in `model/injuries.py`; a locked occupant is never swapped out (the stash row holds and says "swap him in on Tuesday", `ir_locked` names him), a locked player is not stashed or dropped today, and `ledger.drop_order` sorts locked bodies last with `lock_note` on the row. If the card still names a locked move, check `locked` on his roster row in the packet (the lines cache needs his kickoff) |
| Lineup row says "set it this way", then a waiver row below says "add X and start him at WR", so the lineup is set twice and the first setting is wrong once the add goes through; or the pickup was on another roster by the time the email was read | `report.todos` put the lineup row first, before the adds that change it, and the waiver row named one body on a wire that moves between the 6 AM sync and the click | fixed 2026-10-10: the roster moves (IR, pickups, drops) come first and the lineup row after them, with a free-agent pickup's start as its last move (`_pickup_move`: "X: free agent → WR over Y once the add above is through"; a claim is not folded, it lands on waiver day), and each waiver row ends "if he is gone, <next body>". The lineup above the fold still stands when the add fails |
| A QB, WR, TE or K reads a point or two under every source on a Sunday with a windy forecast, and the Vegas line does not explain it | the wind discount: from 2026-10-08 (D5) `model/projections.py` `wind_mult` scales this week's `mu` for passing-game players and K when the kickoff forecast (`sources/weather.py` `by_team`, Open-Meteo at the home stadium) is over `WIND_MPH_START` (15 mph), linearly to `WIND_PENALTY_AT_CAP` (QB/WR/TE 10%, K 15%) at `WIND_MPH_CAP` (25); domes, ESPN `indoor` venues and a missing forecast read 1.0; `mu_ros` never sees it | the row's `sources.weather_mult` and `sources.wind_mph` say so, and the flag `wind:22mph x0.93` is in the roster table; if the number looks wrong for the game, check `wx_<home>_<date>` in `data/cache` against the kickoff hour |
