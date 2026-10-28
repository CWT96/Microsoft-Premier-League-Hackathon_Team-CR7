// The Premier X ops dashboard: seeded per-user simulation of the demo match.
// Run: node frontend/ops-dashboard/sim/simulate.js
// Writes ../data/data.js (loaded by index.html) and two CSVs. Same seed -> same numbers every run.
//
// Match facts come from the same synthetic match the fan app plays (data/source, nbr-cal-seed42,
// shown as Arsenal vs Chelsea): the minute timeline with stoppage time, the goals and scorers, and
// how many live calls can open each minute. Calls use the fan app's rules (backend/demo/calls.py):
// a call opens on a completed pass, locks on the receiver's pass / shot / dribble, needs a window of
// at least 3 s, and calls are at least 6 s apart (shots first, then dribbles, then passes).
// User behaviour (scans, playing, leaving, answering, signing up) is simulated here.
var fs = require("fs");
var path = require("path");
var OUT = path.join(__dirname, "..", "data");
var REPO = path.join(__dirname, "..", "..", "..");
var MATCH_FILE = "9000001.json"; // nbr-cal-seed42
var seed = 20261026;
function rnd() { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; }
function pois(l) { var L = Math.exp(-l), k = 0, p = 1; do { k++; p *= rnd(); } while (p > L); return k - 1; }
var i, j;

// ---- match facts from the event log
var events = JSON.parse(fs.readFileSync(path.join(REPO, "data", "source", "events", MATCH_FILE), "utf8"));
var teamCode = {}; JSON.parse(fs.readFileSync(path.join(REPO, "data", "source", "extras", "teams.json"), "utf8"))
  .forEach(function (t) { teamCode[t.team_id] = t.code; });
var DISPLAY = { NBR: "ARS", CAL: "CHE" };
var names = {}, namesSrc = fs.readFileSync(path.join(REPO, "backend", "demo", "names.py"), "utf8"), mm, re = /"(\d+)": \("([^"]+)", \d+\)/g;
while ((mm = re.exec(namesSrc))) names[mm[1]] = mm[2];
function evT(e) { return e.minute * 60 + e.second + (parseFloat(e.timestamp.split(":")[2]) % 1); }
var lastMin = { 1: 0, 2: 0 };
events.forEach(function (e) { if (e.minute > lastMin[e.period]) lastMin[e.period] = e.minute; });

// timeline buckets: first half 0..last, 3 half-time minutes, second half 45..last, 20 minutes after full time
function clockLabel(half, m) {
  if (half === 1) return m === 0 ? "KO" : m >= 45 ? (m === 45 ? "45'" : "45+" + (m - 45) + "'") : m + "'";
  return m === 45 ? "2H KO" : m >= 90 ? (m === 90 ? "90'" : "90+" + (m - 90) + "'") : m + "'";
}
var T = [], bucket = { 1: {}, 2: {} }, m;
for (m = 0; m <= lastMin[1]; m++) { bucket[1][m] = T.length; T.push({ phase: "1H", clock: clockLabel(1, m) }); }
var HT0 = T.length;
for (i = 0; i < 3; i++) T.push({ phase: "HT", clock: "HT" });
var H2 = T.length;
for (m = 45; m <= lastMin[2]; m++) { bucket[2][m] = T.length; T.push({ phase: "2H", clock: clockLabel(2, m) }); }
var FT = T.length - 1;
for (i = 1; i <= 20; i++) T.push({ phase: "POST", clock: "FT+" + i });
var N = T.length;
function idxOf(e) { return bucket[e.period][e.minute]; }

var goals = events.filter(function (e) { return e.type.name === "Shot" && e.shot.outcome && e.shot.outcome.name === "Goal"; })
  .map(function (e) {
    return { i: idxOf(e), team: DISPLAY[teamCode[e.team.id]], scorer: names[String(e.player.id)] || e.player.name,
             clock: e.minute + "'", t: evT(e) };
  });
function afterGoal(i) { var s = 0; goals.forEach(function (g) { if (i >= g.i && i < g.i + 8) s += Math.exp(-(i - g.i) / 2.5); }); return s; }

// live calls the fan app can open each minute (port of find_moments + select_calls over each half)
var WALK = { Pass: 1, Carry: 1, Shot: 1, Dribble: 1 }, ANSWER = { Pass: "PASS", Shot: "SHOOT", Dribble: "DRIBBLE" }, PRIO = { SHOOT: 0, DRIBBLE: 1, PASS: 2 };
var walk = events.filter(function (e) { return WALK[e.type.name]; })
  .sort(function (a, b) { return a.period - b.period || evT(a) - evT(b) || a.index - b.index; });
var moments = [];
walk.forEach(function (e, k) {
  if (e.type.name !== "Pass" || e.pass.outcome || !e.pass.recipient) return;
  var rid = e.pass.recipient.id;
  for (var q = k + 1; q < walk.length; q++) {
    var f = walk[q];
    if (f.period !== e.period || f.player.id !== rid) break;
    var ans = ANSWER[f.type.name];
    if (!ans) continue;
    if (evT(f) - evT(e) >= 3) moments.push({ half: e.period, open: evT(e), lock: evT(f), answer: ans, ev: e });
    break;
  }
});
var opened = []; for (i = 0; i < N; i++) opened.push(0);
var callsByAnswer = { PASS: 0, SHOOT: 0, DRIBBLE: 0 };
[1, 2].forEach(function (h) {
  var acc = [];
  moments.filter(function (c) { return c.half === h; })
    .sort(function (a, b) { return PRIO[a.answer] - PRIO[b.answer] || a.lock - b.lock; })
    .forEach(function (c) {
      if (acc.every(function (a) { return Math.abs(c.open - a.open) >= 6 && (c.open >= a.lock || a.open >= c.lock); })) acc.push(c);
    });
  acc.forEach(function (c) { opened[idxOf(c.ev)]++; callsByAnswer[c.answer]++; });
});
var totalOpened = opened.reduce(function (a, b) { return a + b; }, 0);

var channels = {
  room:    { name: "Friend room invites",   start: 0.82, htSign: 0.26, ftSign: 0.50, newPL: 0.80 },
  pub:     { name: "Partner pubs and bars", start: 0.63, htSign: 0.17, ftSign: 0.38, newPL: 0.79 },
  tv:      { name: "TV broadcast QR",       start: 0.55, htSign: 0.16, ftSign: 0.36, newPL: 0.83 },
  stadium: { name: "Stadium big screen",    start: 0.50, htSign: 0.12, ftSign: 0.27, newPL: 0.46 }
};
// expected scans per timeline index, by channel (QR shown at specific moments)
function scanRate(ch, i) {
  var t = T[i], r = 0;
  if (t.phase === "POST") return 0;
  if (ch === "pub")     r = i === 0 ? 800 : (t.phase === "HT" ? 140 : 48 + 55 * afterGoal(i));
  if (ch === "stadium") r = i === 0 ? 1300 : (t.phase === "HT" ? 520 : 12 + 90 * afterGoal(i));
  if (ch === "tv")      r = i === 0 ? 300 : (t.phase === "HT" ? 600 : 18 + 140 * afterGoal(i));
  if (ch === "room")    r = 0; // generated from inviters below
  return r;
}

var per = []; for (i = 0; i < N; i++) per.push({ idx: i, clock: T[i].clock, phase: T[i].phase, scans: 0, started: 0, live: 0, calls: 0, offered: 0, signups: 0 });

var users = [], id = 0;
function makeUser(ch, i) {
  var u = { id: ++id, channel: ch, scan: i, started: false, start: null, end: null, calls: 0, forecasts: 0, forecastHits: 0, sawHT: false, htDeclined: false, sawFT: false, signup: null, signupTrigger: "", newToPL: rnd() < channels[ch].newPL };
  users.push(u); return u;
}
for (i = 0; i < N; i++) ["pub", "stadium", "tv"].forEach(function (ch) { var n = pois(scanRate(ch, i)); for (var k = 0; k < n; k++) makeUser(ch, i); });

// play each user minute by minute (room invites spawned by players who create rooms)
function play(u) {
  var c = channels[u.channel];
  if (!(rnd() < c.start)) return;
  u.started = true; u.start = Math.min(u.scan + (rnd() < 0.7 ? 0 : 1), FT);
  var hostsRoom = u.channel !== "room" && rnd() < 0.16;
  for (var j = u.start; j <= FT; j++) {
    var t = T[j];
    // half-time save window (design 04b): a sheet opens for everyone who played some of the first half,
    // before anyone leaves at the break. Room members see friends who already saved.
    if (j === HT0 && u.start <= HT0 - 3) {
      u.sawHT = true;
      var ph = c.htSign + 0.04 * Math.min(u.forecastHits, 3) + (u.calls > 20 ? 0.04 : 0);
      if (rnd() < ph) { var r = rnd(); u.signup = HT0 + (r < 0.6 ? 0 : r < 0.85 ? 1 : 2); u.signupTrigger = "half-time"; }
      else u.htDeclined = true;
    }
    // "Maybe later" leaves a chip; a few tap it during the second half
    if (u.htDeclined && u.signup === null && t.phase === "2H" && rnd() < 0.0012) { u.signup = j; u.signupTrigger = "half-time-chip"; }
    // leaving
    var hz = 0.012;
    if (j === HT0) hz = 0.13;
    if (j - u.start < 4) hz += 0.02;              // early bounce
    if (u.channel === "room") hz *= 0.6;
    if (afterGoal(j) > 0.5) hz *= 0.5;
    if (j > u.start && rnd() < hz) {
      u.end = j;
      break;
    }
    // live calls: answer some of the calls that open this minute (more eyes on the phone right after a goal)
    if (t.phase !== "HT" && opened[j] > 0) {
      var pAns = Math.min(0.92, 0.5 * (1 + 0.6 * afterGoal(j)) * (u.channel === "room" ? 1.1 : 1));
      var a = 0; for (var q = 0; q < opened[j]; q++) if (rnd() < pAns) a++;
      u.calls += a; per[j].calls += a; per[j].offered += opened[j];
    }
    else if (t.phase === "HT" && rnd() < 0.5) u.forecasts += 1;     // HT: full-time result locks when the second half starts
    // forecasts open every 10 minutes of play
    if (t.phase !== "HT" && j % 10 === 3 && rnd() < 0.55) { u.forecasts++; if (rnd() < 0.34) u.forecastHits++; }
    // room invites
    if (hostsRoom && j === u.start + 1) { var n = 1 + pois(2.2); for (var k = 0; k < n; k++) { var f = makeUser("room", Math.min(j + pois(3), FT)); play(f); } }
  }
  if (u.end === null) {
    u.end = N - 1; u.sawFT = true;
    var p = c.ftSign + 0.04 * Math.min(u.forecastHits, 3) + (u.calls > 40 ? 0.05 : 0);
    if (u.signup === null && rnd() < p) {
      var delay = 1 + Math.floor(-Math.log(1 - rnd()) * 2.6); // results animation, then save screen
      u.signup = Math.min(FT + delay, N - 1); u.signupTrigger = "full-time";
    }
    // players drift away after FT
    u.end = FT + 1 + Math.floor(-Math.log(1 - rnd()) * 3);
  }
}
var initial = users.slice(); initial.forEach(play);

// ---- aggregate
users.forEach(function (u) {
  per[u.scan].scans++;
  if (!u.started) return;
  per[u.start].started++;
  for (var j = u.start; j <= Math.min(u.end, N - 1); j++) if (j <= FT || j < u.end) per[j].live++;
  if (u.signup !== null) per[u.signup].signups++;
});

var started = users.filter(function (u) { return u.started; });
var funnel = {
  scanned: users.length,
  started: started.length,
  called: started.filter(function (u) { return u.calls > 0; }).length,
  forecast: started.filter(function (u) { return u.forecasts > 0; }).length,
  sawHT: started.filter(function (u) { return u.sawHT; }).length,
  sawFT: started.filter(function (u) { return u.sawFT; }).length,
  sawPrompt: started.filter(function (u) { return u.sawHT || u.sawFT; }).length,
  signedUp: users.filter(function (u) { return u.signup !== null; }).length,
  htSignups: users.filter(function (u) { return u.signupTrigger === "half-time" || u.signupTrigger === "half-time-chip"; }).length,
  ftSignups: users.filter(function (u) { return u.signupTrigger === "full-time"; }).length,
  htSignedLeftEarly: users.filter(function (u) { return (u.signupTrigger === "half-time" || u.signupTrigger === "half-time-chip") && !u.sawFT; }).length
};
var byCh = {}; Object.keys(channels).forEach(function (k) { byCh[k] = { name: channels[k].name, scans: 0, signups: 0 }; });
users.forEach(function (u) { byCh[u.channel].scans++; if (u.signup !== null) byCh[u.channel].signups++; });
var su = users.filter(function (u) { return u.signup !== null; });
var newShare = su.filter(function (u) { return u.newToPL; }).length / su.length;
var trig = {}; su.forEach(function (u) { trig[u.signupTrigger] = (trig[u.signupTrigger] || 0) + 1; });

var series = { scans: [], started: [], live: [], calls: [], signups: [] };
per.forEach(function (p) { for (var k in series) series[k].push(p[k]); });
series.opened = opened;
var chList = ["room", "pub", "tv", "stadium"].map(function (k) { return [byCh[k].name, byCh[k].scans, byCh[k].signups]; });
fs.writeFileSync(path.join(OUT, "data.js"),
  "// Generated by sim/simulate.js. Do not edit by hand.\nwindow.SIM = " +
  JSON.stringify({
    match: { id: "nbr-cal-seed42", home: "ARS", away: "CHE", score: [goals.filter(function (g) { return g.team === "ARS"; }).length, goals.filter(function (g) { return g.team === "CHE"; }).length] },
    timeline: { labels: T.map(function (t) { return t.clock; }), phases: T.map(function (t) { return t.phase; }), HT0: HT0, H2: H2, FT: FT },
    goals: goals.map(function (g) { return { i: g.i, team: g.team, clock: g.clock, scorer: g.scorer }; }),
    calls: { opened: totalOpened, byAnswer: callsByAnswer },
    series: series, funnel: funnel, channels: chList, newShare: +newShare.toFixed(3), triggers: trig
  }) + ";\n");

// CSVs
var csv = ["minute_index,match_clock,phase,qr_scans,games_started,players_online,calls_opened,calls_answered,signups"];
per.forEach(function (p) { csv.push([p.idx, p.clock, p.phase, p.scans, p.started, p.live, opened[p.idx], p.calls, p.signups].join(",")); });
fs.writeFileSync(path.join(OUT, "premier_x_minute_by_minute.csv"), csv.join("\n") + "\n");
var uc = ["user_id,channel,scan_clock,started_game,start_clock,left_clock,calls,forecasts,forecast_hits,saw_halftime_prompt,stayed_to_full_time,signed_up,signup_clock,signup_trigger,new_to_pl_account"];
users.forEach(function (u) { uc.push([u.id, u.channel, T[u.scan].clock, u.started, u.started ? T[u.start].clock : "", u.started ? T[Math.min(u.end, N - 1)].clock : "", u.calls, u.forecasts, u.forecastHits, u.sawHT, u.sawFT, u.signup !== null, u.signup !== null ? T[u.signup].clock : "", u.signupTrigger, u.newToPL].join(",")); });
fs.writeFileSync(path.join(OUT, "premier_x_users.csv"), uc.join("\n") + "\n");

// report
var mx = 0, mi = 0; per.forEach(function (p) { if (p.live > mx) { mx = p.live; mi = p.idx; } });
var cum = 0, suFT = 0, su10 = 0; per.forEach(function (p) { cum += p.signups; if (p.idx === FT) suFT = cum; if (p.idx === FT + 10) su10 = cum; });
console.log("timeline N", N, "HT0", HT0, "H2", H2, "FT", FT, "goals", JSON.stringify(goals.map(function (g) { return [g.i, T[g.i].clock, g.team, g.scorer]; })));
console.log("calls opened", totalOpened, JSON.stringify(callsByAnswer), "per min max", Math.max.apply(null, opened));
console.log("funnel", JSON.stringify(funnel));
console.log("live@23", per[23].live, "peak", mx, "at", T[mi].clock, "totalCalls", per.reduce(function (a, p) { return a + p.calls; }, 0));
console.log("signups before FT", suFT, "within FT+10", su10 - suFT);
console.log("channels", JSON.stringify(byCh)); console.log("newShare", newShare.toFixed(3), "triggers", JSON.stringify(trig));
