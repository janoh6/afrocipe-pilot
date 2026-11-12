"""Regenerates tests/fixtures (the committed preview data). Run: python3 tests/make_fixtures.py"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixturelib  # noqa: E402

out = Path(__file__).resolve().parent / "fixtures"
shutil.rmtree(out, ignore_errors=True)
world, hidden = fixturelib.standard_world()
world.write(out)
(out / "hidden.txt").write_text("# preview hide list\n" + "\n".join(hidden) + "\n", encoding="utf-8")
print("wrote", len(list(out.iterdir())), "files")
