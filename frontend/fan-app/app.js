// Matchday Call fan app: landing -> pick a team -> live calls over WS /demo/ws.
// The server drives all timing; this file only reacts to its messages.
"use strict";

const $ = (id) => document.getElementById(id);
const TEAM_NAMES = { ARS: "Arsenal", CHE: "Chelsea" };
const POP_MS = 1600;      // points pop-up over the pitch
const GOAL_MS = 2600;     // goal banner
const JUMP_MS = 2000;     // jump card (JUMP_CARD_S on the server)
const RESULT_HOLD_MS = 4000; // result stays on the call card, then back to "reading the play"
const SWITCH_GUARD_MS = 600; // taps ignored on call options right after the auto-switch to REAL TIME
const SILENCE_MS = 40000; // no message for this long (half time is 10 s) = connection lost

let team = null;          // ARS or CHE, picked on screen 2
let ws = null;            // current socket; messages from older sockets are ignored
let s = null;             // live state of the current run
const timers = {};

function later(name, ms, fn) {
  clearTimeout(timers[name]);
  timers[name] = setTimeout(fn, ms);
}

function clearTimers() {
  Object.keys(timers).forEach((k) => clearTimeout(timers[k]));
}

function show(screen) {
  ["landing", "pick", "live"].forEach((id) => { $(id).hidden = id !== screen; });
  window.scrollTo(0, 0);
}

function scoreText(score) {
  return score[0] + " – " + score[1];
}

// ............................................................ screens 1 and 2

$("go-pick").onclick = () => show("pick");
$("back").onclick = () => show("landing");
document.querySelectorAll(".team-btn").forEach((btn) => {
  btn.onclick = () => {
    team = btn.dataset.team;
    document.querySelectorAll(".team-btn").forEach((b) => {
      b.setAttribute("aria-pressed", String(b === btn));
    });
    $("pick-summary").textContent = "You're backing " + TEAM_NAMES[team] + ". Kick-off as soon as you tap.";
    $("start").disabled = false;
  };
});
$("start").onclick = () => { if (team) startRun(); };
$("again").onclick = () => startRun();

// ............................................................ socket

function startRun() {
  if (ws) {
    const old = ws;
    ws = null;               // detach first so its close does not show "connection lost"
    try { old.close(); } catch (e) { /* already closed */ }
  }
  clearTimers();
  s = { score: [0, 0], total: 0, streak: 0, call: null, picks: {}, right: 0, calls: 0, finished: false,
    tab: "live", pops: [],
    fc: { live: false, info: null, cards: {}, points: 0,
      final: { ft: null, hs: 0, aw: 0, flash: "", saved: null, locked: false, res: null } } };
  resetLiveUi();
  show("live");

  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/demo/ws";
  const sock = new WebSocket(url);
  ws = sock;
  sock.onopen = () => send({ type: "start", data: { team: team } });
  later("silence", SILENCE_MS, () => dropSocket(sock));
  sock.onmessage = (ev) => {
    if (sock !== ws) return;
    later("silence", SILENCE_MS, () => dropSocket(sock));
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (!msg || typeof msg.type !== "string") return;
    try { handle(msg.type, msg.data || {}); } catch (e) { console.error(msg.type, e); }
  };
  sock.onclose = (ev) => dropSocket(sock, ev.code);
}

// The socket closed, or went silent: show "connection lost" unless the match finished.
function dropSocket(sock, code) {
  if (sock !== ws) return;
  ws = null;
  try { sock.close(); } catch (e) { /* already closed */ }
  if (!s.finished) connectionLost(code);
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function handle(type, d) {
  // play resumed after half time (forecast replies can arrive during the break)
  if (type !== "demo.half_time" && !type.startsWith("forecast.")) $("modal").hidden = true;
  switch (type) {
    case "demo.session": onSession(d); break;
    case "demo.clock": onClock(d); break;
    case "demo.event": onEvent(d); break;
    case "demo.shape": onShape(d); break;
    case "demo.goal": onGoal(d); break;
    case "demo.jump": onJump(d); break;
    case "call.open": onCallOpen(d); break;
    case "call.locked": onCallLocked(d); break;
    case "call.result": onCallResult(d); break;
    case "demo.half_time": onBreak("half", d); break;
    case "demo.full_time": onBreak("full", d); break;
    case "forecast.locked": onForecastLocked(d); break;
    case "forecast.rejected": onForecastRejected(d); break;
    case "forecast.result": onForecastResult(d); break;
    case "forecast.final_saved": s.fc.final.saved = d; renderForecasts(); break;
    case "forecast.final_locked": onFinalLocked(d); break;
    case "forecast.final_result": onFinalResult(d); break;
    default: break; // unknown types are ignored
  }
}

// ............................................................ live screen

function resetLiveUi() {
  $("clock").textContent = "KICK-OFF";
  $("score").textContent = "0 – 0";
  $("total").textContent = "0";
  $("backing").textContent = "Backing " + TEAM_NAMES[team];
  ["gain", "jump", "goal", "pop", "modal"].forEach((id) => { $(id).hidden = true; });
  showTag(false);
  $("players").textContent = ""; // no dots left over from the previous run
  setStreak(0);
  moveBall([52.5, 34], null);
  showStandby();
  setTab("live");
  $("tab-dot").hidden = true;
  $("fc-cards").textContent = "";
  $("ft-opts").textContent = "";
  renderForecasts();
}

function onSession(d) {
  const you = d.you && TEAM_NAMES[d.you.team];
  if (you) $("backing").textContent = "Backing " + you;
  buildForecasts(d.forecasts);
}

function onClock(d) {
  if (typeof d.clock === "string") $("clock").textContent = "LIVE " + d.clock;
  if (Array.isArray(d.score)) setScore(d.score);
  setLive(true);
}

function setScore(score) {
  s.score = score;
  $("score").textContent = scoreText(score);
  const f = s.fc.final; // the full-time pick can never be below the live score
  if (f.locked || f.res || (f.hs >= score[0] && f.aw >= score[1])) return;
  f.hs = Math.max(f.hs, score[0]);
  f.aw = Math.max(f.aw, score[1]);
  if (f.ft) {
    const ft = resultOf(f.hs, f.aw);
    setFinal(ft, ft !== f.ft ? "result" : ""); // explain a result the live score forced
  }
  else renderForecasts();
}

// Place the ball at xy, then glide to end_xy. A null keeps the ball where it is.
function onEvent(d) {
  moveBall(d.xy, d.end_xy);
  showTag(Boolean(d.player));
  if (!d.player) return;
  const text = $("tag-text");
  text.textContent = d.player;
  let w = d.player.length * 1.8;
  try { w = text.getComputedTextLength(); } catch (e) { /* not rendered yet */ }
  placeTag(w + 2.4);
}

// Draw the 22 shirt-number dots. Dots are keyed team-num; any not in this shape are hidden.
const SVG_NS = "http://www.w3.org/2000/svg";

function onShape(d) {
  if (!Array.isArray(d.players)) return;
  const seen = new Set();
  d.players.forEach((p) => {
    if (!p || !TEAM_NAMES[p.team]) return;
    const key = p.team + "-" + p.num;
    seen.add(key);
    let g = $("pl-" + key);
    if (!g) {
      g = document.createElementNS(SVG_NS, "g");
      g.id = "pl-" + key;
      g.setAttribute("class", "pl " + p.team);
      const c = document.createElementNS(SVG_NS, "circle");
      c.setAttribute("r", "1.6");
      const t = document.createElementNS(SVG_NS, "text");
      t.textContent = String(p.num);
      g.append(c, t);
      $("players").append(g);
    }
    const x = Math.max(0, Math.min(105, Number(p.x) || 0));
    const y = Math.max(0, Math.min(68, Number(p.y) || 0));
    g.style.transform = "translate(" + x + "px, " + y + "px)";
    g.style.display = "";
  });
  Array.from($("players").children).forEach((g) => {
    if (!seen.has(g.id.slice(3))) g.style.display = "none";
  });
}

function moveBall(xy, endXy) {
  const g = $("ball");
  if (Array.isArray(xy)) {
    g.classList.add("snap");
    setBall(xy);
    void g.getBoundingClientRect(); // commit the jump before the glide starts
    g.classList.remove("snap");
  }
  if (Array.isArray(endXy)) setBall(endXy);
}

function setBall(xy) {
  const x = Math.max(1, Math.min(104, Number(xy[0]) || 0));
  const y = Math.max(1, Math.min(67, Number(xy[1]) || 0));
  $("ball").style.transform = "translate(" + x + "px, " + y + "px)";
  $("ball").dataset.x = x;
  $("ball").dataset.y = y;
}

// The label is SVG, which has no .hidden property; toggle the attribute instead.
function showTag(on) {
  if (on) $("tag").removeAttribute("hidden");
  else $("tag").setAttribute("hidden", "");
}

// Keep the name label inside the pitch: above the ball, below it near the top edge.
function placeTag(w) {
  const x = Number($("ball").dataset.x), y = Number($("ball").dataset.y);
  let left = -w / 2;
  if (x + left < 1) left = 1 - x;
  if (x + left + w > 104) left = 104 - x - w;
  const top = y < 9 ? 3 : -7.6;
  $("tag-bg").setAttribute("x", left);
  $("tag-bg").setAttribute("y", top);
  $("tag-bg").setAttribute("width", w);
  const text = $("tag-text");
  text.setAttribute("x", left + 1.2);
  text.setAttribute("y", top + 2.3);
}

function onGoal(d) {
  if (Array.isArray(d.score)) setScore(d.score);
  const name = TEAM_NAMES[d.team] || "";
  $("goal-line").textContent = (d.player ? d.player + " scores for " : "Goal for ") + name + " · " + scoreText(s.score);
  if (!$("pop").hidden) { // goal first, then the pop it interrupted
    $("pop").hidden = true;
    clearTimeout(timers.pop);
    s.pops.unshift(s.lastPop);
  }
  const el = $("goal");
  el.hidden = true; void el.offsetWidth; el.hidden = false; // restart the animation
  later("goal", GOAL_MS, () => { el.hidden = true; nextPop(); });
}

function onJump(d) {
  $("jump-title").textContent = d.title || "";
  $("jump-clock").textContent = d.clock ? "Match clock " + d.clock : "";
  $("jump").hidden = false;
  showTag(false);
  if (typeof d.clock === "string") $("clock").textContent = "LIVE " + d.clock;
  showStandby(); // a call never spans a jump
  setLive(false);
  later("jump", JUMP_MS, () => { $("jump").hidden = true; });
}

// ............................................................ calls

function showStandby() {
  if (s) s.call = null;
  $("standby").hidden = false;
  $("ask").hidden = true;
}

function optionButtons() {
  return Array.from(document.querySelectorAll("#ask .opt"));
}

const SUBS = { PASS: "to a teammate", SHOOT: "at goal", DRIBBLE: "run with it" };

function onCallOpen(d) {
  if (typeof d.call_id !== "string") return;
  clearTimeout(timers.result);
  if (s.tab === "fc") { // never let a live call go unseen
    setTab("live");
    s.switchedAt = Date.now(); // a tap meant for the forecast panel must not land on a call
  }
  s.call = { id: d.call_id, player: d.player || "the player", locked: false, result: false };
  const prompt = String(d.prompt || "What's the call?");
  const cut = prompt.indexOf(". ");
  $("call-ctx").textContent = cut > 0 ? prompt.slice(0, cut + 1) : "";
  $("call-q").textContent = cut > 0 ? prompt.slice(cut + 2) : prompt;
  $("call-team").textContent = (TEAM_NAMES[d.team] || "").toUpperCase();
  $("call-team").hidden = !TEAM_NAMES[d.team];
  setStatus("● OPEN · LOCKS WHEN " + s.call.player.toUpperCase() + " PLAYS IT", false);
  setLine("Call it before the ball moves.", false);
  optionButtons().forEach((b) => {
    b.className = "opt";
    b.disabled = false;
    b.querySelector("small").textContent = SUBS[b.dataset.pick];
  });
  $("standby").hidden = true;
  $("ask").hidden = false;
}

optionButtons().forEach((btn) => {
  btn.onclick = () => {
    const call = s && s.call;
    if (!call || call.locked || s.picks[call.id]) return;
    if (Date.now() - (s.switchedAt || 0) < SWITCH_GUARD_MS) return;
    s.picks[call.id] = btn.dataset.pick;
    send({ type: "call.answer", data: { call_id: call.id, pick: btn.dataset.pick } });
    optionButtons().forEach((b) => {
      b.disabled = true;
      b.classList.add(b === btn ? "mine" : "faded");
    });
    btn.querySelector("small").textContent = "YOUR CALL";
    setStatus("LOCKED IN · WAITING FOR THE PLAY", false);
    setLine("Points land the moment it happens.", false);
  };
});

function onCallLocked(d) {
  const call = s.call;
  if (!call || call.id !== d.call_id) return;
  call.locked = true;
  optionButtons().forEach((b) => { b.disabled = true; });
  if (!s.picks[call.id]) setStatus("CALL CLOSED", true);
}

function onCallResult(d) {
  // Totals come from the server, so update them even if the card has moved on.
  if (typeof d.total === "number") setTotal(d.total, d.points);
  if (typeof d.streak === "number") setStreak(d.streak);
  s.calls += 1;
  if (d.correct) s.right += 1;
  showPop(d);
  const call = s.call;
  if (!call || call.id !== d.call_id) return;
  call.locked = true;
  markOptions(d.answer, d.pick);
  setStatus("CALL CLOSED", true);
  setLine(resultLine(d), true);
  later("result", RESULT_HOLD_MS, showStandby);
}

function markOptions(answer, pick) {
  optionButtons().forEach((b) => {
    const p = b.dataset.pick, small = b.querySelector("small");
    b.disabled = true;
    b.className = "opt";
    if (p === answer) {
      b.classList.add("answer");
      small.textContent = p === pick ? "YOU CALLED IT" : "IT WAS THIS";
    } else if (p === pick) {
      b.classList.add("wrong");
      small.textContent = "YOUR CALL";
    } else {
      b.classList.add("faded");
    }
  });
}

function resultLine(d) {
  const answer = String(d.answer || "").toUpperCase();
  if (d.too_quick) return "Too quick! It was a " + answer + ". No points lost, your streak is safe.";
  if (d.correct) return d.streak >= 3 ? "On fire! " + d.streak + " right in a row." : "Nailed it. Keep the streak going.";
  return "So close. It was a " + answer + ". +10 just for playing.";
}

function setStatus(text, done) {
  $("call-status").textContent = text;
  $("call-status").classList.toggle("done", done);
}

function setLine(text, done) {
  $("call-line").textContent = text;
  $("call-line").classList.toggle("done", done);
}

// ............................................................ points

function setTotal(total, gained) {
  s.total = total;
  $("total").textContent = total.toLocaleString("en-US");
  if (gained > 0) {
    const gain = $("gain"), wrap = $("total-wrap");
    gain.textContent = "+" + fmt(gained);
    gain.hidden = true; void gain.offsetWidth; gain.hidden = false;
    wrap.classList.remove("bump"); void wrap.offsetWidth; wrap.classList.add("bump");
    later("gain", 1300, () => { gain.hidden = true; });
  }
}

function setStreak(streak) {
  s && (s.streak = streak);
  document.querySelectorAll("#pips i").forEach((pip, i) => pip.classList.toggle("on", i < streak));
  $("streak-hint").textContent = streak >= 3 ? "On fire: bonus on every right call" : "3 right in a row = bonus";
}

function ribbon(pts, text, color) {
  const div = document.createElement("div");
  div.className = "ribbon rib1";
  const a = document.createElement("span");
  a.style.color = color;
  a.textContent = pts;
  const b = document.createElement("span");
  b.textContent = text;
  div.append(a, b);
  return div;
}

// One pop at a time: later ones wait in s.pops (and all wait for the goal banner).
function showPop(d) {
  if (!$("goal").hidden || !$("pop").hidden) { s.pops.push(d); return; }
  s.lastPop = d;
  const pts = $("pop-pts"), list = $("pop-ribbons");
  list.textContent = "";
  pts.className = "pop-pts pop";
  if (d.fc) {
    if (d.points > 0) pts.classList.add("good");
    pts.textContent = "+" + fmt(d.points);
    $("pop-label").textContent = d.label;
    list.append(ribbon("+" + fmt(d.points), d.line, d.points > 0 ? "#00FF85" : "#CDB8D1"));
  } else if (d.too_quick) {
    pts.classList.add("quick");
    pts.textContent = "TOO QUICK!";
    $("pop-label").textContent = "";
    list.append(ribbon("+0", "IT WAS A " + String(d.answer || "").toUpperCase() + " · STREAK SAFE", "#CDB8D1"));
  } else if (d.correct) {
    pts.classList.add("good");
    pts.textContent = "+" + d.points;
    $("pop-label").textContent = "CORRECT CALL";
    list.append(ribbon("+10", "CALLED IT", "#EEDFF0"), ribbon("+100", "READ THE PLAY", "#00FF85"));
    if (d.streak_bonus > 0) list.append(ribbon("+" + d.streak_bonus, "STREAK x" + d.streak, "#00FF85"));
    if (navigator.vibrate) navigator.vibrate(30);
  } else {
    pts.textContent = "+" + (d.points || 0);
    $("pop-label").textContent = "CALLED IT";
    list.append(ribbon("+" + (d.points || 0), "TOOK PART", "#EEDFF0"));
  }
  const el = $("pop");
  el.hidden = true; void el.offsetWidth; el.hidden = false;
  later("pop", POP_MS, () => { el.hidden = true; nextPop(); });
}

function nextPop() {
  const d = s.pops.shift();
  if (d) showPop(d);
}

// ............................................................ forecasts

const FT_NAMES = { ars: "an Arsenal win", draw: "a draw", che: "a Chelsea win" };
const fmt = (n) => (Number(n) || 0).toLocaleString("en-US");
const resultOf = (hs, aw) => (hs > aw ? "ars" : hs === aw ? "draw" : "che");

function el(tag, cls, text) {
  const e = document.createElement(tag);
  e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function setTab(tab) {
  s.tab = tab;
  const fc = tab === "fc";
  $("tab-live").classList.toggle("on", !fc);
  $("tab-fc").classList.toggle("on", fc);
  $("rt").hidden = fc;
  $("streak").hidden = fc;
  $("fc").hidden = !fc;
  if (fc) $("tab-dot").hidden = true;
}
$("tab-live").onclick = $("fc-back").onclick = () => { if (s) setTab("live"); };
$("tab-fc").onclick = () => { if (s) setTab("fc"); };
$("ht-pick").onclick = () => { $("modal").hidden = true; setTab("fc"); };

// Window picks open on demo.clock and close on jumps, half time and full time.
function setLive(on) {
  s.fc.live = on;
  renderForecasts();
}

function settled() {
  if (s.tab !== "fc") $("tab-dot").hidden = false;
}

function optButton(o, onclick) {
  const b = el("button", "opt", String(o.label || ""));
  b.dataset.pick = o.id;
  b.onclick = onclick;
  return b;
}

function buildForecasts(info) {
  if (!info || !Array.isArray(info.windows) || !info.final) return;
  s.fc.info = info;
  s.fc.cards = {};
  $("fc-cards").textContent = "";
  info.windows.forEach((w) => {
    const card = el("div", "card fc-card");
    const top = el("div", "row-between"), left = el("div", "badges");
    left.append(el("span", "badge", w.label), el("span", "muted small fc-range"));
    top.append(left, el("span", "fc-pts", "+" + fmt(w.points)));
    const opts = el("div", "fc-opts");
    w.options.forEach((o) => opts.append(optButton(o, () => pickWindow(w.key, o.id))));
    card.append(top, el("div", "fc-q", w.question), opts, el("div", "fc-note"));
    $("fc-cards").append(card);
    s.fc.cards[w.key] = { def: w, el: card, state: "open", pick: null, range: "", res: null };
  });
  $("ft-pts").textContent = "+" + fmt(info.final.points);
  $("ft-exact").textContent = "+" + fmt(info.final.exact_points) + " extra if you nail it";
  $("ft-opts").textContent = "";
  info.final.options.forEach((o) => $("ft-opts").append(optButton(o, () => pickResult(o.id))));
  renderForecasts();
}

function pickWindow(key, pick) {
  const c = s.fc.cards[key];
  if (!c || c.state !== "open" || !s.fc.live) return;
  Object.assign(c, { state: "sending", pick: pick });
  send({ type: "forecast.pick", data: { key: key, pick: pick } });
  renderForecasts();
}

function onForecastLocked(d) {
  const c = s.fc.cards[d.key];
  if (!c) return;
  Object.assign(c, { state: "locked", pick: d.pick, range: d.from_clock + "–" + d.to_clock, to: d.to_clock });
  renderForecasts();
}

function onForecastRejected(d) {
  if (d.key === "final") return onFinalRejected(d);
  const c = s.fc.cards[d.key];
  if (c && c.state === "sending") Object.assign(c, { state: "open", pick: null });
  if (d.reason === "not_live") setLive(false); // "Opens when play resumes" until the next clock
  else renderForecasts();
}

function onForecastResult(d) {
  if (typeof d.total === "number") setTotal(d.total, d.points);
  s.fc.points += Number(d.points) || 0;
  settled();
  const c = s.fc.cards[d.key];
  showPop({ fc: true, points: d.points, label: c ? c.def.label : "FORECAST",
    line: d.correct ? "FORECAST HIT" : d.outcome === "none" ? "NO SHOTS · NO POINTS LOST" : "NOT THIS TIME" });
  if (!c) return;
  Object.assign(c, { state: "result", pick: d.pick, res: d });
  renderForecasts();
  later("fc-" + d.key, RESULT_HOLD_MS, () => {
    Object.assign(c, { state: "open", pick: null, range: "", res: null });
    renderForecasts();
  });
}

// Full time: result and exact score are linked (design Forecast.dc.html pickResult / bump).
function pickResult(id) {
  const f = s.fc.final;
  if (f.locked || f.res || f.ft === id) return;
  let hs = f.hs, aw = f.aw;
  if (id === "ars" && hs <= aw) hs = aw + 1;
  if (id === "draw") hs = aw = Math.max(hs, aw);
  if (id === "che" && aw <= hs) aw = hs + 1;
  if (hs > 9 || aw > 9) return;
  Object.assign(f, { hs: hs, aw: aw });
  setFinal(id, "score");
}

function bump(field, step) {
  const f = s.fc.final;
  if (f.locked || f.res) return;
  const min = field === "hs" ? s.score[0] : s.score[1];
  f[field] = Math.max(min, Math.min(9, f[field] + step));
  const ft = resultOf(f.hs, f.aw);
  setFinal(ft, ft !== f.ft ? "result" : "");
}
[["hs-up", "hs", 1], ["hs-down", "hs", -1], ["aw-up", "aw", 1], ["aw-down", "aw", -1]].forEach(([id, field, step]) => {
  $(id).onclick = () => { if (s) bump(field, step); };
});

// Show the new pick now; send it once the taps settle. final_saved is what counts.
function setFinal(ft, flash) {
  const f = s.fc.final;
  Object.assign(f, { ft: ft, flash: flash });
  renderForecasts();
  later("final", 300, () => send({ type: "forecast.final", data: { result: f.ft, home: f.hs, away: f.aw } }));
}

function onFinalRejected(d) {
  const f = s.fc.final;
  if (d.reason === "locked") f.locked = true;
  else if (f.saved) Object.assign(f, { ft: f.saved.result, hs: f.saved.home, aw: f.saved.away, flash: "" });
  else Object.assign(f, { ft: null, hs: s.score[0], aw: s.score[1], flash: "" });
  renderForecasts();
}

function onFinalLocked(d) {
  const f = s.fc.final;
  clearTimeout(timers.final);
  f.locked = true;
  f.saved = d.result ? d : null;
  if (f.saved) Object.assign(f, { ft: d.result, hs: d.home, aw: d.away, flash: "" });
  else f.ft = null; // nothing reached the server before the lock
  renderForecasts();
}

function onFinalResult(d) {
  if (typeof d.total === "number") setTotal(d.total, d.points);
  s.fc.points += Number(d.points) || 0;
  s.fc.final.res = d;
  settled();
  showPop({ fc: true, points: d.points, label: "FULL TIME",
    line: d.exact_points > 0 ? "RESULT AND EXACT SCORE" : d.points > 0 ? "RESULT RIGHT" : "NOT THIS TIME" });
  renderForecasts();
}

function renderForecasts() {
  let riding = 0;
  Object.values(s.fc.cards).forEach((c) => {
    renderCard(c);
    if (c.state === "locked") riding += c.def.points;
  });
  const f = s.fc.final, info = s.fc.info;
  if (f.saved && !f.res && info) riding += info.final.points + info.final.exact_points;
  $("fc-potential").textContent = "+" + fmt(riding);
  renderFinal();
}

function setNote(node, text, cls) {
  node.textContent = text;
  node.className = "fc-note" + (cls ? " " + cls : "");
}

function renderCard(c) {
  c.el.classList.toggle("on", c.state !== "open");
  c.el.querySelector(".fc-range").textContent = c.range;
  c.el.querySelectorAll(".opt").forEach((b) => {
    const p = b.dataset.pick;
    b.className = "opt";
    b.disabled = c.state !== "open" || !s.fc.live;
    if (c.state === "open") return;
    if (c.state === "result" && p === c.res.outcome) b.classList.add("answer");
    else if (p === c.pick) b.classList.add(c.state === "result" ? "wrong" : "mine");
    else b.classList.add("faded");
  });
  const note = c.el.querySelector(".fc-note");
  if (c.state === "result") setNote(note, c.res.correct ? "Forecast hit +" + fmt(c.res.points) : String(c.res.text || ""), c.res.correct ? "hit" : "");
  else if (c.state === "locked") setNote(note, "LOCKED IN · SCORED AT " + c.to, "lock");
  else if (c.state === "sending") setNote(note, "Locking in…", "");
  else if (!s.fc.live) setNote(note, "Opens when play resumes", "");
  else setNote(note, "Scored over the next " + c.def.minutes + " match minutes.", "");
}

function finalText(r) {
  const opt = s.fc.info && s.fc.info.final.options.find((o) => o.id === r.result);
  return (opt ? opt.label : "") + " " + r.home + " – " + r.away;
}

function renderFinal() {
  const f = s.fc.final, off = f.locked || Boolean(f.res);
  const answer = f.res && Array.isArray(f.res.final) ? resultOf(f.res.final[0], f.res.final[1]) : null;
  $("ft-card").classList.toggle("on", Boolean(f.ft));
  $("ft-opts").querySelectorAll(".opt").forEach((b) => {
    const p = b.dataset.pick;
    b.disabled = off;
    b.className = "opt" + (p === answer ? " answer" : p === f.ft ? (answer ? " wrong" : " mine") : f.ft ? " faded" : "");
  });
  $("hs").textContent = f.hs;
  $("aw").textContent = f.aw;
  $("hs-up").disabled = off || f.hs >= 9;
  $("aw-up").disabled = off || f.aw >= 9;
  $("hs-down").disabled = off || f.hs <= s.score[0];
  $("aw-down").disabled = off || f.aw <= s.score[1];
  $("steppers").classList.toggle("off", !f.ft);
  const note = $("ft-note");
  if (f.res) {
    const fin = (f.res.final || []).join(" – ");
    setNote(note, f.res.points > 0 ? "Forecast hit +" + fmt(f.res.points) + (f.res.exact_points > 0 ? ", exact score too!" : "")
      : "Not this time. It ended " + fin + ".", f.res.points > 0 ? "hit" : "");
  } else if (f.locked) {
    setNote(note, "LOCKED AT HALF TIME" + (f.saved ? " · " + finalText(f.saved) : " · NO PICK"), "lock");
  } else if (!f.ft) {
    setNote(note, "Pick a result or set a score. Each one updates the other.", "");
  } else {
    const hint = f.flash === "result" ? "Your score means " + FT_NAMES[f.ft] + ", so we set that as your result."
      : f.flash === "score" ? "We set a score that fits " + FT_NAMES[f.ft] + ". Adjust it if you like."
        : "Result and exact score stay in step.";
    setNote(note, hint + (f.saved ? " Saved: " + finalText(f.saved) + "." : ""), f.flash ? "flash" : "");
  }
}

// ............................................................ breaks and errors

function onBreak(kind, d) {
  if (Array.isArray(d.score)) setScore(d.score);
  showStandby();
  const full = kind === "full";
  if (full) s.finished = true;
  setLive(false);
  $("clock").textContent = full ? "FULL TIME" : "HALF TIME";
  const secs = Math.round(Number(d.seconds));
  const line = full
    ? "You got " + s.right + " of " + s.calls + " calls right. Thanks for playing!"
    : secs > 0 ? "Second half starts in " + secs + " seconds." : "Second half starts soon.";
  openModal(full ? "FULL TIME" : "HALF TIME", line, full);
  $("modal-fc").textContent = "Forecasts: +" + fmt(s.fc.points);
  $("modal-fc").hidden = false;
  $("ht-pick").hidden = full || s.fc.final.locked; // the full-time pick stays open until half 2
}

function connectionLost(code) {
  clearTimers();
  const line = code === 1013
    ? "Lots of people are playing right now. Try again in a moment."
    : "We lost the connection to the match.";
  openModal("CONNECTION LOST", line, true);
}

function openModal(kicker, line, again) {
  $("modal-kicker").textContent = kicker;
  $("modal-score").textContent = scoreText(s.score);
  $("modal-total").textContent = s.total.toLocaleString("en-US");
  $("modal-line").textContent = line;
  $("again").hidden = !again;
  $("modal-fc").hidden = true;
  $("ht-pick").hidden = true;
  $("modal").hidden = false;
}
