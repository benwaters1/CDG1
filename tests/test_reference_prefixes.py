"""The code a guest types in has to be the code the app gave them.

Four booking types, four prefixes — rooms GUD, events EVT, dinner DIN,
ateliers WRK — and the page a guest goes to when they have lost their email
prints an example in the box. Three of those four printed CDG-XXXXXX.

NOTHING HAS EVER GENERATED A CDG REFERENCE. So somebody holding WRK-4B2K9X
arrived at the one page whose entire job is to find their booking, was shown
a hint that matched nothing they had, and — if they trusted it over their own
email — typed something the form then told them it could not find. No error,
no log, and the guest concludes the booking is gone.

It was handed over by the design side as a question, "GUD or CDG?", which is
itself the wrong question: there are four, one per kind, and one of the four
pages was already right. A question with a false premise is how you get a
consistent answer that is consistently wrong.

So the prefix is defined ONCE and read by both halves: the generator makes it
and the template prints it. This file is what stops a fifth spelling appearing
in either half — it does not compare the pages against a list written here,
because that would be the same second copy in a new place. It asks the
GENERATOR what a reference looks like, and then asks the PAGE.
"""
import re

from _harness import Suite
import _harness

m = _harness.m

# The page each kind sends a guest to, and the function that mints its code.
KINDS = [
    ("room", "/book/manage", "make_reference_code", "rooms"),
    ("event", "/events/find", "make_event_reference_code", "events"),
    ("restaurant", "/restaurant/find", "make_restaurant_reference_code", "dinner"),
    ("workshop", "/workshops/find", "make_workshop_reference_code", "ateliers"),
]
SHOWN = re.compile(r'placeholder="([A-Za-z]{2,5}-X+)"')


def run():
    s = Suite("The reference a guest is asked for")
    anon = m.app.test_client()

    s.section("Every kind mints a code, and it is not the same code")
    minted = {}
    for kind, _path, maker, _label in KINDS:
        minted[kind] = getattr(m, maker)()
    s.check("all four produce a reference",
            all(len(c) > 4 for c in minted.values()), detail=str(minted))
    s.check("and no two kinds share a prefix",
            len({c.split("-")[0] for c in minted.values()}) == 4,
            detail=f"{sorted(c.split('-')[0] for c in minted.values())} — two "
                   "kinds with one prefix is a code that finds the wrong "
                   "booking, which is worse than finding none")

    s.section("The page asks for the code that kind actually has")
    for kind, path, _maker, label in KINDS:
        want = minted[kind].split("-")[0] + "-"
        page = anon.get(path)
        body = page.get_data(as_text=True) if page.status_code == 200 else ""
        shown = SHOWN.findall(body)
        s.check(f"the {label} page shows a {want}example",
                page.status_code == 200 and shown
                and all(h.startswith(want) for h in shown),
                detail=f"HTTP {page.status_code}, box says {shown or 'nothing'}, "
                       f"the app issues {minted[kind]}")

    s.section("And nowhere offers one the app cannot issue")
    # ASKED OF THE RENDERED PAGES, not of the source, because the hint is
    # built from a template global now and the source no longer contains the
    # letters at all. What a guest reads is the thing that has to be right.
    real = {c.split("-")[0] + "-" for c in minted.values()}
    invented = {}
    for _kind, path, _maker, label in KINDS:
        page = anon.get(path)
        if page.status_code != 200:
            continue
        for hint in SHOWN.findall(page.get_data(as_text=True)):
            prefix = hint.split("-")[0] + "-"
            if prefix not in real:
                invented.setdefault(label, set()).add(prefix)
    s.check("no page invents a prefix", not invented,
            detail=f"{ {k: sorted(v) for k, v in invented.items()} } — CDG- was "
                   "on three of these pages and has never been issued by "
                   "anything")

    s.section("One definition, so the two halves cannot drift again")
    s.check("the generators read the same map the templates do",
            set(m.REFERENCE_PREFIXES) == {"room", "event", "restaurant", "workshop"},
            detail=str(sorted(m.REFERENCE_PREFIXES)))
    for kind, _path, maker, label in KINDS:
        s.check(f"what {label} mints starts with what the map says",
                getattr(m, maker)().startswith(m.REFERENCE_PREFIXES[kind]),
                detail=f"{getattr(m, maker)()} against "
                       f"{m.REFERENCE_PREFIXES[kind]}")
    s.check("and the templates are handed it rather than repeating it",
            m.app.jinja_env.globals.get("ref_prefix") is m.REFERENCE_PREFIXES,
            detail="a copy passed per render is a call site somebody forgets, "
                   "and these pages render from three branches each")

    return s
