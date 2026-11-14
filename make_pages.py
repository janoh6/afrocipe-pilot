#!/usr/bin/env python3
"""Write every page of the site, so that all of them share one head, header and footer.

Content pages (guides, about) come from content/<page>.<lang>.md; the other four take their <main> from
content/pages/<page>.html. A content page holds the three languages; css shows the one that matches <html lang>. The text is ours, not form
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
# The buttons in a page's title band: what the reader of that page came to do.
ACTIONS = {
    "submit": '<a class="btn" data-form="submit" href="https://tally.so/r/%(form)s" data-i18n="btn_share">Share a recipe</a>',
    "test": '<a class="btn" href="recipes.html" data-i18n="btn_test">Test a recipe</a>',
    "signup": '<a class="btn btn-quiet" data-form="signup" href="https://tally.so/r/%(form)s" data-i18n="link_join">Join as a tester</a>',
}
PAGE_ACTIONS = {"guide-testers": ("test", "signup"), "guide-writers": ("submit",)}
CSP = ("default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "base-uri 'none'; form-action 'none'")

HEADER = """<a class="skip" href="#main" data-i18n="skip">Skip to content</a>
<header class="site-header no-print">
  <div class="wrap bar">
    <a class="brand" href="index.html">
      <img class="logo" src="assets/AFROCIPE_Glyph_Master_RGB.svg" alt="" width="1000" height="593">
      <span class="brand-name">Afrocipe</span>
      <span class="brand-tag" data-i18n="brand_tag">Pilot</span>
    </a>
    <div id="langswitch" class="langs" role="group"></div>
    <button type="button" id="menu-toggle" class="menu-toggle" aria-controls="site-nav" aria-expanded="false"><span class="menu-icon" aria-hidden="true"></span><span class="menu-label" data-i18n="menu">Menu</span></button>
    <nav id="site-nav" class="nav" aria-label="Main">
      <ul>
        <li><a href="index.html" data-i18n="nav_home">Home</a></li>
        <li><a href="recipes.html" data-i18n="nav_recipes">Recipes</a></li>
        <li><a href="guide-testers.html" data-i18n="nav_testers">For testers</a></li>
        <li><a href="guide-writers.html" data-i18n="nav_writers">For writers</a></li>
        <li><a href="about.html" data-i18n="nav_about">About</a></li>
      </ul>
    </nav>
  </div>
</header>"""

FOOTER = """<footer class="site-footer no-print">
  <div class="footer-cta">
    <div class="wrap footer-cta-row">
      <p class="footer-cta-title" data-i18n="cta_title">Take part in the pilot</p>
      <p class="footer-cta-actions">
        <a class="btn btn-gold" data-form="submit" href="https://tally.so/r/RWdKp4" data-i18n="btn_share">Share a recipe</a>
        <a class="btn btn-line" href="recipes.html" data-i18n="btn_test">Test a recipe</a>
      </p>
    </div>
  </div>
  <div class="wrap footer-grid">
    <div>
      <p class="footer-name">Afrocipe</p>
      <p data-i18n="footer_tagline">Help us test African recipes.</p>
    </div>
    <nav aria-label="Footer">
      <p class="footer-h" data-i18n="footer_explore">Explore</p>
      <ul>
        <li><a href="recipes.html" data-i18n="nav_recipes">Recipes</a></li>
        <li><a href="guide-testers.html" data-i18n="guide_testers">Guide for testers</a></li>
        <li><a href="guide-writers.html" data-i18n="guide_writers">Guide for recipe writers</a></li>
        <li><a href="about.html" data-i18n="home_about_link">More about Afrocipe</a></li>
      </ul>
    </nav>
    <div>
      <p class="footer-h" data-i18n="footer_contact">Questions? Write to</p>
      <p><a id="contact" href="mailto:afrocipe12@gmail.com">afrocipe12@gmail.com</a></p>
    </div>
  </div>
  <div class="wrap">
    <div class="footer-base">
      <p><small data-i18n="footer_rights">&copy; 2026 Afrocipe. Recipes belong to their writers.</small></p>
      <p><small data-i18n="footer_note">A pilot by Afrocipe.</small></p>
    </div>
  </div>
</footer>"""

# The pages whose <main> is written by hand: file name -> (title, title key, extra head lines, page scripts).
SHELLS = {
    "index": ("Afrocipe Pilot", "", "", ()),
    "recipes": ("Recipes - Afrocipe Pilot", "recipes_title", "", ("recipes.js",)),
    "recipe": ("Recipe - Afrocipe Pilot", "", "", ("recipe.js",)),
    "dashboard": ("Dashboard - Afrocipe Pilot", "dash_title", '<meta name="robots" content="noindex, nofollow">\n', ("dashboard.js",)),
}


def document(title, main, title_key="", head_extra="", scripts=()):
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="%s">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<meta name="description" content="Help test African recipes. Share a recipe, or cook one and send a report.">
%s<meta name="theme-color" content="#0B3D2E">
<link rel="icon" href="assets/AFROCIPE_Glyph_Master_RGB.svg" type="image/svg+xml">
<link rel="stylesheet" href="css/style.css">
<script src="js/early.js"></script>
</head>
<body%s>
%s
<main id="main">
%s
</main>
%s
<script src="js/config.js"></script>
<script src="js/i18n.js"></script>
<script src="js/common.js"></script>
%s</body>
</html>
""" % (CSP, title, head_extra, ' data-title="%s"' % title_key if title_key else "", HEADER, main.strip("\n"), FOOTER,
       "".join('<script src="js/%s"></script>\n' % s for s in scripts))


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
    printer = ('<button type="button" class="btn btn-quiet" data-print data-i18n="print_pdf">Print or save as PDF</button>'
               if name in PRINTABLE else "")
    for lang in LANGS:
        md = (ROOT / "content" / ("%s.%s.md" % (name, lang))).read_text(encoding="utf-8")
        head, _, rest = md.partition("\n## ")          # the title and any introduction come before the first section
        intro = render(head, lang).replace("<p>", '<p class="lead">')
        buttons = [ACTIONS[kind] % ({"form": FORMS[kind][lang]} if kind in FORMS else {}) for kind in PAGE_ACTIONS.get(name, ())]
        tools = ('<p class="tools no-print">%s</p>\n' % "\n".join(buttons + [printer] if printer else buttons)) if buttons or printer else ""
        blocks.append('<article lang="%s" data-lang="%s">\n<header class="page-head"><div class="wrap">\n%s\n%s</div></header>\n'
                      '<div class="wrap section prose">\n%s\n</div>\n</article>'
                      % (lang, lang, intro, tools, render("## " + rest, lang)))
    return document("%s - Afrocipe Pilot" % PAGES[name], "\n".join(blocks))


def shell(name):
    title, key, extra, scripts = SHELLS[name]
    return document(title, (ROOT / "content" / "pages" / (name + ".html")).read_text(encoding="utf-8"), key, extra, scripts)


def main():
    check, stale = "--check" in sys.argv[1:], []
    for name in list(PAGES) + list(SHELLS):
        target, text = ROOT / "site" / (name + ".html"), (page(name) if name in PAGES else shell(name))
        if check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                stale.append(target.name)
        else:
            target.write_text(text, encoding="utf-8")
    if stale:
        raise SystemExit("make_pages: out of date, run python3 make_pages.py: %s" % ", ".join(stale))


if __name__ == "__main__":
    main()
