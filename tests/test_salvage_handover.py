"""Taking only what a stale handover adds, and refusing the rest.

Three exports arrived in two days from a tree 107 commits behind, after the
design side's container was reset and their working directory restored from an
old zip. Every one was unusable whole and every one carried something worth
keeping — new partials, then 977 lines of stylesheet, then a refined component
block. Each was pulled out by hand, and the third time is where that stops
being analysis and becomes a script.

check_handover.py names what a zip would revert. export_for_design.py stops a
stale zip being made. This is the case between them: a zip that IS stale,
cannot be installed, and still has real work in it.

WHAT HAS TO HOLD, AND WHY EACH IS A WAY OF BEING WRONG THAT LOOKS LIKE WORKING.

  A FILE THAT REMOVES ANYTHING IS REFUSED. That is the whole point. The third
  export would have dropped a macro parameter the live call site passes, which
  is a page that raises rather than a page that looks odd.

  A RUN IS WIDENED UNTIL ITS BLOCKS CLOSE. A diff boundary does not respect a
  rule boundary. The hand-written version of this cut two CSS rules in half and
  left the file two braces open — which is exactly the fault that had been
  silently eating rules in the live stylesheet the week before, where an
  unclosed rule swallows everything after it and the browser says nothing.

  IT WRITES NOWHERE NEAR templates/ OR static/. Everything salvaged lands in
  pending-design/, because a file pulled out of a stale export still has to be
  read: the last three carried macros reading columns that do not exist and a
  premise about a page that had not been true for months.

  AND NOTHING IS WRITTEN WITHOUT --write. A tool that installs on the same
  command that reports is a tool nobody runs twice.
"""
from _harness import Suite

import io
import os
import subprocess
import sys
import zipfile

import _harness

m = _harness.m
ROOT = _harness.ROOT
TOOL = os.path.join(ROOT, "tools", "salvage_handover.py")


def _run(zip_path, *args):
    r = subprocess.run([sys.executable, TOOL, zip_path] + list(args),
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def _zip(tmp, files):
    path = os.path.join(tmp, "handover.zip")
    with zipfile.ZipFile(path, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return path


def run():
    s = Suite("taking only what a stale handover adds")
    import tempfile
    tmp = tempfile.mkdtemp(prefix="gudanes_test_salvage_")

    head_css = subprocess.run(
        ["git", "show", "HEAD:static/gudanes.css"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout

    s.section("It reads the zip against HEAD")

    # A file exactly as HEAD has it has nothing to say about it.
    same = _zip(tmp, {"static/gudanes.css": head_css})
    out = _run(same)
    s.check("a file identical to HEAD is counted as such",
            "identical to HEAD                  1" in out, detail=out[:200])
    s.check("and nothing is offered to salvage",
            "ADDS ONLY" not in out and "NEW —" not in out, detail=out[:200])

    s.section("A file that only adds is salvageable whole")

    added = head_css + "\n.zzsalvage-new{ color: red; }\n"
    addonly = _zip(tmp, {"static/gudanes.css": added})
    out = _run(addonly)
    s.check("it is named as adding only", "ADDS ONLY" in out, detail=out[:300])
    s.check("and not as removing anything", "REMOVES ONLY" not in out)

    s.section("A file that removes anything is refused")

    # THE ONE THAT MATTERS. The third export dropped a macro parameter the live
    # call site passes -- a page that raises, not a page that looks odd.
    cut = "\n".join(head_css.split("\n")[:-40])
    removes = _zip(tmp, {"static/gudanes.css": cut})
    out = _run(removes)
    s.check("a file that only removes is refused", "REMOVES ONLY" in out,
            detail=out[:300])
    s.check("and nothing from it is offered as salvageable",
            "ADDS ONLY" not in out, detail="a file that takes something away is "
                                           "not a file to install a piece of")

    # Block-sized on purpose. A real handover adds BLOCKS; a one- or two-line
    # difference is the collapse signature rather than an edit, which is what
    # MIN_RUN exists to ignore — so a two-line fixture would never reach the
    # salvage path at all and would prove nothing about it.
    BLOCK = ("\n.zzsalvage-mixed{\n"
             "  color: blue;\n"
             "  padding: 2px;\n"
             "}\n"
             ".zzsalvage-mixed b{ font-weight: 600; }\n")
    mixed = _zip(tmp, {"static/gudanes.css": cut + BLOCK})
    out = _run(mixed)
    s.check("one that adds AND removes is reported with both counts",
            "ADDS AND REMOVES" in out, detail=out[:300])

    s.section("Nothing is written without being asked")

    before = set(os.listdir(os.path.join(ROOT, "pending-design"))) \
        if os.path.isdir(os.path.join(ROOT, "pending-design")) else set()
    out = _run(mixed)
    s.check("a plain run says so", "Nothing written" in out, detail=out[-200:])
    after = set(os.listdir(os.path.join(ROOT, "pending-design"))) \
        if os.path.isdir(os.path.join(ROOT, "pending-design")) else set()
    s.check("and writes nothing at all", before == after,
            detail="a tool that installs on the same command that reports is "
                   "one nobody runs twice")

    s.section("Its own output does not break whoever is reading it")

    # It printed an em-dash, and printing one to a Windows console raises
    # UnicodeEncodeError -- which killed the tool mid-report, before it ever
    # reached the write loop. The salvage directory was created and left empty
    # and the run looked like a tool that had found nothing.
    out = _run(mixed)
    s.check("every line it prints is plain ASCII",
            all(ord(c) < 128 for c in out),
            detail=repr([c for c in out if ord(c) >= 128][:6])
                   + " — a tool whose stdout crashes the caller's decoder "
                     "fails on the machine it is needed on")

    s.section("What it writes, when asked")

    out = _run(mixed, "--write")
    s.check("it says where it put things", "Wrote" in out, detail=out[-300:])
    made = [d for d in os.listdir(os.path.join(ROOT, "pending-design"))
            if d.startswith("salvage-")]
    s.check("into pending-design, not templates or static", bool(made),
            detail=str(os.listdir(os.path.join(ROOT, "pending-design"))))
    written, d = [], None
    if made:
        d = os.path.join(ROOT, "pending-design", made[0])
        # Files only. A broken flattening leaves a DIRECTORY here -- which is
        # the very thing the last check in this section is about -- and
        # opening one raises PermissionError rather than failing a check.
        written = sorted(f for f in os.listdir(d)
                         if os.path.isfile(os.path.join(d, f)))
        stray_dirs = sorted(f for f in os.listdir(d)
                            if os.path.isdir(os.path.join(d, f)))
        s.check("and nothing is laid out as a live path", not stray_dirs,
                detail=str(stray_dirs) + " — a salvage that keeps real paths "
                       "leaves static/gudanes.css sitting one move away from "
                       "the live file")
    s.check("carrying the added run", any("gudanes" in f for f in written),
            detail=str(written) + " — a run shorter than MIN_RUN is ignored on "
                   "purpose, because a one-line difference against a hundred-"
                   "line deletion is a collapsed file rather than an edit")
    if written:
        body = io.open(os.path.join(d, written[0]), encoding="utf-8").read()
        s.check("and it holds what the zip added",
                ".zzsalvage-mixed" in body, detail=body[-200:])
        # A diff boundary does not respect a rule boundary.
        import re as _re
        clean = _re.sub(r"/\*.*?\*/", "", body, flags=_re.S)
        s.check("with every block closed",
                clean.count("{") == clean.count("}"),
                detail="%d open, %d closed — an unclosed rule swallows every "
                       "rule after it and the browser says nothing"
                       % (clean.count("{"), clean.count("}")))
        s.check("and it says what it is and where it came from",
                "salvaged from" in body and "not installed" in body,
                detail=body[:200])
        # Nothing may reach the live surface.
        for f in written:
            s.check("%s is not a path into templates or static" % f[:28],
                    "/" not in f and "\\" not in f,
                    detail="salvaged names are flattened so nothing can be "
                           "dropped straight onto a live file")

    s.section("A diff boundary does not respect a rule boundary")

    # Asked of the function directly, because getting a CLI fixture to produce
    # a run that starts mid-rule depends on how SequenceMatcher happens to
    # align -- and a control that only fires by luck is not a control.
    #
    # This is the fault that ate rules in the live stylesheet for a week: an
    # unclosed rule swallows everything after it and the browser says nothing.
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import salvage_handover as S

    ours_lines = [".a{", "  color: red;", "}"]
    # The insert lands INSIDE .a, so the naive run is "  padding: 1px;" and a
    # dangling close -- unbalanced until it is widened out to the whole rule.
    theirs_lines = [".a{", "  padding: 1px;", "  margin: 0;", "  border: 0;",
                    "  color: red;", "}"]
    runs = S.added_runs(ours_lines, theirs_lines)
    # Judged on the TEXT, never by asking the tool's own predicate about its
    # own output — that is circular, and it showed: weakening whole_rules()
    # weakened this check with it, so the control stayed green.
    joined = ["\n".join(r) for r in runs]
    s.check("a run that starts mid-rule is widened until it closes",
            joined and all(t.count("{") == t.count("}") for t in joined),
            detail=str(runs))
    s.check("and it is handed back as whole rules, not loose declarations",
            joined and all(t.count("{") >= 1 for t in joined),
            detail=str(runs) + " — three bare declarations lifted out of "
                   "somebody else's rule balance vacuously, nought against "
                   "nought, and attach themselves to whatever precedes them")
    s.check("so the selector comes with them",
            joined and all(t.lstrip().startswith((".", "#", "@", ":", "*"))
                           or "{" in t.split("\n")[0] for t in joined),
            detail=str(runs))

    s.section("A one-line difference is not an edit")

    # The collapse signature: +1 against a hundred-line deletion. Every one of
    # the fifty-eight files in the third export looked like this, and none of
    # them was a change anybody made.
    tiny = _zip(tmp, {"static/gudanes.css": cut + "\n.zz-tiny{ color: red; }\n"})
    out = _run(tiny, "--write")
    s.check("it is reported but not salvaged",
            "ADDS AND REMOVES" in out and "Wrote 0 file(s)" in out,
            detail=out[-240:])

    # This suite writes into the real pending-design/. Clear what it made, or
    # the next run finds a stale directory and reads it as its own output.
    import shutil
    for d in made:
        shutil.rmtree(os.path.join(ROOT, "pending-design", d), ignore_errors=True)

    s.section("It does not fall over on nonsense")

    s.check("a zip that is not there is reported, not crashed",
            "No such zip" in _run(os.path.join(tmp, "nope.zip")))
    empty = _zip(tmp, {})
    s.check("an empty zip is fine", "read" in _run(empty))
    binary = os.path.join(tmp, "bin.zip")
    with zipfile.ZipFile(binary, "w") as z:
        z.writestr("static/x.png", b"\x89PNG\r\n\x1a\n\x00\x01\x02")
    s.check("and a binary file inside one is skipped rather than decoded",
            "read" in _run(binary), detail="it is not ours to reason about")

    return s


if __name__ == "__main__":
    print(run().report())
