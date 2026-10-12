# Gmail trade doorbell — setup

ESPN emails you within seconds of any trade proposal. A tiny Google Apps Script watches your Gmail
for those emails and fires the "FF trade offer" routine, which re-reads ESPN and emails you a verdict.
This is the primary alert path. The hourly GitHub poller (`.github/workflows/trade-poll.yml`) is the
backstop, and the morning briefing is the final net.

Nothing in the script is secret; the two secrets live only in Script Properties inside your Google account.

## What you need first
- The routine's **trigger id** and **API fire token**. Open the "FF trade offer" routine page (URL in
  `ops.local.md`) and copy both from its API trigger section.

## 1. Create the project
1. Go to https://script.google.com while signed in to the Gmail account ESPN emails.
2. Click **New project**. Rename it (top-left, "Untitled project") to `FF trade doorbell`.
3. In the editor, delete the placeholder `myFunction` and paste the whole contents of
   `scripts/gmail_trade_doorbell.gs`. Press **Ctrl+S** to save.

## 2. Set the two Script Properties
1. Click the gear icon (**Project Settings**) in the left sidebar.
2. Scroll to **Script Properties** and click **Add script property**. Add:
   - `FF_TRADE_ROUTINE_ID` = the trigger id
   - `FF_ROUTINE_FIRE_TOKEN` = the fire token
3. Click **Save script properties**.

## 3. Authorize once by running it by hand
1. Back in the editor (the `< >` icon), pick `checkForTradeOffers` in the function dropdown next to
   **Run**, then click **Run**.
2. A dialog asks for permission. Click **Review permissions**, choose your account, click **Advanced**,
   then **Go to FF trade doorbell (unsafe)** (it is unsafe only in the sense that Google did not review
   it; you wrote it), then **Allow**. It needs Gmail access (to search and label) and "connect to an
   external service" (to call the fire endpoint).
3. The run finishes. If there is no unhandled ESPN proposal email in the last 2 hours, it does nothing,
   which is fine.

## 4. Add the 1-minute trigger
1. Click the clock icon (**Triggers**) in the left sidebar, then **Add Trigger** (bottom right).
2. Set: function `checkForTradeOffers`, deployment `Head`, event source **Time-driven**,
   type **Minutes timer**, interval **Every minute**. Failure notifications: **Notify me daily**.
3. Click **Save**.

From now on, each ESPN proposal email gets the label `ff-alerted` after the routine is fired, and you
should get an `FF trade offer — <league> — <date>` email a few minutes later. The label is only a
marker for you: what has been handled is decided per message id (see below), so a second proposal
that Gmail threads under the first one (ESPN's subject is the same every time) still fires.

## How it handles its own failures
- **Transient Google faults** (`We're sorry, a server error occurred`) are retried three times with
  backoff inside the run. If the run still fails it stays quiet, because the trigger fires again in a
  minute. Only **5 consecutive** failed runs are allowed to raise an Apps Script failure notification,
  so a one-off blip no longer emails you. The quiet path is itself fault-proof: the lock, the failure
  counter write and the lock release are each guarded, because during a Google fault they throw the
  same error as the search did, and until 2026-10-12 the counter write in the catch block was the
  throw that reached the failure notice. A Google call that hangs for minutes instead of failing fast
  is not retried once the run is 2 minutes in (`RETRY_BUDGET_MS`); the next minute takes over rather
  than three hangs stacking up to the 6-minute execution limit.
- **A rejected fire** (rotated token, deleted routine) is the dangerous case: it means no alert will
  ever arrive, and the only other record is a log nobody reads. After **3 rejections in a row** the
  script emails you `FF trade doorbell — cannot reach the trade routine`, at most once an hour.
- **A proposal is never alerted twice, and none is skipped.** Every fired message id is recorded in
  Script Properties (`ff_fired_message_ids`, with the time it fired; ids older than 3 days are pruned,
  at most 200 kept). The search does not exclude the label, because Gmail labels are per thread and
  ESPN's identical subject threads every proposal together; a message whose id is not on record is
  new, however its thread is labeled. The id is recorded *before* the thread is labeled, so a failed
  label write makes the next run repair the label rather than fire again. Two new proposals in one
  thread on the same run get one fire; the routine re-reads every pending offer anyway.

## Debugging
- **Execution log:** click the list icon (**Executions**) in the left sidebar. Each run shows its
  status and any `console.log` lines: "Fired routine for ...", "Fire failed (N in a row) ...",
  "Already fired ...; label repaired", or "Missing Script Property ...".
- **Prove the fire path works:** pick `fireTestOffer` in the function dropdown and click **Run**. It
  fires the routine once; with no pending offers the routine stops without emailing, so it is safe to
  run any time. The log says `Fire succeeded: HTTP 2xx` or `Fire FAILED: ...`.
- **Nothing fires:** confirm both Script Properties are still set (Project Settings), that the
  trigger still exists (Triggers), and that the ESPN email is in the inbox from `fantasy@espnmail.com`
  with "Trade Proposal" in the subject and arrived in the last 2 hours. To make the script fire again
  for a message it already handled, run `resetDoorbellState` (the label does not matter; the id
  record does).
- **HTTP 401/403 in the log:** the fire token was rotated. Copy the new one from the routine page into
  the Script Property. This is the case the escalation email is for.
- **HTTP 404:** the trigger id is wrong or the routine was deleted.
- **Rotating the token:** only the Script Property changes; the script file never does.
- **After editing the script:** run `node scripts/gmail_trade_doorbell.test.js` in the repo first. It
  exercises the retry, escalation and no-double-fire paths against a stubbed Apps Script runtime.

## Script Properties the script manages itself
Alongside the two you set, the script keeps `ff_failed_runs`, `ff_fire_failures`,
`ff_last_escalation_ms` and `ff_fired_message_ids` (a `{messageId: firedAtMs}` map, pruned to the
last 3 days and 200 ids). `resetDoorbellState` clears those four and leaves your two credentials alone.
