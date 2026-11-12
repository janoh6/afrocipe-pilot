/* Private dashboard: decrypts data/dashboard.enc.json in the browser (PBKDF2 + AES-GCM). */
(function () {
  var form = document.getElementById("unlock");
  var pass = document.getElementById("pass");
  var msg = document.getElementById("msg");
  var out = document.getElementById("out");

  function b64(s) { var bin = atob(s), a = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a; }

  async function decrypt(blob, passphrase) {
    var enc = new TextEncoder();
    var base = await crypto.subtle.importKey("raw", enc.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    var key = await crypto.subtle.deriveKey(
      { name: "PBKDF2", salt: b64(blob.salt), iterations: blob.iterations, hash: blob.hash },
      base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    var plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(blob.iv) }, key, b64(blob.ct));
    return JSON.parse(new TextDecoder().decode(plain));
  }

  function el(tag, text, cls) { var e = document.createElement(tag); if (text !== undefined) e.textContent = String(text); if (cls) e.className = cls; return e; }

  function table(head, rows) {
    var wrap = el("div", undefined, "table-wrap");
    var t = el("table");
    var tr = el("tr");
    head.forEach(function (h) { var th = el("th", h); th.scope = "col"; tr.appendChild(th); });
    var thead = el("thead"); thead.appendChild(tr); t.appendChild(thead);
    var tb = el("tbody");
    rows.forEach(function (r) { var row = el("tr"); r.forEach(function (c, i) { row.appendChild(i === 0 ? (function () { var th = el("th", c); th.scope = "row"; return th; })() : el("td", c)); }); tb.appendChild(row); });
    t.appendChild(tb); wrap.appendChild(t);
    return wrap;
  }

  var lang = window.PAGE_LANG;
  function L(k, v) { return t(lang, k, v); }
  document.title = L("dash_title") + " - " + L("site_name");

  function show(d) {
    out.textContent = "";
    var t0 = d.totals;
    out.appendChild(el("p", L("dash_built", { d: d.generated }), "fine"));
    out.appendChild(el("h2", L("dash_totals")));
    out.appendChild(table([L("dash_count"), L("dash_number")], [
      [L("dash_published"), t0.recipes_published], [L("dash_hidden"), t0.hidden], [L("dash_awaiting"), t0.awaiting_consent]]));
    out.appendChild(el("h2", L("dash_bylang")));
    out.appendChild(table([L("dash_language"), L("dash_signups"), L("dash_reports")],
      ["en", "fr", "sw"].map(function (l) { return [l.toUpperCase(), t0.signups[l], t0.reports[l]]; })));
    out.appendChild(el("h2", L("dash_recipes")));
    out.appendChild(table([L("dash_code"), L("dash_dish"), L("dash_language"), L("dash_tests"), L("dash_made"), L("dash_not"), L("dash_cannot"), L("dash_stopped"), L("dash_unrecognised"), L("dash_reached", { n: d.target_tests })],
      d.recipes.map(function (r) { var o = r.outcomes; return [r.code, r.dish_name, r.lang.toUpperCase(), r.tests, o.made, o.not, o.cannot_tell, o.stopped, o.unrecognised || 0, r.reached_target ? L("dash_yes") : L("dash_no")]; })));
  }

  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    msg.textContent = "";
    out.textContent = "";
    try {
      var res = await fetch("data/dashboard.enc.json", { cache: "no-cache" });
      if (!res.ok) { msg.textContent = L("dash_nofile"); return; }
      var blob = await res.json();
      var data;
      try { data = await decrypt(blob, pass.value); }
      catch (e) { msg.textContent = L("dash_wrong"); return; }
      pass.value = "";
      show(data);
    } catch (e) { msg.textContent = L("dash_loadfail"); }
  });
})();
