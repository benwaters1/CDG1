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
"""
import re

from _harness import Suite, clients
import _harness

m = _harness.m

# The block only works if the base template renders it. A child overriding a
# block the parent never outputs is inert, which is exactly the failure mode.
BASE = "templates/public_base.html"

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
            src = open(f"templates/{name}", encoding="utf-8").read()
        except FileNotFoundError:
            s.check(f"{name} exists", False, detail="template missing")
            continue
        s.check(f"{name} sets noindex",
                "block robots" in src and "noindex" in src,
                detail="a leaked link to this page could be indexed")

    s.section("public pages stay indexable")
    for path in PUBLIC:
        r = anon.get(path, follow_redirects=True)
        if r.status_code != 200:
            s.check(f"{path} loads", False, detail=str(r.status_code))
            continue
        body = r.get_data(as_text=True)
        s.check(f"{path} is not noindex", not NOINDEX.search(body),
                detail="this page would be removed from search results")

    return s


if __name__ == "__main__":
    print(run().report())
