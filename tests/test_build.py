import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import build  # noqa: E402
import fixturelib  # noqa: E402

PASS = "test-passphrase-123"


def tmpdir(prefix="pilot-test-"):
    """A temporary directory that is removed when the test module finishes (even after a failure)."""
    td = tempfile.TemporaryDirectory(prefix=prefix)
    unittest.addModuleCleanup(td.cleanup)
    return Path(td.name)


def run(world, hidden=(), cfg=None, argv_extra=(), passphrase=PASS, out=None, env_extra=None):
    """Write the world as fixtures and run build.main. Returns (code, stdout, stderr, out_dir)."""
    tmp = tmpdir()
    fx = tmp / "fx"
    world.write(fx)
    hid = tmp / "hidden.txt"
    hid.write_text("# comment line\n" + "\n".join("%s  # why" % h for h in hidden) + "\n", encoding="utf-8")
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps(cfg or world.cfg), encoding="utf-8")
    out_dir = Path(out) if out else tmp / "out"
    argv = ["--fixtures", str(fx), "--config", str(cfg_path), "--hidden", str(hid), "--out", str(out_dir)] + list(argv_extra)
    so, se = io.StringIO(), io.StringIO()
    env = {"DASHBOARD_PASSPHRASE": passphrase}
    env.update(env_extra or {})
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            code = build.main(argv)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return code, so.getvalue(), se.getvalue(), out_dir


def recipes_of(out_dir):
    return json.loads((out_dir / "recipes.json").read_text(encoding="utf-8"))


def decrypt(path, passphrase):
    return subprocess.run(["node", str(ROOT / "tests" / "decrypt.mjs"), str(path)],
                          env=dict(os.environ, DASHBOARD_PASSPHRASE=passphrase), capture_output=True)


def all_output_text(out_dir):
    return "".join(p.read_text(encoding="utf-8") for p in sorted(out_dir.rglob("*")) if p.is_file())


def marked_world():
    """One recipe, a marker string in every field, allowed or not; reports and sign-ups too."""
    w = fixturelib.World()
    answers = {k: "MK_" + k for k in build.TEXT_FIELDS if k != "pilot_dish"}
    answers.update(name="MK_name", email="MK_email", agreement="MK_agreement", licence="MK_licence",
                   origin="MK_origin", family="MK_family", taught="MK_taught", upload=[{"url": "MK_upload"}])
    w.recipe("en", "Mark01", pilot_dish="MK_pilot_dish", **answers)
    w.report("en", "Mark01", name="MK_rname", email="MK_remail", city="MK_rcity", describe="MK_rdescribe")
    w.signup["en"].add({"name": "MK_sname", "email": "MK_semail"})
    return w


ALLOWED_MARKERS = ["MK_" + k for k in build.TEXT_FIELDS]
FORBIDDEN_MARKERS = ["MK_name", "MK_email", "MK_agreement", "MK_licence", "MK_origin", "MK_family",
                     "MK_taught", "MK_upload", "MK_rname", "MK_remail", "MK_rcity", "MK_rdescribe",
                     "MK_sname", "MK_semail"]


class AllowList(unittest.TestCase):
    # MUTATION: add "name" (or any personal field) to build.TEXT_FIELDS and to the config mapping,
    # or add a personal key to PUBLIC_FIELDS: the forbidden-marker assertions below must fail.
    def test_only_allowed_markers_reach_the_output(self):
        code, so, se, out = run(marked_world())
        self.assertEqual(code, 0, se)
        recipes = recipes_of(out)
        self.assertEqual(len(recipes), 1)
        self.assertEqual(list(recipes[0].keys()), list(build.PUBLIC_FIELDS))
        text = all_output_text(out)
        for m in ALLOWED_MARKERS:
            self.assertIn(m, text, m)
        for m in FORBIDDEN_MARKERS:
            self.assertNotIn(m, text, m)
        plain = json.loads(decrypt(out / "dashboard.enc.json", PASS).stdout)
        for m in FORBIDDEN_MARKERS:
            self.assertNotIn(m, json.dumps(plain), m)

    def test_config_cannot_map_a_personal_field(self):
        w = fixturelib.World()
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["name"] = {"id": None, "title": "Your full name"}
        code, so, se, out = run(w, cfg=cfg)
        self.assertNotEqual(code, 0)
        self.assertIn("name", se)
        self.assertFalse((out / "recipes.json").exists())

    def test_record_shape(self):
        w = fixturelib.World()
        w.recipe("fr", "abCD12ef", submitted="2026-11-09")
        code, _, se, out = run(w)
        r = recipes_of(out)[0]
        self.assertEqual(r["code"], "R-ABCD12")
        self.assertEqual(r["lang"], "fr")
        self.assertEqual(r["submitted"], "2026-11-09")
        self.assertEqual(r["tests"], 0)


EN_OPT = fixturelib.load_config()["submission_forms"]["en"]["fields"]["consent"]["option"]


class Consent(unittest.TestCase):
    # MUTATION: drop the consent check in build.build_recipes (remove the `if not form.ticked(...)`
    # branch): the absent-recipe assertions below must fail.
    def test_missing_or_unticked_consent_is_absent(self):
        w = fixturelib.World()
        w.recipe("en", "yesA01")
        w.recipe("en", "noneA1", consent=None)       # question exists, never answered
        w.recipe("en", "falseA", consent=False)
        w.recipe("en", "listFa", consent=[False])
        w.recipe("en", "strFal", consent="false")
        w.recipe("en", "listTr", consent=[EN_OPT])
        code, _, se, out = run(w)
        self.assertEqual(code, 0, se)
        ids = {r["id"] for r in recipes_of(out)}
        self.assertEqual(ids, {"yesA01", "listTr"})

    # MUTATION: make FormData.ticked treat any non-empty string or number as a tick again: the
    # not-consented shapes below must fail.
    def test_consent_allow_list_end_to_end_in_every_language(self):
        cfg = fixturelib.load_config()
        for lang in build.LANGS:
            opt = cfg["submission_forms"][lang]["fields"]["consent"]["option"]
            other = [cfg["submission_forms"][l]["fields"]["consent"]["option"] for l in build.LANGS if l != lang][0]
            w = fixturelib.World()
            yes = {"boolTr": True, "uuid01": [opt], "uuidSt": opt, "uuid02": ["x", opt]}
            no = {"non001": "Non", "hap001": "Hapana", "oth001": [other], "othSt1": other, "one001": 1,
                  "true01": "true", "dict01": {"a": True}, "dict02": {opt: True}, "empty1": [], "none01": None,
                  "listTr": [True], "emptyS": "", "zero01": 0, "yesStr": "yes", "dupl01": [other, "Oui"]}
            for sid, v in list(yes.items()) + list(no.items()):
                w.recipe(lang, sid, consent=v)
            code, _, se, out = run(w)
            self.assertEqual(code, 0, se)
            self.assertEqual({r["id"] for r in recipes_of(out)}, set(yes), lang)

    def test_consent_by_exact_configured_text(self):
        w = fixturelib.World()
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["consent"]["option_text"] = "I agree to the pilot site"
        w.recipe("en", "text01", consent=["I agree to the pilot site"])
        w.recipe("en", "text02", consent=["i agree to the pilot site"])
        w.recipe("en", "text03", consent=["I agree"])
        w.recipe("en", "text04", consent="I agree to the pilot site")      # a single string is a uuid, not text
        code, _, se, out = run(w, cfg=cfg)
        self.assertEqual(code, 0, se)
        self.assertEqual({r["id"] for r in recipes_of(out)}, {"text01"})

    def test_config_without_consent_option_is_refused(self):
        w = fixturelib.World()
        cfg = copy.deepcopy(w.cfg)
        del cfg["submission_forms"]["fr"]["fields"]["consent"]["option"]
        w.recipe("fr", "frc001")
        code, _, se, out = run(w, cfg=cfg)
        self.assertNotEqual(code, 0)
        self.assertIn("consent", se)
        self.assertFalse((out / "recipes.json").exists())

    def test_no_consent_question_on_the_form_fails_closed(self):
        w = fixturelib.World()
        w.sub["en"].drop("consent")
        w.recipe("en", "yesA01")
        code, _, se, out = run(w)
        self.assertNotEqual(code, 0)
        self.assertIn("consent", se)
        self.assertFalse((out / "recipes.json").exists())


class HideList(unittest.TestCase):
    # MUTATION: stop applying the hide list in build.build_recipes (make its `if sid.casefold() in
    # hide_keys ...` branch never true): this test and the next must fail.
    def test_hidden_id_is_absent_and_counted(self):
        w = fixturelib.World()
        w.recipe("en", "keep01")
        w.recipe("en", "hide01")
        code, so, se, out = run(w, hidden=["hide01"])
        self.assertEqual(code, 0, se)
        self.assertEqual([r["id"] for r in recipes_of(out)], ["keep01"])
        plain = json.loads(decrypt(out / "dashboard.enc.json", PASS).stdout)
        self.assertEqual(plain["totals"]["hidden"], 1)
        self.assertEqual(plain["totals"]["recipes_published"], 1)

    # MUTATION: compare the raw id only (drop the casefold, or the code arm): this test must fail.
    def test_hidden_by_code_or_other_case(self):
        w = fixturelib.World()
        for sid in ("keep02", "AbCdEf01", "Zz9988xx", "Qq7766yy"):
            w.recipe("en", sid)
        code, so, se, out = run(w, hidden=["abcdef01", "r-zz9988", "\tR-QQ7766\t"])
        self.assertEqual(code, 0, se)
        self.assertEqual([r["id"] for r in recipes_of(out)], ["keep02"])

    def test_hide_file_comments_and_blank_lines(self):
        p = tmpdir() / "h.txt"
        p.write_text("# top\n\nabc123  # because\n  \n#def456\nxyz789\n", encoding="utf-8")
        self.assertEqual(build.load_hidden(p), {"abc123", "xyz789"})


class Masking(unittest.TestCase):
    UNTOUCHED = [
        "cuire.De temps", "sel.Ca", "Cook it.So good", "1995-2005", "1 000 000 FCFA", "12.11.2024", "1000 g",
        "1 h 15", "180 C (350 F)", "4-6", "1 1/2", "2 cups rice", "350 g flour, 20 minutes", "1.5 kg",
        "Heat to 180 C for 45 min", "1 000 g", "Stir well.Then add salt", "Prix : 1 000 000 000 FCFA",
        "5 000 000 000 g", "KSh 1 000 000 000", "Du 12.11.2024 au 15.11.2024", "Serves 4 6 8",
    ]
    MASKED = [
        "write to cook@example.org now", "see https://example.com/page?x=1 please", "see http://example.com",
        "www.example.com", "www.site.org/abc", "visit mysite.com for more", "example.co.za", "go to example.co.za now",
        "+254 712 345 678", "0712345678", "call +233 24 123 4567", "call 0241234567", "tel (020) 7946-0958",
        "name[at]gmail.com", "name (at) gmail.com", "@handle", "follow @cook_maria today", "mail me: a.b+c@d.fr",
        "a@b.c+example@gmail.com",
    ]

    def test_text_that_is_not_contact_data_is_left_alone(self):
        for c in self.UNTOUCHED:
            self.assertEqual(build.mask(c), c, c)

    def test_contact_data_is_masked(self):
        for c in self.MASKED:
            m = build.mask(c)
            self.assertIn(build.MASK, m, c)
            self.assertNotRegex(m, r"example|@|mysite|www\.|\d{7}|4567|0958|handle|cook_maria|gmail|a\.b|d\.fr", c)

    def test_masking_keeps_the_text_around_it(self):
        self.assertEqual(build.mask("Write to cook@example.org, then cuire.De temps"),
                         "Write to [removed], then cuire.De temps")
        self.assertEqual(build.mask("Call 0712345678 for 500 g rice"), "Call [removed] for 500 g rice")

    # MUTATION: go back to "(?:[ .\-]?(?:\(\d+\)|\d+))*" in _NUMBER (exponential), unbound the
    # {1,64} before the @ in _EMAIL or _EMAIL_AT, or search the whole prefix for a unit (quadratic): this must fail.
    def test_masking_is_fast_on_adversarial_text(self):
        import time
        n = 100_000
        # 27 digits and a letter: about 6 s with the exponential pattern (each extra digit doubles it), instant now.
        cases = ["1" * 27 + "x", "a" * n + "@", "a" * n + " [at] b",
                 "a." * (n // 2) + "@b", "FCFA " + "1 2 3 4 5 6 7 8 9 0 x " * (n // 22), "1 " * (n // 2) + "x",
                 "(1" * (n // 2), "+1" * (n // 2) + "x"]
        for c in cases:
            t = time.monotonic()
            build.mask(c)
            self.assertLess(time.monotonic() - t, 2.0, c[:30])

    def test_masks_in_every_free_text_field_end_to_end(self):
        w = fixturelib.World()
        vals = {k: "x me@a.org y" for k in build.TEXT_FIELDS if k != "pilot_dish"}
        w.recipe("en", "mask01", **vals)
        code, _, se, out = run(w)
        r = recipes_of(out)[0]
        for k in build.TEXT_FIELDS:
            self.assertNotIn("@", r[k], k)
        self.assertNotIn("a.org", r["dish_name"])


class MissingQuestion(unittest.TestCase):
    def test_missing_question_fails_names_the_field_and_keeps_the_old_files(self):
        w = fixturelib.World()
        w.recipe("sw", "swa001")
        w.sub["sw"].drop("method")
        out = tmpdir() / "site-data"
        out.mkdir()
        (out / "recipes.json").write_text('["last good"]', encoding="utf-8")
        (out / "dashboard.enc.json").write_text("last good dashboard", encoding="utf-8")
        code, so, se, _ = run(w, out=out)
        self.assertNotEqual(code, 0)
        self.assertIn("method", se)
        self.assertEqual((out / "recipes.json").read_text(encoding="utf-8"), '["last good"]')
        self.assertEqual((out / "dashboard.enc.json").read_text(encoding="utf-8"), "last good dashboard")
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["dashboard.enc.json", "recipes.json"])

    def test_missing_report_question_fails(self):
        w = fixturelib.World()
        w.rep["fr"].drop("contribution")
        code, so, se, out = run(w)
        self.assertNotEqual(code, 0)
        self.assertIn("contribution", se)
        self.assertFalse(out.exists() and any(out.iterdir()))

    def test_optional_hidden_fields_may_be_absent(self):
        w = fixturelib.World()
        for l in fixturelib.LANGS:
            w.rep[l].drop("dish")
            w.rep[l].drop("version")
        code, so, se, out = run(w)
        self.assertEqual(code, 0, se)

    def test_deleted_question_does_not_count(self):
        w = fixturelib.World()
        w.recipe("en", "del001")
        f = w.sub["en"]
        orig = f.questions
        def q():
            qs = orig()
            for x in qs:
                if x["id"] == f.qid("method"):
                    x["isDeleted"] = True
            return qs
        f.questions = q
        code, _, se, _ = run(w)
        self.assertNotEqual(code, 0)
        self.assertIn("method", se)


class Matching(unittest.TestCase):
    def test_id_is_preferred_over_title(self):
        w = fixturelib.World()
        w.recipe("en", "idm001", dish_name="By id")
        cfg = copy.deepcopy(w.cfg)
        # a title that matches nothing, but the right id
        cfg["submission_forms"]["en"]["fields"]["dish_name"]["title"] = "No such title"
        code, _, se, out = run(w, cfg=cfg)
        self.assertEqual(code, 0, se)
        self.assertEqual(recipes_of(out)[0]["dish_name"], "By id")

    def test_title_used_when_no_id(self):
        w = fixturelib.World()
        w.recipe("en", "tit001", dish_name="By title")
        cfg = copy.deepcopy(w.cfg)
        for grp in ("submission_forms", "report_forms"):
            for l in cfg[grp]:
                for c in cfg[grp][l]["fields"].values():
                    c["id"] = None
        code, _, se, out = run(w, cfg=cfg)
        self.assertEqual(code, 0, se)
        self.assertEqual(recipes_of(out)[0]["dish_name"], "By title")

    # MUTATION: let FormData.find return the first title match again: these two tests must fail.
    def test_stale_id_and_shared_title_fails_closed(self):
        w = fixturelib.World()
        w.sub["en"].defs.insert(0, ("evil", "INPUT_TEXT", "Method"))   # a personal question titled like one
        w.recipe("en", "dup001", evil="MK_name")
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["method"]["id"] = "stale-id"
        code, so, se, out = run(w, cfg=cfg)
        self.assertNotEqual(code, 0)
        self.assertIn("method", se)
        self.assertNotIn("MK_name", so + se)
        self.assertFalse((out / "recipes.json").exists())

    def test_hidden_field_named_like_a_question_fails_closed(self):
        w = fixturelib.World()
        w.sub["en"].defs.insert(0, ("hf", "HIDDEN_FIELDS", "Ingredients"))
        w.recipe("en", "hid001", hf="MK_name")
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["ingredients"]["id"] = None
        code, so, se, out = run(w, cfg=cfg)
        self.assertNotEqual(code, 0)
        self.assertIn("ingredients", se)
        self.assertFalse((out / "recipes.json").exists())

    def test_wrong_id_falls_back_to_title(self):
        w = fixturelib.World()
        w.recipe("en", "fb0001", dish_name="Fallback")
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["dish_name"]["id"] = "not-a-real-id"
        code, _, se, out = run(w, cfg=cfg)
        self.assertEqual(code, 0, se)
        self.assertEqual(recipes_of(out)[0]["dish_name"], "Fallback")


class Logging(unittest.TestCase):
    # MUTATION: add a print of an answer (e.g. print(clean_text(...)) or the record) in build.py:
    # this test must fail.
    def test_logs_contain_no_answer(self):
        w = marked_world()
        w.recipe("sw", "Mark02", dish_name="MK_dish_swahili", ingredients="MK_ing_sw")
        code, so, se, out = run(w)
        self.assertEqual(code, 0, se)
        logs = so + se
        self.assertIn("build ok: 2 recipes published", logs)
        for m in ALLOWED_MARKERS + FORBIDDEN_MARKERS + ["MK_dish_swahili", "MK_ing_sw", "Mark01", "Mark02", PASS]:
            self.assertNotIn(m, logs, m)

    # MUTATION: log the hidden / awaiting-consent / report / sign-up counts again: this test must fail.
    def test_public_log_has_no_private_totals(self):
        w = fixturelib.World()
        w.recipe("en", "pub001")
        w.recipe("en", "hid001")
        for i in range(3):
            w.recipe("fr", "nc%04d" % i, consent=False)
        for _ in range(4):
            w.report("sw", "pub001")
        w.report("sw", "pub001", contribution=0)
        w.signups("fr", 7)
        code, so, se, out = run(w, hidden=["hid001"])
        self.assertEqual(code, 0, se)
        self.assertEqual(so.strip().splitlines(), ["build ok: 1 recipes published"])
        self.assertEqual(se, "")
        for word in ("hidden", "awaiting", "consent", "report", "sign-up", "signup", "set aside", "sw=", "fr=", "en="):
            self.assertNotIn(word, (so + se).lower(), word)

    def test_failure_logs_contain_no_answer(self):
        w = marked_world()
        w.sub["en"].drop("method")
        code, so, se, out = run(w)
        self.assertNotEqual(code, 0)
        for m in ALLOWED_MARKERS + FORBIDDEN_MARKERS + [PASS]:
            self.assertNotIn(m, so + se, m)

    def test_discover_prints_ids_types_titles_only(self):
        w = marked_world()
        code, so, se, out = run(w, argv_extra=["discover"])
        self.assertEqual(code, 0, se)
        self.assertIn("Name of the dish", so)
        self.assertIn("INPUT_TEXT", so)
        self.assertIn(w.sub["en"].qid("dish_name"), so)
        for m in ALLOWED_MARKERS + FORBIDDEN_MARKERS + ["Mark01"]:
            self.assertNotIn(m, so + se, m)


class Dashboard(unittest.TestCase):
    def test_encrypted_not_plaintext_and_decrypts(self):
        w = fixturelib.World()
        w.recipe("en", "dash01", dish_name="Zanzibar Pilau Special")
        w.report("en", "dash01", overall=0)
        code, _, se, out = run(w)
        raw = (out / "dashboard.enc.json").read_text(encoding="utf-8")
        for s in ("Zanzibar", "dash01", "R-DASH01", "recipes_published", "signups", "outcomes"):
            self.assertNotIn(s, raw)
        blob = json.loads(raw)
        self.assertGreaterEqual(blob["iterations"], 200000)
        self.assertEqual(blob["hash"], "SHA-256")
        ok = decrypt(out / "dashboard.enc.json", PASS)
        self.assertEqual(ok.returncode, 0)
        plain = json.loads(ok.stdout)
        self.assertEqual(plain["recipes"][0]["code"], "R-DASH01")
        self.assertEqual(plain["recipes"][0]["tests"], 1)
        self.assertEqual(plain["recipes"][0]["outcomes"]["made"], 1)
        self.assertNotEqual(decrypt(out / "dashboard.enc.json", PASS + "x").returncode, 0)

    def test_two_builds_use_fresh_salt_and_iv(self):
        w = fixturelib.World()
        a = json.loads((run(w)[3] / "dashboard.enc.json").read_text())
        b = json.loads((run(w)[3] / "dashboard.enc.json").read_text())
        self.assertNotEqual(a["salt"], b["salt"])
        self.assertNotEqual(a["iv"], b["iv"])

    def test_totals_outcomes_and_target_flag(self):
        w = fixturelib.World()
        w.recipe("en", "five01", dish_name="Five")
        w.recipe("fr", "two001", dish_name="Two")
        w.recipe("sw", "nocon1", consent=False)
        for _ in range(5):
            w.report("en", "five01", overall=0)
        w.report("fr", "two001", overall=1)
        w.report("sw", "two001", finished=1)
        w.signups("en", 2)
        w.signups("sw", 4)
        code, _, se, out = run(w)
        plain = json.loads(decrypt(out / "dashboard.enc.json", PASS).stdout)
        t = plain["totals"]
        self.assertEqual((t["recipes_published"], t["hidden"], t["awaiting_consent"]), (2, 0, 1))
        self.assertEqual(t["signups"], {"en": 2, "fr": 0, "sw": 4})
        self.assertEqual(t["reports"], {"en": 5, "fr": 1, "sw": 1})
        by = {r["code"]: r for r in plain["recipes"]}
        self.assertTrue(by["R-FIVE01"]["reached_target"])
        self.assertFalse(by["R-TWO001"]["reached_target"])
        self.assertEqual(by["R-TWO001"]["outcomes"], {"made": 0, "not": 1, "cannot_tell": 0, "stopped": 1, "unrecognised": 0})

    def test_abandoned_report_counts_as_stopped(self):
        w = fixturelib.World()
        w.recipe("en", "aband1")
        w.rep["en"].add({"recipe": "aband1", "contribution": 1, "finished": 0})   # no overall answer
        code, _, se, out = run(w)
        plain = json.loads(decrypt(out / "dashboard.enc.json", PASS).stdout)
        self.assertEqual(plain["recipes"][0]["tests"], 1)
        self.assertEqual(plain["recipes"][0]["outcomes"]["stopped"], 1)

    def test_no_passphrase_fails_and_writes_nothing(self):
        w = fixturelib.World()
        w.recipe("en", "nop001")
        tmp = tmpdir()
        fx = tmp / "fx"
        w.write(fx)
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps(w.cfg))
        env = {k: v for k, v in os.environ.items() if k not in ("DASHBOARD_PASSPHRASE", "TALLY_API_KEY")}
        res = subprocess.run([sys.executable, str(ROOT / "build.py"), "--config", str(cfg), "--out", str(tmp / "o")],
                             env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((tmp / "o").exists())


class ReportCounts(unittest.TestCase):
    def test_contribution_yes_is_ignored_in_all_languages(self):
        w = fixturelib.World()
        w.recipe("en", "cnt001")
        w.report("en", "cnt001")
        w.report("fr", "cnt001")
        w.report("sw", "cnt001")
        w.report("en", "cnt001", contribution=0)
        w.report("fr", "cnt001", contribution=0)
        w.report("sw", "cnt001", contribution=0)
        code, _, se, out = run(w)
        self.assertEqual(recipes_of(out)[0]["tests"], 3)

    def test_partial_reports_and_other_recipes_not_counted(self):
        w = fixturelib.World()
        w.recipe("en", "cnt001")
        w.recipe("en", "cnt002")
        w.report("en", "cnt001")
        w.report("en", "cnt001", completed=False)
        w.report("en", "cnt002")
        w.report("en", "unknown")
        code, _, se, out = run(w)
        got = {r["id"]: r["tests"] for r in recipes_of(out)}
        self.assertEqual(got, {"cnt001": 1, "cnt002": 1})

    def test_pagination_reads_every_page(self):
        w = fixturelib.World()
        for i in range(5):
            w.recipe("en", "pg%04d" % i)
            w.report("en", "pg0000")
        old = build.PAGE_LIMIT
        build.PAGE_LIMIT = 2
        try:
            code, _, se, out = run(w)
        finally:
            build.PAGE_LIMIT = old
        self.assertEqual(code, 0, se)
        got = {r["id"]: r["tests"] for r in recipes_of(out)}
        self.assertEqual(len(got), 5)
        self.assertEqual(got["pg0000"], 5)

    def test_answer_shapes_for_ticks(self):
        f = build.FormData("x", "x", [{"id": "q", "type": "CHECKBOX", "title": "t", "fields": []}], [], [])
        q = f.questions[0]
        for val, want in ((True, True), (False, False), ([False], False), ([], False), ("opt-1", True),
                          (["opt-1"], True), (["a", "opt-1"], True), (["opt-2"], False), ("opt-2", False),
                          ("true", False), ("false", False), ("Non", False), ("Hapana", False), (1, False),
                          (None, False), ({"a": True}, False), ({"opt-1": True}, False), ([True], False),
                          (["Agree text"], True), (["agree text"], False), ("Agree text", False)):
            sub = {"responses": [{"questionId": "q", "answer": val}]}
            self.assertEqual(f.ticked(sub, q, "opt-1", "Agree text"), want, repr(val))
        self.assertFalse(f.ticked({"responses": []}, q, "opt-1"))
        # no configured option: only the boolean True counts
        self.assertFalse(f.ticked({"responses": [{"questionId": "q", "answer": ["opt-1"]}]}, q, None))
        self.assertTrue(f.ticked({"responses": [{"questionId": "q", "answer": True}]}, q, None))

    # MUTATION: accept a bare True on any consent question again (drop the box count in FormData.ticked): must fail.
    def test_true_for_another_box_of_the_consent_question_is_not_consent(self):
        boxes = [{"uuid": "opt-1", "type": "CHECKBOX"}, {"uuid": "opt-2", "type": "CHECKBOX"}]
        f = build.FormData("x", "x", [{"id": "q", "type": "CHECKBOXES", "title": "t", "fields": boxes}], [], [])
        q = f.questions[0]
        self.assertFalse(f.ticked({"responses": [{"questionId": "opt-2", "answer": True}]}, q, "opt-1"))
        self.assertFalse(f.ticked({"responses": [{"questionId": "q", "answer": True}]}, q, "opt-1"))
        self.assertFalse(f.ticked({"responses": [{"questionId": "opt-1", "answer": True}]}, q, None))
        self.assertTrue(f.ticked({"responses": [{"questionId": "opt-1", "answer": True}]}, q, "opt-1"))
        self.assertTrue(f.ticked({"responses": [{"questionId": "q", "answer": ["opt-1"]}]}, q, "opt-1"))

    # MUTATION: count a report whose contribution answer is not clearly "No" (or go back to YES_WORDS):
    # the next two tests must fail.
    def test_contribution_is_decided_by_the_configured_option_in_every_language(self):
        cfg = fixturelib.load_config()
        for lang in build.LANGS:
            f = cfg["report_forms"][lang]["fields"]["contribution"]
            w = fixturelib.World()
            w.recipe("en", "cfg001")
            w.report(lang, "cfg001", contribution=f["no"])             # a clear No: counted
            w.report(lang, "cfg001", contribution=[f["no"]])
            w.report(lang, "cfg001", contribution=1)                   # option uuid of "No": counted
            w.report(lang, "cfg001", contribution=f["yes"])            # set aside
            w.report(lang, "cfg001", contribution=0)                   # option uuid of "Yes": set aside
            w.report(lang, "cfg001", contribution="Maybe")             # unrecognised: set aside
            w.report(lang, "cfg001", contribution="yes")               # not the configured text: set aside
            w.report(lang, "cfg001", contribution="No thanks")
            w.report(lang, "cfg001", contribution=[f["no"], f["yes"]])  # two options: unclear
            w.report(lang, "cfg001", contribution={"x": 1})
            w.report(lang, "cfg001", contribution=None)                # missing: set aside
            w.report(lang, "cfg001", contribution="")
            code, _, se, out = run(w)
            self.assertEqual(code, 0, se)
            self.assertEqual(recipes_of(out)[0]["tests"], 3, lang)

    def test_a_french_yes_on_the_english_form_is_not_a_yes(self):
        w = fixturelib.World()
        w.recipe("en", "xl0001")
        w.report("en", "xl0001", contribution="Oui")
        w.report("en", "xl0001", contribution="Ndiyo")
        code, _, se, out = run(w)
        self.assertEqual(recipes_of(out)[0]["tests"], 0)

    # MUTATION: guess the outcome (for example by position) when the text is not configured: must fail.
    def test_unrecognised_outcome_goes_to_its_own_bucket(self):
        cfg = fixturelib.load_config()
        for lang in build.LANGS:
            o = cfg["report_forms"][lang]["fields"]["overall"]
            w = fixturelib.World()
            w.recipe("en", "out001")
            w.report(lang, "out001", overall=o["made"])
            w.report(lang, "out001", overall=o["not_made"])
            w.report(lang, "out001", overall=o["cannot_tell"])
            w.report(lang, "out001", overall="Made it, more or less")
            w.report(lang, "out001", overall=["a", "b"])
            w.report(lang, "out001", finished="Whatever")
            w.report(lang, "out001", finished=1)                        # stopped (position)
            code, _, se, out = run(w)
            self.assertEqual(code, 0, se)
            plain = json.loads(decrypt(out / "dashboard.enc.json", PASS).stdout)
            self.assertEqual(plain["recipes"][0]["tests"], 7, lang)
            self.assertEqual(plain["recipes"][0]["outcomes"],
                             {"made": 1, "not": 1, "cannot_tell": 1, "stopped": 1, "unrecognised": 3}, lang)

    def test_config_must_name_the_report_options(self):
        w = fixturelib.World()
        cfg = copy.deepcopy(w.cfg)
        del cfg["report_forms"]["sw"]["fields"]["contribution"]["yes"]
        code, _, se, _ = run(w, cfg=cfg)
        self.assertNotEqual(code, 0)
        self.assertIn("contribution.yes", se)


class EmptyAndKeyless(unittest.TestCase):
    def test_empty_mode_writes_empty_list_and_no_dashboard(self):
        out = tmpdir() / "d"
        self.assertEqual(build.main(["--empty", "--out", str(out)]), 0)
        self.assertEqual(json.loads((out / "recipes.json").read_text()), [])
        self.assertFalse((out / "dashboard.enc.json").exists())

    def test_keyless_real_build_fails(self):
        tmp = tmpdir()
        env = {k: v for k, v in os.environ.items() if k != "TALLY_API_KEY"}
        res = subprocess.run([sys.executable, str(ROOT / "build.py"), "--out", str(tmp / "o")],
                             env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("TALLY_API_KEY", res.stderr)
        self.assertFalse((tmp / "o").exists())


class Site(unittest.TestCase):
    def files(self, *suffix):
        return [p for p in (ROOT / "site").rglob("*") if p.suffix in suffix and "data" not in p.parts]

    def test_no_untrusted_html_sinks(self):
        for p in self.files(".js"):
            t = p.read_text(encoding="utf-8")
            self.assertNotRegex(t, r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(", p.name)

    def test_no_external_assets(self):
        for p in self.files(".html", ".css", ".js"):
            t = p.read_text(encoding="utf-8")
            for m in re.finditer(r"""(src|href)=["'](?:https?:)?//([^/"']+)""", t):
                # only plain links out to the Tally forms are allowed; never a script, image or stylesheet
                if m.group(1) == "src" or m.group(2) != "tally.so":
                    self.fail("external asset in %s: %s" % (p.name, m.group(0)))
            self.assertNotRegex(t, r"@import|url\(\s*['\"]?https?:", p.name)

    def test_forbidden_words_only_in_the_notice(self):
        word = re.compile(r"\bARI\b|certified|\bscores?\b|verified", re.I)
        for p in self.files(".html", ".js"):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if word.search(line):
                    self.assertRegex(line, r"notice", "%s:%d %s" % (p.name, n, line.strip()))

    def test_notice_on_list_and_recipe_pages(self):
        for name in ("recipes.html", "recipe.html"):
            self.assertIn('data-i18n="notice"', (ROOT / "site" / name).read_text(encoding="utf-8"), name)

    def test_cname_and_noindex_dashboard(self):
        self.assertEqual((ROOT / "site" / "CNAME").read_text().strip(), "pilot.afrocipe.com")
        self.assertIn("noindex", (ROOT / "site" / "dashboard.html").read_text())

    # MUTATION: remove the CSP meta from a page, or add an inline <script>, <style> or style="" / on*= attribute.
    def test_csp_on_every_page_and_no_inline_script_or_style(self):
        want = ["default-src 'self'", "connect-src 'self'", "img-src 'self' data:", "style-src 'self'",
                "script-src 'self'", "base-uri 'none'", "form-action 'none'"]
        pages = sorted((ROOT / "site").glob("*.html"))
        self.assertEqual(len(pages), 7)
        for p in pages:
            t = p.read_text(encoding="utf-8")
            m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)"', t)
            self.assertTrue(m, p.name)
            self.assertEqual([d.strip() for d in m.group(1).split(";")], want, p.name)
            for sc in re.findall(r"<script\b([^>]*)>(.*?)</script>", t, re.S):
                self.assertIn("src=", sc[0], p.name)
                self.assertEqual(sc[1].strip(), "", p.name)
            self.assertNotRegex(t, r"<style\b|\sstyle\s*=|\son[a-z]+\s*=|javascript:", p.name)
        for p in self.files(".js"):
            self.assertNotRegex(p.read_text(encoding="utf-8"), r"setAttribute\(\s*['\"]style|cssText|createElement\(\s*['\"](script|style)", p.name)

    # MUTATION: edit a content/*.md file without running make_pages.py, or edit a generated page by hand.
    def test_content_pages_are_generated_from_content(self):
        res = subprocess.run([sys.executable, str(ROOT / "make_pages.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_content_pages_hold_each_language_once_and_only_allowed_links(self):
        for name in ("guide-testers", "guide-writers", "about"):
            t = (ROOT / "site" / (name + ".html")).read_text(encoding="utf-8")
            for lang in ("en", "fr", "sw"):
                self.assertEqual(t.count('<article lang="%s" data-lang="%s">' % (lang, lang)), 1, name)
            for href in re.findall(r'href="([^"]+)"', t):
                self.assertRegex(href, r"^(#main|[a-z-]+\.html|assets/[\w.]+|css/style\.css|mailto:afrocipe12@gmail\.com|"
                                       r"https://tally\.so/r/\w+)$", name)

    def test_every_page_asks_the_reader_to_take_part(self):
        import make_pages
        for needle in ('data-form="submit"', 'href="recipes.html" data-i18n="btn_test"'):
            self.assertIn(needle, make_pages.FOOTER)           # the footer is on every page (next test)
        testers = (ROOT / "site" / "guide-testers.html").read_text(encoding="utf-8")
        writers = (ROOT / "site" / "guide-writers.html").read_text(encoding="utf-8")
        self.assertEqual(testers.count('<p class="tools no-print"><a class="btn" href="recipes.html"'), 3)
        self.assertEqual(testers.count('class="btn btn-quiet" data-form="signup"'), 3)
        for lang, form in make_pages.FORMS["submit"].items():
            self.assertIn('<p class="tools no-print"><a class="btn" data-form="submit" href="https://tally.so/r/%s"' % form, writers, lang)

    def test_every_page_has_the_same_header_and_every_key_exists(self):
        import make_pages
        i18n = (ROOT / "site" / "js" / "i18n.js").read_text(encoding="utf-8")
        for p in sorted((ROOT / "site").glob("*.html")):
            t = p.read_text(encoding="utf-8")
            self.assertIn(make_pages.HEADER, t, p.name)
            self.assertIn(make_pages.FOOTER, t, p.name)
            self.assertEqual(t.count("<main "), 1, p.name)
            self.assertEqual(t.count("<h1"), 3 if "data-lang" in t else (0 if p.name == "recipe.html" else 1), p.name)
            for key in set(re.findall(r'data-i18n="(\w+)"', t)):
                self.assertEqual(len(re.findall(r"^    %s: " % key, i18n, re.M)), 3, "%s %s" % (p.name, key))

    def test_home_page_introduces_the_pilot_and_carries_the_notice(self):
        t = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        for key in ("home_lead", "home_lead2", "role_writer", "role_tester", "notice"):
            self.assertIn('data-i18n="%s"' % key, t)
        self.assertIn('data-i18n="notice"', (ROOT / "site" / "about.html").read_text(encoding="utf-8"))

    def test_dashboard_page_is_translated(self):
        t = (ROOT / "site" / "dashboard.html").read_text(encoding="utf-8")
        js = (ROOT / "site" / "js" / "dashboard.js").read_text(encoding="utf-8")
        for key in ("dash_title", "dash_private", "dash_pass", "dash_show"):
            self.assertIn('data-i18n="%s"' % key, t)
        for lit in ("Wrong passphrase", "Recipes published", "Sign-ups", "Cannot tell", "Show the numbers"):
            self.assertNotIn('"%s"' % lit, js)
        i18n = (ROOT / "site" / "js" / "i18n.js").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"\bdash_unrecognised:", i18n)), 3)
        for needle in ("Tableau de bord du pilote", "Dashibodi ya majaribio", "Phrase secrète", "Nenosiri"):
            self.assertIn(needle, i18n)

    def test_every_language_has_every_key(self):
        js = (ROOT / "site" / "js" / "i18n.js").read_text(encoding="utf-8")
        res = subprocess.run(["node", "-e", """
          const src = require('fs').readFileSync(process.argv[1], 'utf8');
          const g = {}; new Function('window', 'localStorage', 'navigator', 'document', src + '; window.__d = DICT;')(g, {}, {language:'en'}, {});
          const d = g.__d, ks = Object.keys(d.en);
          for (const l of ['fr', 'sw']) for (const k of ks) if (!(k in d[l])) { console.log('missing ' + l + ' ' + k); process.exit(1); }
          for (const l of ['fr', 'sw']) for (const k of Object.keys(d[l])) if (!(k in d.en)) { console.log('extra ' + l + ' ' + k); process.exit(1); }
        """, str(ROOT / "site" / "js" / "i18n.js")], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


class Workflow(unittest.TestCase):
    def text(self):
        return (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

    def test_workflow_is_least_privilege_and_serialised(self):
        t = self.text()
        for needle in ("schedule:", "workflow_dispatch:", "concurrency:", "permissions:", "contents: read",
                       "upload-pages-artifact@", "deploy-pages@", "SITE_URL",
                       "TALLY_API_KEY", "DASHBOARD_PASSPHRASE", "ALLOW_EMPTY_FIRST_DEPLOY", "ALLOW_EMPTY_DEPLOY",
                       "deploy_guard.py shrink", "deploy_guard.py first-deploy"):
            self.assertIn(needle, t, needle)
        self.assertNotRegex(t, r"permissions:\s*write-all")

    # MUTATION: tag an action by version (uses: x/y@v4) or drop persist-credentials: false: must fail.
    def test_actions_are_pinned_to_a_commit_and_checkout_keeps_no_credentials(self):
        t = self.text()
        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", t, re.M)
        self.assertGreaterEqual(len(uses), 3)
        for ref, rest in uses:
            self.assertRegex(ref, r"@[0-9a-f]{40}$", ref)
            self.assertRegex(rest, r"#\s*v\d", ref)
        self.assertRegex(t, r"checkout@[0-9a-f]{40}[^\n]*\n\s*with:\s*\n\s*persist-credentials: false")

    # MUTATION: give the build job pages: write or id-token: write again: must fail.
    def test_only_the_deploy_job_has_pages_and_id_token(self):
        t = self.text()
        head, deploy = t.split("  deploy:\n")
        self.assertNotRegex(head, r"pages:\s*write|id-token:\s*write")
        self.assertRegex(deploy, r"pages:\s*write")
        self.assertRegex(deploy, r"id-token:\s*write")

    def test_the_shrink_guard_runs_before_the_artifact_is_uploaded(self):
        t = self.text()
        self.assertLess(t.index("deploy_guard.py shrink"), t.index("upload-pages-artifact"))
        self.assertLess(t.index("Fetch the live recipe list"), t.index("Build the site data"))


class DeployGuard(unittest.TestCase):
    def setUp(self):
        import deploy_guard
        self.g = deploy_guard

    # MUTATION: return ok when new_count is 0 and live_count > 0: must fail.
    def test_shrink_to_empty_is_refused(self):
        ok, msg = self.g.shrink_decision(200, 12, 0)
        self.assertFalse(ok)
        self.assertIn("12", msg)

    def test_shrink_override_and_normal_cases(self):
        sd = self.g.shrink_decision
        self.assertTrue(sd(200, 12, 0, allow_empty_deploy=True)[0])
        self.assertTrue(sd(200, 12, 11)[0])
        self.assertTrue(sd(200, 0, 0)[0])          # nothing live, nothing new
        self.assertTrue(sd(200, 0, 3)[0])
        self.assertTrue(sd(404, None, 0)[0])       # first deploy
        self.assertTrue(sd(404, None, 5)[0])
        self.assertFalse(sd(500, None, 5)[0])      # cannot tell: do not deploy
        self.assertFalse(sd(0, None, 5)[0])
        self.assertFalse(sd(200, None, 5)[0])      # live file is not a recipe list
        self.assertFalse(sd(200, 3, None)[0])

    # MUTATION: allow the first-deploy flag when the live status is not 404: must fail.
    def test_empty_first_deploy_only_when_nothing_is_live(self):
        f = self.g.first_deploy_ok
        self.assertTrue(f(404, True))
        self.assertFalse(f(404, False))
        self.assertFalse(f(200, True))
        self.assertFalse(f(500, True))
        self.assertFalse(f(0, True))

    def test_command_line(self):
        d = tmpdir()
        live, new, empty = d / "live.json", d / "new.json", d / "empty.json"
        live.write_text(json.dumps([{"id": "a"}, {"id": "b"}]), encoding="utf-8")
        new.write_text(json.dumps([{"id": "a"}]), encoding="utf-8")
        empty.write_text("[]\n", encoding="utf-8")

        def cli(*args):
            return subprocess.run([sys.executable, str(ROOT / "deploy_guard.py")] + list(args),
                                  capture_output=True, text=True).returncode
        self.assertEqual(cli("shrink", "--status", "200", "--live", str(live), "--new", str(new)), 0)
        self.assertEqual(cli("shrink", "--status", "200", "--live", str(live), "--new", str(empty)), 1)
        self.assertEqual(cli("shrink", "--status", "200", "--live", str(live), "--new", str(empty),
                             "--allow-empty-deploy", "true"), 0)
        self.assertEqual(cli("shrink", "--status", "200", "--live", str(live), "--new", str(empty),
                             "--allow-empty-deploy", "yes"), 1)
        self.assertEqual(cli("shrink", "--status", "404", "--new", str(empty)), 0)
        self.assertEqual(cli("first-deploy", "--status", "404", "--allow-empty-first-deploy", "true"), 0)
        self.assertEqual(cli("first-deploy", "--status", "200", "--allow-empty-first-deploy", "true"), 1)
        self.assertEqual(cli("first-deploy", "--status", "404"), 1)


class RealApiStructure(unittest.TestCase):
    """Short question ids, one field per question, one HIDDEN_FIELDS question (the structure the live API returns)."""

    def _tests_for(self, w, cfg=None):
        code, so, se, out = run(w, cfg=cfg)
        self.assertEqual(code, 0, se)
        return {r["id"]: r["tests"] for r in recipes_of(out)}

    def test_fixtures_have_the_real_structure(self):
        f = fixturelib.World().rep["en"]
        qs = {q["id"]: q for q in f.questions()}
        self.assertNotIn(f.uuid("contribution"), qs)                      # question ids are short ids
        q = qs[f.qid("contribution")]
        self.assertEqual(q["fields"][0]["blockGroupUuid"], f.uuid("contribution"))
        hq = [q for q in qs.values() if q["type"] == "HIDDEN_FIELDS"]
        self.assertEqual(len(hq), 1)
        self.assertIsNone(hq[0]["title"])
        self.assertEqual({x["title"] for x in hq[0]["fields"]}, {"recipe", "dish", "version"})

    def test_question_matched_by_block_group_uuid_not_title(self):
        w = fixturelib.World()
        w.recipe("en", "bgu001", dish_name="By group uuid")
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["dish_name"]["title"] = "No such title"
        code, _, se, out = run(w, cfg=cfg)
        self.assertEqual(code, 0, se)
        self.assertEqual(recipes_of(out)[0]["dish_name"], "By group uuid")

    def test_question_matched_by_field_uuid_when_group_uuid_differs(self):
        w = fixturelib.World()
        w.recipe("en", "fu0001", dish_name="By field uuid")
        f = w.sub["en"]
        orig = f.questions
        def q():
            qs = orig()
            for x in qs:
                for fld in x["fields"]:
                    fld["blockGroupUuid"] = "something-else"
            return qs
        f.questions = q
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["dish_name"]["title"] = "No such title"
        code, _, se, out = run(w, cfg=cfg)
        self.assertEqual(code, 0, se)
        self.assertEqual(recipes_of(out)[0]["dish_name"], "By field uuid")

    def test_id_matching_two_questions_fails_closed(self):
        w = fixturelib.World()
        w.recipe("en", "amb001")
        f = w.sub["en"]
        orig = f.questions
        def q():
            qs = orig()
            for x in qs:
                if x["title"] == "Your full name":      # a personal question claims the method's uuid
                    x["fields"][0]["blockGroupUuid"] = f.uuid("method")
            return qs
        f.questions = q
        code, so, se, out = run(w)
        self.assertNotEqual(code, 0)
        self.assertIn("method", se)
        self.assertFalse((out / "recipes.json").exists())

    def test_title_fallback_must_still_be_unique(self):
        w = fixturelib.World()
        w.sub["en"].defs.insert(0, ("evil", "INPUT_TEXT", "Method"))
        w.recipe("en", "dup002", evil="MK_name")
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["method"]["id"] = "stale-id"
        code, so, se, out = run(w, cfg=cfg)
        self.assertNotEqual(code, 0)
        self.assertNotIn("MK_name", so + se)
        self.assertFalse((out / "recipes.json").exists())

    def test_hidden_field_by_uuid_or_by_title(self):
        for mode in ("uuid", "title"):
            w = fixturelib.World()
            w.recipe("en", "hu0001")
            w.report("en", "hu0001")
            cfg = copy.deepcopy(w.cfg)
            c = cfg["report_forms"]["en"]["fields"]["recipe"]
            if mode == "uuid":
                c["title"] = "no such name"
            else:
                c["id"] = "stale-uuid"
            self.assertEqual(self._tests_for(w, cfg), {"hu0001": 1}, mode)

    def test_required_hidden_field_missing_fails_naming_it(self):
        w = fixturelib.World()
        w.rep["en"].drop("recipe")
        code, so, se, out = run(w)
        self.assertNotEqual(code, 0)
        self.assertIn("recipe", se)
        self.assertFalse(out.exists() and any(out.iterdir()))

    def test_hidden_field_matches_only_the_hidden_question(self):
        # an ordinary question titled "recipe" must not satisfy the hidden field
        w = fixturelib.World()
        w.rep["en"].drop("recipe")
        w.rep["en"].defs.append(("rq", "INPUT_TEXT", "recipe"))
        code, so, se, out = run(w)
        self.assertNotEqual(code, 0)
        self.assertIn("recipe", se)

    def test_two_hidden_fields_with_one_name_fails_closed(self):
        w = fixturelib.World()
        w.rep["en"].defs.append(("recipe2", "HIDDEN_FIELD", "recipe"))
        cfg = copy.deepcopy(w.cfg)
        cfg["report_forms"]["en"]["fields"]["recipe"]["id"] = "stale-uuid"
        code, so, se, out = run(w, cfg=cfg)
        self.assertNotEqual(code, 0)
        self.assertIn("recipe", se)

    # MUTATION: drop the HIDDEN_FIELDS exclusion in FormData.find: the hidden (URL-supplied) value is published.
    def test_published_field_id_never_matches_a_hidden_field(self):
        w = fixturelib.World()
        f = w.sub["en"]
        f.defs.append(("hx", "HIDDEN_FIELD", "src"))
        f.hidden_shape = "b"                                    # a response whose questionId is the field uuid
        w.recipe("en", "hx0001", dish_name="Real dish", hx="MK_hidden")
        cfg = copy.deepcopy(w.cfg)
        cfg["submission_forms"]["en"]["fields"]["dish_name"]["id"] = f.uuid("hx")
        code, so, se, out = run(w, cfg=cfg)
        self.assertEqual(code, 0, se)
        self.assertEqual(recipes_of(out)[0]["dish_name"], "Real dish")
        self.assertNotIn("MK_hidden", all_output_text(out) + so + se)


class HiddenAnswerShapes(unittest.TestCase):
    """The shape of a hidden-field answer is unverified; three shapes are accepted, and anything else is not counted."""

    def counts(self, shape, rid="hs0001", mutate=None):
        w = fixturelib.World()
        w.recipe("en", "hs0001")
        w.rep["en"].hidden_shape = shape
        w.report("en", rid)
        if mutate:
            mutate(w)
        code, so, se, out = run(w)
        self.assertEqual(code, 0, se)
        if len(str(rid)) > 3:
            self.assertNotIn(str(rid), so + se)
        return recipes_of(out)[0]["tests"]

    def test_shape_a_dict_by_field_uuid(self):
        self.assertEqual(self.counts("a"), 1)

    def test_shape_a_dict_by_field_name(self):
        self.assertEqual(self.counts("a_name"), 1)

    def test_shape_b_response_per_field_uuid(self):
        self.assertEqual(self.counts("b"), 1)

    def test_shape_c_list_of_named_values(self):
        self.assertEqual(self.counts("c"), 1)

    def test_shape_c_other_naming_keys_and_answer_key(self):
        def mutate(w):
            for sub in w.rep["en"].subs:
                for r in sub["responses"]:
                    if isinstance(r["answer"], list) and r["answer"] and isinstance(r["answer"][0], dict):
                        r["answer"] = [{"name": it["title"], "answer": it["value"]} for it in r["answer"]]
        self.assertEqual(self.counts("c", mutate=mutate), 1)

    def test_bad_values_are_not_counted_fail_closed(self):
        for shape in ("a", "a_name", "b", "c"):
            for bad in ("x" * 65, "has space", "semi;colon", "new\nline", "ok\n", "", "<b>", "é1", 12345, True, None,
                        ["hs0001"], {"v": "hs0001"}):
                self.assertEqual(self.counts(shape, rid=bad), 0, (shape, bad))

    def test_boundary_lengths(self):
        w = fixturelib.World()
        long_ok = "A" * 64
        w.recipe("en", long_ok)
        w.report("en", long_ok)
        code, _, se, out = run(w)
        self.assertEqual(recipes_of(out)[0]["tests"], 1)

    def test_unrelated_response_is_not_a_hidden_value(self):
        # a text answer to another question holding the recipe id must not be read as the hidden field
        w = fixturelib.World()
        w.recipe("en", "hs0001")
        w.rep["en"].drop("recipe")
        w.rep["en"].defs.append(("rq", "INPUT_TEXT", "Something else"))
        cfg = copy.deepcopy(w.cfg)
        cfg["report_forms"]["en"]["fields"]["recipe"]["optional"] = True
        code, so, se, out = run(w)
        self.assertNotEqual(code, 0)               # the required hidden field is missing: the build stops

    def test_answer_for_another_hidden_field_is_not_the_recipe(self):
        w = fixturelib.World()
        w.recipe("en", "hs0001")
        w.report("en", "other01", dish="hs0001", version="hs0001")
        code, so, se, out = run(w)
        self.assertEqual(recipes_of(out)[0]["tests"], 0)


class Shapes(unittest.TestCase):
    def test_shape_function_never_returns_a_value(self):
        marks = ["MK_str", 4242424, 3.14159, True]
        v = {"MK_key value": "MK_str", "a-b": [["MK_str", 4242424], {"deep": True, "3f2a1b4c-0000-4000-8000-123456789abc": 3.14159}]}
        out = build.shape(v)
        for m in ("MK_str", "4242424", "3.14159", "True", "MK_key", "value"):
            self.assertNotIn(m, out)
        self.assertIn("3f2a1b4c-0000-4000-8000-123456789abc", out)
        self.assertIn("str", out)
        self.assertIn("<key>", out)
        for m in marks:
            self.assertEqual(build.shape(m), type(m).__name__)

    # MUTATION: print any identifier-like key again (NAME_KEY instead of SHAPE_KEYS): this test must fail.
    def test_identifier_like_user_keys_are_not_printed(self):
        v = {"Kwame_Mensah": "x", "MKkey": {"Amina": [{"Mama_Ngozi": 1}]}, "recipe": "Jo11of",
             "3f2a1b4c-0000-4000-8000-123456789abc": [{"uuid": "u", "value": "v"}]}
        out = build.shape(v)
        for m in ("Kwame", "Mensah", "MKkey", "Amina", "Ngozi", "Jo11of"):
            self.assertNotIn(m, out)
        for k in ("recipe", "uuid", "value", "3f2a1b4c-0000-4000-8000-123456789abc"):
            self.assertIn(k, out)
        # through the command: a hidden-field dict carrying a user-like key prints <key>
        w = fixturelib.World()
        w.report("en", "Rid001")
        for r in w.rep["en"].subs[-1]["responses"]:
            if isinstance(r["answer"], dict):
                r["answer"]["Kwame_Mensah"] = {"Amina": "MK_val"}
        code, so, se, _ = run(w, argv_extra=["shapes"])
        self.assertEqual(code, 0, se)
        for m in ("Kwame", "Amina", "MK_val", "Rid001"):
            self.assertNotIn(m, so + se)
        self.assertIn("<key>", so)

    def test_shapes_command_prints_structure_only_and_first_submission_only(self):
        w = marked_world()
        w.recipe("en", "MARK_second", dish_name="MK_second_submission")
        w.report("en", "MK_report_id", city="MK_city", describe="MK_describe")
        w.report("en", "MK_report_second")
        w.rep["en"].hidden_shape = "c"
        w.report("en", "MK_hidden_c")
        w.signups("en", 2)
        code, so, se, out = run(w, argv_extra=["shapes"])
        self.assertEqual(code, 0, se)
        for m in ALLOWED_MARKERS + FORBIDDEN_MARKERS + ["Mark01", "MARK_second", "MK_second_submission", "MK_report_id",
                                                         "MK_report_second", "MK_hidden_c", "MK_city", "MK_describe",
                                                         "writer@example.org", "tester@example.org", "Writer Name",
                                                         "signup@example.org", "Signup Name", PASS]:
            self.assertNotIn(m, so + se, m)
        self.assertIn("== submission en", so)
        self.assertIn("str", so)
        self.assertIn(w.sub["en"].qid("dish_name"), so)
        self.assertIn(w.rep["en"].uuid("recipe"), so)             # a uuid key is structure
        self.assertFalse(out.exists())                            # writes nothing

    def test_shapes_for_a_form_without_submissions(self):
        code, so, se, out = run(fixturelib.World(), argv_extra=["shapes"])
        self.assertEqual(code, 0, se)
        self.assertEqual(so.count("no completed submission"), 9)


class TempDirs(unittest.TestCase):
    # MUTATION: go back to tempfile.mkdtemp() in the helpers: the leftover directory fails this test.
    def test_tests_leave_no_temporary_directories_behind(self):
        scratch = tmpdir()
        env = dict(os.environ, TMPDIR=str(scratch))
        res = subprocess.run([sys.executable, "-m", "unittest", "tests.test_build.HideList",
                              "tests.test_build.Masking", "tests.test_build.DeployGuard",
                              "tests.test_build.EmptyAndKeyless"],
                             cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(list(scratch.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
