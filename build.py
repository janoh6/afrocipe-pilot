#!/usr/bin/env python3
"""Afrocipe pilot site builder (Python 3 standard library only).

Reads the Tally forms through the Tally REST API (or fixtures), and writes
  site/data/recipes.json        public: an allow-list of recipe fields
  site/data/dashboard.enc.json  private: totals, encrypted with a passphrase

Logging prints counts and field names only. It never prints an answer.
A missing expected question stops the build with a non-zero exit and writes nothing.

Usage:
  TALLY_API_KEY=... DASHBOARD_PASSPHRASE=... python3 build.py
  python3 build.py --fixtures tests/fixtures
  python3 build.py --empty            # empty recipe list, no dashboard (first deploy)
  TALLY_API_KEY=... python3 build.py discover
  TALLY_API_KEY=... python3 build.py shapes   # answer structure of each form's first submission, no values
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TALLY_BASE = "https://api.tally.so"
TALLY_VERSION = "2025-02-01"
PAGE_LIMIT = 500
MAX_PAGES = 200
MAX_TEXT = 10000

# --- the allow-list ---------------------------------------------------------
# Logical fields read from a submission form. Nothing else is ever read:
# not the name, the email, the agreement, the licence choice, the provenance
# page or the uploads. Config may map only these (plus "consent", which is
# read to decide, and never output).
TEXT_FIELDS = (
    "dish_name", "other_names", "pilot_dish", "makes", "ingredients",
    "method", "signs", "total_time", "equipment", "finished_dish",
)
CONSENT_FIELD = "consent"
SUBMISSION_FIELDS = (CONSENT_FIELD,) + TEXT_FIELDS
# Fields of a public record, in output order.
PUBLIC_FIELDS = ("id", "code", "lang", "submitted", "tests") + TEXT_FIELDS
# Report-form fields: the recipe link and the outcome answers (counted, never published).
REPORT_FIELDS = ("recipe", "dish", "version", "contribution", "finished", "overall")
# Option texts / positions each report question needs in config.json (see the config _comment).
REPORT_OPTION_KEYS = {"contribution": ("yes", "no"), "finished": ("finished_index", "stopped_index"),
                      "overall": ("made", "not_made", "cannot_tell")}
REPORT_REQUIRED = ("recipe", "contribution", "finished", "overall")
LANGS = ("en", "fr", "sw")
OUTCOMES = ("made", "not", "cannot_tell", "stopped", "unrecognised")

OPTION_TYPES = ("MULTIPLE_CHOICE_OPTION", "DROPDOWN_OPTION", "MULTI_SELECT_OPTION", "RANKING_OPTION")


class BuildError(Exception):
    """A fatal, loggable problem. The message never contains an answer."""


class AmbiguousTitle(Exception):
    """A title fallback matched more than one question."""


def log(msg):
    print("build: " + msg, flush=True)


# --- masking ----------------------------------------------------------------
MASK = "[removed]"
# The part before the @ is at most 64 characters (the RFC limit). An unbounded run was rescanned from every position
# of a long word, which was quadratic: 100 kB of letters took over a minute.
_EMAIL = re.compile(r"[\w.+'-]{1,64}@[\w-]+(?:\.[\w-]+)+")
# "name[at]gmail.com", "name (at) gmail.com": the usual way to dodge an email filter.
_EMAIL_AT = re.compile(r"(?i)[\w.+'-]{1,64}\s?[\[(]\s?at\s?[\])]\s?[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)\S+")
# A bare domain is a standalone token that ends in a known, lowercase TLD. A missing space after a full stop
# ("cuire.De temps", "it.So good") puts a capital letter after the dot, so a capital never matches.
_TLDS = ("com|org|net|io|co|me|app|fr|uk|tv|ly|cm|ke|tz|ng|gh|sn|ci|za|info|biz|site|xyz|dev|so|link|be|ca|de")
_BARE_DOMAIN = re.compile(r"(?<![\w@./-])(?:[A-Za-z0-9-]+\.)+(?:%s)(?![\w-])(?:/\S*)?" % _TLDS)
_HANDLE = re.compile(r"(?<![\w@.])@\w{3,}")
# A phone number: an optional +, then digit groups joined by single spaces, dots, dashes or brackets.
_DATE = r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
_YEARS = r"(?:19|20)\d\d\s?[-\u2013]\s?(?:19|20)\d\d"
# Two digit groups are never adjacent without a separator or a bracket between them: "(?:\d+)(?:\d+)*" splits a
# digit run in exponentially many ways, and 40 digits followed by a letter hung the build.
_NUMBER = r"\+?(?:\(\d+\)|\d+)(?:[ .\-](?:\(\d+\)|\d+)|\(\d+\)|(?<=\))\d+)*"
_NUMBER_SCAN = re.compile(r"(?<![\w.])(?:(?P<skip>%s|%s)|(?P<num>%s))(?![\w])" % (_DATE, _YEARS, _NUMBER))
_UNIT_AFTER = re.compile(r"(?i)\s?(?:fcfa|cfa|xof|xaf|ksh|kes|usd|eur|ghs|ngn|tzs|ugx|kg|mg|g|ml|cl|dl|l)(?![a-z])")
_UNIT_BEFORE = re.compile(r"(?i)(?:fcfa|cfa|xof|xaf|ksh|kes|usd|eur|ghs|ngn|tzs|ugx|[$\u20ac\u00a3\u20a6])\s?$")


def _mask_numbers(text):
    def one(m):
        if m.group("skip") or sum(ch.isdigit() for ch in m.group(0)) < 9:
            return m.group(0)
        # The look-back is 8 characters (the longest unit and a space): searching the whole prefix was quadratic.
        if _UNIT_AFTER.match(text, m.end()) or _UNIT_BEFORE.search(text, max(0, m.start() - 8), m.start()):
            return m.group(0)     # an amount, not a phone number
        return MASK
    return _NUMBER_SCAN.sub(one, text)


def mask(text):
    """Best-effort removal of emails, web addresses, @handles and phone numbers. Recipe text is left alone."""
    text = _EMAIL.sub(MASK, text)
    text = _EMAIL_AT.sub(MASK, text)
    text = _URL.sub(MASK, text)
    text = _BARE_DOMAIN.sub(MASK, text)
    text = _HANDLE.sub(MASK, text)
    return _mask_numbers(text)


def clean_text(value):
    value = str(value).replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.split("\n")).strip()
    return mask(value)[:MAX_TEXT]


# --- Tally access -----------------------------------------------------------
class TallyApi:
    """GET JSON from the Tally REST API. Tests replace `get`."""

    def __init__(self, key):
        self.key = key

    def get(self, path, params=None):
        url = TALLY_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self.key,
            "tally-version": TALLY_VERSION,
            "Accept": "application/json",
            "User-Agent": "afrocipe-pilot-build",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise BuildError("Tally API returned HTTP %d for %s" % (e.code, path)) from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise BuildError("Tally API request failed for %s (%s)" % (path, type(e).__name__)) from None


class FixtureApi:
    """Reads form_<id>.json and submissions_<id>.json from a directory."""

    def __init__(self, directory):
        self.dir = Path(directory)

    def get(self, path, params=None):
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "forms":
            name = "form_%s.json" % parts[1]
        elif len(parts) == 3 and parts[2] == "submissions":
            name = "submissions_%s.json" % parts[1]
        else:
            raise BuildError("fixture path not supported: " + path)
        f = self.dir / name
        if not f.exists():
            raise BuildError("fixture file missing: " + name)
        data = json.loads(f.read_text(encoding="utf-8"))
        if name.startswith("submissions_"):
            page, limit = int((params or {}).get("page", 1)), int((params or {}).get("limit", PAGE_LIMIT))
            subs = data.get("submissions", [])
            chunk = subs[(page - 1) * limit: page * limit]
            data = dict(data, submissions=chunk, page=page, limit=limit, hasMore=page * limit < len(subs))
        return data


def derive_questions(blocks):
    """Questions from GET /forms/{id} blocks, when the submissions response has none."""
    out, title, seen = [], None, set()
    for b in blocks or []:
        t, p = b.get("type"), b.get("payload") or {}
        if t == "TITLE":
            title = "".join(part[0] for part in p.get("safeHTMLSchema", [])
                            if isinstance(part, list) and part and isinstance(part[0], str))
        elif t == "HIDDEN_FIELDS":
            hf = [h for h in p.get("hiddenFields", []) if h.get("uuid")]
            gid = b.get("groupUuid") or b.get("uuid") or "hidden"
            out.append({"id": gid, "type": "HIDDEN_FIELDS", "title": None,
                        "fields": [{"uuid": h.get("uuid"), "type": "HIDDEN_FIELD", "blockGroupUuid": gid,
                                    "title": h.get("name")} for h in hf]})
        elif t in ("INPUT_TEXT", "TEXTAREA", "INPUT_EMAIL", "INPUT_NUMBER", "INPUT_LINK", "INPUT_PHONE_NUMBER",
                   "INPUT_DATE", "INPUT_TIME", "LINEAR_SCALE", "RATING", "FILE_UPLOAD", "CHECKBOX",
                   "MULTIPLE_CHOICE_OPTION", "DROPDOWN_OPTION", "MULTI_SELECT_OPTION", "RANKING_OPTION"):
            gid = b.get("groupUuid")
            if gid in seen:
                continue
            seen.add(gid)
            out.append({"id": gid, "type": t, "title": title or "", "fields": []})
    return out


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


class FormData:
    """One form: its questions, its option labels and its completed submissions."""

    def __init__(self, key, form_id, questions, form_blocks, submissions):
        self.key = key
        self.form_id = form_id
        self.questions = [q for q in questions if not q.get("isDeleted")]
        self.submissions = [s for s in submissions if s.get("isCompleted", True)]
        self.opt_text = {}      # option uuid -> text
        self.opt_index = {}     # option uuid -> position in its question
        for q in self.questions:
            n = 0
            for f in q.get("fields") or []:
                if f.get("type") in OPTION_TYPES:
                    self.opt_text[f.get("uuid")] = f.get("title", "")
                    self.opt_index[f.get("uuid")] = n
                    n += 1
        groups = {}
        for b in form_blocks or []:
            if b.get("type") in OPTION_TYPES:
                groups.setdefault(b.get("groupUuid"), []).append(b)
        for blocks in groups.values():
            for n, b in enumerate(blocks):
                u = b.get("uuid")
                p = b.get("payload") or {}
                self.opt_text.setdefault(u, p.get("text", ""))
                self.opt_index.setdefault(u, p.get("index", n))
        self._alias = {}
        for q in self.questions:
            for a in [q.get("id")] + [x for f in q.get("fields") or [] for x in (f.get("uuid"), f.get("blockGroupUuid"))]:
                if a:
                    self._alias.setdefault(a, set()).add(q.get("id"))

    def find(self, cfg):
        """Question matching a config entry: by id when one is set, else exact title.

        An id matches a question's own id, or the uuid / blockGroupUuid of one of its fields (the
        live API gives questions short ids, and the form-builder uuid sits on the field). A field
        of the HIDDEN_FIELDS question never matches an id here: hidden fields have find_hidden.
        An id that matches more than one question is ambiguous and fatal.

        A title fallback must match exactly one question. Two questions sharing a title (or a
        hidden field named like a question) would otherwise let the first one win, and that
        could be a personal question. Raises AmbiguousTitle, which the caller makes fatal."""
        qid, title = cfg.get("id"), norm(cfg.get("title"))
        if qid:
            hits = [q for q in self.questions if q.get("id") == qid or (
                q.get("type") != "HIDDEN_FIELDS" and any(qid in (f.get("uuid"), f.get("blockGroupUuid"))
                                                         for f in q.get("fields") or []))]
            if len(hits) > 1:
                raise AmbiguousTitle()
            if hits:
                return hits[0]
        if title:
            hits = [q for q in self.questions if norm(q.get("title")) == title]
            if len(hits) > 1:
                raise AmbiguousTitle()
            if hits:
                return hits[0]
        return None

    def find_hidden(self, cfg):
        """(question, field) of a hidden field: by field uuid first, then by exact unique field name.
        Only fields of HIDDEN_FIELDS questions are considered. None when absent."""
        fid, title = cfg.get("id"), norm(cfg.get("title"))
        pairs = [(q, f) for q in self.questions if q.get("type") == "HIDDEN_FIELDS"
                 for f in q.get("fields") or []]
        if fid:
            hits = [(q, f) for q, f in pairs if f.get("uuid") == fid]
            if len(hits) > 1:
                raise AmbiguousTitle()
            if hits:
                return hits[0]
        if title:
            hits = [(q, f) for q, f in pairs if norm(f.get("title")) == title]
            if len(hits) > 1:
                raise AmbiguousTitle()
            if hits:
                return hits[0]
        return None

    def hidden_value(self, sub, hq, field):
        """The value of one hidden field in a submission, or None when it is absent or unusable.
        The answer shape is unverified, so three shapes are tried in order:
          (a) a response for the HIDDEN_FIELDS question, answer a dict keyed by field uuid or name;
          (b) a response whose questionId is the field uuid;
          (c) a response for the HIDDEN_FIELDS question, answer a list of dicts naming the field.
        Only a string of 1-64 characters from [A-Za-z0-9_-] is used. Never logged."""
        fu, fname = field.get("uuid"), norm(field.get("title"))
        keys = [k for k in (fu, fname) if k]
        responses = [r for r in sub.get("responses") or [] if isinstance(r, dict)]
        found, got = False, None
        for r in responses:
            if r.get("questionId") == hq.get("id") and isinstance(r.get("answer"), dict):
                for k in keys:
                    if k in r["answer"]:
                        found, got = True, r["answer"][k]
                        break
            if found:
                break
        if not found:
            for r in responses:
                if fu and r.get("questionId") == fu:
                    found, got = True, r.get("answer")
                    break
        if not found:
            for r in responses:
                if r.get("questionId") == hq.get("id") and isinstance(r.get("answer"), list):
                    for item in r["answer"]:
                        if isinstance(item, dict) and any(item.get(n) in keys for n in
                                                          ("uuid", "id", "title", "name", "key") if item.get(n)):
                            found = True
                            got = item.get("value", item.get("answer"))
                            break
                if found:
                    break
        if isinstance(got, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", got):
            return got
        return None

    def raw_answers(self, sub, question):
        ids = {question.get("id")} | {x for f in question.get("fields") or []
                                     for x in (f.get("uuid"),) if x}
        found = []
        for r in sub.get("responses") or []:
            qid = r.get("questionId")
            if qid in ids or question.get("id") in self._alias.get(qid, ()):
                found.append(r.get("answer"))
        if not found:
            return None
        return found[0] if len(found) == 1 else found

    def options(self, raw):
        """[(index, text)] for the option uuids (or option texts) in an answer."""
        items = raw if isinstance(raw, list) else [raw]
        out = []
        for it in items:
            if isinstance(it, str) and it in self.opt_text:
                out.append((self.opt_index.get(it), self.opt_text[it]))
            elif isinstance(it, str) and it.strip():
                out.append((None, it))
        return out

    def text(self, sub, question):
        raw = self.raw_answers(sub, question)
        if raw is None or isinstance(raw, (bool, dict)):
            return ""
        if isinstance(raw, (int, float)):
            return str(raw)
        if isinstance(raw, str):
            return self.opt_text.get(raw, raw)
        if isinstance(raw, list):
            return ", ".join(t for _, t in self.options(raw) if isinstance(t, str))
        return ""

    def ticked(self, sub, question, option, option_text=None):
        """Consent is an allow-list. Only these answers mean yes:
          - the boolean True, on a question with at most one box, or keyed by the consent option's uuid,
          - the consent option's uuid (alone, or in a list),
          - a list holding the option's exact configured text.
        Anything else (another option, "true", 1, a dict, [], None) is not consent. On a question with several
        boxes, a bare True keyed by another box's uuid is that other box, not consent."""
        raw = self.raw_answers(sub, question)
        if raw is True:
            if len([f for f in question.get("fields") or [] if f.get("uuid")]) <= 1:
                return True
            return bool(option) and any(r.get("questionId") == option and r.get("answer") is True
                                        for r in sub.get("responses") or [])
        if not option:
            return False
        if isinstance(raw, str):
            return raw == option
        if isinstance(raw, list):
            return any(isinstance(v, str) and (v == option or (option_text and v == option_text)) for v in raw)
        return False


def fetch_form(api, key, form_id, need_blocks=True):
    """Questions, form blocks and all completed submissions of one form."""
    blocks = []
    if need_blocks:
        form = api.get("/forms/" + form_id)
        blocks = form.get("blocks") or []
    questions, subs, page = None, [], 1
    while True:
        data = api.get("/forms/%s/submissions" % form_id,
                       {"page": page, "limit": PAGE_LIMIT, "filter": "completed"})
        if questions is None:
            questions = data.get("questions") or []
        subs.extend(data.get("submissions") or [])
        if not data.get("hasMore"):
            break
        page += 1
        if page > MAX_PAGES:
            raise BuildError("too many pages for form " + key)
    if not questions:
        questions = derive_questions(blocks)
    return FormData(key, form_id, questions, blocks, subs)


def count_submissions(api, form_id):
    """Completed submissions of a form, without keeping any answer."""
    data = api.get("/forms/%s/submissions" % form_id, {"page": 1, "limit": 1, "filter": "completed"})
    totals = data.get("totalNumberOfSubmissionsPerFilter") or {}
    if isinstance(totals.get("completed"), int):
        return totals["completed"]
    n, page = 0, 1
    while True:
        data = api.get("/forms/%s/submissions" % form_id,
                       {"page": page, "limit": PAGE_LIMIT, "filter": "completed"})
        n += sum(1 for s in data.get("submissions") or [] if s.get("isCompleted", True))
        if not data.get("hasMore") or page >= MAX_PAGES:
            return n
        page += 1


# --- config -----------------------------------------------------------------
def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for lang in LANGS:
        sf = cfg["submission_forms"][lang]["fields"]
        extra = sorted(set(sf) - set(SUBMISSION_FIELDS))
        if extra:
            raise BuildError("field not in the allow-list: " + ", ".join(extra))
        missing = sorted(set(SUBMISSION_FIELDS) - set(sf))
        if missing:
            raise BuildError("config lacks field(s): " + ", ".join(missing))
        if not (sf[CONSENT_FIELD].get("option") or "").strip():
            raise BuildError("consent option uuid is not configured (%s)" % lang)
        rf = cfg["report_forms"][lang]["fields"]
        extra = sorted(set(rf) - set(REPORT_FIELDS))
        if extra:
            raise BuildError("report field not recognised: " + ", ".join(extra))
        need = [(f, k) for f, ks in REPORT_OPTION_KEYS.items() for k in ks if f in rf and rf[f].get(k) in (None, "")]
        if need:
            raise BuildError("report option not configured (%s): %s" % (lang, ", ".join("%s.%s" % x for x in need)))
    return cfg


def load_hidden(path):
    ids = set()
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ids.add(line)
    return ids


HIDDEN_NAMES = ("recipe", "dish", "version")


def resolve_fields(form, fields_cfg, label, hidden=()):
    """Map logical field -> question (or, for names in `hidden`, (HIDDEN_FIELDS question, field)).
    A missing expected question or required hidden field is fatal."""
    out, missing, ambiguous = {}, [], []
    for name, c in fields_cfg.items():
        try:
            q = form.find_hidden(c) if name in hidden else form.find(c)
        except AmbiguousTitle:
            ambiguous.append(name)
            continue
        if q is None:
            if not c.get("optional"):
                missing.append(name)
        else:
            out[name] = q
    if ambiguous:
        raise BuildError("title matches more than one question in %s form: %s" % (label, ", ".join(ambiguous)))
    if missing:
        raise BuildError("missing expected question(s) in %s form: %s" % (label, ", ".join(missing)))
    return out


# --- the build --------------------------------------------------------------
UNCLEAR = object()      # an answer is there, but it is not one single known option


def choice(form, sub, question):
    """None when unanswered, UNCLEAR when it is not exactly one option, else (index, text).
    An option uuid is turned into its configured option text and position first."""
    raw = form.raw_answers(sub, question)
    if raw is None or raw == [] or (isinstance(raw, str) and not raw.strip()):
        return None
    opts = form.options(raw)
    return opts[0] if len(opts) == 1 else UNCLEAR


def is_text(got, wanted):
    return isinstance(got, tuple) and got[1] is not None and norm(got[1]) == norm(wanted)


def contribution_counts(got, cfg_field):
    """True when the report counts. Only a clear "No" counts: Yes, a missing answer and
    anything unrecognised are set aside (an unclear report is not counted)."""
    return is_text(got, cfg_field["no"])


def count_reports(report_forms, cfg):
    """Counts of reports per recipe id, outcome counts, and reports per language."""
    counts, outcomes, per_lang, excluded = {}, {}, {l: 0 for l in LANGS}, 0
    for lang in LANGS:
        form = report_forms[lang]
        fcfg = cfg["report_forms"][lang]["fields"]
        q = resolve_fields(form, fcfg, "report " + lang, hidden=HIDDEN_NAMES)
        for sub in form.submissions:
            if not contribution_counts(choice(form, sub, q["contribution"]), fcfg["contribution"]):
                excluded += 1
                continue
            per_lang[lang] += 1
            rid = form.hidden_value(sub, *q["recipe"])
            if not rid:
                continue
            counts[rid] = counts.get(rid, 0) + 1
            o = outcomes.setdefault(rid, {k: 0 for k in OUTCOMES})
            fin, ov = choice(form, sub, q["finished"]), choice(form, sub, q["overall"])
            if ov is None:                  # abandoned before the overall answer
                o["stopped"] += 1
            elif fin is UNCLEAR or (fin is not None and fin[0] not in (fcfg["finished"]["finished_index"],
                                                                      fcfg["finished"]["stopped_index"])):
                o["unrecognised"] += 1
            elif fin is not None and fin[0] == fcfg["finished"]["stopped_index"]:
                o["stopped"] += 1
            elif is_text(ov, fcfg["overall"]["made"]):
                o["made"] += 1
            elif is_text(ov, fcfg["overall"]["not_made"]):
                o["not"] += 1
            elif is_text(ov, fcfg["overall"]["cannot_tell"]):
                o["cannot_tell"] += 1
            else:
                o["unrecognised"] += 1
    return counts, outcomes, per_lang, excluded


def build_recipes(sub_forms, cfg, hidden, counts):
    recipes, n_hidden, n_awaiting = [], 0, 0
    # Hide by id or by the visible code (R-XXXXXX), ignoring case: a near miss must hide, not publish.
    hide_keys = {h.strip().casefold() for h in hidden}
    for lang in LANGS:
        form = sub_forms[lang]
        q = resolve_fields(form, cfg["submission_forms"][lang]["fields"], "submission " + lang)
        for sub in form.submissions:
            sid = str(sub.get("id") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
                continue
            ccfg = cfg["submission_forms"][lang]["fields"][CONSENT_FIELD]
            if not form.ticked(sub, q[CONSENT_FIELD], ccfg.get("option"), ccfg.get("option_text")):
                n_awaiting += 1
                continue
            if sid.casefold() in hide_keys or ("R-" + sid[:6]).casefold() in hide_keys:
                n_hidden += 1
                continue
            values = {
                "id": sid,
                "code": "R-" + sid[:6].upper(),
                "lang": lang,
                "submitted": str(sub.get("submittedAt") or "")[:10] if re.match(r"\d{4}-\d{2}-\d{2}", str(sub.get("submittedAt") or "")) else "",
                "tests": counts.get(sid, 0),
            }
            for name in TEXT_FIELDS:
                values[name] = clean_text(form.text(sub, q[name])) if name in q else ""
            recipes.append({k: values[k] for k in PUBLIC_FIELDS})
    recipes.sort(key=lambda r: (r["submitted"], r["id"]), reverse=True)
    return recipes, n_hidden, n_awaiting


def dashboard_plain(recipes, n_hidden, n_awaiting, signups, per_lang, outcomes, target, now):
    return {
        "generated": now,
        "target_tests": target,
        "totals": {
            "recipes_published": len(recipes),
            "hidden": n_hidden,
            "awaiting_consent": n_awaiting,
            "signups": signups,
            "reports": per_lang,
        },
        "recipes": [{
            "code": r["code"],
            "dish_name": r["dish_name"],
            "lang": r["lang"],
            "tests": r["tests"],
            "outcomes": outcomes.get(r["id"], {k: 0 for k in OUTCOMES}),
            "reached_target": r["tests"] >= target,
        } for r in recipes],
    }


def encrypt_dashboard(plain, passphrase):
    """AES-256-GCM, key from PBKDF2-HMAC-SHA256 (done by node:crypto)."""
    env = dict(os.environ, DASHBOARD_PASSPHRASE=passphrase)
    try:
        res = subprocess.run(["node", str(ROOT / "encrypt.mjs")], input=json.dumps(plain).encode("utf-8"),
                             env=env, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise BuildError("could not run node to encrypt the dashboard") from None
    if res.returncode != 0:
        raise BuildError("dashboard encryption failed")
    return json.loads(res.stdout.decode("utf-8"))


def write_atomic(files):
    """Write all files or none: stage every file, then rename."""
    staged = []
    try:
        for path, text in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            staged.append((Path(tmp), path))
    except Exception:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise
    for tmp, path in staged:
        os.replace(tmp, path)


def run_build(api, cfg, hidden, out_dir, passphrase, now):
    sub_forms = {l: fetch_form(api, "submission " + l, cfg["submission_forms"][l]["id"]) for l in LANGS}
    rep_forms = {l: fetch_form(api, "report " + l, cfg["report_forms"][l]["id"]) for l in LANGS}
    signups = {l: count_submissions(api, cfg["signup_forms"][l]) for l in LANGS}
    counts, outcomes, per_lang, excluded = count_reports(rep_forms, cfg)
    recipes, n_hidden, n_awaiting = build_recipes(sub_forms, cfg, hidden, counts)
    target = int(cfg.get("target_tests", 5))
    plain = dashboard_plain(recipes, n_hidden, n_awaiting, signups, per_lang, outcomes, target, now)
    enc = encrypt_dashboard(plain, passphrase)
    write_atomic([
        (out_dir / "recipes.json", json.dumps(recipes, ensure_ascii=False, indent=1) + "\n"),
        (out_dir / "dashboard.enc.json", json.dumps(enc) + "\n"),
    ])
    # The repository is public, so its log carries no private total (hidden, awaiting consent, reports, sign-ups).
    print("build ok: %d recipes published" % len(recipes), flush=True)
    return recipes


def run_discover(api, cfg):
    """Print each question's id, type and title. Never an answer."""
    forms = []
    for group in ("submission_forms", "report_forms"):
        for l in LANGS:
            forms.append((group[:-6] + " " + l, cfg[group][l]["id"]))
    for l in LANGS:
        forms.append(("signup " + l, cfg["signup_forms"][l]))
    for label, fid in forms:
        questions = None
        try:
            data = api.get("/forms/%s/submissions" % fid, {"page": 1, "limit": 1, "filter": "all"})
            questions = data.get("questions") or None
        except BuildError:
            pass
        if not questions:
            questions = derive_questions(api.get("/forms/" + fid).get("blocks"))
        print("== %s (%s): %d questions" % (label, fid, len(questions)))
        for q in questions:
            if q.get("isDeleted"):
                continue
            print("%s\t%s\t%s" % (q.get("id"), q.get("type"), norm(q.get("title"))))


UUID_KEY = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
NAME_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")


def shape(value, depth=0):
    """The structure of an answer. Every string, number and boolean is replaced by its type name; a dict
    shows its keys (only when they look like a uuid or a plain identifier), a list its length.
    No value can reach the output."""
    if isinstance(value, dict):
        if depth >= 6:
            return "dict"
        items = []
        for k, v in list(value.items())[:50]:
            ks = k if isinstance(k, str) and (UUID_KEY.fullmatch(k) or NAME_KEY.fullmatch(k)) else "<key>"
            items.append("%s: %s" % (ks, shape(v, depth + 1)))
        return "{" + ", ".join(items) + "}"
    if isinstance(value, list):
        if depth >= 6:
            return "list[%d]" % len(value)
        return "list[%d](%s)" % (len(value), ", ".join(shape(v, depth + 1) for v in value[:5]))
    return type(value).__name__


def run_shapes(api, cfg):
    """For each form, the first completed submission only: per response, the question type and answer shape."""
    forms = []
    for group in ("submission_forms", "report_forms"):
        for l in LANGS:
            forms.append((group[:-6] + " " + l, cfg[group][l]["id"]))
    for l in LANGS:
        forms.append(("signup " + l, cfg["signup_forms"][l]))
    for label, fid in forms:
        data = api.get("/forms/%s/submissions" % fid, {"page": 1, "limit": 1, "filter": "completed"})
        types = {q.get("id"): q.get("type") for q in data.get("questions") or []}
        subs = [s for s in data.get("submissions") or [] if s.get("isCompleted", True)]
        print("== %s (%s)" % (label, fid))
        if not subs:
            print("no completed submission")
            continue
        for r in subs[0].get("responses") or []:
            qid = r.get("questionId")
            qt = types.get(qid)
            qs = qid if qid in types and isinstance(qid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", qid) else "<unknown>"
            print("%s\t%s\t%s" % (qs, qt if isinstance(qt, str) and NAME_KEY.fullmatch(qt) else "<unknown>",
                                  shape(r.get("answer"))))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the Afrocipe pilot site data.")
    ap.add_argument("command", nargs="?", default="build", choices=["build", "discover", "shapes"])
    ap.add_argument("--fixtures", metavar="DIR", help="read JSON files from DIR instead of the network")
    ap.add_argument("--empty", action="store_true", help="write an empty recipe list and no dashboard")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--hidden", default=None, help="hide list (default hidden.txt; with --fixtures, DIR/hidden.txt if present)")
    ap.add_argument("--out", default=str(ROOT / "site" / "data"))
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    try:
        if args.empty and args.command == "build":
            write_atomic([(out_dir / "recipes.json", "[]\n")])
            log("empty build: 0 recipes, no dashboard")
            return 0
        cfg = load_config(args.config)
        if args.fixtures:
            api = FixtureApi(args.fixtures)
            passphrase = os.environ.get("DASHBOARD_PASSPHRASE") or "pilot-fixture"
            if not os.environ.get("DASHBOARD_PASSPHRASE"):
                log("fixtures mode: using the fixture passphrase")
        else:
            key = os.environ.get("TALLY_API_KEY", "")
            if not key:
                raise BuildError("TALLY_API_KEY is not set")
            api = TallyApi(key)
            passphrase = os.environ.get("DASHBOARD_PASSPHRASE", "")
        if args.command == "discover":
            run_discover(api, cfg)
            return 0
        if args.command == "shapes":
            run_shapes(api, cfg)
            return 0
        if not passphrase:
            raise BuildError("DASHBOARD_PASSPHRASE is not set")
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hidden_path = args.hidden or (str(Path(args.fixtures) / "hidden.txt") if args.fixtures and (Path(args.fixtures) / "hidden.txt").exists() else str(ROOT / "hidden.txt"))
        run_build(api, cfg, load_hidden(hidden_path), out_dir, passphrase, now)
        return 0
    except BuildError as e:
        print("build: ERROR: %s" % e, file=sys.stderr)
        return 1
    except Exception as e:  # never print a message that could hold an answer
        print("build: ERROR: unexpected %s" % type(e).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
