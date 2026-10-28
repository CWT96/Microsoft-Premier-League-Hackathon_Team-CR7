// app.js — The Premier X: shared state, the /demo/ws connection and screen navigation.
//
// Screens are the design templates in screens/*.dc.html, run by dc.js. Each template's
// Component reads `app.state` in renderVals() and calls the actions below. Every server
// message lands in app.state and re-renders the screen on show.

(function () {
  "use strict";

  var SCREENS = ["Main", "Pick", "Live", "Forecast", "HalfTime", "Result", "Signup"];
  var REVEAL_MS = 3600;   // a call's result stays on the card this long (the feed fades with it)
  var FEED_MS = 3600;     // the call history chips fade out after this long (mcFeed)

  function fresh() {
    return {
      team: "ARS", players: [],
      phase: "pre",            // pre | live | half | full
      connecting: false, error: "",
      session: null, roomCode: "", friends: [], htBoard: null, callsAtHalf: null,
      live: null, clockLabel: "", score: [0, 0], jump: null,
      total: 0, streak: 0, lastCallId: null,
      call: null,              // {id, player, team, prompt, phase: ask|locked|reveal|missed, choice, result}
      feed: [], feedAt: 0, feedKey: 0,
      // pitch, in metres (105 x 68): dots {key: {team, num, x, y, dur}}, the ball and the
      // holder ring {x, y, dur, ease}; dur in ms is the CSS transition that lands them on time
      dots: {}, ball: { x: 52.5, y: 34, dur: 0, ease: "linear", feet: false },
      ring: { x: 52.5, y: 34, dur: 0, ease: "linear" }, holder: null, lastEvent: null,
      fc: { pending: {}, last: null, final: null, finalLocked: false, finalResult: null },
      htEndsAt: 0, ht: null, ft: null,
      saved: false, email: "", signup: null, signupError: ""
    };
  }

  var app = {
    state: fresh(),
    squads: null,
    names: {},       // "ARS:Saka" -> 7
    current: null,   // {name, screen}
    ws: null,
    timers: [],
    signupWaiters: [],

    // ---- helpers used by the templates ----
    fmt: function (n) { return Number(n || 0).toLocaleString("en-US"); },
    minute: function (clock) {
      if (!clock) { return ""; }
      var m = /^(\d+)(?:\+(\d+))?:(\d+)$/.exec(clock);
      if (!m) { return clock; }
      return m[2] !== undefined ? m[1] + "+" + (Number(m[2]) + 1) + "'" : (Number(m[1]) + 1) + "'";
    },
    scoreLine: function () { var s = app.state.score; return s[0] + " – " + s[1]; },
    isLive: function () { return app.state.phase === "live" && !app.state.jump; },
    teamName: function (code) { return code === "CHE" ? "Chelsea" : "Arsenal"; },

    // the fan plus Mia, Dan and Leo: running points during play, the boards at half/full time
    roomBoard: function () {
      var s = app.state;
      if (s.ft) { return s.ft.room.players; }
      var rows = [{ name: "You", pts: s.total, saved: s.saved, me: true }];
      s.friends.forEach(function (f) {
        var now = s.lastCallId ? (f.calls[s.lastCallId] || 0) : 0;
        var pts = now;
        if (s.htBoard) {
          var atHt = s.htBoard.filter(function (p) { return p.name === f.name; })[0];
          var base = s.callsAtHalf ? (f.calls[s.callsAtHalf] || 0) : 0;
          pts = (atHt ? atHt.pts : 0) + (now - base);
        }
        rows.push({ name: f.name, pts: pts, saved: f.saved, me: false });
      });
      rows.sort(function (a, b) { return b.pts - a.pts || (a.me ? -1 : b.me ? 1 : 0); });
      return rows;
    },
    rank: function () {
      var b = app.roomBoard();
      for (var i = 0; i < b.length; i++) { if (b[i].me) { return i + 1; } }
      return 1;
    },
    friendPts: function (name) {
      var row = app.roomBoard().filter(function (p) { return p.name === name; })[0];
      return row ? row.pts : 0;
    },

    // ---- state ----
    set: function (patch) {
      Object.assign(app.state, patch);
      app.refresh();
    },
    refresh: function () {
      if (app.current) { app.current.screen.comp.forceUpdate(); }
    },
    later: function (ms, fn) { app.timers.push(setTimeout(fn, ms)); },

    // ---- navigation ----
    go: function (name) {
      if (SCREENS.indexOf(name) === -1) { return; }
      if (app.current && app.current.name === name) { return; }
      DC.load("screens/" + name + ".dc.html", { app: app }).then(function (mod) {
        var host = document.getElementById("screen");
        if (app.current) { app.current.screen.unmount(); }
        document.getElementById("screen-css").textContent = mod.tpl.css;
        document.title = mod.tpl.title || "The Premier X";
        var screen = new DC.Screen(mod.tpl, mod.Component, { app: app }, host);
        app.current = { name: name, screen: screen };
        screen.mount();
        // screens cross in with a short fade and lift, never a hard cut
        if (host.animate) {
          var k = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--k")) || 1;
          host.animate([{ opacity: 0, transform: "scale(" + k + ") translateY(10px)" },
                        { opacity: 1, transform: "scale(" + k + ") translateY(0)" }],
                       { duration: 380, easing: "cubic-bezier(0.05, 0.7, 0.1, 1)" });
        }
      }).catch(function (e) { console.error(e); });
    },

    // ---- actions ----
    pickTeam: function (team) { if (app.state.team !== team) { app.set({ team: team, players: [] }); } },
    togglePlayer: function (id) {
      var cur = app.state.players.slice();
      var i = cur.indexOf(id);
      if (i !== -1) { cur.splice(i, 1); } else if (cur.length < 2) { cur.push(id); }
      app.set({ players: cur });
    },
    start: function () {
      app.stop();
      // only Arsenal v Chelsea has match data: a fan browsing another match plays it as the
      // side in the same place (home -> Arsenal, away -> Chelsea), without backed players
      var team = app.state.team, players = app.state.players.slice();
      if (team !== "ARS" && team !== "CHE") {
        var m = (app.squads && app.squads.matches || []).filter(function (x) { return x.away === team; })[0];
        team = m ? "CHE" : "ARS";
        players = [];
      }
      var keep = { team: team, players: players };
      app.state = Object.assign(fresh(), keep, { connecting: true });
      var url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/demo/ws";
      var ws = new WebSocket(url);
      app.ws = ws;
      ws.onopen = function () {
        ws.send(JSON.stringify({ type: "start", data: { team: keep.team, players: keep.players } }));
      };
      ws.onmessage = function (m) {
        var env;
        try { env = JSON.parse(m.data); } catch (e) { return; }
        if (app.ws === ws) { app.onMessage(env.type, env.data || {}); }
      };
      ws.onclose = function (e) {
        if (app.ws !== ws) { return; }
        app.ws = null;
        app.signupWaiters.splice(0).forEach(function (f) { f({ ok: false, reason: "offline" }); });
        if (app.state.phase !== "full") {
          app.set({ connecting: false, error: e.code === 1013 ? "Busy right now. Try again in a minute." : "Connection lost." });
        }
      };
      app.go("Live");
    },
    stop: function () {
      app.timers.forEach(clearTimeout);
      app.timers = [];
      if (app.ws) { var w = app.ws; app.ws = null; try { w.close(); } catch (e) { /* closed */ } }
    },
    restart: function () { app.stop(); app.state = Object.assign(fresh(), { team: app.state.team }); app.go("Main"); app.refresh(); },
    send: function (type, data) {
      if (app.ws && app.ws.readyState === 1) { app.ws.send(JSON.stringify({ type: type, data: data })); return true; }
      return false;
    },
    answer: function (opt) {
      var c = app.state.call;
      if (!c || c.phase !== "ask") { return; }
      app.send("call.answer", { call_id: c.id, pick: opt.toUpperCase() });
      app.set({ call: Object.assign({}, c, { phase: "locked", choice: opt }) });
    },
    forecastPick: function (key, pick) {
      if (app.state.fc.pending[key]) { return; }
      app.send("forecast.pick", { key: key, pick: pick });
    },
    forecastFinal: function (result, home, away) {
      if (app.state.fc.finalLocked) { return; }
      app.send("forecast.final", { result: result, home: home, away: away });
    },
    signup: function (email) {
      return new Promise(function (resolve) {
        if (app.state.saved) { resolve({ ok: true }); return; }
        if (!app.send("signup", { email: email })) { resolve({ ok: false, reason: "offline" }); return; }
        app.state.email = email;
        app.signupWaiters.push(resolve);
      });
    },

    // ---- server messages ----
    onMessage: function (type, d) {
      var s = app.state;
      var now = Date.now();
      switch (type) {
        case "demo.session":
          app.set({ session: d, connecting: false, roomCode: d.room ? d.room.code : "",
                    friends: d.room ? d.room.friends : [], phase: "live" });
          break;
        case "demo.jump":
          if (s.phase === "half") { app.go("Live"); }
          app.set({ jump: d, phase: "live", clockLabel: d.clock, endCard: null });
          break;
        case "demo.clock":
          app.set({ live: d, jump: null, clockLabel: d.clock, score: d.score, phase: "live" });
          break;
        case "demo.event":
          // read by the demo.shape that follows it; a goal leaves the shot's flight alone
          if (d.type !== "goal") { app.state.lastEvent = d; }
          break;
        case "demo.shape":
          app.pitch(d);
          break;
        case "demo.goal":
          // a goal joins the call history chips still on screen (a call often settles on the goal)
          var still = Date.now() - s.feedAt < FEED_MS ? s.feed : [];
          app.pushFeed([{ label: "GOAL · " + String(d.player).toUpperCase(), pts: d.score[0] + "–" + d.score[1], color: "#00FF85" }].concat(still));
          app.set({ score: d.score });
          break;
        case "call.open":
          app.set({ call: { id: d.call_id, player: d.player, team: d.team, prompt: d.prompt, phase: "ask", choice: null } });
          break;
        case "call.result":
          var c = s.call && s.call.id === d.call_id ? s.call : { id: d.call_id, player: "", prompt: "", choice: null };
          var entries = [];
          if (d.too_quick) {
            entries.push({ label: "TOO QUICK · STREAK SAFE", pts: "+0", color: "#CDB8D1" });
          } else {
            if (d.streak_bonus) { entries.push({ label: "STREAK x" + d.streak, pts: "+" + d.streak_bonus, color: "#00FF85" }); }
            entries.push({ label: d.correct ? "CORRECT CALL" : "CALLED IT", pts: "+" + (d.points - d.streak_bonus), color: d.correct ? "#00FF85" : "#EEDFF0" });
          }
          app.pushFeed(entries);
          app.set({ call: Object.assign({}, c, { phase: d.too_quick ? "missed" : "reveal", result: d }),
                    total: d.total, streak: d.streak, lastCallId: d.call_id });
          app.later(REVEAL_MS, function () {
            if (app.state.call && app.state.call.id === d.call_id) { app.set({ call: null }); }
          });
          break;
        case "demo.half_time":
          // the whistle shows on the pitch first, then the half-time screen fades in
          app.set({ phase: "half", ht: d, total: d.total, score: d.score, htBoard: d.room.players,
                    callsAtHalf: s.lastCallId, htEndsAt: now + d.seconds * 1000, call: null, jump: null,
                    endCard: { title: "Half time", label: "ARS " + d.score[0] + " – " + d.score[1] + " CHE" } });
          app.later(1600, function () { if (app.state.phase === "half") { app.go("HalfTime"); } });
          break;
        case "demo.full_time":
          app.set({ phase: "full", ft: d, total: d.total, score: d.score, call: null, jump: null, saved: d.saved,
                    endCard: { title: "Full time", label: "ARS " + d.score[0] + " – " + d.score[1] + " CHE" } });
          app.later(2600, function () { app.go("Result"); });
          break;
        case "forecast.locked":
          var pend = Object.assign({}, s.fc.pending); pend[d.key] = d;
          app.set({ fc: Object.assign({}, s.fc, { pending: pend }) });
          break;
        case "forecast.result":
          var was = s.fc.pending[d.key];
          var p2 = Object.assign({}, s.fc.pending); delete p2[d.key];
          var last = Object.assign({ from_clock: was ? was.from_clock : "" }, d);
          app.set({ fc: Object.assign({}, s.fc, { pending: p2, last: last }), total: d.total });
          break;
        case "forecast.final_saved":
          app.set({ fc: Object.assign({}, s.fc, { final: d }) });
          break;
        case "forecast.final_locked":
          app.set({ fc: Object.assign({}, s.fc, { finalLocked: true }) });
          break;
        case "forecast.final_result":
          app.set({ fc: Object.assign({}, s.fc, { finalResult: d }), total: d.total });
          break;
        case "forecast.rejected":
          if (d.key === "final" && d.reason === "locked") { app.set({ fc: Object.assign({}, s.fc, { finalLocked: true }) }); }
          break;
        case "signup.saved":
          app.set({ saved: true, total: d.total, signup: d, signupError: "" });
          app.signupWaiters.splice(0).forEach(function (f) { f({ ok: true }); });
          break;
        case "signup.rejected":
          var already = d.reason === "already";
          app.set({ saved: already || s.saved, signupError: already ? "" : d.reason });
          app.signupWaiters.splice(0).forEach(function (f) { f({ ok: already, reason: d.reason }); });
          break;
        default:
          break;
      }
    },
    // ---- the pitch ----
    // A demo.shape is where everyone stands at the NEXT on-ball moment, reached in d.dur s.
    // The demo.event just before it says what the ball does meanwhile: a pass flies to the
    // receiver (who arrives with it), a shot flies to goal, anything else stays at the holder's
    // feet. CSS transitions do the motion, so it runs at the display's frame rate.
    pitch: function (d) {
      var s = app.state;
      var D = Math.max(0, d.dur || 0) * 1000;
      var E = s.lastEvent;
      s.lastEvent = null;
      var VMAX = 9;  // m/s: nobody but the ball carrier and the receiver runs faster than this
      var flight = E ? Math.max(0, E.flight || 0) * 1000 : 0;
      var holderKey = d.holder ? d.players.filter(function (p) {
        return app.names[p.team + ":" + d.holder] === p.num;
      }).map(function (p) { return p.team + ":" + p.num; })[0] : null;
      var receiverKey = E && E.type === "pass" && E.to ? E.team + ":" + app.names[E.team + ":" + E.to] : null;
      var dots = {};
      d.players.forEach(function (p) {
        var key = p.team + ":" + p.num;
        var old = s.dots[key];
        var dur = D;
        if (D > 0 && old) {
          var dist = Math.sqrt(Math.pow(p.x - old.x, 2) + Math.pow(p.y - old.y, 2));
          if (key === receiverKey && flight > 0) { dur = flight; }
          else if (key !== holderKey) { dur = Math.max(D, dist / VMAX * 1000); }
        }
        if (!old) { dur = 0; }
        dots[key] = { team: p.team, num: p.num, x: p.x, y: p.y, dur: Math.round(dur) };
      });
      var holder = d.holder ? { team: holderKey ? holderKey.split(":")[0] : (E ? E.team : ""), name: d.holder } : null;
      var ball, ring;
      var hd = holderKey ? dots[holderKey] : null;
      if (D === 0 || !E) {
        ball = { x: d.ball[0], y: d.ball[1], dur: Math.round(D), ease: "linear", feet: !!d.holder };
        ring = hd ? { x: hd.x, y: hd.y, dur: Math.round(D), ease: "linear" } : { x: d.ball[0], y: d.ball[1], dur: Math.round(D), ease: "linear" };
      } else if (E.type === "pass" && E.end_xy) {
        var easePass = "cubic-bezier(.25,.7,.35,1)";
        ball = { x: E.end_xy[0], y: E.end_xy[1], dur: Math.round(flight), ease: easePass, feet: !!E.to };
        ring = { x: E.end_xy[0], y: E.end_xy[1], dur: Math.round(flight), ease: easePass };
        if (E.to) { holder = { team: E.team, name: E.to }; } else { holder = null; }
      } else if (E.type === "shot" && E.end_xy) {
        ball = { x: E.end_xy[0], y: E.end_xy[1], dur: Math.round(flight), ease: "cubic-bezier(.15,.8,.3,1)", feet: false };
        holder = { team: E.team, name: E.player };
        var sk = E.team + ":" + app.names[E.team + ":" + E.player];
        var sd = dots[sk];
        ring = sd ? { x: sd.x, y: sd.y, dur: sd.dur, ease: "linear" } : { x: E.xy[0], y: E.xy[1], dur: 0, ease: "linear" };
      } else {
        ball = { x: d.ball[0], y: d.ball[1], dur: Math.round(D), ease: "linear", feet: !!d.holder };
        ring = hd ? { x: hd.x, y: hd.y, dur: hd.dur, ease: "linear" } : { x: d.ball[0], y: d.ball[1], dur: Math.round(D), ease: "linear" };
      }
      // the ball starts at the event's spot: if it is somewhere else, put it there first
      var from = E && E.xy ? E.xy : null;
      var off = from && Math.sqrt(Math.pow(from[0] - s.ball.x, 2) + Math.pow(from[1] - s.ball.y, 2)) > 4;
      if (off && D > 0) {
        app.set({ ball: { x: from[0], y: from[1], dur: 0, ease: "linear", feet: E.type !== "shot" } });
        app.afterPaint(function () { app.set({ dots: dots, ball: ball, ring: ring, holder: holder }); });
      } else {
        app.set({ dots: dots, ball: ball, ring: ring, holder: holder });
      }
    },
    afterPaint: function (fn) { requestAnimationFrame(function () { requestAnimationFrame(fn); }); },

    pushFeed: function (entries) {
      var key = app.state.feedKey + 1;
      app.set({ feed: entries, feedAt: Date.now(), feedKey: key });
      app.later(FEED_MS + 50, function () { if (app.state.feedKey === key) { app.set({ feed: [] }); } });
    }
  };

  // links between the design's screens ("Pick.dc.html") become app navigation
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a) { return; }
    var href = a.getAttribute("href");
    var m = /^([A-Za-z]+)\.dc\.html$/.exec(href);
    if (m) {
      e.preventDefault();
      var action = a.getAttribute("data-go");
      if (action && typeof app[action] === "function") { app[action](); } else { app.go(m[1]); }
    } else if (href.charAt(0) === "#") {
      e.preventDefault();
    }
  });

  function fit() {
    var k = Math.min(window.innerWidth / 390, window.innerHeight / 844);
    if (window.innerWidth <= 500) { k = window.innerWidth / 390; }
    document.documentElement.style.setProperty("--k", String(Math.min(k, 1.25)));
  }
  window.addEventListener("resize", fit);
  fit();

  window.app = app;

  fetch("/demo/squads").then(function (r) { return r.json(); }).then(function (sq) {
    app.squads = sq;
    Object.keys(sq.teams).forEach(function (code) {
      sq.teams[code].players.forEach(function (p) { app.names[code + ":" + p.name] = p.num; });
    });
    app.refresh();
  }).catch(function () { app.set({ error: "Could not load the squads." }); });

  SCREENS.forEach(function (n) { DC.load("screens/" + n + ".dc.html", { app: app }).catch(function () {}); });
  app.go("Main");
})();
