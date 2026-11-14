// Browser check: every page at 360, 800, 1280 and 1920 px in en/fr/sw, with zero console errors, page errors,
// failed requests or CSP violations. Usage:
//   python3 build.py --fixtures tests/fixtures && python3 -m http.server -d site 8765 &
//   PLAYWRIGHT=<path to the playwright package> node tests/browser_check.mjs http://localhost:8765
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT || "playwright");
const base = process.argv[2] || "http://localhost:8765";
const PASS = process.env.DASHBOARD_PASSPHRASE || "pilot-fixture";
const pages = ["index.html", "recipes.html", "recipe.html?id=Jo11of", "dashboard.html", "guide-testers.html", "guide-writers.html", "about.html"];
const problems = [];
let checked = 0;
const headers = {};
const gapsSeen = {};
const browser = await chromium.launch();
for (const width of [360, 800, 1280, 1920]) {
  for (const lang of ["en", "fr", "sw"]) {
    const ctx = await browser.newContext({ viewport: { width, height: 800 } });
    for (const p of pages) {
      const page = await ctx.newPage();
      const tag = `${p} ${lang} ${width}`;
      page.on("console", (m) => { if (["error", "warning"].includes(m.type())) problems.push(`${tag}: console ${m.type()}: ${m.text()}`); });
      page.on("pageerror", (e) => problems.push(`${tag}: pageerror: ${e.message}`));
      page.on("requestfailed", (r) => problems.push(`${tag}: request failed: ${r.url()}`));
      await page.addInitScript(() => document.addEventListener("securitypolicyviolation", (e) => { (window.__csp = window.__csp || []).push(e.violatedDirective + " " + e.blockedURI); }));
      await page.goto(`${base}/${p}${p.includes("?") ? "&" : "?"}lang=${lang}`, { waitUntil: "networkidle" });
      if (p === "recipes.html" && (await page.locator(".card").count()) === 0) problems.push(`${tag}: no recipe cards`);
      if (p.startsWith("recipe.html") && (await page.locator("h1").innerText()).trim() === "") problems.push(`${tag}: empty heading`);
      if (p === "dashboard.html") {
        await page.fill("#pass", PASS);
        await page.click("button[type=submit]");
        await page.waitForSelector("#out table", { timeout: 15000 });
        const head = await page.locator("#out thead").first().innerText();
        const wantHead = { en: "Count", fr: "Élément", sw: "Kipengele" }[lang];
        if (!head.includes(wantHead)) problems.push(`${tag}: dashboard not translated (${head.trim()})`);
        if ((await page.locator("#out table").count()) !== 3) problems.push(`${tag}: expected 3 tables`);
      }
      if (/^(guide|about)/.test(p)) {
        const shown = await page.locator("article[data-lang]:visible").count();
        const l = await page.locator("article[data-lang]:visible").first().getAttribute("lang");
        if (shown !== 1 || l !== lang) problems.push(`${tag}: expected one ${lang} block, saw ${shown} (${l})`);
      }
      // Gutters: every container on the page starts and ends at the same x, header and footer included.
      const edges = await page.evaluate(() => [...document.querySelectorAll(".wrap")].filter((e) => e.offsetParent !== null)
        .map((e) => { const r = e.getBoundingClientRect(); return Math.round(r.left) + ":" + Math.round(r.right); }));
      if (new Set(edges).size !== 1) problems.push(`${tag}: containers do not share one gutter (${[...new Set(edges)].join(" ")})`);
      // Footer: at or below the bottom of the window, never floating in the middle of a short page.
      const foot = await page.evaluate(() => Math.round(document.querySelector(".site-footer").getBoundingClientRect().bottom + window.scrollY) >= window.innerHeight);
      if (!foot) problems.push(`${tag}: footer is not at the bottom of the window`);
      const navShown = await page.locator("#site-nav").isVisible();
      if (width < 896 && navShown) problems.push(`${tag}: phone menu should start closed`);
      if (width < 896) { await page.click("#menu-toggle"); if (!(await page.locator("#site-nav").isVisible())) problems.push(`${tag}: menu button does not open the menu`); await page.click("#menu-toggle"); }
      if (width >= 896 && !navShown) problems.push(`${tag}: menu hidden on a wide screen`);
      // Header: the logo, every menu link and the language switch sit at the same place on every page.
      const hdr = await page.evaluate(() => [...document.querySelectorAll(".brand, .nav a, .langs, .menu-toggle")].filter((e) => e.offsetParent !== null)
        .map((e) => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map((n) => Math.round(n * 2) / 2).join(","); }).join(" | "));
      const hk = `${lang} ${width}`;
      if (!(hk in headers)) headers[hk] = [p, hdr];
      else if (headers[hk][1] !== hdr) problems.push(`${tag}: header differs from ${headers[hk][0]}\n    ${headers[hk][1]}\n    ${hdr}`);
      // Gaps: the space between the header and the first content, and between the last content and the footer, is the same on every page.
      const gaps = await page.evaluate(() => {
        const vis = [...document.querySelectorAll("main .wrap > *")].filter((e) => e.offsetParent !== null && e.getBoundingClientRect().height > 0);
        const top = Math.min(...vis.map((e) => e.getBoundingClientRect().top)), bottom = Math.max(...vis.map((e) => e.getBoundingClientRect().bottom));
        return [Math.round(top - document.querySelector(".site-header").getBoundingClientRect().bottom), Math.round(document.querySelector(".site-footer").getBoundingClientRect().top - bottom)].join(" / ");
      });
      if (!(width in gapsSeen)) gapsSeen[width] = [p, gaps];
      else if (gapsSeen[width][1] !== gaps) problems.push(`${tag}: top / bottom gap ${gaps}, but ${gapsSeen[width][1]} on ${gapsSeen[width][0]}`);
      const fr = await page.evaluate(() => { const w = document.querySelector(".footer-grid").getBoundingClientRect(), s = getComputedStyle(document.querySelector(".footer-grid")); const c = document.querySelector("#contact").getBoundingClientRect(); return Math.round(w.right - parseFloat(s.paddingRight) - c.right); });
      if (width >= 640 && fr !== 0) problems.push(`${tag}: footer's last column stops ${fr}px short of the right gutter`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      if (overflow) problems.push(`${tag}: horizontal overflow`);
      const csp = await page.evaluate(() => window.__csp || []);
      for (const v of csp) problems.push(`${tag}: CSP violation: ${v}`);
      if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/${p.split(/[.?]/)[0]}-${lang}-${width}.png`, fullPage: true });
      checked++;
      await page.close();
    }
    await ctx.close();
  }
}
await browser.close();
console.log(`${checked} page loads checked, ${problems.length} problems`);
for (const x of problems) console.log(x);
process.exit(problems.length ? 1 : 0);
