/* Shared page behaviour: language, translated text, language switch. Uses textContent only. */
(function () {
  var lang = getLang();
  window.PAGE_LANG = lang;
  document.documentElement.lang = lang;

  function apply(root) {
    var nodes = (root || document).querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) nodes[i].textContent = t(lang, nodes[i].getAttribute("data-i18n"));
    var labels = (root || document).querySelectorAll("[data-i18n-aria]");
    for (var j = 0; j < labels.length; j++) labels[j].setAttribute("aria-label", t(lang, labels[j].getAttribute("data-i18n-aria")));
  }
  window.applyTranslations = apply;

  function switcher() {
    var box = document.getElementById("langswitch");
    if (!box) return;
    box.setAttribute("aria-label", t(lang, "lang_label"));
    ["en", "fr", "sw"].forEach(function (code) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = code.toUpperCase();
      b.lang = code;
      b.title = LANG_NAMES[code];
      b.setAttribute("aria-label", LANG_NAMES[code]);
      if (code === lang) b.setAttribute("aria-current", "true");
      b.addEventListener("click", function () { setLang(code); location.href = location.pathname + location.search.replace(/([?&])lang=[^&]*&?/, "$1").replace(/[?&]$/, ""); });
      box.appendChild(b);
    });
  }

  function links() {
    var m = document.querySelectorAll("[data-form]");
    for (var i = 0; i < m.length; i++) m[i].href = SITE.formUrl(m[i].getAttribute("data-form"), lang);
    var g = document.querySelectorAll("[data-guide]");
    for (var k = 0; k < g.length; k++) g[k].href = SITE.guides[g[k].getAttribute("data-guide")][lang];
    var mail = document.getElementById("contact");
    if (mail) { mail.textContent = SITE.contact; mail.href = "mailto:" + SITE.contact; }
  }

  apply();
  switcher();
  links();
})();
