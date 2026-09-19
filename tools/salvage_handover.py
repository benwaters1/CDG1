"""Take only what a stale handover actually adds, and refuse the rest.

    python tools/salvage_handover.py <zip>            # report
    python tools/salvage_handover.py <zip> --write    # write what is salvageable

THE PROBLEM THIS EXISTS FOR, STATED ONCE.

check_handover.py names what a zip would revert. export_for_design.py stops a
stale zip being made. Between the two there is a case neither covers: a zip
that IS stale, cannot be installed, and still contains real new work that would
otherwise be thrown away with it.

Three exports in two days arrived from a tree 107 commits behind after the
design side's container was reset and their working directory restored from an
old zip. Every one of them was unusable as a whole and every one of them
carried something worth keeping -- new partials the first time, 977 lines of
stylesheet the second, a refined component block the third. Each was pulled out
by hand, and doing the same analysis a third time is the point at which it
stops being analysis and starts being a script.

WHAT IT DOES.

For every file in the zip it asks one question: measured against HEAD, does
this file ADD anything, and does it REMOVE anything?

  - New file, nowhere in the tree      -> salvageable, whole
  - Identical to HEAD                  -> nothing to do
  - Adds and removes nothing but noise -> nothing to do
  - Removes lines HEAD has             -> REFUSED, and named
  - Adds lines, removes nothing        -> salvageable, whole

A file that both adds and removes is the interesting one and it is NOT
installed. It is reported with both counts, and for text that is safe to split
-- CSS is the case that keeps happening -- the added runs alone are written to
a salvage directory so they can be used without the removals riding along.

WHAT IT WILL NOT DO.

It will not write into templates/ or static/. Everything salvageable goes to
`pending-design/salvage-<date>/`, because a file pulled out of a stale export
still has to be read by a person before it is wired to anything -- the last
three carried macros reading columns that do not exist and a premise about a
page that had not been true for months.

It will not split a file whose syntax it cannot check. A CSS block can be
verified balanced before it is written; a template cannot be cut on a diff
boundary without risking a broken tag, so a partial template is reported and
left alone.

And it does not decide anything. Nothing it writes is installed by running it.
"""
import argparse
import datetime
import difflib
import io
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Enough of a difference to be worth reporting as a run rather than noise. A
# one-line change against a hundred-line deletion is a collapsed file, not an
# edit, and the count below is what tells those apart at a glance.
MIN_RUN = 3
SPLITTABLE = (".css",)


def head_text(path):
    """The file as it is on HEAD, or None if HEAD has never had it."""
    r = subprocess.run(["git", "show", "HEAD:" + path], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return None if r.returncode else r.stdout.replace("\r\n", "\n")


def balanced(css):
    """True when every block in this CSS closes. Comments are blanked first
    so a brace inside one does not count -- which it did, the first time this
    check was written by hand, and reported a healthy file as broken."""
    clean = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return clean.count("{") == clean.count("}")


def whole_rules(css):
    """True when this run is CSS somebody could paste, not a piece of a rule.

    Balanced is not enough and the difference is easy to miss: a run of bare
    declarations -- `padding: 1px;` and two more, taken from the middle of
    somebody else's rule -- has no braces at all, so it balances vacuously,
    0 against 0. Dropped into a stylesheet on its own it is either ignored or
    it attaches itself to whatever rule happens to precede it.

    So a usable run has to close every block AND open at least one. Found by
    a negative control that stayed green when the widening was deleted: the
    fixture's run had no braces, so removing the thing that fixes unbalanced
    runs changed nothing.
    """
    clean = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return clean.count("{") == clean.count("}") and clean.count("{") > 0


def added_runs(ours, theirs):
    """Contiguous runs the zip has and HEAD does not, each widened until its
    blocks close. A diff boundary does not respect a rule boundary: the first
    hand-written version of this cut two rules in half and left the file two
    braces open, which is the exact fault that had been silently eating rules
    in the live stylesheet the week before."""
    sm = difflib.SequenceMatcher(None, ours, theirs, autojunk=False)
    runs = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag not in ("insert", "replace") or (j2 - j1) < MIN_RUN:
            continue
        start, end = j1, j2
        guard = 0
        # Backwards first: a run that begins mid-rule needs its selector.
        while start > 0 and not whole_rules("\n".join(theirs[start:end])) and guard < 400:
            start -= 1
            guard += 1
        # Then forwards, for one that ends before its closing brace.
        while end < len(theirs) and not whole_rules("\n".join(theirs[start:end])) and guard < 800:
            end += 1
            guard += 1
        # Still not whole after both: it is a fragment of somebody else's rule
        # and there is no honest way to hand it over. Dropped rather than
        # written, and the file it came from is still reported.
        if whole_rules("\n".join(theirs[start:end])):
            runs.append(theirs[start:end])
    return runs


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("zip")
    ap.add_argument("--write", action="store_true",
                    help="write what is salvageable into pending-design/")
    ap.add_argument("--out", default=None,
                    help="where to write it. Defaults to "
                         "pending-design/salvage-<date>. The suite points this "
                         "at a temporary directory, because a test that writes "
                         "into the default one deletes whatever a real run "
                         "left there — which it did, taking two committed "
                         "files with it.")
    args = ap.parse_args()

    if not os.path.exists(args.zip):
        print("No such zip: %s" % args.zip)
        return 2

    z = zipfile.ZipFile(args.zip)
    names = sorted(n for n in z.namelist() if not n.endswith("/"))

    brand_new, add_only, mixed, refused, unchanged = [], [], [], [], []
    for name in names:
        try:
            theirs = z.read(name).decode("utf-8")
        except UnicodeDecodeError:
            continue                       # binary: not ours to reason about
        theirs = theirs.replace("\r\n", "\n")
        ours = head_text(name)
        if ours is None:
            brand_new.append(name)
            continue
        if ours == theirs:
            unchanged.append(name)
            continue
        d = list(difflib.unified_diff(ours.split("\n"), theirs.split("\n"),
                                      n=0, lineterm=""))
        a = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
        r = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
        if r == 0:
            add_only.append((name, a))
        elif a == 0:
            refused.append((name, a, r))
        else:
            mixed.append((name, a, r))

    print("%-34s %s" % ("zip", os.path.basename(args.zip)))
    print("%-34s %d file(s)" % ("read", len(names)))
    print("%-34s %d" % ("identical to HEAD", len(unchanged)))
    print()

    if brand_new:
        print("NEW -- nowhere in the tree, salvageable whole:")
        for n in brand_new:
            print("   %s" % n)
        print()
    if add_only:
        print("ADDS ONLY -- salvageable whole:")
        for n, a in add_only:
            print("   %-52s +%d" % (n, a))
        print()
    if mixed:
        print("ADDS AND REMOVES -- not installed; added runs salvaged where the")
        print("syntax can be checked:")
        for n, a, r in sorted(mixed, key=lambda x: -x[1]):
            print("   %-52s +%-6d -%d" % (n, a, r))
        print()
    if refused:
        total = sum(x[2] for x in refused)
        print("REMOVES ONLY -- %d file(s), %d line(s). Nothing to salvage."
              % (len(refused), total))
        for n, _a, r in sorted(refused, key=lambda x: -x[2])[:6]:
            print("   %-52s -%d" % (n, r))
        if len(refused) > 6:
            print("   ... and %d more" % (len(refused) - 6))
        print()

    if not args.write:
        print("Nothing written. Run again with --write to salvage.")
        print("Run tools/check_handover.py after installing anything, and")
        print("tools/export_for_design.py to stop the next one being stale.")
        return 0

    out = args.out or os.path.join(
        ROOT, "pending-design",
        "salvage-" + datetime.date.today().isoformat())
    os.makedirs(out, exist_ok=True)
    written = 0

    for name in brand_new + [n for n, _a in add_only]:
        dest = os.path.join(out, name.replace("/", "__"))
        io.open(dest, "wb").write(z.read(name))
        written += 1

    for name, _a, _r in mixed:
        if not name.endswith(SPLITTABLE):
            continue
        ours = head_text(name).split("\n")
        theirs = z.read(name).decode("utf-8").replace("\r\n", "\n").split("\n")
        runs = added_runs(ours, theirs)
        if not runs:
            continue
        body = ("/* Added runs only, salvaged from %s.\n"
                "   The rest of that file REMOVES lines this tree has, so it was\n"
                "   not installed. Nothing below is edited except the run\n"
                "   boundaries, widened until each block closes. */\n\n"
                % os.path.basename(args.zip))
        body += "\n\n".join("\n".join(r) for r in runs) + "\n"
        if name.endswith(".css") and not balanced(body):
            print("   refusing to write %s -- the salvaged runs do not balance"
                  % name)
            continue
        # FLATTENED, deliberately. Keeping the real path would leave
        # pending-design/salvage-<date>/static/gudanes.css sitting one move
        # away from the live file, which is exactly the accident this whole
        # tool exists to prevent.
        dest = os.path.join(out, name.replace("/", "__"))
        io.open(dest, "w", encoding="utf-8", newline="\r\n").write(body)
        written += 1

    print("Wrote %d file(s) to %s" % (written, os.path.relpath(out, ROOT)))
    print("Nothing was installed. Read them before wiring anything up -- the")
    print("last three exports carried macros reading columns that do not exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
