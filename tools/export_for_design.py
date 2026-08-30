"""Hand the design side a snapshot that is current, so the next zip cannot revert anything.

    python tools/export_for_design.py

WHAT THIS IS FOR.

Eleven handovers in a row have arrived reverting committed work — 142 lines
across 17 commits in the last one alone. Two tools already exist for the
aftermath: repair_handover.py puts back the regressions that have happened
before, and check_handover.py names the ones that have not. Both are cures.

The disease is one sentence: the zip is built from a copy of the tree that has
fallen behind. GET-LATEST.bat in this folder says so out loud — "your files are
12 versions behind the live site". A file edited from a stale copy carries every
line that copy was missing, so installing it is a revert of everything committed
to that file since, whether or not anybody touched those lines.

So this is the other end. It writes a zip of the design surface AS IT IS ON
MAIN RIGHT NOW, and refuses to write one that is already out of date. Start each
round of design work from this and there is nothing to repair afterwards,
because there is nothing behind to revert to.

WHAT IT REFUSES, AND WHY EACH REFUSAL IS THE POINT.

  - A tree with uncommitted changes. Those changes are not on main, so a design
    round built on them would hand back files carrying work nobody else has,
    and installing it would look like an unrelated revert.
  - A tree behind origin. This is the actual fault, caught at the only moment
    it is cheap: before the copy is made rather than after it comes back.
  - A tree ahead of origin. Unpushed work is invisible to everyone else, and a
    zip built on it hands out a future nobody can see.

Every one of those is fixed by the same two commands the message prints. None
of them is overridable, because an override is how a snapshot gets taken from
a stale tree at four in the afternoon when somebody is in a hurry — which is
exactly how all eleven happened.

WHICH FILES. Not a list kept by hand. It asks git which files past handovers
actually delivered, the same way check_handover.py asks git which commit put a
line there. A file the design side starts touching is in the next export
without anybody remembering to add it.
"""
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "gudanes-design-current.zip")

# Commits that installed a handover. Matched on the subject because that is
# what the convention has actually been for sixteen of them.
HANDOVER = re.compile(r"^(final_\d+|install(ing)?\b.*handover|.*handover.*install)",
                      re.I)

# Never handed out, whatever history says a handover once touched. app.py is
# the other agent's and the owner's; the suite is what catches a bad handover,
# so shipping it out and back is how a check gets quietly softened.
NEVER = ("app.py", "tests/", "tools/", "translations.py", ".env", "gudanes_hr.db")


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def design_surface():
    """Every file a past handover delivered, asked of git rather than listed."""
    log = git("log", "--format=%H|%s").splitlines()
    files = set()
    for line in log:
        sha, _, subject = line.partition("|")
        if not HANDOVER.match(subject.strip()):
            continue
        for name in git("show", "--name-only", "--format=", sha).splitlines():
            name = name.strip()
            if name and not any(name == n or name.startswith(n) for n in NEVER):
                files.add(name)
    return sorted(f for f in files if os.path.exists(os.path.join(ROOT, f)))


def refuse_if_not_current():
    """Stop unless this tree is exactly main. Every branch here is the bug."""
    problems = []

    dirty = [l for l in git("status", "--porcelain").splitlines()
             if l and not l.startswith("??")]
    if dirty:
        problems.append(
            f"{len(dirty)} uncommitted change(s). A copy taken now carries work "
            "that is not on main, and handing it back would read as a revert of "
            "something nobody recognises.")

    git("fetch", "--quiet", "origin", check=False)
    try:
        behind = len([l for l in git("log", "--oneline", "HEAD..origin/main").splitlines() if l])
        ahead = len([l for l in git("log", "--oneline", "origin/main..HEAD").splitlines() if l])
    except RuntimeError:
        problems.append("could not compare against origin/main — is there a remote?")
        behind = ahead = 0

    if behind:
        problems.append(
            f"{behind} commit(s) behind origin/main. This is the fault itself: "
            "a design round started here would hand back files missing "
            f"everything in those {behind} commits.")
    if ahead:
        problems.append(
            f"{ahead} commit(s) ahead of origin/main. Unpushed work is invisible "
            "to everyone else, so a zip built on it hands out a future nobody "
            "else can see.")

    if not problems:
        return
    print("NOT EXPORTING.\n")
    for p in problems:
        print(f"  - {p}")
    print("\nFix it with:\n")
    print("    git status                  # deal with anything uncommitted")
    print("    git pull --rebase origin main")
    print("    git push origin main        # only if you are ahead")
    print("\nThen run this again. There is deliberately no way to override "
          "this;\nevery handover that had to be repaired was taken from a tree "
          "in one of\nthese states.")
    sys.exit(1)


README = """These files are the château's public pages, exactly as they are live
right now — taken from main at {when}, at commit {sha}.

TWO RULES, AND THE SECOND IS THE ONE THAT KEEPS BEING BROKEN.

1. Start from these. Not from an older copy, not from what you had last time.
   Every file here is current.

2. Send back ONLY the files you actually changed.

Rule 2 is not tidiness. The last eleven handovers each arrived containing every
file whether or not it had been touched, and each untouched file was written
from a copy that had fallen behind — so installing them reverted work nobody
had asked to revert. The most recent undid 142 lines across 17 commits,
including a fix made an hour earlier. Nothing in the design was wrong; the
files simply carried an older version of everything else in them.

If a file is not in your reply, nothing about it changes. That is the whole
mechanism.

WHAT IS NOT HERE, and please do not add it: app.py, the tests, and the tools
directory. The application logic and the checks that catch a bad handover are
not part of the design surface, and a check that travels out and back is a
check that can quietly come back softer.

{count} files.
"""


def main():
    refuse_if_not_current()
    files = design_surface()
    if not files:
        print("No design surface found in history — has anything been handed over yet?")
        return 1

    sha = git("rev-parse", "--short", "HEAD").strip()
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(os.path.join(ROOT, rel), rel)
        z.writestr("READ-ME-FIRST.txt",
                   README.format(when=when, sha=sha, count=len(files)))

    size = os.path.getsize(OUT) / 1024
    print(f"Wrote {os.path.basename(OUT)} — {len(files)} files, {size:.0f} KB, "
          f"from main at {sha}.\n")
    print("Send this to whoever does the design work, and ask them to send back")
    print("only the files they changed. The note inside says the same thing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
