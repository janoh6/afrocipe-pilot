#!/usr/bin/env python3
"""Write the content pages (guides, about) from content/<page>.<lang>.md into site/<page>.html.

Each page holds the three languages; css shows the one that matches <html lang>. The text is ours, not form
input, and is still escaped. Markdown subset: # and ## headings, paragraphs, "1." lists, "-" lists, "- [ ]" check
lists, | tables | (first row is the header), **bold**, [text](target). A target is a site page, mailto:, or
form:<kind> for a Tally form in the page language. A line "!notice" inserts the site's standard notice, and
"!placeholder text" a visibly unfinished box.

    python3 make_pages.py          write the pages
    python3 make_pages.py --check  exit 1 if a page on disk differs
"""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LANGS = ("en", "fr", "sw")
PAGES = {"guide-testers": "Guide for testers", "guide-writers": "Guide for recipe writers", "about": "About"}
FORMS = {
    "submit": {"en": "RWdKp4", "fr": "VpQ1VJ", "sw": "Ge9BVL"},
    "signup": {"en": "QoeBd8", "fr": "J1pkL4", "sw": "OQ4BZY"},
}
PRINTABLE = ("guide-testers", "guide-writers")
CSP = ("default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "base-uri 'none'; form-action 'none'")

HEADER = """<a class="skip" href="#main" data-i18n="skip">Skip to content</a>
<header class="site-header no-print">
  <div class="wrap bar">
    <a class="brand" href="index.html">
      <span class="logo"><img src="assets/AFROCIPE_Glyph_Master_RGB.svg" alt="" width="1000" height="593"></span>
      <span class="brand-name" data-i18n="site_name">Afrocipe Pilot</span>
    </a>
    <nav class="nav" aria-label="Main">
      <a href="index.html" data-i18n="nav_home">Home</a>
      <a href="recipes.html" data-i18n="nav_recipes">Recipes</a>
      <a href="guide-testers.html" data-i18n="nav_testers">For testers</a>
      <a href="guide-writers.html" data-i18n="nav_writers">For writers</a>
      <a href="about.html" data-i18n="nav_about">About</a>
    </nav>
    <div id="langswitch" class="langs" role="group"></div>
  </div>
</header>"""

FOOTER = """<footer class="site-footer no-print">
  <div class="wrap">
    <p><span data-i18n="footer_contact">Questions? Write to</span> <a id="contact" href="mailto:afrocipe12@gmail.com">afrocipe12@gmail.com</a></p>
    <p class="fine" data-i18n="footer_note">A pilot by Afrocipe.</p>
  </div>
</footer>
<script src="js/config.js"></script>
<script src="js/i18n.js"></script>
<script src="js/common.js"></script>"""


def inline(text, lang):
    out, pos = [], 0
    for m in re.finditer(r"\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)", text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        if m.group(1) is not None:
            out.append("<strong>%s</strong>" % inline(m.group(1), lang))
        else:
            label, target = html.escape(m.group(2), quote=False), m.group(3)
            if target.startswith("form:"):
                kind = target[5:]
                out.append('<a data-form="%s" href="https://tally.so/r/%s">%s</a>' % (kind, FORMS[kind][lang], label))
            elif re.fullmatch(r"[a-z-]+\.html|mailto:afrocipe12@gmail\.com", target):
                out.append('<a href="%s">%s</a>' % (target, label))
            else:
                raise SystemExit("make_pages: link target not allowed: %s" % target)
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def table(rows, lang):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head, body = cells[0], cells[1:]
    out = ['<div class="table-wrap"><table class="stack">', "<thead><tr>"]
    out += ["<th scope=\"col\">%s</th>" % inline(c, lang) for c in head]
    out.append("</tr></thead>\n<tbody>")
    for r in body:
        if len(r) != len(head):
            raise SystemExit("make_pages: table row has %d cells, header has %d" % (len(r), len(head)))
        out.append("<tr>" + "".join('<td data-label="%s">%s</td>' % (html.escape(h, quote=True), inline(c, lang))
                                    for h, c in zip(head, r)) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def render(md, lang):
    out, lines, i = [], md.splitlines(), 0

    def take(pattern):
        nonlocal i
        got = []
        while i < len(lines) and re.match(pattern, lines[i]):
            got.append(re.sub(pattern, "", lines[i], count=1))
            i += 1
        return got

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("## "):
            out.append("<h2>%s</h2>" % inline(line[3:], lang)); i += 1
        elif line.startswith("# "):
            out.append("<h1>%s</h1>" % inline(line[2:], lang)); i += 1
        elif line == "!notice":
            out.append('<p class="notice" data-i18n="notice" role="note"></p>'); i += 1
        elif line.startswith("!placeholder "):
            out.append('<p class="placeholder">%s</p>' % inline(line[13:], lang)); i += 1
        elif line.startswith("|"):
            out.append(table(take(r"(?=\|)"), lang))
        elif line.startswith("- [ ] "):
            out.append('<ul class="checks">\n%s\n</ul>' % "\n".join("<li>%s</li>" % inline(x, lang) for x in take(r"- \[ \] ")))
        elif line.startswith("- "):
            out.append("<ul>\n%s\n</ul>" % "\n".join("<li>%s</li>" % inline(x, lang) for x in take(r"- ")))
        elif re.match(r"\d+\. ", line):
            out.append("<ol>\n%s\n</ol>" % "\n".join("<li>%s</li>" % inline(x, lang) for x in take(r"\d+\. ")))
        else:
            out.append("<p>%s</p>" % inline(line, lang)); i += 1
    return "\n".join(out)


def page(name):
    blocks = []
    for lang in LANGS:
        md = (ROOT / "content" / ("%s.%s.md" % (name, lang))).read_text(encoding="utf-8")
        blocks.append('<article class="prose" lang="%s" data-lang="%s">\n%s\n</article>' % (lang, lang, render(md, lang)))
    tools = ('<p class="tools no-print"><button type="button" class="btn btn-quiet" data-print data-i18n="print_pdf">'
             'Print or save as PDF</button></p>\n') if name in PRINTABLE else ""
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="%s">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s - Afrocipe Pilot</title>
<meta name="description" content="Help test African recipes. Share a recipe, or cook one and send a report.">
<link rel="icon" href="assets/AFROCIPE_Glyph_Master_RGB.svg" type="image/svg+xml">
<link rel="stylesheet" href="css/style.css">
</head>
<body>
%s
<main id="main" class="wrap">
%s%s
</main>
%s
</body>
</html>
""" % (CSP, PAGES[name], HEADER, tools, "\n".join(blocks), FOOTER)


def main():
    check, stale = "--check" in sys.argv[1:], []
    for name in PAGES:
        target, text = ROOT / "site" / (name + ".html"), page(name)
        if check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                stale.append(target.name)
        else:
            target.write_text(text, encoding="utf-8")
    if stale:
        raise SystemExit("make_pages: out of date, run python3 make_pages.py: %s" % ", ".join(stale))


if __name__ == "__main__":
    main()
