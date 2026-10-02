"""The script that puts back what a handover keeps taking out.

check_handover.py has had a suite since it was written; this one has not, and
it is the half that actually edits files. On the eleventh handover it died on
step 2 of 10: the comment it searched for as an anchor was no longer on main,
`.index()` raised, and the eight steps after it never ran. Nothing said so.
The report listed the two it had managed before falling over, which reads
exactly like a run that found nothing left to do — so the tree looked repaired
and was not, and the six missing repairs were found one at a time by the suite
afterwards.

So the property worth testing hardest is not that any one repair works. It is
that a repair which CANNOT work says so, does not stop the others, and does
not exit 0 — because a silent skip in a tool whose whole job is to be the
safety net is worse than the tool not existing, which at least nobody trusts.

Everything runs against a throwaway repository in a temp directory. The real
tree is the one another agent is working in.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

from _harness import Suite
import _harness

m = _harness.m

TOOL = "repair_handover.py"


def _git(cwd, *args):
    return subprocess.run(
        ("git", "-c", "user.email=test@example.invalid",
         "-c", "user.name=Test", "-c", "core.autocrlf=false") + args,
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _tool_source():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "tools", TOOL)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _run(cwd, source=None):
    """Run the tool (optionally a doctored copy) in a throwaway repo."""
    tools = os.path.join(cwd, "tools")
    os.makedirs(tools, exist_ok=True)
    if source is not None:
        with open(os.path.join(tools, TOOL), "w", encoding="utf-8") as fh:
            fh.write(source)
    proc = subprocess.run([sys.executable, os.path.join("tools", TOOL)],
                          cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _write(cwd, rel, text):
    path = os.path.join(cwd, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _read(cwd, rel):
    with open(os.path.join(cwd, rel.replace("/", os.sep)), encoding="utf-8") as fh:
        return fh.read()


# A tree small enough to reason about, carrying two of the things the script
# repairs: the parent robots block and one child's noindex override.
BASE = "public_base.html", """<!DOCTYPE html>
<head>
<title>{% block title %}Stay{% endblock %}</title>
<meta name="description" content="{% block description %}A house.{% endblock %}">
{% block robots %}{% endblock %}
</head>
<footer><a href="{{ url_for('privacy_page') }}">{{ t('Privacy Policy') }}</a></footer>
"""
CHILD = "find_booking.html", """{% extends "public_base.html" %}
{% block title %}Find{% endblock %}
{% block robots %}<meta name="robots" content="noindex, nofollow">{% endblock %}
{% block content %}{% endblock %}
"""


def _repo(tool_src):
    """A throwaway repo with the two files committed, then stripped as a
    handover strips them."""
    cwd = tempfile.mkdtemp(prefix="ztest-repair-")
    _git(cwd, "init", "-q")
    for rel, text in (BASE, CHILD):
        _write(cwd, "templates/" + rel, text)
    _write(cwd, "static/gudanes.css",
           ".g-plate__l{ display: grid; }\n.g-plate__row{ min-width: 0; }\n")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", "the tree before the handover")
    # Now do what a handover does: take the two blocks and the rule out.
    _write(cwd, "templates/" + BASE[0], BASE[1].replace(
        "{% block robots %}{% endblock %}\n", ""))
    _write(cwd, "templates/" + CHILD[0], CHILD[1].replace(
        '{% block robots %}<meta name="robots" content="noindex, nofollow">{% endblock %}\n', ""))
    _write(cwd, "static/gudanes.css", ".g-plate__l{ display: grid; }\n")
    return cwd


def run():
    s = Suite("repair handover")
    src = _tool_source()
    if src is None:
        s.check(f"tools/{TOOL} exists", False, detail="the tool is missing")
        return s

    s.section("It puts back what the handover took out")
    cwd = _repo(src)
    try:
        code, out = _run(cwd, src)
        base = _read(cwd, "templates/" + BASE[0])
        child = _read(cwd, "templates/" + CHILD[0])
        s.check("the parent's robots block is back", "{% block robots %}{% endblock %}" in base,
                detail=base[:90])
        s.check("and the child's noindex with it",
                'content="noindex, nofollow"' in child, detail=child[:90])
        s.check("the stylesheet rule is back", ".g-plate__row{" in _read(cwd, "static/gudanes.css"))
        s.check("it exits 0 when every step ran", code == 0, detail=f"exit {code}")

        s.section("Running it twice changes nothing")
        # It is run by hand, often twice, and a repair that stacks would put a
        # second robots block in the parent rather than notice the first.
        before = _read(cwd, "templates/" + BASE[0])
        code2, out2 = _run(cwd, src)
        s.check("the second run repairs nothing", "0 repair(s)" in out2, detail=out2[-80:])
        s.check("and the file is untouched", _read(cwd, "templates/" + BASE[0]) == before)
        s.check("still exits 0", code2 == 0, detail=f"exit {code2}")
    finally:
        shutil.rmtree(cwd, ignore_errors=True)

    s.section("A step that cannot run does not take the others with it")
    # The actual failure, reproduced: one repair raises because what it
    # searched for is no longer there. Before this was isolated, the eight
    # steps after it never ran and the report did not say so.
    broken = src.replace(
        "def repair_child_noindex():",
        "def repair_child_noindex():\n"
        "    raise ValueError('substring not found')\n", 1)
    if broken == src:
        s.check("the second repair could be made to fail", False,
                detail="repair_child_noindex is not defined as expected")
    else:
        cwd = _repo(src)
        try:
            code, out = _run(cwd, broken)
            s.check("the failing step is named, not swallowed",
                    "COULD NOT RUN" in out and "noindex on guest pages" in out,
                    detail=out[:200])
            s.check("and the reason it gave is reported",
                    "substring not found" in out, detail=out[-200:])
            # The whole point. These come after the one that failed.
            s.check("a later step still runs",
                    ".g-plate__row{" in _read(cwd, "static/gudanes.css"),
                    detail="the stylesheet repair was skipped")
            s.check("and so does the last one",
                    "the typed dates on the event enquiry" in out, detail=out[-300:])
            # A run that could not do its job must not look like a clean one.
            s.check("it does not exit 0", code != 0, detail=f"exit {code}")
        finally:
            shutil.rmtree(cwd, ignore_errors=True)

    s.section("The stylesheet rule is read from git, not written from memory")
    # It used to carry the rule as a literal. That literal was right when it
    # was written and was later replaced on main by a different one, so the
    # script spent a handover putting the older of two fixes back.
    cwd = _repo(src)
    try:
        _write(cwd, "static/gudanes.css", ".g-plate__l{ display: grid; }\n")
        _git(cwd, "add", "-A")
        _git(cwd, "commit", "-q", "-m", "wip")
        # Change what main says the rule is, then take it away again.
        _write(cwd, "static/gudanes.css",
               ".g-plate__l{ display: grid; }\n.g-plate__row{ outline: 1px solid red; }\n")
        _git(cwd, "add", "-A")
        _git(cwd, "commit", "-q", "-m", "main moves the rule on")
        _write(cwd, "static/gudanes.css", ".g-plate__l{ display: grid; }\n")
        _run(cwd, src)
        css = _read(cwd, "static/gudanes.css")
        s.check("it restores what main says today",
                "outline: 1px solid red" in css, detail=css[:160])
        s.check("not a rule remembered in the script",
                "position: relative" not in css, detail=css[:160])
    finally:
        shutil.rmtree(cwd, ignore_errors=True)

    s.section("The seven from 1 October go back, once")
    # Every zip that week was built on 8114e1c, before any of these existed,
    # so every one arrived without them: the slider's import, the Workshops
    # deposit and rooms lines, the bathroom read from its column, the arrival
    # card's context, the after-dark instruction, the Legal Notice's host and
    # RCS line, and its footer link. A miniature of each fault, repaired once
    # and left alone the second time. The other repairs find nothing of
    # theirs in these miniatures; only these seven are read.
    seven = ("the slider's import on Restoration",
             "the deposit and rooms lines on Workshops",
             "the bathroom read from its column",
             "the arrival card's context",
             "the after-dark instruction on Contact",
             "the Legal Notice's host and RCS line",
             "the Legal Notice footer links")
    cwd = tempfile.mkdtemp(prefix="ztest-repair7-")
    try:
        _git(cwd, "init", "-q")
        faults = {
            "templates/restoration.html":
                "{% from '_floorplan.html' import floorplan %}\n{{ the_reveal(reveal_pairs) }}\n",
            "templates/workshops_public.html":
                '  <div class="g-key">\n    <div class="g-key__i">\n'
                '      <p class="g-key__k">Excursions and transfers</p>\n'
                '      <p class="g-key__v">Included, except on the workshops spent at the house</p>\n'
                '    </div>\n  </div>\n',
            "templates/book_rooms.html":
                "          {% if room['amenities'] %}\n"
                "            {% for tag in room['amenities'].split(',') %}<li>{{ tag.strip() }}</li>{% endfor %}\n"
                "          {% else %}\n          {% endif %}\n",
            "templates/booking_confirmation.html":
                "{% from '_arrival_card.html' import arrival_card %}\n{{ arrival_card(booking) }}\n",
            "templates/contact.html":
                '      <p class="g-key__k">By car</p>\n'
                '      <p class="g-key__v">An hour away. The last stretch climbs; take it slowly after dark</p>\n',
            "templates/legal.html":
                "    {% if company and company['vat_number'] %}\n    {% endif %}\n"
                '    <div><dt>Host</dt><dd>Railway Corp., San Francisco, California, United States \u00b7 '
                '<a href="https://railway.com" rel="noopener">railway.com</a></dd></div>\n',
            "templates/public_base.html":
                "        <ul>\n          <li><a href=\"{{ url_for('privacy_page') }}\">{{ t('Privacy Policy') }}</a></li>\n        </ul>\n",
        }
        for rel, text in faults.items():
            _write(cwd, rel, text)
        _git(cwd, "add", "-A")
        _git(cwd, "commit", "-q", "-m", "a tree carrying the seven faults")
        code, out = _run(cwd, src)
        named = {label: next((l for l in out.splitlines() if l.rstrip().endswith(label)), "")
                 for label in seven}
        s.check("each of the seven ran", all(named.values()) and
                not any("COULD NOT RUN" in l for l in named.values()),
                detail=str({k: v.strip()[:30] for k, v in named.items()}))
        r = lambda rel: _read(cwd, rel)
        s.check("the slider is imported", "import the_reveal" in r("templates/restoration.html"))
        ws = r("templates/workshops_public.html")
        s.check("the deposit line reads the route",
                "deposit_pct" in ws and "The whole amount is due then" in ws)
        s.check("and the rooms line is back", "Arranged for two" in ws)
        s.check("the bathroom follows its column",
                "'bathroom' not in tag|lower" in r("templates/book_rooms.html"))
        s.check("the arrival card is imported with context",
                "import arrival_card with context" in r("templates/booking_confirmation.html"))
        s.check("the after-dark instruction is in the By car line",
                "telephone when you leave Les Cabannes" in r("templates/contact.html"))
        legal = r("templates/legal.html")
        s.check("the host is named in full", "548 Market St PMB 68956" in legal)
        s.check("and the RCS line sits above the VAT row",
                legal.find("Trade register") < legal.find("company['vat_number']"))
        s.check("the footer links the Legal Notice beside the privacy policy",
                "<li><a href=\"{{ url_for('legal_page') }}\">" in r("templates/public_base.html"))
        before = {rel: r(rel) for rel in faults}
        code2, out2 = _run(cwd, src)
        again = [l for l in out2.splitlines()
                 if any(l.rstrip().endswith(label) for label in seven)]
        s.check("a second run leaves all seven alone",
                len(again) == 7 and all("already fine" in l for l in again),
                detail=" | ".join(l.strip()[:40] for l in again))
        s.check("and changes no file", all(r(rel) == text for rel, text in before.items()))
    finally:
        shutil.rmtree(cwd, ignore_errors=True)

    s.section("A claim the owner corrected is rewritten in the copy, not in the notes")
    # 2 October: "tennis or padel" arrived in the free-time copy. There is no
    # padel court; the tennis court is lined for pickleball. The design side's
    # ledger still says padel, so the next zip will too.
    cwd = tempfile.mkdtemp(prefix="ztest-repair-claims-")
    try:
        _git(cwd, "init", "-q")
        page = ("{# 2 Oct (owner): tennis or padel, the pool #}\n"
                "<p>A game of tennis or padel, a swim in the pool.</p>\n"
                "<li>Padel, tennis and the pool</li>\n")
        _write(cwd, "templates/free_time.html", page)
        _git(cwd, "add", "-A")
        _git(cwd, "commit", "-q", "-m", "a page carrying the word")
        code, out = _run(cwd, src)
        fixed = _read(cwd, "templates/free_time.html")
        s.check("the copy says pickleball",
                "tennis or pickleball, a swim" in fixed and "<li>Pickleball, tennis" in fixed,
                detail=fixed)
        s.check("and keeps the capital where the word began a line",
                "<li>Pickleball," in fixed, detail=fixed)
        s.check("the design's note is left as it was written",
                "{# 2 Oct (owner): tennis or padel, the pool #}" in fixed, detail=fixed)
        s.check("it reports what it changed",
                re.search(r"restored\s+2\s+claims the owner has corrected", out) is not None,
                detail=out[-400:])
        code2, out2 = _run(cwd, src)
        s.check("a second run leaves it alone",
                re.search(r"already fine\s+claims the owner has corrected", out2) is not None
                and _read(cwd, "templates/free_time.html") == fixed,
                detail=out2[-300:])
    finally:
        shutil.rmtree(cwd, ignore_errors=True)

    s.section("It says where the tool is documented")
    s.check("the docstring points at check_handover first",
            "check_handover" in src[:2000], detail="ordering advice is missing")
    s.check("and names the real fix rather than only the workaround",
            "export_for_design" in src[:3000], detail="the upstream fix is unmentioned")

    return s


if __name__ == "__main__":
    print(run().report())
