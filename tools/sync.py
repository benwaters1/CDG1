"""Fetch, merge, prove, push. The loop that has failed by hand every time.

    python tools/sync.py

Two agents push to this repository. Every push from here has been rejected at
least once because the other one landed work first, and the fix has been the
same five commands every time: fetch, merge, resolve nothing, run the suite,
push. Doing it by hand means it gets skipped, and the interesting half — did
their work and ours still agree? — is the half that gets skipped first.

WHAT THIS WILL NOT DO.

It will not push a red suite. That is the whole point of it: the danger in
automating a merge is that it makes shipping easier than checking, and `main`
deploys to production on push. A conflict, a crashed suite or one failed check
stops it and prints what to look at.

It will not resolve a conflict. Telling two people's edits apart is a
judgement about intent, and a script that guessed would be wrong in the
direction nobody notices. It stops and names the files.

It will not commit your working tree. Uncommitted changes stop it before the
fetch, because merging on top of them produces a state nobody chose.

    --no-push     do everything except the push
    --allow-red   push even if checks failed. Prints what it is overriding.
"""
import argparse
import re
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args, check=True):
    r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        print("  git %s failed:\n%s" % (" ".join(args), (r.stderr or r.stdout).strip()))
        sys.exit(1)
    return r


def say(step, detail=""):
    print("%-26s %s" % (step, detail))


def stop(why, detail=""):
    print("\nSTOPPED: %s" % why)
    if detail:
        print(detail)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--allow-red", action="store_true")
    args = ap.parse_args()

    # 1. A dirty tree is somebody mid-thought. Merging over it produces a
    #    state nobody chose and a diff nobody can read afterwards.
    dirty = [l for l in git("status", "--porcelain").stdout.splitlines()
             if not l.startswith("??")]
    if dirty:
        stop("there are uncommitted changes",
             "\n".join("  " + l for l in dirty[:12])
             + "\n\nCommit or stash them first — a merge on top of them is a "
               "state nobody chose.")

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    say("on branch", branch)

    git("fetch", "origin")

    # A branch nobody has pushed yet has nothing to compare against. Running
    # the suite on it is still worth doing; pushing it is a decision about
    # where the work should live, which is not this script's to make.
    upstream = git("rev-parse", "--verify", "--quiet", "origin/%s" % branch,
                   check=False)
    if upstream.returncode != 0:
        say("no counterpart on origin", "for %s" % branch)
        print("  Running the suite anyway; nothing will be pushed.")
        print()
        behind = ahead = 0
        args.no_push = True
    else:
        counts = git("rev-list", "--left-right", "--count",
                     "origin/%s...HEAD" % branch).stdout.split()
        behind, ahead = int(counts[0]), int(counts[1])
        say("behind / ahead", "%d / %d" % (behind, ahead))

    if upstream.returncode == 0 and not behind and not ahead:
        say("nothing to do", "already in sync")
        return

    if behind:
        landed = git("log", "--oneline", "HEAD..origin/%s" % branch).stdout.strip()
        print("\n  they landed:")
        for line in landed.splitlines()[:10]:
            print("    " + line)
        print()
        merge = git("merge", "origin/%s" % branch, "--no-edit", check=False)
        conflicts = [l[3:] for l in git("status", "--porcelain").stdout.splitlines()
                     if l[:2] in ("UU", "AA", "DU", "UD", "AU", "UA", "DD")]
        if conflicts:
            stop("the merge conflicts in %d file(s)" % len(conflicts),
                 "\n".join("  " + c for c in conflicts)
                 + "\n\nResolve them by hand. Telling two people's edits apart "
                   "is a judgement about intent, and a script that guessed "
                   "would be wrong in the direction nobody notices.")
        if merge.returncode != 0:
            stop("the merge failed", (merge.stderr or merge.stdout).strip())
        say("merged", "cleanly")

    # 2. The half that matters. Their work and ours have never been run
    #    together before this moment, and this is the only place it is
    #    checkable.
    say("running the suite", "(this is the slow part)")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    suite = subprocess.run([sys.executable, "tests/run.py"], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
    out = suite.stdout

    total = re.search(r"(\d+)/(\d+) checks passed across (\d+) suite", out)
    crashed = [l.strip() for l in out.splitlines() if l.strip().startswith("CRASHED")]
    failed = [l.strip()[2:] for l in out.splitlines() if l.strip().startswith("- ")]

    # The positive control has to have fired, or a green run proves nothing.
    proven = "harness reports failures" in out
    if not proven:
        stop("the suite did not report its own self-check",
             "Without the deliberate failing check, a green run is unproven "
             "rather than clean.")

    if total:
        say("checks", "%s of %s across %s suites"
            % (total.group(1), total.group(2), total.group(3)))
    else:
        stop("could not read a result out of the suite", out[-800:])

    if crashed or failed:
        detail = ""
        if crashed:
            detail += "\n".join("  " + c for c in crashed) + "\n"
        detail += "\n".join("  - " + f for f in failed[:15])
        if args.allow_red:
            print("\n  OVERRIDING %d failure(s) and %d crash(es):"
                  % (len(failed), len(crashed)))
            print(detail)
        else:
            stop("%d check(s) failed and %d suite(s) crashed"
                 % (len(failed), len(crashed)),
                 detail + "\n\nmain deploys on push. Fix these, or re-run with "
                          "--allow-red if you know what you are shipping.")

    if args.no_push:
        say("not pushing", "--no-push")
        return

    push = git("push", "origin", branch, check=False)
    if push.returncode != 0:
        # Somebody landed work in the seconds since the fetch. Say so plainly
        # rather than retrying in a loop: another run does the whole thing
        # properly, including the suite.
        stop("the push was rejected — somebody landed work while this ran",
             (push.stderr or push.stdout).strip() + "\n\nRun this again.")
    say("pushed", (push.stderr or push.stdout).strip().splitlines()[-1]
        if (push.stderr or push.stdout).strip() else "ok")


if __name__ == "__main__":
    main()
