#!/usr/bin/env python3
"""Deploy guards, run by the workflow (Python 3 standard library only).

  deploy_guard.py shrink --status HTTP --live FILE --new FILE [--allow-empty-deploy true]
      Fail when the live site lists recipes and the new build lists none.
  deploy_guard.py first-deploy --status HTTP [--allow-empty-first-deploy true]
      Say whether a keyless, empty first deploy is allowed: only while no live recipes.json exists (404).

--status is the HTTP status curl got for the live data/recipes.json. Nothing here prints a recipe.
"""
import argparse
import json
import sys
from pathlib import Path


def count_recipes(path):
    """Number of recipes in a recipes.json file; None if it is missing or not a list."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return len(data) if isinstance(data, list) else None


def shrink_decision(status, live_count, new_count, allow_empty_deploy=False):
    """(ok, message). `status` is the live file's HTTP status (int); counts may be None when unreadable."""
    if allow_empty_deploy:
        return True, "ALLOW_EMPTY_DEPLOY is true: deploying without the shrink check."
    if status == 404:
        return True, "No live recipes.json yet (404): first deploy."
    if status != 200:
        return False, "Could not read the live recipes.json (HTTP %s). Not deploying." % status
    if live_count is None:
        return False, "The live recipes.json is not a recipe list. Not deploying."
    if new_count is None:
        return False, "The new recipes.json is missing or not a recipe list. Not deploying."
    if live_count > 0 and new_count == 0:
        return False, "The live site lists %d recipes and this build lists 0. Not deploying. (Set ALLOW_EMPTY_DEPLOY to true to override.)" % live_count
    return True, "Live: %d recipes, new build: %d." % (live_count, new_count)


def first_deploy_ok(status, allow_empty_first_deploy=False):
    """A keyless empty first deploy is allowed only when the flag is on and nothing is live yet."""
    return bool(allow_empty_first_deploy) and status == 404


def truthy(value):
    return str(value or "").strip().lower() == "true"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["shrink", "first-deploy"])
    ap.add_argument("--status", type=int, default=0)
    ap.add_argument("--live")
    ap.add_argument("--new")
    ap.add_argument("--allow-empty-deploy", default="")
    ap.add_argument("--allow-empty-first-deploy", default="")
    a = ap.parse_args(argv)
    if a.command == "first-deploy":
        ok = first_deploy_ok(a.status, truthy(a.allow_empty_first_deploy))
        print("first deploy allowed" if ok else
              "An empty first deploy needs ALLOW_EMPTY_FIRST_DEPLOY=true and no live recipes.json (HTTP status was %d)." % a.status)
        return 0 if ok else 1
    live = count_recipes(a.live) if a.live and a.status == 200 else None
    ok, msg = shrink_decision(a.status, live, count_recipes(a.new) if a.new else None, truthy(a.allow_empty_deploy))
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
