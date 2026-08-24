"""What a guest sees when they mistype an address or something breaks.

Until now a 404 was Flask's bare white page — no way back to the château, no
sign it was even the same website. The commonest way to reach one is not a
typo: it is a manage link from an email that has expired, which means the
person seeing it is a guest who has already paid and is trying to find their
own booking.

The 500 half is deliberately thin. A handler that depends on anything which
might itself be broken is a handler that raises inside the failure it exists
to soften, so it is passed nothing but the code.
"""
import re

from _harness import Suite, clients
import _harness

m = _harness.m


def run():
    s = Suite("The error pages")
    _oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()

    s.section("A mistyped address lands on the château")
    r = anon.get("/no-such-page-at-all")
    s.check("it is still a 404", r.status_code == 404, detail=str(r.status_code))
    body = r.get_data(as_text=True)
    s.check("but a themed one, not Flask's bare page",
            "g-wrap" in body or "g-nav" in body, detail="public shell missing")
    heading = re.search(r"<h1[^>]*>([^<]+)", body)
    s.check("with a heading that says so", bool(heading),
            detail=heading.group(1).strip() if heading else "(no h1)")

    s.section("And offers a way back")
    # The point of the page. Somebody who has lost their booking link needs
    # find-my-booking, not an apology.
    for label, needle in [("the front page", 'href="/"'),
                          ("the rooms", "/book"),
                          ("find my booking", "/book/manage"),
                          ("contact", "/contact")]:
        s.check(f"a route to {label}", needle in body, detail=f"{needle} missing")

    s.section("An expired manage link is the common case")
    for label, url in [("a room booking", "/book/confirmation/nobody-issued-this"),
                       ("a restaurant booking", "/restaurant/confirmation/nope"),
                       ("a workshop", "/workshops/confirmation/nope")]:
        r = anon.get(url)
        s.check(f"{label} link that has expired is themed",
                r.status_code == 404 and "g-wrap" in r.get_data(as_text=True),
                detail=str(r.status_code))

    s.section("A machine gets JSON, not a page to parse")
    # Two /api/ routes abort(404), and their caller is a cron job. Handing a
    # scheduled task a page of HTML is how it fails silently.
    r = anon.get("/api/sync-ical")
    s.check("the API 404 is JSON",
            r.headers.get("Content-Type", "").startswith("application/json"),
            detail=r.headers.get("Content-Type", ""))
    s.check("and says what happened", "not found" in r.get_data(as_text=True),
            detail=r.get_data(as_text=True)[:60])
    s.check("an unknown /api/ path too",
            anon.get("/api/does-not-exist").headers
            .get("Content-Type", "").startswith("application/json"))

    s.section("A 500 is softened without leaking anything")
    # Called directly rather than by breaking a route: Flask refuses new routes
    # once the app has served a request, and the handler is the thing under
    # test either way.
    with m.app.test_request_context("/something"):
        page, code = m.server_error(RuntimeError("deliberate, for the test"))
    s.check("it is a 500", code == 500, detail=str(code))
    s.check("themed like the rest of the site", "g-wrap" in page or "g-nav" in page)
    s.check("and the exception is not on the page",
            "RuntimeError" not in page and "deliberate, for the test" not in page,
            detail="the error text reached the guest")
    s.check("nor a traceback", "Traceback" not in page)

    with m.app.test_request_context("/api/something"):
        payload, code = m.server_error(RuntimeError("deliberate"))
    s.check("the API 500 is JSON too", code == 500 and payload.is_json,
            detail=str(code))

    s.section("The handlers are actually registered")
    # A handler defined but never wired is the failure this whole suite would
    # otherwise miss — every check above would pass against Flask's own page
    # if it happened to contain the same words.
    # Not sorted: the keys are a mix of ints and None — CSRFError is
    # registered by exception class, not by status code — and sorting mixed
    # types raises. It only raised depending on which suite ran first, which
    # is the worst kind of intermittent.
    registered = m.app.error_handler_spec[None]
    s.check("404 is handled by ours", 404 in registered,
            detail=str([k for k in registered if isinstance(k, int)]))
    s.check("500 is handled by ours", 500 in registered)

    return s
