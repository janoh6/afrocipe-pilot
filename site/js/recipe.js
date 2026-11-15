/* One recipe sheet. Recipe text is untrusted input: textContent only, no HTML parsing. */
(function () {
  var lang = window.PAGE_LANG;
  var root = document.getElementById("sheet");
  var status = document.getElementById("status");
  var id = null;
  try { id = new URLSearchParams(location.search).get("id"); } catch (e) { id = null; }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  /* One entry per non-empty line; a leading "1." or "-" is dropped, the list adds its own marker. */
  function lines(s) {
    return String(s || "").split("\n").map(function (l) { return l.replace(/^\s*(?:\d{1,2}\s*[.)]|[-*\u2022])\s*/, "").trim(); })
      .filter(function (l) { return l.length > 0; });
  }
  /* A writer's own sub-heading ("For the sauce", "Phase 1: ...") is a short line with no closing full stop that does
     not start with an amount. Headings are never adjacent, never last, and at most a third of the lines: a recipe
     written as short steps without full stops keeps every line as a step. The words are never changed. */
  function headingLike(l) {
    if (/:$/.test(l)) return l.length <= 80;
    return l.length <= 60 && !/[.!?;,]$/.test(l) && !/^[\d\u00bc-\u00be\u2150-\u215e]/.test(l);
  }
  function groups(items) {
    var marks = items.map(function (l, i) { return i < items.length - 1 && headingLike(l); });
    var n = marks.filter(Boolean).length;
    var adjacent = marks.some(function (m, i) { return m && marks[i + 1]; });
    if (n === 0 || adjacent || n * 3 > items.length) marks = items.map(function () { return false; });
    var out = [{ title: "", items: [] }];
    items.forEach(function (l, i) {
      if (marks[i]) out.push({ title: l.replace(/:$/, ""), items: [] });
      else out[out.length - 1].items.push(l);
    });
    return out.filter(function (g) { return g.title || g.items.length; });
  }
  /* "Soak the bread: tear it..." shows its short label in bold, so a cook can find the step at a glance. */
  function withLead(parent, text) {
    var i = text.indexOf(": ");
    if (i >= 2 && i <= 45 && i < text.length - 2) {
      parent.appendChild(el("strong", "lead-in", text.slice(0, i + 1)));
      parent.appendChild(document.createTextNode(text.slice(i + 1)));
    } else parent.appendChild(document.createTextNode(text));
    return parent;
  }
  function section(title, nodes, name) {
    var s = el("section", "block block-" + name);
    s.appendChild(el("h2", null, title));
    nodes.forEach(function (n) { s.appendChild(n); });
    return s;
  }
  /* kind: "tick" (a box to tick off), "step" (numbered across the whole method, tick when done) or "plain". */
  function grouped(items, kind) {
    var nodes = [];
    groups(items).forEach(function (g) {
      if (g.title) nodes.push(el("h3", "group", g.title));
      if (!g.items.length) return;
      var l = el(kind === "step" ? "ol" : "ul", kind === "plain" ? "lines" : "ticks ticks-" + kind);
      g.items.forEach(function (i) {
        var li = el("li");
        if (kind === "plain") withLead(li, i);
        else {
          var label = el("label", "tick");
          var box = el("input");
          box.type = "checkbox";
          label.appendChild(box);
          if (kind === "step") label.appendChild(el("span", "num"));
          label.appendChild(withLead(el("span", "tick-text"), i));
          li.appendChild(label);
        }
        l.appendChild(li);
      });
      nodes.push(l);
    });
    return nodes;
  }
  function paragraphs(s) {
    return String(s || "").split("\n").map(function (l) { return l.trim(); }).filter(function (l) { return l.length > 0; })
      .map(function (l) { return withLead(el("p"), l); });
  }
  /* Keep the screen on while cooking, where the browser can (Screen Wake Lock). */
  function tools() {
    var box = el("div", "cook-tools no-print");
    box.appendChild(el("p", "cook-hint", t(lang, "tick_hint")));
    if (!("wakeLock" in navigator)) return box;
    var lock = null, wanted = false;
    var b = el("button", "btn-quiet", t(lang, "keep_on"));
    b.type = "button";
    b.setAttribute("aria-pressed", "false");
    function show() { b.setAttribute("aria-pressed", wanted ? "true" : "false"); b.textContent = t(lang, wanted ? "keep_on_active" : "keep_on"); }
    function acquire() {
      navigator.wakeLock.request("screen").then(function (l) { lock = l; }).catch(function () { wanted = false; show(); });
    }
    b.addEventListener("click", function () {
      wanted = !wanted;
      if (wanted) acquire(); else if (lock) { lock.release().catch(function () {}); lock = null; }
      show();
    });
    document.addEventListener("visibilitychange", function () { if (wanted && document.visibilityState === "visible") acquire(); });
    box.appendChild(b);
    return box;
  }

  function render(r) {
    document.title = (r.dish_name || r.code) + " - " + t(lang, "site_name");
    var head = document.getElementById("sheet-head");
    head.lang = r.lang;
    var top = el("p", "sheet-top");
    top.appendChild(el("span", "code", r.code));
    top.appendChild(document.createTextNode(" "));
    top.appendChild(el("span", "ver", t(lang, "version", { v: "v1" })));
    top.appendChild(document.createTextNode(" "));
    top.appendChild(el("span", "tag", LANG_NAMES[r.lang] || r.lang));
    head.appendChild(top);
    head.appendChild(el("h1", null, r.dish_name || r.code));
    if (r.other_names) head.appendChild(el("p", "also", t(lang, "f_also") + ": " + r.other_names.replace(/\s*\n\s*/g, ", ")));

    var art = el("article", lines(r.ingredients).length ? "sheet sheet-cols" : "sheet");
    art.lang = r.lang;
    var dl = el("dl", "facts");
    [["f_makes", r.makes], ["f_time", r.total_time], ["f_equipment", r.equipment]].forEach(function (p) {
      if (!p[1]) return;
      var row = el("div", "fact");
      row.appendChild(el("dt", null, t(lang, p[0])));
      var dd = el("dd");
      String(p[1]).split("\n").map(function (l) { return l.trim(); }).filter(function (l) { return l.length > 0; })
        .forEach(function (l) { dd.appendChild(el("span", "fact-line", l)); });
      row.appendChild(dd);
      dl.appendChild(row);
    });
    art.appendChild(dl);

    var ing = lines(r.ingredients);
    if (ing.length) art.appendChild(section(t(lang, "f_ingredients"), grouped(ing, "tick"), "ingredients"));
    var steps = lines(r.method);
    if (steps.length) art.appendChild(section(t(lang, "f_method"), grouped(steps, "step"), "method"));
    var signs = lines(r.signs);
    if (signs.length) art.appendChild(section(t(lang, "f_signs"), grouped(signs, "plain"), "signs"));
    if (r.finished_dish) art.appendChild(section(t(lang, "f_finished"), paragraphs(r.finished_dish), "finished"));
    return art;
  }

  function report(r) {
    var box = el("div", "report no-print");
    box.appendChild(el("p", "reminder", t(lang, "reminders")));
    var a = el("a", "btn btn-big", t(lang, "btn_report"));
    a.href = SITE.formUrl("report", lang) + "?recipe=" + encodeURIComponent(r.id) +
      "&dish=" + encodeURIComponent(r.dish_name || "") + "&version=v1";
    a.id = "report-link";
    box.appendChild(a);
    box.appendChild(el("p", "fine", t(lang, "not_author")));
    return box;
  }

  fetch("data/recipes.json", { cache: "no-cache" })
    .then(function (res) { if (!res.ok) throw new Error("http"); return res.json(); })
    .then(function (list) {
      var r = (Array.isArray(list) ? list : []).filter(function (x) { return x.id === id; })[0];
      if (!r) { status.textContent = t(lang, "not_found"); return; }
      status.textContent = "";
      root.appendChild(tools());
      root.appendChild(render(r));
      root.appendChild(report(r));
    })
    .catch(function () { status.textContent = t(lang, "load_error"); });
})();
