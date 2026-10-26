// Matchday Call ops dashboard: seeded per-user simulation of one match (ARS 2-1 CHE, MW9).
// Run: node frontend/ops-dashboard/sim/simulate.js
// Writes ../data/data.js (loaded by index.html) and two CSVs. Same seed -> same numbers every run.
var fs = require("fs");
var path = require("path");
var OUT = path.join(__dirname, "..", "data");
var seed = 20261026;
function rnd() { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; }
function pois(l) { var L = Math.exp(-l), k = 0, p = 1; do { k++; p *= rnd(); } while (p > L); return k - 1; }

// timeline: 0..45 first half, 46..48 HT, 49..97 second half (46'..94'), 98..117 FT+1..+20
var T = [], m, i;
for (m = 0; m <= 45; m++) T.push({ phase: "1H", clock: m === 0 ? "KO" : m + "'" });
for (i = 0; i < 3; i++) T.push({ phase: "HT", clock: "HT" });
for (m = 46; m <= 94; m++) T.push({ phase: "2H", clock: m > 90 ? "90+" + (m - 90) + "'" : m + "'" });
for (i = 1; i <= 20; i++) T.push({ phase: "POST", clock: "FT+" + i });
var N = T.length, FT = 97, HT0 = 46;
var goals = [{ i: 18, team: "ARS", clock: "18'" }, { i: 57, team: "CHE", clock: "54'" }, { i: 80, team: "ARS", clock: "77'" }];
function afterGoal(i) { var s = 0; goals.forEach(function (g) { if (i >= g.i && i < g.i + 8) s += Math.exp(-(i - g.i) / 2.5); }); return s; }

var channels = {
  room:    { name: "Friend room invites",   start: 0.82, ftSign: 0.50, newPL: 0.80 },
  pub:     { name: "Partner pubs and bars", start: 0.63, ftSign: 0.38, newPL: 0.79 },
  tv:      { name: "TV broadcast QR",       start: 0.55, ftSign: 0.36, newPL: 0.83 },
  stadium: { name: "Stadium big screen",    start: 0.50, ftSign: 0.27, newPL: 0.46 }
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

var users = [], id = 0;
function makeUser(ch, i) {
  var u = { id: ++id, channel: ch, scan: i, started: false, start: null, end: null, calls: 0, forecasts: 0, forecastHits: 0, sawFT: false, signup: null, signupTrigger: "", newToPL: rnd() < channels[ch].newPL };
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
    // leaving
    var hz = 0.012;
    if (t.phase === "HT" && j === HT0) hz = 0.13;
    if (j - u.start < 4) hz += 0.02;              // early bounce
    if (u.channel === "room") hz *= 0.6;
    if (afterGoal(j) > 0.5) hz *= 0.5;
    if (j > u.start && rnd() < hz) {
      u.end = j;
      // exit prompt "save points before you go" (only if they had points)
      if (u.calls >= 3 && rnd() < 0.07) { u.signup = j; u.signupTrigger = "exit-save"; }
      break;
    }
    // calls
    if (t.phase !== "HT") u.calls += pois(0.75 * (1 + 1.4 * afterGoal(j)));
    else if (rnd() < 0.5) u.forecasts += 1;     // HT: second-half forecast round
    // forecasts open every 10 minutes of play
    if (t.phase !== "HT" && j % 10 === 3 && rnd() < 0.55) { u.forecasts++; if (rnd() < 0.34) u.forecastHits++; }
    // room invites
    if (hostsRoom && j === u.start + 1) { var n = 1 + pois(2.2); for (var k = 0; k < n; k++) { var f = makeUser("room", Math.min(j + pois(3), FT)); play(f); } }
  }
  if (u.end === null) {
    u.end = N - 1; u.sawFT = true;
    var p = c.ftSign + 0.04 * Math.min(u.forecastHits, 3) + (u.calls > 40 ? 0.05 : 0);
    if (rnd() < p) {
      var delay = 1 + Math.floor(-Math.log(1 - rnd()) * 2.6); // results animation, then save screen
      u.signup = Math.min(FT + delay, N - 1); u.signupTrigger = "full-time";
    }
    // players drift away after FT
    u.end = FT + 1 + Math.floor(-Math.log(1 - rnd()) * 3);
  }
}
var initial = users.slice(); initial.forEach(play);

// ---- aggregate
var per = []; for (i = 0; i < N; i++) per.push({ idx: i, clock: T[i].clock, phase: T[i].phase, scans: 0, started: 0, live: 0, calls: 0, signups: 0 });
// calls per minute: recompute by spreading each user's calls over their active minutes with goal weighting
users.forEach(function (u) {
  per[u.scan].scans++;
  if (!u.started) return;
  per[u.start].started++;
  var last = Math.min(u.end, FT), w = [], ws = 0, j;
  for (j = u.start; j <= last; j++) { var x = T[j].phase === "HT" ? 0.05 : 1 + 1.4 * afterGoal(j); w.push(x); ws += x; }
  for (j = u.start; j <= Math.min(u.end, N - 1); j++) if (j <= FT || T[j].phase === "POST" && j < u.end) per[j].live++;
  for (j = u.start; j <= last; j++) per[j].calls += u.calls * w[j - u.start] / ws;
  if (u.signup !== null) per[u.signup].signups++;
});
per.forEach(function (p) { p.calls = Math.round(p.calls); });

var started = users.filter(function (u) { return u.started; });
var funnel = {
  scanned: users.length,
  started: started.length,
  called: started.filter(function (u) { return u.calls > 0; }).length,
  forecast: started.filter(function (u) { return u.forecasts > 0; }).length,
  sawFT: started.filter(function (u) { return u.sawFT; }).length,
  signedUp: users.filter(function (u) { return u.signup !== null; }).length
};
var byCh = {}; Object.keys(channels).forEach(function (k) { byCh[k] = { name: channels[k].name, scans: 0, signups: 0 }; });
users.forEach(function (u) { byCh[u.channel].scans++; if (u.signup !== null) byCh[u.channel].signups++; });
var su = users.filter(function (u) { return u.signup !== null; });
var newShare = su.filter(function (u) { return u.newToPL; }).length / su.length;
var trig = {}; su.forEach(function (u) { trig[u.signupTrigger] = (trig[u.signupTrigger] || 0) + 1; });

var out = { N: N, FT: FT, goals: goals, per: per, funnel: funnel, channels: byCh, newShare: newShare, triggers: trig };
var series = { scans: [], started: [], live: [], calls: [], signups: [] };
per.forEach(function (p) { for (var k in series) series[k].push(p[k]); });
var chList = ["room", "pub", "tv", "stadium"].map(function (k) { return [byCh[k].name, byCh[k].scans, byCh[k].signups]; });
fs.writeFileSync(path.join(OUT, "data.js"),
  "// Generated by sim/simulate.js. Do not edit by hand.\nwindow.SIM = " +
  JSON.stringify({ series: series, funnel: funnel, channels: chList, newShare: +newShare.toFixed(3), triggers: trig }) + ";\n");

// CSVs
var csv = ["minute_index,match_clock,phase,qr_scans,games_started,players_online,calls,signups"];
per.forEach(function (p) { csv.push([p.idx, p.clock, p.phase, p.scans, p.started, p.live, p.calls, p.signups].join(",")); });
fs.writeFileSync(path.join(OUT, "matchday_call_minute_by_minute.csv"), csv.join("\n") + "\n");
var uc = ["user_id,channel,scan_clock,started_game,start_clock,left_clock,calls,forecasts,forecast_hits,stayed_to_full_time,signed_up,signup_clock,signup_trigger,new_to_pl_account"];
users.forEach(function (u) { uc.push([u.id, u.channel, T[u.scan].clock, u.started, u.started ? T[u.start].clock : "", u.started ? T[Math.min(u.end, N - 1)].clock : "", u.calls, u.forecasts, u.forecastHits, u.sawFT, u.signup !== null, u.signup !== null ? T[u.signup].clock : "", u.signupTrigger, u.newToPL].join(",")); });
fs.writeFileSync(path.join(OUT, "matchday_call_users.csv"), uc.join("\n") + "\n");

// report
var mx = 0, mi = 0; per.forEach(function (p) { if (p.live > mx) { mx = p.live; mi = p.idx; } });
var cum = 0, suFT = 0, su10 = 0; per.forEach(function (p) { cum += p.signups; if (p.idx === FT) suFT = cum; if (p.idx === FT + 10) su10 = cum; });
console.log("funnel", JSON.stringify(funnel));
console.log("live@23", per[23].live, "peak", mx, "at", T[mi].clock, "maxCalls", Math.max.apply(null, per.map(function (p) { return p.calls; })), "totalCalls", per.reduce(function (a, p) { return a + p.calls; }, 0));
console.log("signups before FT", suFT, "within FT+10", su10 - suFT, "share", ((su10 - suFT) / cum).toFixed(3));
console.log("channels", JSON.stringify(byCh)); console.log("newShare", newShare.toFixed(3), "triggers", JSON.stringify(trig));
console.log("HT signups", per[46].signups, per[47].signups, per[48].signups);
