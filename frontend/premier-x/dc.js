// dc.js — a small runtime for the design canvas templates (*.dc.html) of The Premier X.
//
// A template file holds <x-dc><helmet>…</helmet> markup… </x-dc> and a
// <script type="text/x-dc"> with `class Component extends DCLogic`. The markup is used as is:
//   {{expr}}                 text and attribute bindings (expr is JS, evaluated against renderVals())
//   <sc-for list as>         repeat the children for each item of a list
//   <sc-if value>            keep the children only when the value is truthy
//   onClick / onChange       event handlers; ref="{{fn}}" gets the live element; key="{{x}}" keys a node
// Every render builds the screen afresh and morphs it into the live DOM, keyed by template
// position, so CSS transitions run and `sc-if` / `sc-for` content animates in when it appears.

(function () {
  "use strict";

  var BOOL_ATTRS = { disabled: 1, checked: 1, hidden: 1, readonly: 1, selected: 1 };
  var EXPR_RE = /\{\{([\s\S]+?)\}\}/g;
  var fnCache = {};

  function compileExpr(src) {
    var key = src.trim();
    if (!fnCache[key]) {
      try {
        // eslint-disable-next-line no-new-func
        fnCache[key] = new Function("s", "with (s) { return (" + key + "); }");
      } catch (e) {
        console.error("[dc] bad expression", key, e);
        fnCache[key] = function () { return undefined; };
      }
    }
    return fnCache[key];
  }

  function evalExpr(src, scope) {
    try { return compileExpr(src)(scope); } catch (e) { return undefined; }
  }

  function single(value) {
    var m = /^\s*\{\{([\s\S]+?)\}\}\s*$/.exec(value);
    return m && m[1].indexOf("}}") === -1 ? m[1] : null;
  }

  function interpolate(text, scope) {
    return text.replace(EXPR_RE, function (_, expr) {
      var v = evalExpr(expr, scope);
      return v === undefined || v === null || v === false ? "" : String(v);
    });
  }

  // --- base class ------------------------------------------------------------------------

  function DCLogic(props) {
    this.props = props || {};
    this.state = {};
    this.__screen = null;
  }
  DCLogic.prototype.setState = function (patch, cb) {
    var next = typeof patch === "function" ? patch(this.state) : patch;
    this.state = Object.assign({}, this.state, next);
    if (this.__screen) { this.__screen.schedule(cb); } else if (cb) { cb(); }
  };
  DCLogic.prototype.forceUpdate = function (cb) { if (this.__screen) { this.__screen.schedule(cb); } };
  DCLogic.prototype.renderVals = function () { return {}; };

  // --- template parsing ----------------------------------------------------------------------

  var tidSeq = 0;

  function parseTemplate(html, baseUrl) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var root = doc.querySelector("x-dc");
    var helmet = root.querySelector("helmet");
    var styles = [];
    if (helmet) {
      helmet.querySelectorAll("style").forEach(function (s) { styles.push(s.textContent); });
      helmet.remove();
    }
    var script = doc.querySelector('script[type="text/x-dc"]');
    var title = doc.querySelector("title");
    (function mark(node) {
      for (var c = node.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 1) {
          c.__tid = "t" + (++tidSeq);
          if (baseUrl) {
            ["src", "href"].forEach(function (a) {
              var v = c.getAttribute(a);
              if (v && v.indexOf("{{") === -1 && /^\.\.?\//.test(v) && !/\.dc\.html$/.test(v)) {
                c.setAttribute(a, new URL(v, baseUrl).href);
              }
            });
          }
          mark(c);
        }
      }
    })(root);
    return {
      root: root,
      css: styles.join("\n"),
      title: title ? title.textContent : "",
      source: script ? script.textContent : "class Component extends DCLogic {}"
    };
  }

  // --- building one render -------------------------------------------------------------------

  function build(tplParent, scope, path, out) {
    var textIdx = 0;
    for (var c = tplParent.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 3) {
        var t = c.nodeValue;
        if (!t.trim() && t.indexOf("\n") !== -1) { continue; }  // indentation between tags
        var tn = document.createTextNode(t.indexOf("{{") === -1 ? t : interpolate(t, scope));
        tn.__k = path + "#x" + (textIdx++);
        out.push(tn);
        continue;
      }
      if (c.nodeType !== 1) { continue; }
      var tag = c.localName;
      if (tag === "sc-for") {
        var list = evalExpr(single(c.getAttribute("list")) || "[]", scope) || [];
        var as = c.getAttribute("as") || "item";
        for (var i = 0; i < list.length; i++) {
          var sub = Object.create(scope);
          sub[as] = list[i];
          sub[as + "Index"] = i;
          build(c, sub, path + "/" + c.__tid + "." + i, out);
        }
        continue;
      }
      if (tag === "sc-if") {
        if (evalExpr(single(c.getAttribute("value")) || "false", scope)) {
          build(c, scope, path + "/" + c.__tid, out);
        }
        continue;
      }
      out.push(buildElement(c, scope, path));
    }
  }

  function buildElement(tpl, scope, path) {
    var el = tpl.namespaceURI && tpl.namespaceURI !== "http://www.w3.org/1999/xhtml"
      ? document.createElementNS(tpl.namespaceURI, tpl.localName)
      : document.createElement(tpl.localName);
    var dc = { on: {}, ref: null, value: undefined };
    var key = null;
    for (var i = 0; i < tpl.attributes.length; i++) {
      var a = tpl.attributes[i];
      var name = a.name, raw = a.value;
      var expr = single(raw);
      if (name === "key") { key = expr ? evalExpr(expr, scope) : raw; continue; }
      if (name === "ref") { dc.ref = expr ? evalExpr(expr, scope) : null; continue; }
      if (name.slice(0, 2) === "on" && name.length > 2) {
        dc.on[name.slice(2).toLowerCase()] = expr ? evalExpr(expr, scope) : null;
        continue;
      }
      if (name.indexOf("hint-") === 0) { continue; }
      var v = expr ? evalExpr(expr, scope) : (raw.indexOf("{{") === -1 ? raw : interpolate(raw, scope));
      if (BOOL_ATTRS[name]) {
        if (v === true || v === "true" || (expr === null && v !== false && v !== "false")) {
          el.setAttribute(name, "");
        }
        if (name === "checked") { dc.checked = !!(v === true || v === "true"); }
        continue;
      }
      if (name === "value" && (tpl.localName === "input" || tpl.localName === "textarea")) {
        dc.value = v === undefined || v === null ? "" : String(v);
        continue;
      }
      if (name.indexOf("aria-") === 0 && typeof v === "boolean") { el.setAttribute(name, String(v)); continue; }
      if (v === undefined || v === null || v === false) { continue; }
      el.setAttribute(name, v === true ? "" : String(v));
    }
    // an explicit key names the node by itself, so a list item keeps its element when the
    // list reorders (a pitch dot keeps transitioning from where it is)
    el.__k = key !== null && key !== undefined ? "k:" + tpl.__tid + "~" + key : path + "/" + tpl.__tid;
    el.__dc = dc;
    var kids = [];
    build(tpl, scope, el.__k, kids);
    kids.forEach(function (k) { el.appendChild(k); });
    return el;
  }

  // --- morphing into the live DOM ------------------------------------------------------------

  function wire(el) {
    if (el.__wired) { return; }
    el.__wired = true;
    ["click", "change", "input", "keydown", "submit"].forEach(function (type) {
      el.addEventListener(type, function (e) {
        var fn = el.__dc && el.__dc.on[type];
        if (typeof fn === "function") { fn(e); }
        // the design wires onChange on text inputs; fire it on every keystroke too
        if (type === "input" && el.__dc && !el.__dc.on.input && typeof el.__dc.on.change === "function"
            && el.type !== "checkbox") { el.__dc.on.change(e); }
      });
    });
  }

  function adopt(fresh) {
    // fresh nodes go in as they are; wire every element in the subtree
    if (fresh.nodeType === 1) {
      wire(fresh);
      applyProps(fresh, fresh.__dc);
      for (var c = fresh.firstChild; c; c = c.nextSibling) { adopt(c); }
    }
    return fresh;
  }

  function applyProps(el, dc) {
    if (!dc) { return; }
    if (dc.value !== undefined && el.value !== dc.value && document.activeElement !== el) { el.value = dc.value; }
    if (dc.checked !== undefined && el.checked !== dc.checked) { el.checked = dc.checked; }
  }

  function morphChildren(live, fresh, refs) {
    var olds = {};
    for (var c = live.firstChild; c; c = c.nextSibling) {
      if (c.__k) { olds[c.__k] = c; }
    }
    var cursor = live.firstChild;
    var wanted = [];
    for (var f = fresh.firstChild; f; f = f.nextSibling) { wanted.push(f); }
    wanted.forEach(function (n) {
      var old = olds[n.__k];
      var keep = old && old.nodeType === n.nodeType && (n.nodeType !== 1 || old.localName === n.localName);
      var node;
      if (keep) {
        delete olds[n.__k];
        morphNode(old, n, refs);
        node = old;
      } else {
        node = adopt(n);
        collectRefs(node, refs);
      }
      if (node !== cursor) { live.insertBefore(node, cursor); } else { cursor = cursor.nextSibling; }
    });
    // drop what is left over (and any stray nodes without a key)
    var kill = [];
    for (var x = live.firstChild; x; x = x.nextSibling) {
      if (!x.__k || olds[x.__k] === x) { kill.push(x); }
    }
    kill.forEach(function (k) { live.removeChild(k); });
  }

  function collectRefs(node, refs) {
    if (node.nodeType !== 1) { return; }
    if (node.__dc && typeof node.__dc.ref === "function") { refs.push([node.__dc.ref, node]); }
    for (var c = node.firstChild; c; c = c.nextSibling) { collectRefs(c, refs); }
  }

  function morphNode(old, fresh, refs) {
    if (old.nodeType === 3) {
      if (old.nodeValue !== fresh.nodeValue) { old.nodeValue = fresh.nodeValue; }
      return;
    }
    var i, a;
    for (i = old.attributes.length - 1; i >= 0; i--) {
      a = old.attributes[i];
      if (!fresh.hasAttribute(a.name)) { old.removeAttribute(a.name); }
    }
    for (i = 0; i < fresh.attributes.length; i++) {
      a = fresh.attributes[i];
      if (old.getAttribute(a.name) !== a.value) { old.setAttribute(a.name, a.value); }
    }
    old.__dc = fresh.__dc;
    applyProps(old, fresh.__dc);
    if (fresh.__dc && typeof fresh.__dc.ref === "function") { refs.push([fresh.__dc.ref, old]); }
    morphChildren(old, fresh, refs);
  }

  // --- a mounted screen ------------------------------------------------------------------------

  function Screen(tpl, Component, props, host) {
    this.tpl = tpl;
    this.host = host;
    this.pending = [];
    this.queued = false;
    this.dead = false;
    this.comp = new Component(props);
    this.comp.__screen = this;
  }
  Screen.prototype.render = function () {
    if (this.dead) { return; }
    var vals = this.comp.renderVals() || {};
    var scope = Object.create(null);
    Object.keys(vals).forEach(function (k) { scope[k] = vals[k]; });
    var frag = document.createElement("div");
    var kids = [];
    build(this.tpl.root, scope, "", kids);
    kids.forEach(function (k) { frag.appendChild(k); });
    var refs = [];
    morphChildren(this.host, frag, refs);
    refs.forEach(function (r) { try { r[0](r[1]); } catch (e) { console.error(e); } });
  };
  Screen.prototype.schedule = function (cb) {
    var self = this;
    if (cb) { this.pending.push(cb); }
    if (this.queued) { return; }
    this.queued = true;
    requestAnimationFrame(function () {
      self.queued = false;
      self.render();
      var cbs = self.pending; self.pending = [];
      cbs.forEach(function (f) { f(); });
    });
  };
  Screen.prototype.mount = function () {
    this.render();
    if (typeof this.comp.componentDidMount === "function") { this.comp.componentDidMount(); }
  };
  Screen.prototype.unmount = function () {
    if (typeof this.comp.componentWillUnmount === "function") { this.comp.componentWillUnmount(); }
    this.dead = true;
    this.host.innerHTML = "";
  };

  // --- loading ---------------------------------------------------------------------------------

  var cache = {};

  function load(url, globals) {
    if (cache[url]) { return Promise.resolve(cache[url]); }
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) { throw new Error("cannot load " + url); }
      return r.text();
    }).then(function (html) {
      var tpl = parseTemplate(html, new URL(url, location.href).href);
      var names = ["DCLogic"].concat(Object.keys(globals || {}));
      var args = [DCLogic].concat(names.slice(1).map(function (n) { return globals[n]; }));
      // eslint-disable-next-line no-new-func
      var Component = new Function(names.join(","), tpl.source + "\nreturn Component;").apply(null, args);
      cache[url] = { tpl: tpl, Component: Component };
      return cache[url];
    });
  }

  window.DC = { DCLogic: DCLogic, Screen: Screen, load: load };
})();
