# Afrocipe pilot site

A static site for `pilot.afrocipe.com`. Writers send recipes through Tally forms. A scheduled build reads the forms
through the Tally API, publishes an allow-listed part of each consented recipe, counts reports per recipe and
writes a private, encrypted dashboard. The site is source material only: its recipes are not checked and its test
counts are not a score. Design note: `docs/notes/2026-11-12-pilot-site.md` in the main Afrocipe repository.

Python 3 standard library only, plus `node` (for AES-GCM) at build time. No framework, no external fonts or CDN.

## Tests

    python3 -m unittest

## Preview locally

    python3 build.py --fixtures tests/fixtures && python3 -m http.server -d site

Open http://localhost:8000. The fixture dashboard passphrase is `pilot-fixture`. Fixtures are regenerated with
`python3 tests/make_fixtures.py`. Add `?lang=fr` or `?lang=sw` to a page URL to switch language.

## Guides and the About page

The text lives in `content/<page>.<lang>.md` (`guide-testers`, `guide-writers`, `about`; `en`, `fr`, `sw`). After
editing, run `python3 make_pages.py` and commit the regenerated `site/*.html` too; the tests fail if they differ.
The mission and vision on the About page are placeholders (`!placeholder` lines) until the operator supplies them.

## Hide a recipe

Add its id (the part after `recipe.html?id=`, or the Tally submission id) or its code (`R-XXXXXX`, shown on the page;
it hides every recipe whose code matches) on its own line in `hidden.txt`, then push. Case is ignored.
`#` starts a comment. The recipe disappears at the next build. Deleting the submission in Tally does the same.

## Secrets and variable (repository settings)

- Secret `TALLY_API_KEY`: a Tally API key that can read the nine forms.
- Secret `DASHBOARD_PASSPHRASE`: the dashboard passphrase (PBKDF2-SHA256, AES-256-GCM, decrypted in the browser).
- Variable `ALLOW_EMPTY_FIRST_DEPLOY`: set to `true` only for the very first deploy, before the key exists. It lets
  the build publish an empty recipe list, and only while no `data/recipes.json` is live yet (HTTP 404). Without it a
  missing secret fails the build and nothing is deployed, so the last good site stays up. Delete the variable once
  the key is in.
- Variable `ALLOW_EMPTY_DEPLOY`: leave unset. Before each deploy the workflow fetches the live `data/recipes.json`
  and refuses to deploy a build with 0 recipes over a live list with some (`deploy_guard.py`). Set it to `true`
  for one run only if you really mean to empty the site, then delete it.

## Keepalive

On a public repository GitHub disables scheduled workflows after 60 days without repository activity. If the
site stops updating, open the Actions tab and re-enable the workflow (or push any commit). The scheduled build
is what keeps recipes and counts fresh, so check it now and then.

## Finishing the form mapping

`config.json` maps logical fields to Tally questions by question id (preferred) or exact title. With a key, run

    TALLY_API_KEY=... python3 build.py discover

It prints each question's id, type and title (never an answer). Compare with `config.json` and fix any id or title
that differs. A missing expected question fails the build and writes nothing.

## Notes

- Dashboard "reports" per language counts completed reports, not counting those where the contribution question was
  answered Yes. A completed report with no overall answer counts as "stopped".
- Logs print only "build ok" and the number of published recipes (the repository is public). Hidden,
  awaiting-consent, report and sign-up totals appear on the private dashboard only.
- Consent is an allow-list: a recipe is published only when the consent answer is `true` or carries the consent
  option uuid set in `config.json` (`consent.option`). Any other answer means not consented.
- Report answers are read by the option texts and positions in `config.json`, never by guessing. A report whose
  contribution answer is Yes, missing or unrecognised is not counted; an unrecognised finished or overall answer
  counts under "Unrecognised" on the dashboard.
- Each page carries a restrictive Content-Security-Policy `<meta>`: no inline scripts or styles.

## Where the site is served

The deploy guard reads the live recipe list from the repository variable `SITE_URL` (for example
`https://janoh6.github.io/afrocipe-pilot`), or from `site/CNAME` when the variable is not set. Set `SITE_URL`
while the custom domain's DNS is not in place, and change it when the domain goes live.

`ALLOW_EMPTY_DEPLOY=true` skips every guard check, including "could not read the live list". Set it for one run
and remove it straight away.
