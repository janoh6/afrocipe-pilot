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
    return String(s || "").split("\n").map(function (l) { return l.replace(/^\s*(?:\d{1,2}\s*[.)]|[-*•])\s*/, "").trim(); })
      .filter(function (l) { return l.length > 0; });
  }
  function section(title, node, name) {
    var s = el("section", "block block-" + name);
    s.appendChild(el("h2", null, title));
    s.appendChild(node);
    return s;
  }
  function list(tag, items) {
    var l = el(tag, "lines");
    items.forEach(function (i) { l.appendChild(el("li", null, i)); });
    return l;
  }

  function render(r) {
    document.title = (r.dish_name || r.code) + " - " + t(lang, "site_name");
    var art = el("article", lines(r.ingredients).length ? "sheet sheet-cols" : "sheet");
    art.lang = r.lang;
    var top = el("p", "sheet-top");
    top.appendChild(el("span", "code", r.code));
    top.appendChild(document.createTextNode(" "));
    top.appendChild(el("span", "ver", t(lang, "version", { v: "v1" })));
    top.appendChild(document.createTextNode(" "));
    top.appendChild(el("span", "tag", LANG_NAMES[r.lang] || r.lang));
    art.appendChild(top);
    art.appendChild(el("h1", null, r.dish_name || r.code));
    if (r.other_names) art.appendChild(el("p", "also", t(lang, "f_also") + ": " + r.other_names.replace(/\s*\n\s*/g, ", ")));

    var dl = el("dl", "facts");
    [["f_makes", r.makes], ["f_time", r.total_time], ["f_equipment", r.equipment]].forEach(function (p) {
      if (!p[1]) return;
      var row = el("div", "fact");
      row.appendChild(el("dt", null, t(lang, p[0])));
      row.appendChild(el("dd", null, p[1].replace(/\s*\n\s*/g, ", ")));
      dl.appendChild(row);
    });
    art.appendChild(dl);

    var ing = lines(r.ingredients);
    if (ing.length) art.appendChild(section(t(lang, "f_ingredients"), list("ul", ing), "ingredients"));
    var steps = lines(r.method);
    if (steps.length) art.appendChild(section(t(lang, "f_method"), list("ol", steps), "method"));
    var signs = lines(r.signs);
    if (signs.length) art.appendChild(section(t(lang, "f_signs"), list("ul", signs), "signs"));
    if (r.finished_dish) art.appendChild(section(t(lang, "f_finished"), el("p", null, r.finished_dish), "finished"));
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
      root.appendChild(render(r));
      root.appendChild(report(r));
    })
    .catch(function () { status.textContent = t(lang, "load_error"); });
})();
