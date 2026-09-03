"""A template nothing renders, and nobody knows it is there.

Three of them were sitting in templates/ when this was written, and each had
been superseded by something better without anybody noticing the old one was
still on disk:

  _prearrival.html — a whole pre-arrival form, macro and all, posting to
    action=prearrival. There is no handler for that action and never was.
    manage_booking.html asks the same questions with real handlers behind
    them, so the macro was a design that lost and stayed.

  _devices.html — monogram and crown macros, superseded by _marks.html,
    which does the same job and is imported by fourteen pages.

  _monument_note.html — the interesting one. Its own comment says the note
    "was pasted verbatim into nine templates, so correcting a word meant
    finding all nine", and that it is "included instead". It was included by
    nothing. But the four pages that make the same claim turned out to say it
    four genuinely different ways, each written for its page — the weddings
    page in a wedding's voice, the home page in three lines. Replacing them
    with one generic paragraph would have made every one of them worse. The
    extraction was a good instinct about copy that did not want unifying, and
    the file is gone rather than wired in.

WHY IT MATTERS more than tidiness: an orphaned partial reads as live code.
Somebody editing the château's Class I wording would have found and corrected
_monument_note.html, changed nothing any guest can see, and gone away
believing the site now said the right thing.

The sweep understands render_template(f"report_{slug}.html") — a name built
at runtime is still a reference — and it does NOT let a file count as
referenced by itself, which is how the pre-arrival macro hid: its own usage
example, in its own comment, looked like a real import.

Closing that hole turned up nine more, and they are a different thing again:
a coherent batch of guest-facing macros, each with a written contract in its
own header — the next free nights, what the stay actually costs as you change
the nights, what happens next, a read-only link for whoever is paying,
bookings that travel together, add something after booking, something to take
with you, the weather at the château, and a guest's own record of their stays.

None of them has a stylesheet rule, a script, or a route supplying its data.
They are a design that was never landed rather than code that stopped being
called, so deleting them would throw away real work and leaving them unlisted
would let them read as live. They are named below, with the class each one
hangs on, so "not built yet" is PROVED rather than asserted — write the CSS,
wire it into a page, and this file tells you to take it off the list.
"""
import io
import os
import re

from _harness import Suite

import _harness

m = _harness.m

TPL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "templates")

# Files rendered under a name built at runtime, with the line that builds it.
# Named rather than pattern-matched: "anything starting with report_" would
# excuse the next orphan that happens to be called report_something.
DYNAMIC = {
    "report_financial.html": 'render_template(f"report_{slug}.html"',
    "report_guest.html": 'render_template(f"report_{slug}.html"',
    "report_labour.html": 'render_template(f"report_{slug}.html"',
    "report_occupancy.html": 'render_template(f"report_{slug}.html"',
    "report_pace.html": 'render_template(f"report_{slug}.html"',
}


# A batch of guest-facing macros that were designed and never landed: no
# stylesheet rules, no scripts, and no route supplying their data. Kept
# because the design is real and somebody will want it; NAMED because an
# unlisted orphan reads as a live template.
#
# The class each one hangs on is here too, so "not built yet" is proved
# rather than asserted. Write the CSS, wire it into a page, and the check
# below will tell you to take it off this list.
# Seven have come off this list as they were landed, which is all of them, and one was deleted:
# _guest_extras.html described adding an extra after booking, which
# manage_booking.html already does with a real handler behind it. The
# same story as the pre-arrival form -- a design that lost and stayed.
# Seven have come off this list as they were landed, which is all of them: _guest_timeline.html and
# _print_stay.html on the guest's own booking page, _nights_calc.html on the
# room page. Every one of them needed real work first -- two were written
# against column names that do not exist, and the third would have quoted a
# 30% deposit on a house that takes none. "Already written, just include it"
# was true of any of them, which is what to expect of the five left.
#
# The list did its job on the fourth: the availability strip was wired in and
# styled and left on here, and the suite went red on "still unbuilt -- no
# .g-fr rule exists" before the commit.
AWAITING_WIRING = {
    # All three arrived in the eleventh handover, and all three are REDESIGNS
    # of things that already work rather than features waiting to be switched
    # on. That distinction is why they are here and not wired: swapping a
    # working flow for a better-looking one is a deliberate edit somebody
    # should make on purpose, not a side effect of unpacking a zip.
    "_cancel.html": (
        "g-cancel",
        "manage_booking already cancels: the form is on the page and the "
        "handler behind it sends the guest and the house their emails. This "
        "is the same thing with the terms shown and a <details> confirm "
        "instead of a bare button"),
    "_waitlist.html": (
        "g-wait",
        "workshop_register already carries a waiting-list form, and it posts "
        "to /workshops/waitlist/join, which exists. This one posts "
        "action=waitlist to the registration endpoint instead and asks "
        "whether other dates would work, which is the more useful answer"),
    "_downloads.html": (
        "g-dl",
        "there is nothing to download. The macro renders nothing on an empty "
        "list, so wiring it would be dead markup until somebody puts a menu "
        "and an atelier programme in static/"),
}


# The proof reads BOTH stylesheets. It used to read only static/style.css,
# the staff one, while every class on these lists is a public-site class in
# static/gudanes.css -- so "still unbuilt" could not fail for a public
# partial, which is all of them. The three below are named by a class that
# is genuinely absent from both files; if that stops being true for one of
# them, somebody has started building it and it belongs in a page rather
# than on this list.


# Files with one macro landed and another not. AWAITING_WIRING cannot hold
# these: it means "referenced by nothing", and these ARE referenced -- which
# made the suite report the file as wired in and not taken off, true of one
# half and wrong about the file.
#
# Same discipline: the class named is the one the UNLANDED half hangs on, so
# building it turns this red. It did. _linked_bookings.html was the only
# entry and `add_room` is now on the guest's manage page, so the entry is
# gone rather than amended -- and the machinery stays, because the next
# half-landed file should not have to reinvent it.
PARTLY_LANDED = {}


def _templates():
    out = set()
    for root, _dirs, files in os.walk(TPL):
        for f in files:
            if f.endswith((".html", ".txt", ".xml")):
                out.add(os.path.relpath(os.path.join(root, f), TPL)
                        .replace("\\", "/"))
    return out


def _referenced(names, app_src):
    """Every template named by app.py or by another template.

    A file never counts as referencing itself. That is not pedantry: the
    pre-arrival macro carried its own usage example in its own comment, and
    a naive sweep read that as a live import and left it alone.
    """
    hit = set()
    for n in names:
        if f'"{n}"' in app_src or f"'{n}'" in app_src:
            hit.add(n)
    pat = re.compile(
        r"""\{%-?\s*(?:extends|include|import|from)\s+['"]([^'"]+)['"]""")
    for n in names:
        src = io.open(os.path.join(TPL, n), encoding="utf-8").read()
        for other in pat.findall(src):
            if other != n:
                hit.add(other)
        # {% include ['a.html', 'b.html'] %}
        for group in re.findall(r"""\{%-?\s*include\s+\[([^\]]+)\]""", src):
            for q in re.findall(r"""['"]([^'"]+)['"]""", group):
                if q != n:
                    hit.add(q)
    return hit


def run():
    s = Suite("templates nothing renders")
    app_src = io.open(
        os.path.join(os.path.dirname(TPL), "app.py"), encoding="utf-8").read()
    names = _templates()

    s.section("There are templates to check")
    s.check("the folder was found and read", len(names) > 100,
            detail=f"{len(names)} templates")

    hit = _referenced(names, app_src)

    s.section("The ones rendered under a name built at runtime")
    # Listed rather than pattern-matched, and the line that builds the name is
    # checked to still exist -- so a report page dropped from the app shows up
    # here rather than being excused forever by a stale exception.
    for name, builder in sorted(DYNAMIC.items()):
        s.check(f"{name} is still built by {builder[:34]}...",
                builder in app_src,
                detail="the exception outlived the code that justified it")
        s.check(f"and {name} is still on disk", name in names)

    s.section("The batch that was designed and never landed")
    # Proved, not asserted -- but not by the ABSENCE of a stylesheet rule,
    # which is what this used to check and which was never evidence here.
    # These arrive designed: markup and CSS together in a handover, with the
    # wiring left for later. Reading both stylesheets rather than only the
    # staff one showed .g-cancel with eight rules, .g-wait eleven, .g-dl six
    # -- so "still unbuilt" had been passing on a file it never looked in.
    #
    # The true claim is the inverse, and it is the one worth holding: the
    # markup exists, the CSS exists, and NOTHING RENDERS IT. If the styling
    # goes, this stops being a design awaiting wiring and becomes dead
    # markup, which is the thing this suite exists to find.
    #
    # No signal is lost. The one time this list did its job -- the
    # availability strip, wired in and styled and left on here -- the
    # referenced-now check below catches it just as surely, and that is
    # where "somebody has started wiring it in" actually belongs.
    css = ""
    for sheet in ("style.css", "gudanes.css"):
        css += io.open(os.path.join(os.path.dirname(TPL), "static", sheet),
                       encoding="utf-8").read()
    s.check("both stylesheets were found and read", len(css) > 20000,
            detail=f"{len(css)} characters; reading one of the two is how "
                   "this check stopped being able to fail")
    for name, (cls, what) in sorted(AWAITING_WIRING.items()):
        s.check(f"{name} is still on disk ({what})", name in names,
                detail="deleting it throws away the design; if it really is "
                       "not wanted, take it off this list in the same commit")
        s.check(f"and its design survives \u2014 .{cls} is still styled",
                f".{cls}" in css,
                detail="markup with no CSS behind it is not a design "
                       "waiting to be wired, it is dead markup: wire it "
                       "up, or delete it, but do not leave it here "
                       "looking like a plan")

    s.section("The ones with one macro landed and another not")
    for name, (cls, what) in sorted(PARTLY_LANDED.items()):
        s.check(f"{name} is reached from somewhere ({what[:44]}...)",
                name in hit,
                detail="it is on this list because HALF of it is landed; if "
                       "nothing references it at all it belongs on the other "
                       "one")
        s.check(f"and the other half is still unwired \u2014 nothing "
                f"renders .{cls}",
                not any(cls in io.open(os.path.join(TPL, o),
                                       encoding="utf-8").read()
                        for o in names if o != name),
                detail="asked of what renders it, not of whether it has "
                       "CSS: these arrive styled, so an absent rule "
                       "proves nothing about whether a page draws it")

    s.section("Every other template is reached from somewhere")
    orphans = sorted(names - hit - set(DYNAMIC) - set(AWAITING_WIRING)
                     - set(PARTLY_LANDED))
    s.check("nothing else sits in templates/ unreferenced", not orphans,
            detail="a template nothing renders reads as live code: somebody "
                   "corrects it, changes nothing anybody can see, and goes "
                   "away believing they fixed it. Found: " + ", ".join(orphans))

    s.section("And the list is exactly the orphans, with nothing stale on it")
    # An exception list that outlives the file it excuses is how the next
    # orphan gets in: it looks like the list is being maintained.
    stale = sorted(set(AWAITING_WIRING) & hit)
    s.check("nothing on the list is actually referenced now", not stale,
            detail="wired in and not taken off: " + ", ".join(stale))

    s.section("And the three that were found are gone")
    # Named, so that re-adding one is a decision somebody makes on purpose
    # rather than a file that reappears from a handover zip unnoticed.
    for gone, why in (
            ("_prearrival.html", "posts to an action with no handler; "
                                 "manage_booking.html asks the same questions"),
            ("_devices.html", "superseded by _marks.html"),
            ("_monument_note.html", "the four pages say it four different "
                                    "ways on purpose"),
            ("_guest_extras.html", "manage_booking.html already has an "
                                   "add-to-your-stay block with a real "
                                   "handler behind it")):
        s.check(f"{gone} is not back ({why})", gone not in names)

    s.section("The self-reference hole is really closed")
    # The check on the check. _prearrival.html hid behind its own usage
    # example for as long as it existed, so this proves the sweep would not
    # be fooled by the same trick again.
    fake = "zz_selfref_probe.html"
    path = os.path.join(TPL, fake)
    io.open(path, "w", encoding="utf-8").write(
        "{# Use it like this: {% from 'zz_selfref_probe.html' import thing %} #}\n"
        "{% macro thing() %}nothing{% endmacro %}\n")
    try:
        probe_names = _templates()
        probe_hit = _referenced(probe_names, app_src)
        s.check("a file that only mentions its own name is still an orphan",
                fake not in probe_hit,
                detail="which is exactly how the pre-arrival macro survived")
    finally:
        os.remove(path)

    return s


if __name__ == "__main__":
    print(run().report())
