"""Name every piece of committed work a handover zip would undo, before you commit it.

Run this straight after unzipping a `gudanes-final_NN.zip` over the tree and
BEFORE committing:

    python tools/check_handover.py

It prints, for each commit whose work the unzipped files would remove, what that
commit was called and which lines are going. Exit status is 1 if anything is
undone, so it can gate a commit.

WHY THIS EXISTS, given repair_handover.py is right next door.

repair_handover.py puts back eight specific things, because those eight have
each been reverted more than once. That list only ever grows, and it only ever
covers regressions that have ALREADY happened at least once - by definition it
is blind to the next one. Nine handovers in, the ninth still arrived needing
eleven repairs found by hand.

This takes the general form of the problem instead. A handover zip is generated
from a snapshot of the tree, so installing it is a revert of everything
committed to those files since that snapshot. That is not a list to maintain;
it is a thing to compute. For every line the unzipped file removes, `git blame`
says which commit put it there. So the tool needs no knowledge of noindex, or
part-payments, or the manage_token parameter - it finds the ninth regression and
the fiftieth for the same reason it finds the first.

WHAT IT DOES NOT DO. It does not put anything back. A handover carries real new
design work in the same files as the reverts, and telling those apart is a
judgement about intent that belongs to a person. This names the loss precisely
enough to make that judgement quick; `repair_handover.py` is still what applies
the eight known repairs, and the suite is still what catches breakage in
behaviour rather than in text.

AND IT IS STILL A CURE. tools/export_for_design.py is the prevention: it gives
the design side a current snapshot and refuses to make one from a stale tree,
so there is nothing behind for a handover to revert to. Run this anyway — but
if it keeps finding things, the export is not being used.

It also does not read the zip. It reads the working tree against HEAD, so it
sees exactly what is about to be committed however the files got there.
"""
import os
import re
import subprocess
import sys
from collections import defaultdict

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")
BLAME_HEAD = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)")
# A deletion of nothing but punctuation or a closing tag is what reformatting
# looks like, not what losing a feature looks like. Counted, but not quoted.
TRIVIAL = re.compile(r"^[\s{}()\[\];,<>/*+-]*$")


def git(*args):
    """Run git and return stdout, or "" if the command has nothing to say."""
    proc = subprocess.run(("git",) + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.stdout if proc.returncode == 0 else ""


def changed_files():
    """Tracked files the working tree has modified relative to HEAD.

    Added and deleted files are excluded: a file the zip introduces cannot be
    undoing anything, and a deletion is loud enough to notice on its own.
    """
    out = git("diff", "--name-only", "--diff-filter=M", "HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()]


def removed_line_numbers(path):
    """Line numbers IN HEAD'S VERSION that the working tree no longer has.

    --ignore-all-space so that a handover which merely reindents a block is not
    reported as deleting it; that is the single biggest source of noise here,
    and reindentation is exactly what these zips do most.
    """
    out = git("diff", "-U0", "--ignore-all-space", "HEAD", "--", path)
    numbers = []
    for line in out.splitlines():
        m = HUNK.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = 1 if m.group(2) is None else int(m.group(2))
        numbers.extend(range(start, start + count))
    return numbers


def blame(path):
    """{line number in HEAD: (sha, subject)} for one file."""
    out = git("blame", "--line-porcelain", "HEAD", "--", path)
    by_line, subjects = {}, {}
    sha = None
    for line in out.splitlines():
        m = BLAME_HEAD.match(line)
        if m:
            sha, final = m.group(1), int(m.group(2))
            by_line[final] = sha
        elif line.startswith("summary ") and sha:
            subjects.setdefault(sha, line[len("summary "):].strip())
    return {n: (s, subjects.get(s, "")) for n, s in by_line.items()}


def head_lines(path):
    out = git("show", f"HEAD:{path}")
    return out.splitlines()


def report_drift():
    """Say which exported files moved on after the snapshot went out.

    This runs before anything else because it is the only part of the report
    that is knowable BEFORE the zip is opened, and because it explains most of
    what the rest of the report is about to say. A handover is written against
    a moment; the tree does not stop at that moment; every file that moved
    since comes back carrying the version the design side was given.

    Handover 33 is the worked example. Snapshot at 19:51 from a clean tree,
    four commits on those same files by 23:01, and back came templates without
    the noindex layer, without the balance a guest owes, without part-payment
    and without the field that makes the arrival text possible. Nothing was
    done wrong at either end. This paragraph is what would have taken four
    hours off finding out.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from export_for_design import drift_since_export
    except Exception:
        return                      # export tool not present; nothing to say
    try:
        man, moved = drift_since_export()
    except Exception as exc:
        print(f"(could not read the export manifest: {exc})\n")
        return
    if not man:
        return
    print(f"The last export went out at {man['taken_at']}, from {man['commit']}.")
    if not moved:
        print("Nothing has changed in those files since. A handover built from "
              "it\nis working against the current tree.\n")
        return
    print(f"\n{len(moved)} of those files have MOVED SINCE. Anything the zip "
          "contains for\nthese arrives written against the older version, so "
          "expect what is below\nto be missing and put it back:\n")
    for path, commits in moved:
        print(f"  {path}")
        for c in commits[:4]:
            print(f"      {c}")
        if len(commits) > 4:
            print(f"      ... and {len(commits) - 4} more")
    print("\n  git diff " + man["commit"] + " -- <file>    # what it will not have\n")


def main(argv):
    only = argv[1:]
    if not git("rev-parse", "--git-dir"):
        print("Not a git repository.")
        return 2

    report_drift()

    files = changed_files()
    if only:
        files = [f for f in files if any(o in f for o in only)]
    if not files:
        print("Nothing modified against HEAD — nothing to check.")
        return 0

    # commit -> {"subject":…, "files": {path: [lines]}, "n": int}
    undone = defaultdict(lambda: {"subject": "", "files": defaultdict(list), "n": 0})
    checked = 0
    for path in files:
        removed = removed_line_numbers(path)
        if not removed:
            continue
        checked += 1
        who = blame(path)
        source = head_lines(path)
        for n in removed:
            sha, subject = who.get(n, (None, ""))
            if not sha:
                continue
            entry = undone[sha]
            entry["subject"] = subject
            entry["n"] += 1
            text = source[n - 1] if 0 < n <= len(source) else ""
            entry["files"][path].append((n, text))

    print(f"Checked {len(files)} modified file(s); {checked} remove something.\n")
    if not undone:
        print("No committed work is undone by what is in the tree.")
        print("(New content on top of what is already there is fine — that is a "
              "handover doing its job.)")
        return 0

    # Most recent first: the newest commit is the likeliest thing the zip's
    # snapshot predates, and the likeliest to be an unnoticed revert.
    order = git("rev-list", "--no-walk", *undone.keys()) if len(undone) > 1 else ""
    ranked = [s for s in order.splitlines() if s in undone] or list(undone)
    for sha in undone:
        if sha not in ranked:
            ranked.append(sha)

    total = sum(e["n"] for e in undone.values())
    print(f"This tree would UNDO work from {len(undone)} commit(s), "
          f"{total} line(s) in all:\n")
    for sha in ranked:
        e = undone[sha]
        print(f"  {sha[:9]}  {e['subject'] or '(no subject)'}")
        print(f"             {e['n']} line(s) across {len(e['files'])} file(s)")
        for path, lines in sorted(e["files"].items()):
            quotable = [t for _n, t in lines if t.strip() and not TRIVIAL.match(t)]
            print(f"               {path} ({len(lines)})")
            for text in quotable[:2]:
                print(f"                 - {text.strip()[:96]}")
        print()

    print("Each line above is in HEAD and not in the tree. Some of it will be a")
    print("deliberate rewrite; a handover is allowed to change the design. What")
    print("it is not allowed to do is quietly take back a fix, and the commit")
    print("subjects above are how you tell which is which.")
    print()
    print("To put one file back exactly as it is on main:")
    print("    git checkout HEAD -- <path>          # one path at a time, never .")
    print("Then re-run this, run tools/repair_handover.py, and run the suite.")
    return 1


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(main(sys.argv))
