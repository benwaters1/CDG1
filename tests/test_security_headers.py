"""The headers every response carries, and the two that must NOT be everywhere.

Most of this file is ordinary: five headers, present on every page. The two
checks worth the file are the exceptions, because both would fail silently.

Referrer-Policy is the interesting one. A guest's manage link carries its token
in the path, so a URL that reaches a third party IS the booking. Twenty-four
templates already override `{% block robots %}` with noindex for this, but
noindex and robots.txt only ask a crawler not to fetch a path — a URL handed
over in a Referer header can be indexed without ever being crawled. same-origin
closes that half, and nothing else in the app does.

The Outlook add-in must stay frameable. Outlook renders the taskpane inside its
own iframe, so X-Frame-Options: DENY on those paths does not error — the panel
just comes up blank, which reads as "the add-in was never installed" and gets
reported as nothing at all.

Camera must stay permitted. Expense receipts and asset photos use
<input capture="environment">, so a tidy-looking `camera=()` would break
photographing an invoice on exactly the devices staff use for it, and nowhere
else. That is why this asserts the header does NOT mention camera.
"""
from _harness import Suite, clients
import _harness

m = _harness.m

EXPECTED = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def run():
    s = Suite("Security headers")
    oc, ec, owner, emp = clients()

    s.section("Every response carries them")
    r = oc.get("/")
    for header, value in EXPECTED.items():
        s.check(f"{header}: {value}", r.headers.get(header) == value,
                detail=f"got {r.headers.get(header)!r}")
    csp = r.headers.get("Content-Security-Policy") or ""
    s.check("a content security policy is set", bool(csp))
    for directive in ("frame-ancestors", "base-uri", "form-action"):
        s.check(f"it carries {directive}", directive in csp, detail=f"{csp!r}")

    s.section("A guest page gets the referrer protection too")
    # The pages whose URLs are the credential. If the header were ever set only
    # on the admin side, this is the half that would matter.
    anon = m.app.test_client()
    pub = anon.get("/find-booking")
    s.check("the public side sets Referrer-Policy",
            pub.headers.get("Referrer-Policy") == "same-origin",
            detail=f"got {pub.headers.get('Referrer-Policy')!r} — a guest's "
                   "manage token can leave in a Referer header")

    s.section("HSTS only where there is TLS to insist on")
    s.check("not sent over plain http", r.headers.get("Strict-Transport-Security") is None,
            detail="promising HSTS on a local http connection locks the "
                   "developer out of their own machine")
    # Railway terminates TLS and forwards plain HTTP, and there is no ProxyFix,
    # so the forwarded header is the only thing that says how the guest arrived.
    fwd = oc.get("/", headers={"X-Forwarded-Proto": "https"})
    sts = fwd.headers.get("Strict-Transport-Security") or ""
    s.check("sent when the proxy says the guest came over https", "max-age=" in sts,
            detail=f"got {sts!r}")
    s.check("for at least a year", "31536000" in sts, detail=f"got {sts!r}")

    s.section("The Outlook add-in stays frameable")
    for path in ("/outlook-addin/taskpane", "/outlook-addin/compose"):
        page = oc.get(path)
        blocked = (page.headers.get("X-Frame-Options") is not None
                   or "frame-ancestors" in (page.headers.get("Content-Security-Policy") or ""))
        s.check(f"{path} is not frame-blocked", not blocked,
                detail="Outlook renders this in an iframe — blocking it leaves "
                       "the taskpane blank, which nobody reports as a bug")
        s.check(f"{path} still gets the other headers",
                page.headers.get("X-Content-Type-Options") == "nosniff",
                detail="the add-in exemption dropped more than the frame rule")

    s.section("Camera is deliberately still allowed")
    pp = r.headers.get("Permissions-Policy") or ""
    s.check("a permissions policy is set", bool(pp))
    s.check("geolocation is off", "geolocation=()" in pp, detail=f"{pp!r}")
    s.check("microphone is off", "microphone=()" in pp, detail=f"{pp!r}")
    s.check("but camera is NOT restricted", "camera" not in pp,
            detail=f"{pp!r} — expense receipts and asset photos use "
                   "<input capture>, and this would break them on phones only")

    s.section("form-action, which decides whether anybody can pay")
    # The directive that took the payment path down without erroring. Every
    # checkout here is a form POST answered with a 303 to Stripe's hosted
    # page, and a browser checks form-action against where a submission ENDS
    # UP, redirects included. Set to 'self' alone, the POST is made, the
    # Checkout Session is created and charged to the account, and the browser
    # refuses to follow the redirect. Nothing on this side errors. From the
    # outside it is indistinguishable from a dead API key, and that is what
    # it was taken for -- the keys were checked twice before anyone read a
    # header.
    #
    # Checked on a PUBLIC page, because that is where a guest pays. The one
    # above reads the owner's home.
    guest_csp = anon.get("/book").headers.get("Content-Security-Policy") or ""
    action = [d.strip() for d in guest_csp.split(";")
              if d.strip().startswith("form-action")]
    s.check("the public booking page carries form-action", bool(action),
            detail=repr(guest_csp))
    s.check("and it names Stripe's checkout, or no guest can pay",
            action and "https://checkout.stripe.com" in action[0],
            detail="%s -- the browser blocks the redirect and the app never "
                   "hears about it" % (action[0] if action else "absent"))
    s.check("while still keeping this site's own forms on this site",
            action and "'self'" in action[0],
            detail="%s -- dropping 'self' would stop every ordinary form"
                   % (action[0] if action else "absent"))
    # Narrow on purpose: whatever Stripe does after the redirect happens on
    # Stripe's origin, where this policy does not apply. Widening it to a
    # wildcard buys nothing and costs the thing form-action is for.
    s.check("and is not widened to the whole of stripe.com",
            action and "*.stripe.com" not in action[0],
            detail=action[0] if action else "absent")

    return s
