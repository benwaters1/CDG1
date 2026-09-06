"""Pages that show somebody's booking carry a noindex tag of their own.

robots.txt asks crawlers not to walk certain paths. That is a request, and
it only covers crawling — a URL that reaches Google another way (a referrer
header, a forwarded email, a link pasted into a public thread) can still be
indexed without ever being crawled. The per-page meta tag is what actually
keeps a guest's booking out of search results.

This is guarded because it has already been lost once: a design handover
rebuilt public_base.html without the robots block, which silently turned
every override in the child templates into dead markup. Nothing errors when
that happens — the pages render perfectly, and simply become indexable.

THERE ARE NOW TWO STATES AND BOTH MATTER, in opposite directions.
chateaugudanes.com is still Squarespace; this app sits on a Railway URL that
is public and crawlable. So SITE_IS_LIVE decides whether the app believes it
IS the website — false by default, because a new deployment is a staging
deployment until somebody says otherwise.

  Live and noindex: the site disappears from search. The expensive mistake.
  Staging and indexable: a second Château de Gudanes, with the same words and
  the same photographs, competing with the real one for the house's own name.

Both are checked, and the flag is SET rather than read: a test whose answer
depends on what an environment variable happens to hold is one that passes on
one machine and fails on another for reasons nobody can see.
"""
import io
import re

from _harness import Suite, clients
import os

import _harness

m = _harness.m

# The block only works if the base template renders it. A child overriding a
# block the parent never outputs is inert, which is exactly the failure mode.
# Anchored to the repo rather than the working directory: run from tests/
# and a relative path makes this suite CRASH rather than run, which is a
# suite that silently is not protecting anything depending on how it was
# started.
TPL = os.path.join(_harness.ROOT, "templates")
BASE = os.path.join(TPL, "public_base.html")

# Public pages: must NOT be noindex, or the site disappears from search.
PUBLIC = ["/", "/book", "/restaurant", "/workshops", "/events",
          "/gallery", "/contact", "/facilities", "/restoration"]

NOINDEX = re.compile(r'<meta[^>]+name=["\']robots["\'][^>]+noindex', re.I)


def run():
    s = Suite("noindex meta")
    anon = m.app.test_client()

    s.section("the base template renders the block at all")
    base = open(BASE, encoding="utf-8").read()
    s.check("public_base defines block robots", "block robots" in base,
            detail="without this every child override is dead markup")

    s.section("private pages opt out of indexing")
    # Rendered directly rather than fetched: these pages need a real booking
    # and a token, and the question here is only what the template emits.
    for name in ("booking_confirmation.html", "manage_booking.html",
                 "guest_statement.html", "workshop_confirmation.html",
                 "restaurant_confirmation.html", "event_confirmation.html",
                 "guest_feedback_form.html", "find_booking.html"):
        try:
            src = open(os.path.join(TPL, name), encoding="utf-8").read()
        except FileNotFoundError:
            s.check(f"{name} exists", False, detail="template missing")
            continue
        s.check(f"{name} sets noindex",
                "block robots" in src and "noindex" in src,
                detail="a leaked link to this page could be indexed")

    was_live = m.SITE_IS_LIVE
    try:
        s.section("live, the public pages stay indexable")
        m.SITE_IS_LIVE = True
        for path in PUBLIC:
            r = anon.get(path, follow_redirects=True)
            if r.status_code != 200:
                s.check(f"{path} loads", False, detail=str(r.status_code))
                continue
            body = r.get_data(as_text=True)
            s.check(f"{path} is not noindex", not NOINDEX.search(body),
                    detail="this page would be removed from search results")
        # And the per-page overrides still work when live — that is the half
        # the new switch could quietly have broken, since it now wraps the
        # block the overrides depend on.
        body = anon.get("/book/manage", follow_redirects=True).get_data(
            as_text=True)
        s.check("a guest's own page is still noindex when live",
                bool(NOINDEX.search(body)),
                detail="the switch wraps the block those pages override; if "
                       "the wrapping swallowed it, every guest page became "
                       "indexable and nothing would say so")
        robots = anon.get("/robots.txt").get_data(as_text=True)
        s.check("and robots.txt offers the site properly",
                "Disallow: /admin" in robots
                and not robots.strip().endswith("Disallow: /"),
                detail=robots[:120])

        s.section("staging, nothing is offered at all")
        m.SITE_IS_LIVE = False
        for path in PUBLIC:
            r = anon.get(path, follow_redirects=True)
            if r.status_code != 200:
                continue
            s.check(f"{path} is noindex while staging",
                    bool(NOINDEX.search(r.get_data(as_text=True))),
                    detail="a copy of the château on a public address "
                           "competes with the house for its own name")
        robots = anon.get("/robots.txt").get_data(as_text=True)
        s.check("and robots.txt refuses everything",
                robots.strip().endswith("Disallow: /"), detail=repr(robots))

        # Read off the SOURCE, not the environment. Asking what this machine
        # happens to have set says nothing about what a fresh deployment gets,
        # and a fresh deployment getting "live" is the whole failure: the
        # staging copy is indexed the hour it goes up, and nothing anywhere
        # reports it.
        line = [l for l in io.open(
            os.path.join(_harness.ROOT, "app.py"), encoding="utf-8"
        ).read().splitlines() if l.startswith("SITE_IS_LIVE = ")]
        s.check("the switch is read from the environment at all", len(line) == 1,
                detail=str(line))
        s.check("and a deployment nobody has configured is NOT live",
                line and '"SITE_IS_LIVE", "0"' in line[0],
                detail="%s — the default must be off, so a new deployment is "
                       "private until somebody says otherwise"
                       % (line[0] if line else "not found"))
    finally:
        m.SITE_IS_LIVE = was_live

    return s


if __name__ == "__main__":
    print(run().report())
