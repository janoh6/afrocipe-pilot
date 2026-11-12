"""Builds Tally-shaped fixture files (form_<id>.json, submissions_<id>.json) from config.json.

The shapes follow the Tally developer docs (api.tally.so): a form has `blocks`; a submissions
page has `questions` (id, type, title, fields[]), `submissions` (id, isCompleted, submittedAt,
responses[{questionId, answer}]), `page`, `limit`, `hasMore`. Answer shapes for choice and
checkbox questions are assumptions (see README): choices are lists of option uuids; a ticked
checkbox is `true`.
"""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = ("en", "fr", "sw")

# Questions that exist on the live forms but must never reach the output.
EXTRA_SUBMISSION_QUESTIONS = [
    ("name", "INPUT_TEXT", "Your full name"),
    ("email", "INPUT_EMAIL", "Your email"),
    ("agreement", "CHECKBOX", "Your agreement"),
    ("licence", "MULTIPLE_CHOICE_OPTION", "Who can see this recipe?"),
    ("origin", "MULTIPLE_CHOICE_OPTION", "Where does this recipe come from?"),
    ("family", "INPUT_TEXT", "Which family, community or people is it from?"),
    ("taught", "INPUT_TEXT", "Who taught you this recipe? (optional)"),
    ("upload", "FILE_UPLOAD", "Photos or videos (optional)"),
]
EXTRA_REPORT_QUESTIONS = [
    ("name", "INPUT_TEXT", "Full name"),
    ("email", "INPUT_EMAIL", "Email"),
    ("city", "INPUT_TEXT", "City or town"),
    ("describe", "TEXTAREA", "Describe the result"),
]
OPTIONS = {
    "pilot_dish": ["Jollof rice", "Alloco", "Another dish"],
    "licence": ["Open", "Controlled", "Commercial"],
    "origin": ["Passed down", "Created"],
    "contribution": ["Yes", "No"],       # Yes ends the report at that question
    "finished": ["Yes, I finished it", "No, I stopped before the end"],
    "overall": ["Made it", "Did not", "Cannot tell"],
}
TYPES = {
    "consent": "CHECKBOX", "pilot_dish": "DROPDOWN_OPTION", "recipe": "HIDDEN_FIELD",
    "dish": "HIDDEN_FIELD", "version": "HIDDEN_FIELD",
    "contribution": "MULTIPLE_CHOICE_OPTION", "finished": "MULTIPLE_CHOICE_OPTION",
    "overall": "MULTIPLE_CHOICE_OPTION",
    "other_names": "TEXTAREA", "ingredients": "TEXTAREA", "method": "TEXTAREA", "signs": "TEXTAREA",
    "equipment": "TEXTAREA", "finished_dish": "TEXTAREA",
}


def load_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class Form:
    def __init__(self, form_id, defs, ids=None, options=None):
        """defs: list of (key, type, title). `ids[key]` is the form-builder uuid (kept in config.json); the
        live API gives each question a SHORT id instead, used in answers. A def of type HIDDEN_FIELD is one
        field of the form's single HIDDEN_FIELDS question (title null, uuid = ids[key]).
        hidden_shape picks how hidden answers are written: "a" dict by field uuid, "a_name" dict by field
        name, "b" one response per field keyed by the field uuid, "c" list of dicts."""
        self.id = form_id
        self.ids = ids or {}
        self.hidden_shape = "a"
        self.defs = list(defs)
        self.options = dict(OPTIONS, **(options or {}))
        self.subs = []
        self.n = 0

    def uuid(self, key):
        return self.ids.get(key) or "u-%s-%s" % (self.id, key)

    def qid(self, key):
        """The short question id the API reports (stable per key, 6 characters like Tally's)."""
        h = hashlib.sha256(("%s/%s" % (self.id, key)).encode()).hexdigest()
        return h[:6]

    def hidden_qid(self):
        return "hid" + hashlib.sha256(self.id.encode()).hexdigest()[:3]

    def opt(self, key, i):
        return "o-%s-%s-%d" % (self.id, key, i)

    def questions(self):
        out, hidden = [], []
        for key, typ, title in self.defs:
            if typ == "HIDDEN_FIELD":
                hidden.append({"uuid": self.uuid(key), "type": "HIDDEN_FIELD",
                               "questionType": "HIDDEN_FIELDS", "blockGroupUuid": self.hidden_qid(), "title": title})
                continue
            q = {"id": self.qid(key), "type": typ, "title": title, "isDeleted": False, "fields": []}
            if key in self.options and typ in ("MULTIPLE_CHOICE_OPTION", "DROPDOWN_OPTION"):
                q["fields"] = [{"uuid": self.opt(key, i), "type": typ, "blockGroupUuid": self.uuid(key), "title": t}
                               for i, t in enumerate(self.options[key])]
            elif typ == "CHECKBOX":
                q["fields"] = [{"uuid": "c-%s-%s" % (self.id, key), "type": "CHECKBOX",
                                "blockGroupUuid": self.uuid(key), "title": "I agree"}]
            elif typ != "HIDDEN_FIELDS":
                # an ordinary question: ONE field whose uuid and blockGroupUuid are the form-builder uuid
                q["fields"] = [{"uuid": self.uuid(key), "type": "INPUT_FIELD", "questionType": typ,
                                "blockGroupUuid": self.uuid(key), "title": title}]
            out.append(q)
        if hidden:
            out.append({"id": self.hidden_qid(), "type": "HIDDEN_FIELDS", "title": None, "isDeleted": False,
                        "fields": hidden})
        return out

    def blocks(self):
        out = []
        for key, typ, _ in self.defs:
            if key in self.options and typ in ("MULTIPLE_CHOICE_OPTION", "DROPDOWN_OPTION"):
                for i, t in enumerate(self.options[key]):
                    out.append({"type": typ, "uuid": self.opt(key, i), "groupUuid": self.uuid(key),
                                "payload": {"index": i, "text": t}})
        return out

    def add(self, answers, submitted="2026-11-10", completed=True, sid=None):
        """answers: {key: python value}; choice keys take an option index."""
        self.n += 1
        sid = sid or "s%05d" % self.n
        responses, hidden = [], []
        for key, val in answers.items():
            typ = next((t for k, t, _ in self.defs if k == key), None)
            if typ is None:
                continue          # question removed from this form
            if typ == "HIDDEN_FIELD":
                hidden.append((key, val))
                continue
            if key in self.options and typ in ("MULTIPLE_CHOICE_OPTION", "DROPDOWN_OPTION") and isinstance(val, int):
                val = [self.opt(key, val)]
            responses.append({"id": "r-%s-%s" % (sid, key), "questionId": self.qid(key), "answer": val})
        if hidden:
            hs, hq = self.hidden_shape, self.hidden_qid()
            title = {k: next(t for kk, _, t in self.defs if kk == k) for k, _ in hidden}
            rid = "r-%s-hidden" % sid
            if hs == "a":
                responses.append({"id": rid, "questionId": hq, "answer": {self.uuid(k): v for k, v in hidden}})
            elif hs == "a_name":
                responses.append({"id": rid, "questionId": hq, "answer": {title[k]: v for k, v in hidden}})
            elif hs == "b":
                for k, v in hidden:
                    responses.append({"id": rid + k, "questionId": self.uuid(k), "answer": v})
            elif hs == "c":
                responses.append({"id": rid, "questionId": hq, "answer": [
                    {"uuid": self.uuid(k), "title": title[k], "value": v} for k, v in hidden]})
            else:
                raise ValueError(hs)
        self.subs.append({"id": sid, "formId": self.id, "isCompleted": completed,
                          "submittedAt": submitted + "T09:30:00.000Z", "locale": None, "responses": responses})
        return sid

    def drop(self, key):
        self.defs = [d for d in self.defs if d[0] != key]

    def write(self, directory):
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        (d / ("form_%s.json" % self.id)).write_text(json.dumps(
            {"id": self.id, "name": "fixture", "status": "PUBLISHED", "blocks": self.blocks()}), encoding="utf-8")
        (d / ("submissions_%s.json" % self.id)).write_text(json.dumps({
            "page": 1, "limit": 500, "hasMore": False,
            "totalNumberOfSubmissionsPerFilter": {"all": len(self.subs), "completed": len(self.subs), "partial": 0},
            "questions": self.questions(), "submissions": self.subs}), encoding="utf-8")


class World:
    """Nine forms matching config.json: 3 submission, 3 report, 3 sign-up."""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self.sub, self.rep, self.signup = {}, {}, {}
        for l in LANGS:
            sf = self.cfg["submission_forms"][l]
            defs = [(k, TYPES.get(k, "INPUT_TEXT"), c["title"]) for k, c in sf["fields"].items()]
            defs += EXTRA_SUBMISSION_QUESTIONS
            self.sub[l] = Form(sf["id"], defs, {k: c.get("id") for k, c in sf["fields"].items()})
            rf = self.cfg["report_forms"][l]
            defs = [(k, TYPES.get(k, "INPUT_TEXT"), c["title"]) for k, c in rf["fields"].items()]
            defs += EXTRA_REPORT_QUESTIONS
            f = rf["fields"]
            texts = {"contribution": [f["contribution"]["yes"], f["contribution"]["no"]],
                     "overall": [f["overall"]["made"], f["overall"]["not_made"], f["overall"]["cannot_tell"]]}
            self.rep[l] = Form(rf["id"], defs, {k: c.get("id") for k, c in f.items()}, texts)
            self.signup[l] = Form(self.cfg["signup_forms"][l], [
                ("name", "INPUT_TEXT", "Full name"), ("email", "INPUT_EMAIL", "Email")])

    def recipe(self, lang, sid, consent=True, submitted="2026-11-10", **fields):
        base = {"dish_name": "Jollof rice", "other_names": "", "pilot_dish": 0, "makes": "4 plates",
                "ingredients": "2 cups rice\n3 tomatoes\n1 onion", "method": "1. Fry the onion.\n2. Add the rice.",
                "signs": "The rice is soft.", "total_time": "1 hour", "equipment": "A large pot",
                "finished_dish": "Red rice, a little smoky.",
                "name": "Writer Name", "email": "writer@example.org", "agreement": True, "licence": 0,
                "origin": 0, "family": "Family text", "taught": "Aunt text", "upload": [{"url": "https://x"}]}
        base.update(fields)
        if consent is not None:
            base["consent"] = consent
        return self.sub[lang].add(base, submitted=submitted, sid=sid)

    def report(self, lang, recipe, contribution=1, finished=0, overall=0, completed=True, **extra):
        a = {"recipe": recipe, "dish": "x", "version": "v1", "contribution": contribution,
             "finished": finished, "overall": overall, "name": "Tester Name", "email": "tester@example.org",
             "city": "Tester City", "describe": "Free text"}
        a.update(extra)
        return self.rep[lang].add(a, completed=completed)

    def signups(self, lang, n):
        for _ in range(n):
            self.signup[lang].add({"name": "Signup Name", "email": "signup@example.org"})

    def forms(self):
        for g in (self.sub, self.rep, self.signup):
            yield from g.values()

    def write(self, directory):
        for f in self.forms():
            f.write(directory)


def standard_world():
    """The committed preview fixtures: a handful of recipes at different test counts."""
    w = World()
    jollof = w.recipe("en", "Jo11of", dish_name="Jollof rice", other_names="Benachin",
                      ingredients="2 cups long-grain rice\n400 g tomatoes\n1 onion\n2 tbsp oil\nSalt",
                      method="1. Blend the tomatoes and onion.\n2. Fry in oil for 15 minutes.\n3. Add rice and water.\n4. Cover and cook on low heat.",
                      signs="The sauce darkens and the oil rises.\nThe rice is soft and dry.",
                      makes="4 plates", total_time="1 hour 10 minutes", equipment="Large pot with a lid",
                      finished_dish="Orange-red rice, soft grains, slightly smoky.", submitted="2026-11-08")
    alloco = w.recipe("fr", "A110c0", dish_name="Alloco", pilot_dish=1, makes="2 personnes", total_time="20 minutes",
                      ingredients="3 bananes plantains mûres\nHuile\nSel", method="1. Épluchez les bananes.\n2. Coupez en rondelles.\n3. Faites frire.",
                      signs="Les bords sont dorés.", equipment="Poêle profonde", finished_dish="Dorés dehors, tendres dedans.",
                      submitted="2026-11-09")
    w.recipe("sw", "Ug41i1", dish_name="Ugali na sukuma wiki", pilot_dish=2, makes="Watu 4", total_time="Dakika 40",
             ingredients="Unga wa mahindi\nSukuma wiki\nVitunguu", method="1. Chemsha maji.\n2. Ongeza unga.\n3. Koroga.",
             signs="Unga unaachia sufuria.", equipment="Sufuria", finished_dish="Ugali laini.", submitted="2026-11-10")
    w.recipe("en", "Xss111", dish_name="<script>alert(1)</script> Soup", ingredients="<img src=x onerror=alert(1)>\n1 onion",
             method="1. <b>Boil</b> it.\n2. Write to cook@example.org or call +233 24 123 4567.", submitted="2026-11-11")
    w.recipe("en", "Nocons", dish_name="Not consented", consent=False)
    w.recipe("en", "Hidd3n", dish_name="Hidden by the operator")
    w.recipe("en", "Nolegc", dish_name="Before the consent existed", consent=None)
    for _ in range(5):
        w.report("en", "Jo11of", overall=0)
    w.report("en", "Jo11of", overall=1)
    w.report("en", "Jo11of", contribution=0)             # set aside: writer helped
    w.report("fr", "A110c0", overall=2)
    w.report("sw", "A110c0", finished=1)                  # stopped
    w.report("sw", "Ug41i1", completed=False)             # partial: not a report
    w.signups("en", 3)
    w.signups("fr", 2)
    w.signups("sw", 1)
    return w, ["Hidd3n"]
