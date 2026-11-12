/* Recipe list. Recipe text is untrusted: textContent only. */
(function () {
  var lang = window.PAGE_LANG;
  var box = document.getElementById("list");
  var status = document.getElementById("status");

  function counts(n) {
    if (n >= SITE.target) return t(lang, "tests_done", { n: n });
    var m = SITE.target - n;
    return n === 1 ? t(lang, "tests_more_1", { m: m }) : t(lang, "tests_more", { n: n, m: m });
  }

  function card(r) {
    var li = document.createElement("li");
    li.className = "card";
    var a = document.createElement("a");
    a.href = "recipe.html?id=" + encodeURIComponent(r.id);
    a.className = "card-link";
    var h = document.createElement("h2");
    h.textContent = r.dish_name || r.code;
    a.appendChild(h);
    li.appendChild(a);
    var meta = document.createElement("p");
    meta.className = "meta";
    var code = document.createElement("span");
    code.className = "code";
    code.textContent = r.code;
    var tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = LANG_NAMES[r.lang] || r.lang;
    tag.title = t(lang, "written_in", { lang: LANG_NAMES[r.lang] || r.lang });
    meta.appendChild(code);
    meta.appendChild(tag);
    li.appendChild(meta);
    var c = document.createElement("p");
    c.className = "count";
    c.textContent = counts(r.tests || 0);
    li.appendChild(c);
    return li;
  }

  fetch("data/recipes.json", { cache: "no-cache" })
    .then(function (res) { if (!res.ok) throw new Error("http"); return res.json(); })
    .then(function (list) {
      status.textContent = "";
      if (!Array.isArray(list) || list.length === 0) {
        status.textContent = t(lang, "empty");
        return;
      }
      list.sort(function (a, b) {
        return (a.tests || 0) - (b.tests || 0) || String(b.submitted).localeCompare(String(a.submitted)) || String(a.id).localeCompare(String(b.id));
      });
      list.forEach(function (r) { box.appendChild(card(r)); });
    })
    .catch(function () { status.textContent = t(lang, "load_error"); });
})();
