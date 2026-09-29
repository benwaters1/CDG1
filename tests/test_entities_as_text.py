"""A dash that came out as the word for one.

`{{ d.bookings or '&mdash;' }}` reads as a dash in the template, and is
escaped like any other string, because Jinja has no way to know it was meant
as markup: the page is sent `&amp;mdash;` and the browser shows the seven
characters. The covers forecast printed it on every night nobody had booked,
and the supplier statement in the Number column of every invoice without
one. Nothing errored and the pages rendered; the dash was simply a word.

Ten, on seven staff pages, all the same shape: an entity written as a string
inside {{ }}. The character itself ('—') is escaped to itself, and
'&mdash;'|safe says it is markup on purpose. The same spelling outside {{ }}
is markup already and was never the problem.

READ FROM THE SOURCE, because the fallback only renders when the value is
missing, and a check on the output would pass or fail with the database. One
page is rendered as well, to show the source rule and the page agree.

The public pages are not in this: they arrive from the design side as whole
files.
"""
import re

from _harness import Suite, clients
import _harness
from test_row_headings import _public_names

m = _harness.m

_EXPR = re.compile(r"\{\{(.*?)\}\}", re.S)
_LITERAL = re.compile(r"""(['"])((?:(?!\1).)*?&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);(?:(?!\1).)*?)\1""",
                      re.S)
_SAFE_AFTER = re.compile(r"\s*\|\s*safe\b")
_SAFE_END = re.compile(r"\|\s*safe\s*$")


def escaped_entities(src):
    """(line, literal) for every entity written as a string inside {{ }}."""
    # Comments blanked at the same length, so line numbers stay true.
    src = re.sub(r"\{#.*?#\}", lambda c: re.sub(r"[^\n]", " ", c.group(0)), src, flags=re.S)
    out = []
    for e in _EXPR.finditer(src):
        expr = e.group(1)
        if _SAFE_END.search(expr):
            continue                    # the whole expression is marked as markup
        for lit in _LITERAL.finditer(expr):
            if _SAFE_AFTER.match(expr, lit.end()):
                continue
            out.append((src.count("\n", 0, e.start()) + 1, lit.group(0)))
    return out


def run():
    s = Suite("A dash is a dash, not the word for one")

    s.section("The premise, asked of Jinja itself")
    env = m.app.jinja_env
    got = env.from_string("{{ x or '&mdash;' }}").render(x=None)
    s.check("an entity written as a string is escaped like any other string",
            got == "&amp;mdash;", detail="rendered %r" % got)
    mended = env.from_string("{{ x or '—' }}|{{ x or '&mdash;'|safe }}").render(x=None)
    s.check("and the character itself, or |safe, comes out as a dash",
            mended == "—|&mdash;", detail="rendered %r" % mended)

    s.section("No staff template writes one")
    srcs, public = _public_names()
    swept, found = 0, []
    for name in sorted(srcs):
        if name in public:
            continue
        swept += 1
        found += ["%s:%d %s" % (name, line, lit) for line, lit in escaped_entities(srcs[name])]
    # Counted, so a sweep that stopped reading templates would say so.
    s.check("the sweep reads %d staff templates" % swept, swept > 250,
            detail="too few to be reading the templates at all")
    s.check("none writes an entity as a string inside {{ }}", not found,
            detail="%d, e.g. %s -- write the character itself, or add |safe"
                   % (len(found), found[:4]))

    s.section("And the sweep can tell the difference")
    probe = "\n".join([
        "{{ x or '&mdash;' }}",                 # 1  shown as the word
        "{{ x or '—' }}",                  # 2  the character
        "{{ x or '&mdash;'|safe }}",            # 3  markup, said so
        "{{ (x or '&mdash;')|safe }}",          # 4  the whole thing is
        "{{ t('stays &amp; general') }}",       # 5  handed to a function, then escaped
        "&mdash; {{ x }}",                      # 6  markup already
        "{# {{ x or '&mdash;' }} #}",           # 7  a comment
        '{{ x ~ "&#8212;" }}',                  # 8  a number is an entity too
        "{{ '&mdash;'|safe if x is none else x }}",  # 9  said so, mid-expression
    ])
    got = [line for line, _lit in escaped_entities(probe)]
    s.check("it names the three that show as text and none of the six that do not",
            got == [1, 5, 8], detail="named lines %s, expected [1, 5, 8]" % got)

    s.section("The page that showed it most, rendered")
    # Every night ahead is a row whether or not anybody has booked, so the
    # fallback is on the page on any database.
    oc, _ec, _owner, _emp = clients()
    r = oc.get("/restaurant/covers")
    body = r.get_data(as_text=True)
    s.check("the covers forecast opens", r.status_code == 200, response=r)
    s.check("and no night's bookings read as the word for a dash",
            "&amp;mdash;" not in body,
            detail="%d found" % body.count("&amp;mdash;"))
    return s


if __name__ == "__main__":
    print(run().report())
