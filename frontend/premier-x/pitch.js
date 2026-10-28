// pitch.js — the live pitch as a small physics loop: players run, the ball flies or rolls at
// the holder's feet, the holder ring follows. Positions are metres (105 x 68, Northbridge attack
// left to right) and are written to the dots every animation frame.
//
// Server cues (see contracts/README.md): a demo.shape is where everyone stands at the NEXT
// on-ball moment, `dur` seconds from now; the demo.event before it says what the ball does
// meanwhile (a pass flies to `to`, a shot to goal, anything else stays at the holder's feet).
// Here every player heads for his spot to arrive on time, never faster than a sprint, and
// from wherever he really is (a new cue mid-run picks up from there, so nothing teleports).

(function () {
  "use strict";

  var SPRINT = 8.5;     // m/s: off the ball
  var BURST = 11.0;     // m/s: the player on the ball, or running onto a pass
  var FEET = [1.6, 1.4];  // metres: where the ball sits relative to its holder (x toward goal)

  function now() { return performance.now(); }
  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

  function cubic(x1, y1, x2, y2) {  // CSS-style cubic-bezier easing, solved for x
    return function (t) {
      if (t <= 0) { return 0; } if (t >= 1) { return 1; }
      var lo = 0, hi = 1, u = t;
      for (var i = 0; i < 24; i++) {
        u = (lo + hi) / 2;
        var x = 3 * (1 - u) * (1 - u) * u * x1 + 3 * (1 - u) * u * u * x2 + u * u * u;
        if (x < t) { lo = u; } else { hi = u; }
      }
      return 3 * (1 - u) * (1 - u) * u * y1 + 3 * (1 - u) * u * u * y2 + u * u * u;
    };
  }
  // a pass slows a little as it goes; a shot keeps nearly all its pace
  var EASE_PASS = cubic(0.33, 0.53, 0.6, 1);
  var EASE_SHOT = cubic(0.25, 0.4, 0.5, 1);

  var P = {
    dots: {},      // key "NBR:7" -> {team, num, x, y, vx, vy, tx, ty, arrive, cap}
    ball: { x: 52.5, y: 34, mode: "free", holder: null, flight: null, dir: 1 },
    ring: { x: 52.5, y: 34 },
    holder: null,  // key of the player the ring is on (or the pass is going to)
    last: 0,
    names: null,   // "NBR:Mensah" -> 7 (from app)

    keyOf: function (team, name) {
      var n = P.names ? P.names[team + ":" + name] : undefined;
      return n === undefined ? null : team + ":" + n;
    },
    dirOf: function (key) { return key && key.indexOf("CAL") === 0 ? -1 : 1; },

    reset: function () {
      P.dots = {};
      P.ball = { x: 52.5, y: 34, mode: "free", holder: null, flight: null, dir: 1 };
      P.ring = { x: 52.5, y: 34 };
      P.holder = null;
    },

    // one demo.shape (d) and the demo.event that came just before it (E, or null)
    apply: function (d, E) {
      var t = now();
      var D = Math.max(0, d.dur || 0) * 1000;
      var holderKey = d.holder ? (d.players.filter(function (p) {
        return P.names && P.names[p.team + ":" + d.holder] === p.num;
      }).map(function (p) { return p.team + ":" + p.num; })[0] || null) : null;
      var receiver = E && E.type === "pass" && E.to ? P.keyOf(E.team, E.to) : null;
      var flight = E ? Math.max(0, E.flight || 0) * 1000 : 0;
      var seen = {};
      d.players.forEach(function (p) {
        var key = p.team + ":" + p.num;
        seen[key] = true;
        var e = P.dots[key];
        if (!e || D === 0) {  // a new clip: everyone is placed while the card covers the pitch
          P.dots[key] = { team: p.team, num: p.num, x: p.x, y: p.y, vx: 0, vy: 0,
                          tx: p.x, ty: p.y, arrive: t, cap: SPRINT };
          return;
        }
        e.tx = p.x; e.ty = p.y;
        e.arrive = t + (key === receiver && flight > 0 ? flight : D);
        e.cap = key === holderKey || key === receiver ? BURST : SPRINT;
      });
      Object.keys(P.dots).forEach(function (k) { if (!seen[k]) { delete P.dots[k]; } });

      var b = P.ball;
      if (D === 0) {
        P.holder = holderKey;
        b.flight = null;
        if (holderKey) { b.mode = "held"; b.holder = holderKey; b.dir = P.dirOf(holderKey); P.placeAtFeet(); }
        else { b.mode = "free"; b.x = d.ball[0]; b.y = d.ball[1]; }
        P.ring = holderKey ? { x: P.dots[holderKey].x, y: P.dots[holderKey].y } : { x: b.x, y: b.y };
        return;
      }
      if (E && E.type === "pass" && E.end_xy) {
        var from = P.ballStart(E);
        b.mode = "flight";
        b.flight = { x0: from.x, y0: from.y, x1: E.end_xy[0], y1: E.end_xy[1], t0: t,
                     t1: t + Math.max(250, flight), ease: EASE_PASS, then: receiver };
        b.holder = null;
        P.holder = receiver;  // the ring and the tag go with the ball to him
      } else if (E && E.type === "shot" && E.end_xy) {
        var s = P.ballStart(E);
        var gx = E.goal ? (E.end_xy[0] > 52.5 ? 106.0 : -1.0) : E.end_xy[0];  // goals end in the net
        b.mode = "flight";
        b.flight = { x0: s.x, y0: s.y, x1: gx, y1: E.end_xy[1], t0: t, t1: t + Math.max(200, flight),
                     ease: EASE_SHOT, then: null };
        b.holder = null;
        P.holder = P.keyOf(E.team, E.player);  // the ring stays on the shooter
      } else if (holderKey) {
        if (b.mode === "flight" && b.flight) { b.flight.then = holderKey; }
        else { b.mode = "held"; b.holder = holderKey; b.dir = P.dirOf(holderKey); }
        P.holder = holderKey;
      }
    },

    // where a pass or shot leaves from: the ball itself, unless it is somewhere else entirely
    ballStart: function (E) {
      var b = P.ball;
      var spot = { x: E.xy[0], y: E.xy[1] };
      return dist(b, spot) > 6 ? spot : { x: b.x, y: b.y };
    },

    placeAtFeet: function () {
      var b = P.ball, h = P.dots[b.holder];
      if (!h) { return; }
      b.x = Math.max(0.3, Math.min(104.7, h.x + FEET[0] * b.dir));
      b.y = Math.max(0.3, Math.min(67.7, h.y + FEET[1]));
    },

    step: function () {
      var t = now();
      var dt = P.last ? Math.min(0.05, (t - P.last) / 1000) : 0;
      P.last = t;
      if (!dt) { return; }
      Object.keys(P.dots).forEach(function (k) {
        var e = P.dots[k];
        var dx = e.tx - e.x, dy = e.ty - e.y;
        var d = Math.hypot(dx, dy);
        if (d < 0.02) { e.x = e.tx; e.y = e.ty; e.vx = e.vy = 0; return; }
        var left = Math.max((e.arrive - t) / 1000, dt);
        var v = Math.min(d / left, e.cap);           // on time if he can make it, else flat out
        var wx = dx / d * v, wy = dy / d * v;
        var k8 = Math.min(1, dt * 9);                // he speeds up and turns, no instant jumps
        e.vx += (wx - e.vx) * k8; e.vy += (wy - e.vy) * k8;
        var step = Math.hypot(e.vx, e.vy) * dt;
        if (step >= d) { e.x = e.tx; e.y = e.ty; e.vx = e.vy = 0; }
        else { e.x += e.vx * dt; e.y += e.vy * dt; }
      });
      var b = P.ball;
      if (b.mode === "flight" && b.flight) {
        var f = b.flight;
        var u = Math.min(1, (t - f.t0) / (f.t1 - f.t0));
        var p = f.ease(u);
        b.x = f.x0 + (f.x1 - f.x0) * p; b.y = f.y0 + (f.y1 - f.y0) * p;
        if (u >= 1) {
          b.flight = null;
          if (f.then) { b.mode = "loose"; b.holder = f.then; b.dir = P.dirOf(f.then); }
          else { b.mode = "free"; }
        }
      }
      if (b.mode === "loose") {  // landed: his once he gets to it
        var h = P.dots[b.holder];
        if (!h) { b.mode = "free"; }
        else if (dist(h, b) < 3.0) { b.mode = "held"; }
      }
      if (b.mode === "held") {
        var hh = P.dots[b.holder];
        if (hh) {
          var gx = Math.max(0.3, Math.min(104.7, hh.x + FEET[0] * b.dir));
          var gy = Math.max(0.3, Math.min(67.7, hh.y + FEET[1]));
          var k = Math.min(1, dt * 14);                // settles at his feet, then moves with him
          b.x += (gx - b.x) * k; b.y += (gy - b.y) * k;
        }
      }
      // the ring is on the holder; while a pass is in the air it travels with the ball
      var target = (b.mode === "flight" && b.flight && b.flight.then) ? { x: b.x, y: b.y }
        : (P.holder && P.dots[P.holder]) ? P.dots[P.holder] : null;
      if (target) {
        var kr = Math.min(1, dt * 16);
        P.ring.x += (target.x - P.ring.x) * kr; P.ring.y += (target.y - P.ring.y) * kr;
      }
      P.paint();
    },

    // metres -> the drawn pitch (the white lines sit 8px inside the box)
    css: function (m, len) { return "calc(8px + (100% - 16px) * " + (m / len).toFixed(5) + ")"; },

    paint: function () {
      var box = document.querySelector('#screen [data-pitch]');
      if (!box) { return; }
      var els = box.querySelectorAll("[data-k]");
      for (var i = 0; i < els.length; i++) {
        var e = P.dots[els[i].getAttribute("data-k")];
        if (e) { els[i].style.left = P.css(e.x, 105); els[i].style.top = P.css(e.y, 68); }
      }
      var ball = box.querySelector("[data-ball]");
      if (ball) { ball.style.left = P.css(P.ball.x, 105); ball.style.top = P.css(P.ball.y, 68); }
      var ring = box.querySelector("[data-ring]");
      if (ring) {
        ring.style.left = P.css(P.ring.x, 105); ring.style.top = P.css(P.ring.y, 68);
        var tag = ring.querySelector("[data-tag]");
        if (tag) {  // keep the name tag on the pitch near the touchlines and the goal lines
          var x = P.ring.x, y = P.ring.y;  // slides smoothly, never jumps
          var shift = x < 20 ? -8 - x / 20 * 42 : x > 85 ? -50 - (x - 85) / 20 * 42 : -50;
          var top = y < 4 ? 13 : y > 12 ? -27 : 13 - (y - 4) / 8 * 40;
          tag.style.transform = "translateX(" + Math.max(-92, shift).toFixed(1) + "%)";
          tag.style.top = top.toFixed(1) + "px";
        }
      }
    }
  };

  function loop() { P.step(); requestAnimationFrame(loop); }
  requestAnimationFrame(loop);
  window.Pitch = P;
})();
