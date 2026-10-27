// Matchday Call fan app: landing -> pick a team -> live calls over WS /demo/ws.
// The server drives all timing; this file only reacts to its messages.
"use strict";

const $ = (id) => document.getElementById(id);
const TEAM_NAMES = { ARS: "Arsenal", CHE: "Chelsea" };
const POP_MS = 1600;      // points pop-up over the pitch
const GOAL_MS = 2600;     // goal banner
const JUMP_MS = 2000;     // jump card (JUMP_CARD_S on the server)
const RESULT_HOLD_MS = 4000; // result stays on the call card, then back to "reading the play"
const SILENCE_MS = 40000; // no message for this long (half time is 25 s) = connection lost

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
  s = { score: [0, 0], total: 0, streak: 0, call: null, picks: {}, right: 0, calls: 0, finished: false };
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
  if (type !== "demo.half_time") $("modal").hidden = true; // play resumed after half time
  switch (type) {
    case "demo.session": onSession(d); break;
    case "demo.clock": onClock(d); break;
    case "demo.event": onEvent(d); break;
    case "demo.goal": onGoal(d); break;
    case "demo.jump": onJump(d); break;
    case "call.open": onCallOpen(d); break;
    case "call.locked": onCallLocked(d); break;
    case "call.result": onCallResult(d); break;
    case "demo.half_time": onBreak("half", d); break;
    case "demo.full_time": onBreak("full", d); break;
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
  setStreak(0);
  moveBall([52.5, 34], null);
  showStandby();
}

function onSession(d) {
  const you = d.you && TEAM_NAMES[d.you.team];
  if (you) $("backing").textContent = "Backing " + you;
}

function onClock(d) {
  if (typeof d.clock === "string") $("clock").textContent = "LIVE " + d.clock;
  if (Array.isArray(d.score)) setScore(d.score);
}

function setScore(score) {
  s.score = score;
  $("score").textContent = scoreText(score);
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
  if (!$("pop").hidden) { $("pop").hidden = true; s.popAfterGoal = s.lastPop; } // goal first
  const el = $("goal");
  el.hidden = true; void el.offsetWidth; el.hidden = false; // restart the animation
  later("goal", GOAL_MS, () => {
    el.hidden = true;
    if (s.popAfterGoal) showPop(s.popAfterGoal);
    s.popAfterGoal = null;
  });
}

function onJump(d) {
  $("jump-title").textContent = d.title || "";
  $("jump-clock").textContent = d.clock ? "Match clock " + d.clock : "";
  $("jump").hidden = false;
  showTag(false);
  if (typeof d.clock === "string") $("clock").textContent = "LIVE " + d.clock;
  showStandby(); // a call never spans a jump
  later("jump", JUMP_MS, () => { $("jump").hidden = true; });
}

// ............................................................ calls

function showStandby() {
  if (s) s.call = null;
  $("standby").hidden = false;
  $("ask").hidden = true;
}

function optionButtons() {
  return Array.from(document.querySelectorAll(".opt"));
}

const SUBS = { PASS: "to a teammate", SHOOT: "at goal", DRIBBLE: "run with it" };

function onCallOpen(d) {
  if (typeof d.call_id !== "string") return;
  clearTimeout(timers.result);
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
    gain.textContent = "+" + gained;
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

function showPop(d) {
  s.lastPop = d;
  if (!$("goal").hidden) { s.popAfterGoal = d; return; } // shown once the goal banner ends
  const pts = $("pop-pts"), list = $("pop-ribbons");
  list.textContent = "";
  pts.className = "pop-pts pop";
  if (d.too_quick) {
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
  later("pop", POP_MS, () => { el.hidden = true; });
}

// ............................................................ breaks and errors

function onBreak(kind, d) {
  if (Array.isArray(d.score)) setScore(d.score);
  showStandby();
  const full = kind === "full";
  if (full) s.finished = true;
  $("clock").textContent = full ? "FULL TIME" : "HALF TIME";
  const line = full
    ? "You got " + s.right + " of " + s.calls + " calls right. Thanks for playing!"
    : "Second half starts in " + Math.round(Number(d.seconds) || 25) + " seconds.";
  openModal(full ? "FULL TIME" : "HALF TIME", line, full);
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
  $("modal").hidden = false;
}
