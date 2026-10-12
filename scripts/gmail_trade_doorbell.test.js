// Stubbed Apps Script runtime for scripts/gmail_trade_doorbell.gs.
//
// The doorbell runs in Google's environment, so pytest cannot reach it and a mistake there is only
// visible as a trade offer that never gets announced. This fakes GmailApp/UrlFetchApp/Properties and
// asserts the failure paths: transient faults retry, a rejected fire escalates, no proposal is ever
// announced twice, and a second proposal threaded under an already handled one is still announced.
// Run from the repo root, after editing the .gs and before pasting it in:
//
//   node scripts/gmail_trade_doorbell.test.js
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync('scripts/gmail_trade_doorbell.gs', 'utf8');

let out = [], fails = 0;
const eq = (label, a, b) => { const ok = JSON.stringify(a) === JSON.stringify(b);
  if (!ok) { fails++; console.log(`  FAIL ${label}: got ${JSON.stringify(a)} want ${JSON.stringify(b)}`); }
  else console.log(`  ok   ${label}`); };

const SUBJECT = 'A Trade Proposal in Your ESPN Fantasy Football League';
const DAY_MS = 24 * 60 * 60 * 1000;

// The stub models the Gmail facts the finding (TODO C2) rests on: labels live on threads,
// `thread.addLabel` marks the whole thread, a message that arrives later threads under the same
// subject, and `-label:X` in the search drops a thread that carries X. The clock is `state.now`.
// `searchHangMs` is how long each failing search blocks before it throws (the clock moves);
// `state.propsDown` / `state.lockDown` make PropertiesService / LockService throw the same fault.
function makeEnv({searchFails = 0, fireCodes = [200], labelFails = 0, msgId = 'm1', searchHangMs = 0} = {}) {
  const state = { props: {FF_TRADE_ROUTINE_ID: 'trig_x', FF_ROUTINE_FIRE_TOKEN: 'tok'},
                  fires: 0, fired: [], labels: 0, mails: [], slept: 0, searches: 0, released: 0,
                  propsDown: false, lockDown: false, releaseDown: false,
                  now: Date.parse('2026-09-20T00:00:00Z'), threads: [] };
  let sf = searchFails, lf = labelFails, fi = 0;
  const makeMsg = (id, at) => ({ getId: () => id, getSubject: () => SUBJECT,
                                 getFrom: () => 'ESPN <fantasy@espnmail.com>', getDate: () => new Date(at) });
  const makeThread = (msgs) => {
    const t = { msgs, labels: [],
      getMessages: () => t.msgs.slice(),
      getLabels: () => t.labels.slice(),
      addLabel: (l) => { if (lf-- > 0) throw new Error('label backend error');
                         state.labels++; if (!t.labels.some(x => x.getName() === l.getName())) t.labels.push(l); } };
    return t;
  };
  const label = { getName: () => 'ff-alerted' };
  const thread = makeThread([makeMsg(msgId, state.now)]);
  state.threads.push(thread);
  state.addMessage = (t, id) => { t.msgs.push(makeMsg(id, state.now)); };
  state.addThread = (id) => { const t = makeThread([makeMsg(id, state.now)]); state.threads.push(t); return t; };
  class FakeDate extends Date { static now() { return state.now; } }
  const ctx = {
    console, Date: FakeDate, Math, JSON, Array, Object, String, Number, parseInt, Error,
    GmailApp: {
      search: (q, start, max) => { state.searches++;
        if (sf-- > 0) { state.now += searchHangMs; throw new Error('We\'re sorry, a server error occurred.'); }
        const excluded = /-label:(\S+)/.exec(q);
        return state.threads.filter(t => !excluded || !t.labels.some(l => l.getName() === excluded[1]))
          .slice(start || 0, (start || 0) + (max || 100)); },
      getUserLabelByName: () => label, createLabel: () => label },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: k => { if (state.propsDown) throw new Error('We\'re sorry, a server error occurred.');
                          return k in state.props ? state.props[k] : null; },
      setProperty: (k, v) => { state.props[k] = v; },
      deleteProperty: k => { delete state.props[k]; } }) },
    UrlFetchApp: { fetch: (url, opts) => { state.fires++; state.fired.push(JSON.parse(opts.payload).text);
      const c = fireCodes[Math.min(fi++, fireCodes.length - 1)];
      return { getResponseCode: () => c, getContentText: () => 'body' }; } },
    LockService: { getScriptLock: () => {
      if (state.lockDown) throw new Error('We\'re sorry, a server error occurred.');
      return { tryLock: () => true,
               releaseLock: () => { if (state.releaseDown) throw new Error('We\'re sorry, a server error occurred.');
                                    state.released++; } }; } },
    Utilities: { sleep: ms => { state.slept += ms; } },
    Session: { getEffectiveUser: () => ({ getEmail: () => 'dbj2297@gmail.com' }) },
    MailApp: { sendEmail: (to, subj, body) => state.mails.push({to, subj}) },
  };
  vm.createContext(ctx); vm.runInContext(src, ctx);
  return { ctx, state, thread };
}
const firedIds = state => { const raw = state.props.ff_fired_message_ids; if (!raw) return [];
  const v = JSON.parse(raw); return Array.isArray(v) ? v : Object.keys(v); };

console.log('transient search error is retried, run succeeds, no alarm raised');
{ const {ctx, state} = makeEnv({searchFails: 2});
  ctx.checkForTradeOffers();
  eq('searched 3 times', state.searches, 3);
  eq('fired once', state.fires, 1);
  eq('labeled', state.labels, 1);
  eq('no failed-run counter left', state.props.ff_failed_runs, undefined);
  eq('backed off', state.slept, 3000); }

console.log('search down all attempts: run stays quiet until the 5th consecutive failure');
{ const {ctx, state} = makeEnv({searchFails: 99});
  for (let i = 1; i <= 4; i++) ctx.checkForTradeOffers();  // must not throw
  eq('4 failures recorded', state.props.ff_failed_runs, '4');
  let threw = false;
  try { ctx.checkForTradeOffers(); } catch (e) { threw = true; }
  eq('5th failure surfaces to Apps Script', threw, true); }

console.log('Google fault reaches the failure counter too (2026-10-11): the run still ends quietly');
{ const {ctx, state} = makeEnv({searchFails: 99});
  state.propsDown = true;                   // every Google service is faulting, not just Gmail
  let threw = false;
  try { ctx.checkForTradeOffers(); } catch (e) { threw = true; }
  eq('nothing escapes the catch block', threw, false);
  eq('lock still released', state.released, 1);
  state.propsDown = false;                  // Google recovers: the counter resumes from where it was
  ctx.checkForTradeOffers();
  eq('counted from the next failure on', state.props.ff_failed_runs, '1'); }

console.log('lock service faults: quiet, nothing fired, counted as a failed run');
{ const {ctx, state} = makeEnv();
  state.lockDown = true;
  let threw = false;
  try { ctx.checkForTradeOffers(); } catch (e) { threw = true; }
  eq('nothing escapes', threw, false);
  eq('no fire without the lock', state.fires, 0);
  eq('counted', state.props.ff_failed_runs, '1');
  state.lockDown = false;
  ctx.checkForTradeOffers();
  eq('fires once the lock is back', state.fires, 1);
  eq('counter cleared', state.props.ff_failed_runs, undefined); }

console.log('lock release faults after a good run: swallowed');
{ const {ctx, state} = makeEnv();
  state.releaseDown = true;
  let threw = false;
  try { ctx.checkForTradeOffers(); } catch (e) { threw = true; }
  eq('nothing escapes', threw, false);
  eq('fired', state.fires, 1);
  eq('run counted as a success', state.props.ff_failed_runs, undefined); }

console.log('a search that hangs for minutes is not retried past the budget (one hang, not three)');
{ const {ctx, state} = makeEnv({searchFails: 99, searchHangMs: 150 * 1000});
  ctx.checkForTradeOffers();
  eq('one attempt', state.searches, 1);
  eq('no backoff sleep', state.slept, 0);
  eq('one failed run recorded, quietly', state.props.ff_failed_runs, '1'); }

console.log('a search that fails fast still gets its three attempts inside the budget');
{ const {ctx, state} = makeEnv({searchFails: 99, searchHangMs: 30 * 1000});
  ctx.checkForTradeOffers();
  eq('three attempts', state.searches, 3); }

console.log('fire rejected with 401: no label, no duplicate, email after 3 tries');
{ const {ctx, state} = makeEnv({fireCodes: [401]});
  ctx.checkForTradeOffers(); ctx.checkForTradeOffers();
  eq('no email yet', state.mails.length, 0);
  eq('never labeled', state.labels, 0);
  ctx.checkForTradeOffers();
  eq('emailed on 3rd', state.mails.length, 1);
  eq('email subject', state.mails[0].subj, 'FF trade doorbell — cannot reach the trade routine');
  ctx.checkForTradeOffers();
  eq('cooldown holds the 4th', state.mails.length, 1);
  eq('401 not retried within a run', state.fires, 4); }

console.log('fire 500 is retried inside the run');
{ const {ctx, state} = makeEnv({fireCodes: [500, 500, 200]});
  ctx.checkForTradeOffers();
  eq('three attempts', state.fires, 3);
  eq('labeled after success', state.labels, 1); }

console.log('label write fails after a successful fire: next run repairs, does not re-fire');
{ const {ctx, state} = makeEnv({labelFails: 3});   // exhausts the label retries
  ctx.checkForTradeOffers();
  eq('fired once', state.fires, 1);
  eq('not labeled', state.labels, 0);
  eq('id remembered', firedIds(state), ['m1']);
  ctx.checkForTradeOffers();
  eq('still fired only once', state.fires, 1);
  eq('label repaired', state.labels, 1); }

console.log('handled thread is quiet on later runs: no re-fire, no label rewrite');
{ const {ctx, state} = makeEnv();
  ctx.checkForTradeOffers();
  for (let i = 0; i < 10; i++) { state.now += 60 * 1000; ctx.checkForTradeOffers(); }
  eq('fired once', state.fires, 1);
  eq('labeled once', state.labels, 1); }

console.log('second proposal threads under an already labeled one: still fires (TODO C2)');
{ const {ctx, state, thread} = makeEnv();
  ctx.checkForTradeOffers();
  eq('first proposal fired', state.fires, 1);
  eq('thread labeled', thread.labels.length, 1);
  state.now += 40 * 60 * 1000;             // 40 minutes later ESPN sends a second proposal, same subject
  state.addMessage(thread, 'm2');
  ctx.checkForTradeOffers();
  eq('second proposal fired', state.fires, 2);
  eq('both ids remembered', firedIds(state).sort(), ['m1', 'm2']);
  ctx.checkForTradeOffers();
  eq('no third fire', state.fires, 2); }

console.log('two unseen proposals in one thread: one fire covers both, both ids remembered');
{ const {ctx, state, thread} = makeEnv();
  state.addMessage(thread, 'm2');
  ctx.checkForTradeOffers();
  eq('one fire', state.fires, 1);
  eq('both remembered', firedIds(state).sort(), ['m1', 'm2']);
  ctx.checkForTradeOffers();
  eq('nothing more', state.fires, 1); }

console.log('fired ids are pruned after a few days, so Script Properties stay bounded');
{ const {ctx, state} = makeEnv();
  ctx.checkForTradeOffers();
  eq('m1 remembered', firedIds(state), ['m1']);
  state.now += 4 * DAY_MS;
  state.threads.length = 0;                 // the old thread fell out of newer_than:2h
  state.addThread('m9');
  ctx.checkForTradeOffers();
  eq('new proposal fired', state.fires, 2);
  eq('old id pruned', firedIds(state), ['m9']); }

console.log('missing Script Properties: quiet no-op, nothing fired');
{ const {ctx, state} = makeEnv();
  delete state.props.FF_ROUTINE_FIRE_TOKEN;
  ctx.checkForTradeOffers();
  eq('no fire', state.fires, 0);
  eq('no failure recorded', state.props.ff_failed_runs, undefined); }

console.log(fails === 0 ? '\nALL PASS' : `\n${fails} FAILING`);
process.exit(fails === 0 ? 0 : 1);
