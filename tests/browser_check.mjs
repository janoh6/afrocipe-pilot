// Browser check: every page at 360 and 1280 px in en/fr/sw, with zero console errors, page errors,
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
const browser = await chromium.launch();
for (const width of [360, 1280]) {
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
        const shown = await page.locator("article.prose:visible").count();
        const l = await page.locator("article.prose:visible").first().getAttribute("lang");
        if (shown !== 1 || l !== lang) problems.push(`${tag}: expected one ${lang} block, saw ${shown} (${l})`);
      }
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
