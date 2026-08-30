"""The tool that names what a handover zip would take back.

Nine handovers have now been installed over this tree, and every one of them
reverted something: "the two things it undid on the way in", "installed in
full, with four regressions repaired", "with eleven regressions repaired". The
cause is upstream and is one sentence long - the zips are exported from a
snapshot rather than from current main - and until that changes, finding the
reverts is the job.

repair_handover.py puts back eight named things. That list can only ever
contain regressions that have already happened at least once, so it was blind
to all eleven in the ninth handover. check_handover.py takes the general form
instead: installing a zip is a revert of everything committed to those files
since its snapshot, and `git blame` already knows which commit each removed
line came from. No rule per regression, so it finds the tenth as readily as
the first.

TWO PROPERTIES MATTER, and they pull against each other.

It has to FIRE on a revert, naming the commit, or it is decoration. And it has
to STAY SILENT on a handover that merely adds or reindents, or it becomes the
tool everybody skips - these zips reindent constantly, and a checker that cries
wolf on whitespace would be ignored by the third install, which is exactly when
it would have mattered.

Everything here runs against a throwaway repository built in a temp directory.
The real tree is the one another agent is working in, and a test that mutates
it to see what happens is a worse bug than the one it is testing for.
"""
import os
import shutil
import subprocess
import sys
import tempfile

from _harness import Suite
import _harness

m = _harness.m

TOOL = "check_handover.py"


def _git(cwd, *args):
    return subprocess.run(
        # Identity and line endings pinned on the command, so the throwaway
        # repo does not depend on whatever is in the machine's global config.
        ("git", "-c", "user.email=test@example.invalid",
         "-c", "user.name=Test", "-c", "core.autocrlf=false") + args,
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _tool_source():
    """The tool as it sits in the repo, so the test cannot drift from it."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "tools", TOOL)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _run(cwd):
    """Run the tool in a throwaway repo; returns (exit code, output)."""
    proc = subprocess.run([sys.executable, os.path.join("tools", TOOL)],
                          cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _write(cwd, rel, text):
    path = os.path.join(cwd, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


ORIGINAL = """<html>
  <head>
    {% block robots %}{% endblock %}
  </head>
  <body>
    <p>Welcome to the chateau.</p>
  </body>
</html>
"""

WITH_FIX = """<html>
  <head>
    {% block robots %}<meta name="robots" content="noindex">{% endblock %}
  </head>
  <body>
    <p>Welcome to the chateau.</p>
    <a href="{{ url_for('pay_deposit') }}">Pay your deposit</a>
  </body>
</html>
"""


def _repo(tool_src):
    """A repo with two commits: a page, then a fix to it."""
    cwd = tempfile.mkdtemp(prefix="zzhandover_")
    _git(cwd, "init", "-q")
    _write(cwd, os.path.join("tools", TOOL), tool_src)
    _write(cwd, "page.html", ORIGINAL)
    _git(cwd, "add", "tools/" + TOOL, "page.html")
    _git(cwd, "commit", "-q", "-m", "The page as it first shipped")
    _write(cwd, "page.html", WITH_FIX)
    _git(cwd, "add", "page.html")
    _git(cwd, "commit", "-q", "-m", "Keep guests own pages out of search results")
    return cwd


def run():
    s = Suite("Handover check")

    src = _tool_source()
    s.check("the tool is in the repo", src is not None,
            detail="tools/" + TOOL + " is missing, so nothing below ran")
    if src is None:
        return s

    made = []
    try:
        s.section("A zip built from an old snapshot is named, not just noticed")
        # The whole point. The zip carries the pre-fix version of the page, so
        # installing it silently takes the noindex block and the deposit link
        # back out. Nothing errors and the page renders.
        cwd = _repo(src)
        made.append(cwd)
        _write(cwd, "page.html", ORIGINAL)
        code, out = _run(cwd)
        s.check("it refuses the tree", code == 1, detail=f"exit {code}\n{out[:300]}")
        s.check("and names the commit whose work is going",
                "Keep guests own pages out of search results" in out,
                detail=f"{out[:400]}")
        s.check("quoting the line that is being lost",
                "noindex" in out, detail=f"{out[:400]}")
        s.check("it does not put anything back by itself",
                "noindex" not in open(os.path.join(cwd, "page.html"),
                                      encoding="utf-8").read(),
                detail="the tool edited the tree — that judgement is a person's")

        s.section("A handover that only adds is left alone")
        # If it fired on this it would be skipped by the third install, which
        # is precisely when it would have caught something.
        cwd = _repo(src)
        made.append(cwd)
        _write(cwd, "page.html", WITH_FIX.replace(
            "</body>", "    <p>A new paragraph the designer wrote.</p>\n  </body>"))
        code, out = _run(cwd)
        s.check("the tree is accepted", code == 0, detail=f"exit {code}\n{out[:300]}")
        s.check("and it says so plainly", "No committed work is undone" in out,
                detail=f"{out[:300]}")

        s.section("Reindentation is not a regression")
        # These zips reformat constantly. Whitespace-only noise would drown the
        # real finding on the one install that had one.
        cwd = _repo(src)
        made.append(cwd)
        _write(cwd, "page.html", WITH_FIX.replace("    <p>", "        <p>")
                                          .replace("  <body>", "      <body>"))
        code, out = _run(cwd)
        s.check("a reindented file is not reported as undoing anything", code == 0,
                detail=f"exit {code}\n{out[:400]}")

        s.section("A file the handover introduces is not undoing anything")
        cwd = _repo(src)
        made.append(cwd)
        _write(cwd, "brand_new.html", "<p>Something that did not exist before.</p>\n")
        code, out = _run(cwd)
        s.check("an untracked new file is ignored", code == 0,
                detail=f"exit {code}\n{out[:300]}")

        s.section("Losing one real line is enough to fail")
        # The eight in repair_handover are mostly single lines - a robots
        # block, a url_for parameter. One line has to be enough.
        cwd = _repo(src)
        made.append(cwd)
        _write(cwd, "page.html",
               WITH_FIX.replace('    <a href="{{ url_for(\'pay_deposit\') }}">'
                                'Pay your deposit</a>\n', ""))
        code, out = _run(cwd)
        s.check("one deleted line is refused", code == 1, detail=f"exit {code}")
        s.check("and attributed to the commit that added it",
                "Keep guests own pages out of search results" in out,
                detail=f"{out[:300]}")

        s.section("A clean tree passes")
        cwd = _repo(src)
        made.append(cwd)
        code, out = _run(cwd)
        s.check("nothing modified, nothing to say", code == 0, detail=f"exit {code}")
    finally:
        for cwd in made:
            shutil.rmtree(cwd, ignore_errors=True)

    s.section("It is documented where somebody will look for it")
    # A tool nobody knows about is worth nothing, and the file that describes
    # the eight manual repairs is where a person lands after unzipping.
    here = os.path.dirname(os.path.abspath(__file__))
    repair = os.path.join(os.path.dirname(here), "tools", "repair_handover.py")
    text = ""
    if os.path.exists(repair):
        with open(repair, "r", encoding="utf-8") as fh:
            text = fh.read()
    s.check("repair_handover points at it", "check_handover" in text,
            detail="somebody unzipping a handover reads that file and would "
                   "never learn this one exists")

    return s
