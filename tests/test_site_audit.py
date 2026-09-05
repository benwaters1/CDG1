"""The public site as a stranger is handed it, rather than as it is written.

tools/site_audit.py walks the site with no cookie and reports on its structure,
what works, and what a person can actually use. This file freezes the handful
of things that report found which must never come back. The rest stays in the
tool, because it counts pages and links and those numbers move with the
content -- and a number that moves cannot be a pass mark.

WHAT IT FOUND, AND WHY NONE OF IT WAS ALREADY CAUGHT.

  A form on the contact page posted to a route that took GET only. The guest
  filled it in, got a bare 405, and the enquiry went nowhere. Every existing
  check passed: url_for resolved, the template was valid, the route worked.
  The form and the route simply disagreed about the method, and only
  submitting it finds that.

  /restaurant/book answered 404 while the site linked to it from the bar on
  every restaurant page. The dining room is switched off in settings and the
  route called abort(404) -- correct as a route, wrong as a site, because a
  guest tapping "Reserve" was told the page did not exist.

  The footer's headings were h4 under an h2, so every public page at once
  skipped a level. A screen reader announces a missing level as a missing
  section.

  Two navigation links said "Press" and went to different places -- one to the
  press page, one to a section of the restoration page. test_navigation calls
  that "one answer, two doors" for the staff app; the public site had it too.
"""
from _harness import Suite

import re

import _harness

m = _harness.m

# Every public page a stranger can open without a token of their own.
PUBLIC = ["/", "/book", "/restaurant", "/restaurant/book", "/workshops",
          "/events", "/events/weddings", "/events/photoshoots", "/events/private",
          "/facilities", "/restoration", "/gallery", "/press", "/story",
          "/contact", "/privacy", "/book/manage"]


def _headings(html):
    return [int(l) for l, _ in re.findall(r"<h([1-6])[^>]*>(.*?)</h\1>", html, re.S)]


def _links(html):
    out = []
    for href, inner in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        out.append((href, " ".join(re.sub(r"<[^>]+>", " ", inner).split())))
    return out


def run():
    s = Suite("the public site, walked")
    anon = m.app.test_client()          # no cookie: a stranger

    s.section("Every page the site links to answers")

    pages = {}
    for path in PUBLIC:
        r = anon.get(path)
        pages[path] = r
        s.check("%s answers" % path, r.status_code == 200,
                detail="HTTP %s" % r.status_code)

    # The one that was a 404 while two places linked to it.
    closed = anon.get("/restaurant/book")
    s.check("the dining room says it is not taking bookings, rather than 404",
            closed.status_code == 200,
            detail="a guest tapping Reserve was told the page does not exist")
    if closed.status_code == 200:
        body = closed.get_data(as_text=True).lower()
        s.check("and says so in words a guest can act on",
                "not taking reservations" in body or "not open" in body)
        s.check("and offers somewhere to go instead", "/contact" in body)

    s.section("Every form posts somewhere that accepts it")

    bad = []
    for path, r in pages.items():
        if r.status_code != 200:
            continue
        html = r.get_data(as_text=True)
        for tag in re.findall(r"<form[^>]*>", html):
            method = (re.search(r'method="([a-z]+)"', tag, re.I) or [None, "get"])[1].lower()
            action = (re.search(r'action="([^"]*)"', tag) or [None, path])[1] or path
            target = action.split("?")[0]
            if not target.startswith("/"):
                continue
            if not m.app.url_map.bind("x").test(target, method.upper()):
                bad.append("%s -> %s (%s)" % (path, target, method))
    # This is the check that would have caught the contact form: the route
    # existed, url_for resolved, and it did not accept POST.
    s.check("no form posts to a route that will not take it", not bad,
            detail="; ".join(bad[:3]))

    s.section("The enquiry from somebody without dates")

    r = anon.post("/contact", data={
        "action": "flexible", "rough_month": "2028-04", "rough_nights": "4",
        "rough_guests": "2", "flexibility": "a few days either way",
        "email": "zzaudit@example.invalid", "message": "ZZAUDIT roughly April"},
        follow_redirects=True)
    s.check("it is accepted rather than refused", r.status_code == 200,
            detail="HTTP %s — a 405 here loses the enquiry silently" % r.status_code)
    conn = m.get_db()
    kept = conn.execute(
        "SELECT * FROM waitlist_entries WHERE email = ? ORDER BY id DESC LIMIT 1",
        ("zzaudit@example.invalid",)).fetchone()
    s.check("and kept", kept is not None,
            detail="; ".join(_harness.flashes(r)[:1]))
    if kept:
        # The month, not a guessed window inside it: the nudge when a night
        # frees up overlaps this range, so a five-night guess from the first
        # would miss most of the month they asked about.
        s.check("across the whole month they asked about",
                str(kept["desired_arrival"])[:10] == "2028-04-01"
                and str(kept["desired_departure"])[:10] == "2028-05-01",
                detail="%s to %s" % (kept["desired_arrival"], kept["desired_departure"]))
        s.check("with what they actually said kept in words",
                "4 nights" in (kept["notes"] or "")
                and "flexible" in (kept["notes"] or ""),
                detail=str(kept["notes"])[:70])
    task = conn.execute(
        "SELECT * FROM tasks WHERE title LIKE ? ORDER BY id DESC LIMIT 1",
        ("%zzaudit@example.invalid%",)).fetchone()
    s.check("and somebody is asked to answer it", task is not None,
            detail="an enquiry in a list nobody opens is the state this was in")
    r = anon.post("/contact", data={"action": "flexible", "email": "not-an-address"},
                  follow_redirects=True)
    s.check("an address it could not reply to is refused",
            any("does not look right" in f for f in _harness.flashes(r)),
            detail="; ".join(_harness.flashes(r)[:1]))
    conn.execute("DELETE FROM waitlist_entries WHERE email = ?",
                 ("zzaudit@example.invalid",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%zzaudit@example.invalid%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'contact_enquiry'")
    conn.commit()
    conn.close()

    s.section("And the other form that posts to the same place")

    # Two forms post to /contact and they ask different questions. "Tell us
    # roughly" is a month and a length; "tell me when" is a month and an
    # address. Reading only the first set of names answered the second with
    # "that email address does not look right".
    r = anon.post("/contact", data={
        "action": "notify", "notify_month": "November", "notify_year": "2028",
        "notify_email": "zzaudit2@example.invalid"}, follow_redirects=True)
    conn = m.get_db()
    told = conn.execute(
        "SELECT * FROM waitlist_entries WHERE email = ? ORDER BY id DESC LIMIT 1",
        ("zzaudit2@example.invalid",)).fetchone()
    s.check("asking to be told when a month opens is understood", told is not None,
            detail="; ".join(_harness.flashes(r)[:1]))
    if told:
        s.check("and filed against that month",
                str(told["desired_arrival"])[:10] == "2028-11-01"
                and str(told["desired_departure"])[:10] == "2028-12-01",
                detail="%s to %s" % (told["desired_arrival"], told["desired_departure"]))
    conn.execute("DELETE FROM waitlist_entries WHERE email = ?",
                 ("zzaudit2@example.invalid",))
    conn.execute("DELETE FROM submission_log WHERE action = 'contact_enquiry'")
    conn.commit()
    conn.close()

    # With CSRF on in production a form without a token is not weakly guarded,
    # it is unusable: the guest presses the button and gets the timeout page.
    tokenless = []
    for path, r in pages.items():
        if r.status_code != 200:
            continue
        html = r.get_data(as_text=True)
        for mt in re.finditer(r"<form[^>]*method=\"post\"[^>]*>", html, re.I):
            chunk = html[mt.start():html.find("</form>", mt.start()) + 7]
            if "csrf_token" not in chunk:
                tokenless.append("%s: %s" % (path, mt.group(0)[:60]))
    s.check("every public form carries a csrf token", not tokenless,
            detail="; ".join(tokenless[:3]))

    s.section("A screen reader can follow the headings")

    skipped = []
    for path, r in pages.items():
        if r.status_code != 200:
            continue
        levels = _headings(r.get_data(as_text=True))
        for a, b in zip(levels, levels[1:]):
            if b > a + 1:
                skipped.append("%s (h%d then h%d)" % (path, a, b))
                break
    s.check("no page skips a heading level", not skipped,
            detail="; ".join(skipped[:4]) or "")

    s.section("One label, one destination")

    clashes = []
    for path, r in pages.items():
        if r.status_code != 200:
            continue
        by_text = {}
        for href, text in _links(r.get_data(as_text=True)):
            key = text.strip().lower()
            if key and href.startswith("/"):
                by_text.setdefault(key, set()).add(href.split("#")[0])
        for text, hrefs in by_text.items():
            # Siblings are fine and are most of them: "Book in" on each of five
            # room cards goes to five rooms, and the card it sits in is what
            # names it. What is not fine is one word for two unrelated parts of
            # the site -- "Press" going to the press page from the footer and
            # to a section of the restoration page from the navigation, which
            # is what this found. Judged on the first path segment.
            areas = {h.strip("/").split("/")[0] for h in hrefs}
            if len(areas) > 1:
                clashes.append("%s: %r -> %s" % (path, text[:24],
                                                 ", ".join(sorted(hrefs))))
    s.check("no page offers one word for two unrelated places", not clashes,
            detail="; ".join(clashes[:3]))

    s.section("A guest's own details fill themselves in")

    # A workshop registration page too, which is where three of the six live.
    # Checking only the pages that LIST things missed them entirely: the form a
    # guest actually fills in is one level further in.
    conn = m.get_db()
    ses = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    missing = []
    for path in ("/book", "/events", "/workshops",
                 "/workshops/register/%d" % ses["id"] if ses else "/workshops"):
        r = anon.get(path)
        if r.status_code != 200:
            continue
        for tag in re.findall(r"<input[^>]*>", r.get_data(as_text=True)):
            name = (re.search(r'name="([a-z_]+)"', tag) or [None, ""])[1]
            if name in ("guest_name", "contact_name", "guest_email",
                        "contact_email", "guest_phone", "contact_phone") \
                    and "autocomplete=" not in tag:
                missing.append("%s: %s" % (path, name))
    # Only the guest-facing forms. The staff ones deliberately have none --
    # autocomplete on a walk-in booking offers the receptionist their own name
    # while they are typing somebody else's.
    s.check("the public forms let a phone fill in the guest's own details",
            not missing, detail="; ".join(sorted(set(missing))[:4]))

    return s


if __name__ == "__main__":
    print(run().report())
