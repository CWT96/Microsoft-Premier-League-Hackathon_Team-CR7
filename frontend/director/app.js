// app.js: Director view. Loads GET /demo/director?team=ARS|CHE once per side and draws the
// pitch, the fan's phone, the detail panel and the decision timeline for one playhead time t.
//
// Everything on screen is derived from the cues with `at <= t` (latest shape, clock, score,
// event), so scrubbing anywhere is stateless: no history is replayed, nothing accumulates.
// Every field of the JSON is guarded: a missing or malformed value hides its piece of the
// page instead of throwing. Text is only ever set with textContent.

(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var JUMP_CARD_S = 2.0;   // the title card before each clip (contracts/README.md "Demo mode")
  var RESULT_S = 5.0;      // the phone keeps a call's result up this long after it locks
  var GOAL_S = 4.0;        // goal banner on the phone
  var CHART_H = 210;
  var STATUS_LABEL = { picked: "Picked", blocked: "Blocked", too_short: "Too short", other_team: "Other side" };
  var GLYPH = { picked: "▲", blocked: "✕", too_short: "◆", other_team: "○" };
  var LANE = { picked: 14, blocked: 30, too_short: 30, other_team: 44 };

  // ---------- small guards ----------
  function num(v) { return typeof v === "number" && isFinite(v) ? v : null; }
  function str(v) { return typeof v === "string" ? v : (typeof v === "number" && isFinite(v) ? String(v) : ""); }
  function obj(v) { return v && typeof v === "object" && !Array.isArray(v) ? v : {}; }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function xy(v) { return Array.isArray(v) && v.length >= 2 && num(v[0]) !== null && num(v[1]) !== null ? [v[0], v[1]] : null; }
  function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function lerp(a, b, u) { return a + (b - a) * u; }
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }
  function sv(tag, attrs, text) {
    var e = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) { e.textContent = text; }
    return e;
  }

  // last index i with list[i].at <= t, or -1 (list sorted by at)
  function latest(list, t) {
    var lo = 0, hi = list.length - 1, ans = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (list[mid].at <= t) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return ans;
  }

  // ---------- data ----------
  function normalize(raw) {
    raw = obj(raw);
    var cues = arr(raw.cues).filter(function (c) {
      return c && num(c.at) !== null && typeof c.type === "string";
    }).map(function (c, i) { return { at: c.at, type: c.type, data: obj(c.data), i: i }; });
    cues.sort(function (a, b) { return a.at - b.at || a.i - b.i; });
    function of(type) { return cues.filter(function (c) { return c.type === type; }); }

    var lastAt = cues.length ? cues[cues.length - 1].at : 0;
    var duration = num(raw.duration) !== null && raw.duration > 0 ? raw.duration : lastAt;

    // when each picked call's card really shows on the phone: its call.open cue (the pass's
    // arrival can be earlier; the card is held back so it is up at least 2.5 s before lock)
    var cardAt = {};
    of("call.open").forEach(function (c) {
      var id = str(c.data.call_id);
      if (id && !(id in cardAt)) { cardAt[id] = c.at; }
    });
    var moments = arr(raw.moments).filter(function (m) {
      return m && num(m.at_open) !== null;
    }).map(function (m) {
      var lock = num(m.at_lock) !== null && m.at_lock >= m.at_open ? m.at_lock : m.at_open;
      var id = str(m.call_id), card = num(cardAt[id]);
      return {
        id: id, atOpen: m.at_open, atLock: lock,
        atCard: card !== null && card <= lock ? card : m.at_open,
        clockOpen: str(m.clock_open), clockLock: str(m.clock_lock),
        player: str(m.player) || "Unknown player", team: str(m.team), answer: str(m.answer),
        status: STATUS_LABEL[m.status] ? m.status : "blocked", reason: str(m.reason), against: str(m.against)
      };
    }).sort(function (a, b) { return a.atOpen - b.atOpen; });

    var rules = obj(raw.rules);
    var teams = obj(raw.teams);
    var ht = of("demo.half_time")[0] || null;
    return {
      team: str(raw.team),
      home: str(obj(teams.home).code) || "ARS",
      away: str(obj(teams.away).code) || "CHE",
      duration: duration,
      plays: arr(raw.plays).filter(function (p) {
        return p && num(p.at_start) !== null && num(p.at_end) !== null;
      }).map(function (p) { return { title: str(p.title), atStart: p.at_start, atEnd: p.at_end }; })
        .sort(function (a, b) { return a.atStart - b.atStart; }),
      moments: moments,
      picked: moments.filter(function (m) { return m.status === "picked"; }),
      lines: obj(raw.lines),
      movement: arr(raw.movement).filter(function (p) { return p && num(p.at) !== null && num(p.v) !== null; })
        .sort(function (a, b) { return a.at - b.at; }),
      attack: arr(raw.attack).filter(function (a) {
        return a && num(a.at_start) !== null && num(a.at_end) !== null && a.at_end >= a.at_start;
      }),
      resultHold: num(rules.min_result_s) !== null ? rules.min_result_s : 3,
      shapes: of("demo.shape").filter(function (c) { return arr(c.data.players).length > 0; }),
      clocks: of("demo.clock"),
      events: of("demo.event"),
      jumps: of("demo.jump"),
      goals: of("demo.goal"),
      scores: cues.filter(function (c) { return xy(c.data.score); }),
      ht: ht ? { at: ht.at, end: ht.at + (num(ht.data.seconds) !== null ? Math.max(0, ht.data.seconds) : 10) } : null,
      ft: of("demo.full_time")[0] || null
    };
  }

  // ---------- state ----------
  var S = {
    team: "ARS", data: null, t: 0, playing: false, last: 0, token: 0,
    pinned: null, hovered: null, dotEls: {}, keys: {}, chartW: 0
  };

  function dataUrl(team) {
    var q = new URLSearchParams(location.search).get("data");
    // a local stand-in file for offline checks: same origin only, "{team}" is replaced
    if (q) {
      try {
        var u = new URL(q.replace("{team}", team), location.href);
        if (u.origin === location.origin) { return u.href; }
      } catch (e) { /* bad URL: fall through to the endpoint */ }
    }
    return "/demo/director?team=" + encodeURIComponent(team);
  }

  function setStatus(text) {
    var s = $("status");
    s.textContent = text || "";
    s.hidden = !text;
  }

  function load(team) {
    var token = ++S.token;
    S.team = team;
    S.data = null;
    S.playing = false;
    S.t = 0;
    S.pinned = S.hovered = null;
    S.keys = {};
    resetPitch();
    Array.prototype.forEach.call(document.querySelectorAll(".team"), function (b) {
      var on = b.getAttribute("data-team") === team;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    setStatus("Loading…");
    drawChart();
    render();
    fetch(dataUrl(team), { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    }).then(function (raw) {
      if (token !== S.token) { return; }  // the user switched team meanwhile
      S.data = normalize(raw);
      setStatus("");
      if (!S.data.shapes.length && !S.data.moments.length) { setStatus("This cut has no cues to show."); }
      $("scrub").max = String(S.data.duration || 0);
      drawChart();
      if (S.startT !== null) { S.t = clamp(S.startT, 0, S.data.duration); S.startT = null; }  // ?t= on first load
      render();
    }).catch(function (err) {
      if (token !== S.token) { return; }
      setStatus("Could not load the director data (" + (err && err.message ? err.message : "error") + ").");
      render();
    });
  }

  // ---------- derived state at t ----------
  function clockAt(D, t) {
    var c = D.clocks[latest(D.clocks, t)];
    var j = D.jumps[latest(D.jumps, t)];
    // a jump card newer than the last clock tick shows the clip's start clock
    if (j && (!c || j.at > c.at) && str(j.data.clock)) { return str(j.data.clock); }
    return c ? str(c.data.clock) : "";
  }

  function scoreAt(D, t) {
    var s = D.scores[latest(D.scores, t)];
    return s ? xy(s.data.score) : [0, 0];
  }

  function phaseAt(D, t) {
    if (D.ft && t >= D.ft.at) { return { kind: "full" }; }
    if (D.ht && t >= D.ht.at && t < D.ht.end) { return { kind: "half", left: D.ht.end - t }; }
    var j = D.jumps[latest(D.jumps, t)];
    if (j && t < j.at + JUMP_CARD_S) { return { kind: "card", title: str(j.data.title), clock: str(j.data.clock) }; }
    return { kind: "play" };
  }

  // where the 22 players are at t: each shape is a target reached `dur` s after it, starting
  // from wherever the previous glide had got to (a dur-0 shape places everyone at once)
  function playersAt(D, t) {
    var i = latest(D.shapes, t);
    if (i < 0) { return null; }
    var k = i;
    while (k > 0 && !(num(D.shapes[k].data.dur) === 0)) { k--; }
    function targets(s) {
      var out = {};
      arr(s.data.players).forEach(function (p) {
        if (p && num(p.x) !== null && num(p.y) !== null && (p.team === "ARS" || p.team === "CHE")) {
          out[p.team + ":" + str(p.num)] = { team: p.team, num: str(p.num), x: p.x, y: p.y };
        }
      });
      return out;
    }
    function frac(s, at) {
      var d = num(s.data.dur);
      return d && d > 0 ? clamp((at - s.at) / d, 0, 1) : 1;
    }
    function glide(from, to, u) {
      var out = {};
      Object.keys(to).forEach(function (key) {
        var a = from[key] || to[key], b = to[key];
        out[key] = { team: b.team, num: b.num, x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u) };
      });
      return out;
    }
    var start = targets(D.shapes[k]);
    for (var j = k + 1; j <= i; j++) {
      start = glide(start, targets(D.shapes[j - 1]), frac(D.shapes[j - 1], D.shapes[j].at));
    }
    return glide(start, targets(D.shapes[i]), frac(D.shapes[i], t));
  }

  function ballAt(D, t) {
    var si = latest(D.shapes, t), ei = latest(D.events, t);
    var shape = si >= 0 ? D.shapes[si] : null;
    var jump = D.jumps[latest(D.jumps, t)];
    var ev = ei >= 0 ? D.events[ei] : null;
    if (ev && jump && jump.at > ev.at) { ev = null; }  // that event belongs to an earlier clip
    var holder = shape ? str(shape.data.holder) : "";
    var start = ev ? xy(ev.data.xy) : null;
    if (ev && start) {
      var d = ev.data, end = xy(d.end_xy), type = str(d.type);
      if (!holder) { holder = str(d.player); }
      if (end && (type === "pass" || type === "shot")) {
        if (type === "shot" && d.goal === true) { end = [end[0] > 52.5 ? 106 : -1, end[1]]; }
        var f = num(d.flight) || num(d.dur) || 0.5;
        var u = clamp((t - ev.at) / Math.max(0.1, f), 0, 1);
        return { x: lerp(start[0], end[0], u), y: lerp(start[1], end[1], u), holder: holder };
      }
      if (end && num(d.dur) && d.dur > 0) {
        var v = clamp((t - ev.at) / d.dur, 0, 1);
        return { x: lerp(start[0], end[0], v), y: lerp(start[1], end[1], v), holder: holder };
      }
      return { x: start[0], y: start[1], holder: holder };
    }
    var b = shape ? xy(shape.data.ball) : null;
    return b ? { x: b[0], y: b[1], holder: holder } : null;
  }

  // the moment the detail panel shows: hovered, else clicked, else the one nearest the playhead
  function selected(D, t) {
    function byId(id) { return D.moments.filter(function (m) { return m.id === id; })[0] || null; }
    var m = (S.hovered && byId(S.hovered)) || (S.pinned && byId(S.pinned));
    if (m) { return m; }
    var best = null, bestD = Infinity;
    D.moments.forEach(function (x) {
      var d = t < x.atCard ? x.atCard - t : t > x.atLock ? t - x.atLock : 0;
      if (d < bestD || (d === bestD && x.status === "picked" && best && best.status !== "picked")) { best = x; bestD = d; }
    });
    return best;
  }

  // ---------- render ----------
  function render() {
    var D = S.data, t = S.t;
    $("play").textContent = S.playing ? "❚❚" : "▶";
    $("play").setAttribute("aria-label", S.playing ? "Pause" : "Play");
    var scrub = $("scrub");
    if (document.activeElement !== scrub || S.playing) { scrub.value = String(t); }
    var clock = D ? clockAt(D, t) : "";
    $("time").textContent = (clock || "--:--") + " · " + t.toFixed(1) + "s";
    renderPitch(D, t);
    renderPhone(D, t, clock);
    renderDetail(D, t);
    renderPlayhead(D, t);
  }

  function resetPitch() {
    var g = $("dots");
    while (g.firstChild) { g.removeChild(g.firstChild); }
    S.dotEls = {};
  }

  function renderPitch(D, t) {
    var fan = (D && D.team === "CHE") || (!(D && D.team) && S.team === "CHE") ? "CHE" : "ARS";
    var third = $("third"), label = $("third-label");
    third.setAttribute("x", fan === "CHE" ? "0" : "70");
    label.setAttribute("x", fan === "CHE" ? "2" : "103");
    label.setAttribute("text-anchor", fan === "CHE" ? "start" : "end");

    var players = D ? playersAt(D, t) : null;
    var seen = {};
    Object.keys(players || {}).forEach(function (key) {
      var p = players[key], e = S.dotEls[key];
      if (!e) {
        e = { g: sv("g", {}), c: sv("circle", { r: "1.3", "class": "dot " + p.team }), n: sv("text", { "class": "num" }, p.num) };
        e.g.appendChild(e.c); e.g.appendChild(e.n);
        $("dots").appendChild(e.g);
        S.dotEls[key] = e;
      }
      e.g.setAttribute("transform", "translate(" + p.x.toFixed(2) + " " + p.y.toFixed(2) + ")");
      e.g.style.display = "";
      seen[key] = true;
    });
    Object.keys(S.dotEls).forEach(function (k) { if (!seen[k]) { S.dotEls[k].g.style.display = "none"; } });

    var ball = D ? ballAt(D, t) : null, bEl = $("ball"), hEl = $("holder");
    if (ball) {
      var bx = clamp(ball.x, -1.5, 106.5), by = clamp(ball.y, 0, 68);
      bEl.setAttribute("cx", bx.toFixed(2)); bEl.setAttribute("cy", by.toFixed(2));
      bEl.style.display = "";
      hEl.textContent = ball.holder;
      hEl.setAttribute("x", clamp(bx, 6, 99).toFixed(2));
      hEl.setAttribute("y", (by < 6 ? by + 5 : by - 3.6).toFixed(2));
      hEl.style.display = ball.holder ? "" : "none";
      var hot = fan === "CHE" ? ball.x < 35 : ball.x > 70;
      third.classList.toggle("hot", hot);
    } else {
      bEl.style.display = "none"; hEl.style.display = "none";
      third.classList.remove("hot");
    }

    var ph = D ? phaseAt(D, t) : { kind: "none" };
    var card = $("card");
    var title = ph.kind === "card" ? ph.title : ph.kind === "half" ? "Half time" : ph.kind === "full" ? "Full time" : "";
    var sub = ph.kind === "card" ? ph.clock : ph.kind === "half" ? "Second half in " + Math.ceil(ph.left) + " s" : "";
    $("card-title").textContent = title;
    $("card-clock").textContent = sub;
    card.style.display = title ? "" : "none";
  }

  function renderPhone(D, t, clock) {
    var sc = D ? scoreAt(D, t) : [0, 0];
    $("score").textContent = (D ? D.home : "ARS") + " " + sc[0] + "–" + sc[1] + " " + (D ? D.away : "CHE");
    $("clock").textContent = clock || "--:--";

    var goal = D ? D.goals[latest(D.goals, t)] : null;
    var gEl = $("goal");
    if (goal && t < goal.at + GOAL_S) {
      var gd = goal.data;
      gEl.textContent = ["GOAL", str(gd.team), str(gd.player), str(gd.clock)].filter(Boolean).join(" · ");
      gEl.hidden = false;
    } else { gEl.hidden = true; }

    // decide what the screen shows, then rebuild it only when that changes
    var view = { key: "none" };
    if (!D) { view = { key: "loading" }; }
    else {
      var ph = phaseAt(D, t);
      if (ph.kind === "full") { view = { key: "full" }; }
      else if (ph.kind === "half") { view = { key: "half:" + Math.ceil(ph.left), left: Math.ceil(ph.left) }; }
      else if (ph.kind === "card") { view = { key: "card:" + ph.title, title: ph.title, clock: ph.clock }; }
      else {
        var open = D.picked.filter(function (m) { return t >= m.atCard && t < m.atLock; })[0];
        var done = D.picked.filter(function (m) { return t >= m.atLock && t < m.atLock + RESULT_S; }).pop();
        if (open) { view = { key: "open:" + open.id, m: open }; }
        else if (done) { view = { key: "done:" + done.id, m: done, done: true }; }
        else { view = { key: "on" }; }
      }
    }
    if (D && (view.key === "full")) { view.key += ":" + scoreAt(D, t).join("-"); }
    if (S.keys.phone === view.key) { return; }
    S.keys.phone = view.key;

    var box = $("screen");
    while (box.firstChild) { box.removeChild(box.firstChild); }
    if (view.key === "loading") { box.appendChild(el("div", "muted", "Loading…")); return; }
    if (view.key.indexOf("full") === 0) {
      box.appendChild(el("div", "big", "Full time"));
      box.appendChild(el("div", "muted", $("score").textContent));
      return;
    }
    if (view.key.indexOf("half") === 0) {
      box.appendChild(el("div", "big", "Half time"));
      box.appendChild(el("div", "muted", "Second half starts in " + view.left + " s"));
      return;
    }
    if (view.key.indexOf("card") === 0) {
      box.appendChild(el("div", "big", view.title || "Next clip"));
      if (view.clock) { box.appendChild(el("div", "muted", view.clock)); }
      return;
    }
    if (view.m) {
      var m = view.m, ln = obj(D.lines[m.id]);
      box.appendChild(el("div", "who", m.player + (m.team ? " · " + m.team : "")));
      box.appendChild(el("div", "setup", str(ln.setup) || ("What will " + m.player + " do next?")));
      var opts = el("div", "opts");
      ["PASS", "SHOOT", "DRIBBLE"].forEach(function (o) {
        opts.appendChild(el("div", "opt" + (view.done && o === m.answer ? " right" : ""), o));
      });
      box.appendChild(opts);
      if (view.done) {
        box.appendChild(el("div", "explain", (m.answer ? "Answer: " + m.answer + ". " : "") + str(ln.explain)));
      } else {
        box.appendChild(el("div", "muted", "Locks when he plays the ball"));
      }
      return;
    }
    box.appendChild(el("div", "muted", "Match on…"));
  }

  function renderDetail(D, t) {
    var m = D ? selected(D, t) : null;
    var key = D ? (m ? m.id : "empty") : "nodata";
    markSelected(m ? m.id : null);
    if (S.keys.detail === key) { return; }
    S.keys.detail = key;
    var box = $("detail");
    while (box.firstChild) { box.removeChild(box.firstChild); }
    if (!D) { box.appendChild(el("div", "k", "Waiting for data.")); return; }
    if (!m) { box.appendChild(el("div", "k", "No decision moments in this cut.")); return; }

    var times = [m.clockOpen, m.clockLock].filter(Boolean).join("–");
    box.appendChild(el("h2", "", m.player + (m.team ? " (" + m.team + ")" : "") + (times ? " · " + times : "")));
    var r1 = el("div", "row");
    r1.appendChild(el("span", "k", "Right answer: "));
    r1.appendChild(document.createTextNode(m.answer || "unknown"));
    r1.appendChild(el("span", "chip " + m.status, STATUS_LABEL[m.status]));
    box.appendChild(r1);
    if (m.reason && m.reason !== "Picked") { box.appendChild(el("div", "row", m.reason)); }
    if (m.against) {
      var other = D.moments.filter(function (x) { return x.id === m.against; })[0];
      if (other) { box.appendChild(el("div", "row k", "Lost to " + other.player + "'s call" + (other.clockOpen ? " at " + other.clockOpen : ""))); }
    }
    if (m.status === "picked") {
      box.appendChild(el("div", "row k", "Card on the phone " + m.atCard.toFixed(1) + " s → lock " + m.atLock.toFixed(1) +
        " s (" + (m.atLock - m.atCard).toFixed(1) + " s to answer)"));
    } else {
      box.appendChild(el("div", "row k", "Ball reaches him " + m.atOpen.toFixed(1) + " s → he plays it " + m.atLock.toFixed(1) +
        " s (" + (m.atLock - m.atOpen).toFixed(1) + " s)"));
    }

    var ln = D.lines[m.id];
    if (!ln || typeof ln !== "object") {
      box.appendChild(el("div", "src", m.status === "picked" ? "No lines found for this call." : "No lines: the app writes them only for picked calls."));
      return;
    }
    var facts = obj(ln.facts), fk = Object.keys(facts);
    if (fk.length) {
      box.appendChild(el("div", "row k", "Facts sent to the LLM:"));
      var ul = el("ul", "facts");
      fk.forEach(function (k) {
        var v = facts[k];
        var text = Array.isArray(v) ? v.map(str).join("–") : (v && typeof v === "object" ? JSON.stringify(v) : String(v));
        ul.appendChild(el("li", "", k + ": " + text));
      });
      box.appendChild(ul);
    }
    if (str(ln.setup)) {
      var s = el("div", "row"); s.appendChild(el("span", "k", "Setup: ")); s.appendChild(document.createTextNode(str(ln.setup))); box.appendChild(s);
    }
    if (str(ln.explain)) {
      var x = el("div", "row"); x.appendChild(el("span", "k", "Explain: ")); x.appendChild(document.createTextNode(str(ln.explain))); box.appendChild(x);
    }
    box.appendChild(el("div", "src", ln.ai === true ? "written by gpt-4.1-mini on Azure OpenAI" : "template"));
  }

  // ---------- chart ----------
  var C = { padL: 14, padR: 14, top: 56, bot: 172 };
  function cx(D, t) {
    var w = S.chartW - C.padL - C.padR, dur = D && D.duration > 0 ? D.duration : 1;
    return C.padL + clamp(t / dur, 0, 1) * w;
  }

  function drawChart() {
    var svg = $("chart");
    while (svg.firstChild) { svg.removeChild(svg.firstChild); }
    S.markEls = {};
    S.chartW = svg.clientWidth || 800;
    var W = S.chartW, D = S.data;
    svg.setAttribute("viewBox", "0 0 " + W + " " + CHART_H);
    var bg = sv("rect", { "class": "bg", x: 0, y: 0, width: W, height: CHART_H });
    bg.addEventListener("click", function (ev) {
      if (!S.data) { return; }
      var r = svg.getBoundingClientRect();
      var u = clamp((ev.clientX - r.left - C.padL) / Math.max(1, r.width - C.padL - C.padR), 0, 1);
      S.pinned = null;
      seek(u * S.data.duration);
    });
    svg.appendChild(bg);
    if (!D) { return; }
    var band = function (a, b, cls) {
      var x0 = cx(D, a), x1 = cx(D, b);
      if (x1 - x0 < 0.5) { return; }
      svg.appendChild(sv("rect", { "class": cls, x: x0, y: C.top, width: x1 - x0, height: C.bot - C.top, "pointer-events": "none" }));
    };
    D.attack.forEach(function (a) { band(a.at_start, a.at_end, "att-" + (a.team === "CHE" ? "CHE" : "ARS")); });
    D.picked.forEach(function (m) {
      band(m.atCard, m.atLock, "win");
      band(m.atLock, m.atLock + D.resultHold, "hold");
    });
    if (D.ht) {
      band(D.ht.at, D.ht.end, "ht");
      svg.appendChild(sv("text", { "class": "ht-label", x: (cx(D, D.ht.at) + cx(D, D.ht.end)) / 2, y: C.top + 14, "text-anchor": "middle" }, "Half time"));
    }
    svg.appendChild(sv("line", { "class": "base", x1: C.padL, x2: W - C.padR, y1: C.bot, y2: C.bot }));

    // segment boundaries, labelled with the clip's match clock
    D.plays.forEach(function (p) {
      var x = cx(D, p.atStart);
      svg.appendChild(sv("line", { "class": "seg", x1: x, x2: x, y1: C.top - 4, y2: C.bot + 4, "pointer-events": "none" }));
      var c = D.clocks.filter(function (k) { return k.at >= p.atStart - 0.01; })[0];
      var j = D.jumps[latest(D.jumps, p.atStart)];
      var label = (j && str(j.data.clock)) || (c && str(c.data.clock)) || "";
      if (label) { svg.appendChild(sv("text", { "class": "seg-label", x: x + 3, y: C.bot + 18 }, label)); }
    });
    if (D.ft) { svg.appendChild(sv("text", { "class": "seg-label", x: W - C.padR, y: C.bot + 18, "text-anchor": "end" }, "Full time")); }

    // movement: ball speed, one line per clip so the gaps between clips stay empty
    var vmax = Math.max(1, Math.max.apply(null, D.movement.map(function (p) { return p.v; }).concat([0]))) * 1.1;
    var groups = D.plays.length ? D.plays.map(function (p) {
      return D.movement.filter(function (q) { return q.at >= p.atStart - 0.01 && q.at <= p.atEnd + 0.01; });
    }) : [D.movement];
    groups.forEach(function (pts) {
      if (pts.length < 2) { return; }
      var d = pts.map(function (q) {
        return cx(D, q.at).toFixed(1) + "," + (C.bot - clamp(q.v / vmax, 0, 1) * (C.bot - C.top - 4)).toFixed(1);
      }).join(" ");
      svg.appendChild(sv("polyline", { "class": "move", points: d, "pointer-events": "none" }));
    });

    // decision markers: three lanes so picked calls never hide under the others
    D.moments.forEach(function (m) {
      var x = cx(D, m.atCard), y = LANE[m.status];
      var g = sv("g", { tabindex: "0", role: "button", "aria-label": m.player + ": " + STATUS_LABEL[m.status] });
      g.appendChild(sv("rect", { "class": "hit", x: x - 9, y: y - 9, width: 18, height: 18 }));
      var mk = sv("text", { "class": "mk " + m.status, x: x, y: y }, GLYPH[m.status]);
      g.appendChild(mk);
      g.addEventListener("mouseenter", function () { S.hovered = m.id; showTip(m, x, y); render(); });
      g.addEventListener("mouseleave", function () { S.hovered = null; hideTip(); render(); });
      g.addEventListener("focus", function () { S.hovered = m.id; showTip(m, x, y); render(); });
      g.addEventListener("blur", function () { S.hovered = null; hideTip(); render(); });
      function pick() { S.pinned = m.id; S.playing = false; seek(m.atCard); }
      g.addEventListener("click", function (ev) { ev.stopPropagation(); pick(); });
      g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
      svg.appendChild(g);
      S.markEls[m.id] = mk;
    });
    S.markSel = null;

    S.head = sv("line", { "class": "head-line", x1: C.padL, x2: C.padL, y1: 4, y2: C.bot + 4, "pointer-events": "none" });
    svg.appendChild(S.head);
    S.keys.detail = null;
  }

  function markSelected(id) {
    if (S.markSel === id) { return; }
    if (S.markSel && S.markEls && S.markEls[S.markSel]) { S.markEls[S.markSel].classList.remove("sel"); }
    if (id && S.markEls && S.markEls[id]) { S.markEls[id].classList.add("sel"); }
    S.markSel = id;
  }

  function renderPlayhead(D, t) {
    if (!S.head || !D) { return; }
    var x = cx(D, t).toFixed(1);
    S.head.setAttribute("x1", x); S.head.setAttribute("x2", x);
  }

  function showTip(m, x, y) {
    var tip = $("tip"), svg = $("chart");
    var k = svg.getBoundingClientRect().width / Math.max(1, S.chartW);
    tip.textContent = m.player + (m.clockOpen ? " · " + m.clockOpen : "") + " · " + STATUS_LABEL[m.status] +
      (m.reason && m.reason !== "Picked" ? ": " + m.reason : "");
    tip.style.left = clamp(x * k, 120, svg.getBoundingClientRect().width - 120) + "px";
    tip.style.top = (y * k - 10) + "px";
    tip.hidden = false;
  }
  function hideTip() { $("tip").hidden = true; }

  // ---------- controls ----------
  function seek(t) {
    var dur = S.data ? S.data.duration : 0;
    S.t = clamp(num(t) !== null ? t : 0, 0, dur);
    render();
  }

  function frame(now) {
    if (S.playing && S.data) {
      var dt = S.last ? Math.min(0.1, (now - S.last) / 1000) : 0;
      S.t += dt;
      if (S.t >= S.data.duration) { S.t = S.data.duration; S.playing = false; }
      render();
    }
    S.last = now;
    requestAnimationFrame(frame);
  }

  $("play").addEventListener("click", function () {
    if (!S.data) { return; }
    if (!S.playing && S.t >= S.data.duration) { S.t = 0; }
    S.playing = !S.playing;
    if (S.playing) { S.pinned = null; }
    render();
  });
  $("scrub").addEventListener("input", function () {
    S.pinned = null;
    seek(parseFloat($("scrub").value));
  });
  Array.prototype.forEach.call(document.querySelectorAll(".team"), function (b) {
    b.addEventListener("click", function () {
      var team = b.getAttribute("data-team");
      if (team !== S.team || !S.data) { load(team); }
    });
  });
  var resizeQueued = false;
  window.addEventListener("resize", function () {
    if (resizeQueued) { return; }
    resizeQueued = true;
    requestAnimationFrame(function () { resizeQueued = false; drawChart(); render(); });
  });

  var params = new URLSearchParams(location.search);
  S.startT = num(parseFloat(params.get("t")));
  var first = params.get("team");
  load(first === "CHE" ? "CHE" : "ARS");
  requestAnimationFrame(frame);

  // exposed for headless checks only
  window.Director = { state: S, seek: seek, load: load, normalize: normalize };
})();
