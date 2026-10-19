"""The staff side at its own address, admin.chateaugudanes.com (owner, 19 Oct 2026).

One deployment answers on three names: www.chateaugudanes.com is the public
site, admin.chateaugudanes.com is the staff side, and the Railway address is
where the Outlook add-in is installed in everybody's Outlook. This holds the
three apart, and holds one sign-in across the house's two.

WHAT IS REALLY BEING GUARDED, since "does / redirect" is the easy half.

  THE SIGN-IN MUST BE ONE SIGN-IN. The cookie that carries it is scoped to the
  house's domain and named differently from the host-only one www has always
  set, because a browser holding both sends the older first and Flask reads
  the first: a sign-in written and never read back, forms failing their CSRF
  check, and not one error anywhere. So the check signs in for real, through
  the form, with CSRF ON, and then asks www who it is.

  A GUEST'S PAGE IS NEVER TOUCHED. The admin bar carries admin links and is
  sent with Cache-Control: private. A guest getting either -- the bar, or the
  header that would stop their pages being cached -- is the expensive mistake,
  so it is checked on several pages, for a guest and for a member of staff
  with no part of the owner's side.

  THE RAILWAY ADDRESS IS EXACTLY AS IT WAS. A cookie scoped to
  .chateaugudanes.com sent from Railway is refused by the browser, and the
  add-in could never sign in again. Nothing errors when that happens; the
  taskpane simply asks again, for ever.

  AND NOTHING IS SENT TO admin. FROM A COPY THAT IS NOT THE HOUSE'S. A staging
  copy sending its staff to the real admin. signs them in on the wrong
  database.
"""
import re

from werkzeug.security import generate_password_hash

from _harness import Suite, clients

import _harness

m = _harness.m

ADMIN = "https://admin.chateaugudanes.com"
HOUSE = "https://www.chateaugudanes.com"
APEX = "https://chateaugudanes.com"
RAILWAY = "https://cdg1-production-9186.up.railway.app"
LOCAL = "http://localhost"

# The same words test_routes skips a GET on: a download, a sign-out, a
# webhook -- each answers by doing something, not by drawing a page.
SKIP_WORDS = ("export", "backup", "logout", "download", "webhook", "static",
              "stripe-cancel", "pay-deposit", "pay-balance")


def _signed_in(user_id, base_url):
    """A client signed in the way a browser would be after signing in on base_url."""
    c = m.app.test_client()
    with c.session_transaction(base_url=base_url) as sess:
        sess["user_id"] = user_id
    return c


def _bar(response):
    return "g-adminbar" in response.get_data(as_text=True)


def _bar_links(response):
    body = response.get_data(as_text=True)
    found = re.search(r'<div class="g-adminbar".*?</div>', body, re.S)
    return re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', found.group(0)) if found else []


def _cache(response):
    return (response.headers.get("Cache-Control") or "").lower()


def _csrf(response):
    found = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    return found.group(1) if found else None


def _cookie_lines(response):
    return response.headers.getlist("Set-Cookie")


def run():
    s = Suite("The staff side at its own address")
    _oc, _ec, owner, emp = clients()
    was = (m.SITE_IS_LIVE, m.PUBLIC_BASE_URL, m.ADMIN_HOST, m.SHARED_COOKIE_DOMAIN)
    try:
        m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = True, HOUSE
        m.ADMIN_HOST, m.SHARED_COOKIE_DOMAIN = "admin.chateaugudanes.com", ".chateaugudanes.com"
        _host_routing(s, owner)
        _shared_sign_in(s, owner)
        _admin_bar(s, owner, emp)
        _never_indexed(s, owner)
        _railway_untouched(s, owner)
        _one_setting(s, owner)
    finally:
        (m.SITE_IS_LIVE, m.PUBLIC_BASE_URL, m.ADMIN_HOST, m.SHARED_COOKIE_DOMAIN) = was
    return s


def _host_routing(s, owner):
    s.section("admin. is the staff side, and only the staff side")
    guest = m.app.test_client()
    r = guest.get("/", base_url=ADMIN)
    s.check("admin.'s front door is the staff home",
            r.status_code == 302 and (r.headers.get("Location") or "").endswith("/admin"),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = guest.get("/?from=bookmark", base_url=ADMIN)
    s.check("with the tail of the address",
            (r.headers.get("Location") or "").endswith("/admin?from=bookmark"),
            detail=str(r.headers.get("Location")))
    r = guest.get("/admin", base_url=ADMIN)
    s.check("and somebody not signed in is asked to sign in, on admin.",
            r.status_code == 302 and r.headers.get("Location") == ADMIN + "/login",
            detail=f"{r.status_code} {r.headers.get('Location')}")
    oc = _signed_in(owner["id"], ADMIN)
    r = oc.get("/admin", base_url=ADMIN)
    s.check("signed in, /admin is the dashboard",
            r.status_code == 200 and "staff-shell" in r.get_data(as_text=True),
            detail=str(r.status_code))

    # Every staff page with no parameters, on admin., for the owner. None may
    # send them to www (the move must only ever run the other way), and none
    # may fall over for being on a different name.
    moved, broken, walked = [], [], 0
    for rule in sorted(m.app.url_map.iter_rules(), key=str):
        path = str(rule)
        if ("GET" not in rule.methods or "<" in path or any(w in path for w in SKIP_WORDS)
                or not m.is_staff_page(rule.endpoint)):
            continue
        walked += 1
        try:
            r = oc.get(path, base_url=ADMIN)
        except Exception as e:
            broken.append(f"{path}: {type(e).__name__}")
            continue
        where = r.headers.get("Location") or ""
        if r.status_code in (301, 302, 303, 307, 308) and where.startswith(("http://", "https://")) \
                and not where.startswith(ADMIN):
            moved.append(f"{path} -> {where}")
        if r.status_code >= 500:
            broken.append(f"{path}: {r.status_code}")
    s.check(f"all {walked} staff pages stay on admin.", walked > 100 and not moved,
            detail=f"walked {walked}; " + "; ".join(moved[:5]))
    s.check("and every one of them answers there", not broken, detail="; ".join(broken[:5]))

    r = guest.get("/story?utm_source=newsletter", base_url=ADMIN)
    s.check("a public page asked for on admin. goes to www, path and query",
            r.status_code == 301
            and r.headers.get("Location") == HOUSE + "/story?utm_source=newsletter",
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = oc.get("/book", base_url=ADMIN)
    s.check("for staff as well as guests", r.status_code == 301
            and r.headers.get("Location") == HOUSE + "/book",
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = guest.get("/rooms", base_url=ADMIN)
    s.check("and an old Squarespace link on admin. reaches www in one hop",
            r.headers.get("Location") == HOUSE + "/book", detail=str(r.headers.get("Location")))

    s.section("Staff pages asked for on www go to admin.")
    wc = _signed_in(owner["id"], HOUSE)
    r = wc.get("/admin/rooms?show=all", base_url=HOUSE)
    s.check("a staff page on www goes to the same page on admin.",
            r.status_code == 302 and r.headers.get("Location") == ADMIN + "/admin/rooms?show=all",
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = wc.get("/admin/rooms", base_url=APEX)
    s.check("and from the bare domain too",
            r.headers.get("Location") == ADMIN + "/admin/rooms", detail=str(r.headers.get("Location")))
    s.check("a 302, never a 301 a browser keeps for good", r.status_code == 302)
    # The language switch answers a POST? No -- clock-in is the staff POST to
    # hand, but it writes a shift. A POST to a staff page is checked only for
    # NOT being moved: the redirect would drop what was sent.
    r = m.app.test_client().post("/admin/rooms/keep-open", data={}, base_url=HOUSE)
    s.check("a form sent to a staff page on www is not moved",
            not (r.headers.get("Location") or "").startswith(ADMIN + "/admin/rooms/keep-open"),
            detail=f"{r.status_code} {r.headers.get('Location')}")


def _shared_sign_in(s, owner):
    s.section("One sign-in across admin. and www, with CSRF on")
    conn = _harness.db()
    old_hash = conn.execute("SELECT password_hash FROM users WHERE id = ?",
                            (owner["id"],)).fetchone()["password_hash"]
    email = conn.execute("SELECT email FROM users WHERE id = ?", (owner["id"],)).fetchone()["email"]
    password = "test-only-" + "x" * 12
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (generate_password_hash(password), owner["id"]))
    conn.execute("DELETE FROM login_throttle")
    conn.commit()
    conn.close()
    m.app.config["WTF_CSRF_ENABLED"] = True
    try:
        c = m.app.test_client()
        page = c.get("/login", base_url=ADMIN)
        token = _csrf(page)
        s.check("the sign-in page is drawn on admin.", page.status_code == 200 and token,
                detail=str(page.status_code))
        r = c.post("/login", data={"email": email, "password": password, "csrf_token": token},
                   base_url=ADMIN, headers={"Referer": ADMIN + "/login"})
        s.check("signing in on admin. lands on the staff home there",
                r.status_code == 302 and r.headers.get("Location") == ADMIN + "/admin",
                detail=f"{r.status_code} {r.headers.get('Location')}")
        lines = [ln for ln in _cookie_lines(r) if ln.startswith(m.SHARED_SESSION_COOKIE_NAME + "=")]
        line = (lines[-1] if lines else "").lower()
        s.check("the cookie is the shared one", bool(lines), detail=str(_cookie_lines(r)))
        s.check("scoped to the house's domain",
                "domain=.chateaugudanes.com" in line or "domain=chateaugudanes.com" in line,
                detail=line)
        s.check("Secure", "; secure" in line, detail=line)
        s.check("SameSite=Lax", "samesite=lax" in line, detail=line)
        s.check("and out of reach of the page's scripts", "httponly" in line, detail=line)

        r = c.get("/book", base_url=HOUSE)
        s.check("and www knows who it is: the admin bar is there", _bar(r),
                detail="signed in on admin., a guest on www -- the cookie is not shared")

        # CSRF on both names, with the one session. /events/find answers a POST
        # with a 200 when the token is good, and the CSRF handler answers with a
        # redirect when it is not -- so the status alone says which happened.
        token = _csrf(c.get("/admin/rooms", base_url=ADMIN)) or token
        r = c.post("/events/find", data={"csrf_token": token}, base_url=HOUSE,
                   headers={"Referer": HOUSE + "/events/find"})
        s.check("a token drawn on admin. is good on www", r.status_code == 200,
                detail=f"{r.status_code} {r.headers.get('Location')}")
        www_token = _csrf(c.get("/events/find", base_url=HOUSE)) or _csrf(c.get("/contact", base_url=HOUSE))
        r = c.post("/events/find", data={"csrf_token": www_token}, base_url=ADMIN,
                   headers={"Referer": ADMIN + "/events/find"})
        s.check("and one drawn on www is good on admin.", r.status_code == 200,
                detail=f"{r.status_code} {r.headers.get('Location')} token={bool(www_token)}")
        r = c.post("/events/find", data={}, base_url=HOUSE, headers={"Referer": HOUSE + "/"})
        s.check("while no token at all is still refused (CSRF really is on)",
                r.status_code == 302, detail=str(r.status_code))

        r = c.get("/logout", base_url=HOUSE)
        s.check("signing out on www is done on admin.",
                r.headers.get("Location") == ADMIN + "/logout", detail=str(r.headers.get("Location")))
        r = c.get("/logout", base_url=ADMIN)
        s.check("and ends it on both", r.headers.get("Location") == ADMIN + "/login"
                and not _bar(c.get("/book", base_url=HOUSE))
                and c.get("/admin", base_url=ADMIN).headers.get("Location") == ADMIN + "/login",
                detail=str(r.headers.get("Location")))

        s.section("The Railway address and a laptop keep a cookie of their own")
        rc = m.app.test_client()
        token = _csrf(rc.get("/login", base_url=RAILWAY))
        r = rc.post("/login", data={"email": email, "password": password, "csrf_token": token},
                    base_url=RAILWAY, headers={"Referer": RAILWAY + "/login"})
        line = next((ln for ln in _cookie_lines(r) if ln.startswith("session=")), "")
        s.check("signing in on Railway sets the cookie it always did", bool(line),
                detail=str(_cookie_lines(r)))
        s.check("with no Domain, which the browser would refuse there",
                line and "domain=" not in line.lower(), detail=line)
        s.check("and lands on the staff home on Railway, as before",
                r.status_code == 302 and r.headers.get("Location") == "/",
                detail=f"{r.status_code} {r.headers.get('Location')}")
        s.check("where the staff dashboard still answers", rc.get("/", base_url=RAILWAY).status_code == 200)
        s.check("and a house cookie is never sent to Railway",
                _signed_in(owner["id"], ADMIN).get("/admin/rooms", base_url=RAILWAY)
                .headers.get("Location") == "/login")
        lc = m.app.test_client()
        r = lc.get("/login", base_url=LOCAL)
        with lc.session_transaction(base_url=LOCAL) as sess:
            sess["probe"] = 1
        names = {ck.key for ck in lc._cookies.values()}
        s.check("localhost keeps the plain cookie, so local dev signs in",
                "session" in names and m.SHARED_SESSION_COOKIE_NAME not in names, detail=str(names))
    finally:
        m.app.config["WTF_CSRF_ENABLED"] = False
        conn = _harness.db()
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (old_hash, owner["id"]))
        conn.execute("DELETE FROM login_throttle")
        conn.commit()
        conn.close()


def _admin_bar(s, owner, emp):
    s.section("The admin bar, for the owner's side only")
    oc = _signed_in(owner["id"], ADMIN)
    r = oc.get("/book", base_url=HOUSE)
    links = _bar_links(r)
    s.check("the owner on a public page gets the bar", r.status_code == 200 and links,
            detail=str(r.status_code))
    s.check("with Admin, to admin.", (ADMIN + "/admin", "Admin") in links, detail=str(links))
    s.check("and Edit this page, to the screen that sets it",
            (ADMIN + "/admin/rooms", "Edit this page") in links, detail=str(links))
    s.check("sent private, so nothing between keeps it for the next visitor",
            "private" in _cache(r) and "public" not in _cache(r), detail=_cache(r))
    s.check("and varying on the cookie", "cookie" in (r.headers.get("Vary") or "").lower(),
            detail=str(r.headers.get("Vary")))
    body = r.get_data(as_text=True)
    s.check("inside the page's body, before its header",
            re.search(r'<body class="g[^>]*><div class="g-adminbar"', body)
            and body.index("g-adminbar") < body.index('class="g-top"'))

    conn = _harness.db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    conn.close()
    if room:
        r = oc.get(f"/book/{room['id']}", base_url=HOUSE)
        want = ADMIN + f"/admin/rooms/{room['id']}/edit"
        s.check("on a room's page it edits that room",
                (want, "Edit this page") in _bar_links(r), detail=str(_bar_links(r)))
        s.check("and that screen opens on admin.",
                oc.get(want[len(ADMIN):], base_url=ADMIN).status_code == 200)
    r = oc.get("/story", base_url=HOUSE)
    s.check("a page with no screen of its own gets Admin alone",
            [label for _h, label in _bar_links(r)] == ["Admin"], detail=str(_bar_links(r)))
    r = oc.get("/", base_url=HOUSE)
    s.check("and www's front page is the house's, bar and all, not the dashboard",
            _bar(r) and "staff-shell" not in r.get_data(as_text=True), detail=str(r.status_code))
    rules = {rule.endpoint for rule in m.app.url_map.iter_rules()}
    bad = [f"{pub}->{target}" for pub, (target, _k) in m.EDIT_THIS_PAGE.items()
           if pub not in rules or target not in rules or not m.is_staff_page(target)]
    s.check("every Edit this page is a real public page and a real staff screen", not bad,
            detail=str(bad))
    s.check("no bar on the staff side itself", not _bar(oc.get("/admin", base_url=ADMIN)))
    # The one staff page drawn in the public template. Moved by the
    # one-address rule it went to www, which sent it back: a loop.
    r = oc.get("/admin/images", base_url=ADMIN)
    s.check("not even on the staff page that wears the public template",
            r.status_code == 200 and not _bar(r), detail=f"{r.status_code} {r.headers.get('Location')}")
    r = _signed_in(owner["id"], RAILWAY).get("/admin/images", base_url=RAILWAY)
    s.check("which stays where it is asked on Railway too, as a staff page should",
            r.status_code == 200, detail=f"{r.status_code} {r.headers.get('Location')}")

    s.section("A guest's page is never touched")
    guest = m.app.test_client()
    ec = _signed_in(emp["id"], ADMIN) if emp else None
    for path in ("/", "/book", "/story", "/workshops", "/restaurant", "/gallery"):
        r = guest.get(path, base_url=HOUSE)
        s.check(f"{path}: a guest gets no bar and no private header",
                r.status_code == 200 and not _bar(r) and "private" not in _cache(r),
                detail=f"{r.status_code} bar={_bar(r)} cache={_cache(r)}")
        if ec is not None:
            r = ec.get(path, base_url=HOUSE)
            s.check(f"{path}: nor does staff with no part of the owner's side",
                    r.status_code == 200 and not _bar(r) and "private" not in _cache(r),
                    detail=f"{r.status_code} bar={_bar(r)} cache={_cache(r)}")


def _never_indexed(s, owner):
    s.section("Nothing on admin. is indexed")
    guest = m.app.test_client()
    oc = _signed_in(owner["id"], ADMIN)
    for label, r in (("the front door", guest.get("/", base_url=ADMIN)),
                     ("the sign-in page", guest.get("/login", base_url=ADMIN)),
                     ("a staff page", oc.get("/admin", base_url=ADMIN)),
                     ("a public page being moved", guest.get("/story", base_url=ADMIN)),
                     ("a page that is not there", guest.get("/nothing-here", base_url=ADMIN)),
                     ("a stylesheet", guest.get("/static/style.css", base_url=ADMIN)),
                     ("robots.txt itself", guest.get("/robots.txt", base_url=ADMIN))):
        s.check(f"{label} says noindex",
                "noindex" in (r.headers.get("X-Robots-Tag") or ""),
                detail=f"{r.status_code} {r.headers.get('X-Robots-Tag')}")
    body = guest.get("/robots.txt", base_url=ADMIN).get_data(as_text=True)
    s.check("its robots.txt keeps every crawler out",
            body.strip().splitlines() == ["User-agent: *", "Disallow: /"], detail=body[:200])
    r = guest.get("/story", base_url=HOUSE)
    s.check("while www's pages carry no such header",
            r.status_code == 200 and not r.headers.get("X-Robots-Tag"),
            detail=str(r.headers.get("X-Robots-Tag")))
    body = guest.get("/robots.txt", base_url=HOUSE).get_data(as_text=True)
    s.check("and www's robots.txt still opens the public site",
            "Disallow: /admin" in body and "Disallow: /\n" not in body, detail=body[:200])


def _railway_untouched(s, owner):
    s.section("The Railway address, the add-in and Stripe are left alone")
    guest = m.app.test_client()
    rc = _signed_in(owner["id"], RAILWAY)
    r = guest.get("/", base_url=RAILWAY)
    s.check("a guest on Railway's front page still goes to www",
            r.status_code == 301 and r.headers.get("Location") == HOUSE + "/",
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = rc.get("/", base_url=RAILWAY)
    s.check("staff on Railway still get their dashboard at /",
            r.status_code == 200 and "staff-shell" in r.get_data(as_text=True),
            detail=str(r.status_code))
    r = rc.get("/admin/rooms", base_url=RAILWAY)
    s.check("and staff pages answer there, unmoved", r.status_code == 200,
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = guest.get("/admin/rooms", base_url=RAILWAY)
    s.check("signing in on Railway stays on Railway",
            r.headers.get("Location") == "/login", detail=str(r.headers.get("Location")))
    s.check("no noindex header added there",
            not guest.get("/login", base_url=RAILWAY).headers.get("X-Robots-Tag"))
    with m.app.test_request_context("/", base_url=RAILWAY):
        link = m.url_for("admin_feedback", _external=True)
    s.check("a link built on Railway is a Railway link", link.startswith(RAILWAY), detail=link)

    addin = ("/outlook-addin/taskpane", "/outlook-addin/compose",
             "/outlook-addin/launchevent.html", "/outlook-addin/manifest.xml")
    moved = []
    for base in (RAILWAY, ADMIN, HOUSE):
        for client in (guest, _signed_in(owner["id"], base)):
            for p in addin:
                r = client.get(p, base_url=base)
                if r.status_code in (301, 302, 303, 307, 308):
                    moved.append(f"{base}{p} -> {r.headers.get('Location')}")
    s.check("the Outlook add-in is never redirected, on any address", not moved,
            detail="; ".join(moved[:4]))
    moved = []
    for base in (RAILWAY, ADMIN, HOUSE):
        r = guest.post("/webhooks/stripe", data=b"{}", base_url=base,
                       headers={"Stripe-Signature": "t=1,v1=0"},
                       content_type="application/json")
        if r.status_code in (301, 302, 303, 307, 308):
            moved.append(f"{base} -> {r.headers.get('Location')}")
    s.check("nor is Stripe's webhook", not moved, detail="; ".join(moved))


def _one_setting(s, owner):
    s.section("admin. is where staff sign in, decided by one setting")
    guest = m.app.test_client()
    r = guest.get("/login", base_url=HOUSE)
    s.check("the sign-in page on www sends you to admin.'s",
            r.status_code == 302 and r.headers.get("Location") == ADMIN + "/login",
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = guest.post("/admin/rooms/keep-open", data={}, base_url=HOUSE)
    s.check("a sign-in asked for anywhere on the house's names is on admin.",
            r.headers.get("Location") == ADMIN + "/login", detail=str(r.headers.get("Location")))

    with m.app.test_request_context("/", base_url=HOUSE):
        staff = m.external_url("admin_feedback")
        guest_link = m.external_url("book_rooms")
        in_template = m.app.jinja_env.from_string(
            "{{ url_for('admin_feedback', _external=True) }}").render()
        inside = m.url_for("admin_feedback")
    s.check("a staff link in an email is on admin.", staff == ADMIN + "/admin/feedback",
            detail=staff)
    s.check("and in an email template too", in_template == ADMIN + "/admin/feedback",
            detail=in_template)
    s.check("while a guest's link stays on www", guest_link == HOUSE + "/book", detail=guest_link)
    s.check("and a link within a page is left alone", inside == "/admin/feedback", detail=inside)
    with m.app.app_context():
        nightly = m.external_url("admin_feedback")
    s.check("a nightly job's staff link is on admin. too", nightly == ADMIN + "/admin/feedback",
            detail=nightly)

    m.ADMIN_HOST = "office.chateaugudanes.com"
    try:
        r = guest.get("/admin/rooms", base_url=HOUSE)
        s.check("change ADMIN_HOST and everything follows it",
                r.headers.get("Location") == "https://office.chateaugudanes.com/admin/rooms",
                detail=str(r.headers.get("Location")))
        r = guest.get("/", base_url="https://office.chateaugudanes.com")
        s.check("including the front door", (r.headers.get("Location") or "").endswith("/admin"),
                detail=str(r.headers.get("Location")))
        s.check("and the old name is just another name",
                guest.get("/", base_url=ADMIN).status_code == 301)
    finally:
        m.ADMIN_HOST = "admin.chateaugudanes.com"

    m.ADMIN_HOST = ""
    try:
        r = _signed_in(owner["id"], HOUSE).get("/admin/rooms", base_url=HOUSE)
        s.check("set it empty and the staff side stays where it is asked",
                r.status_code == 200, detail=f"{r.status_code} {r.headers.get('Location')}")
        s.check("signing in included",
                guest.get("/admin/rooms", base_url=HOUSE).headers.get("Location") == "/login")
    finally:
        m.ADMIN_HOST = "admin.chateaugudanes.com"

    s.section("A copy that is not the house's never sends anybody to admin.")
    m.SITE_IS_LIVE = False
    try:
        r = guest.get("/admin/rooms", base_url=HOUSE)
        s.check("not live: signing in stays where it was asked",
                r.headers.get("Location") == "/login", detail=str(r.headers.get("Location")))
        with m.app.app_context():
            nightly = m.external_url("admin_feedback")
        s.check("and a job's staff link is the configured address, not admin.",
                nightly == HOUSE + "/admin/feedback", detail=nightly)
        s.check("though anything arriving on admin. still says noindex",
                "noindex" in (guest.get("/login", base_url=ADMIN).headers.get("X-Robots-Tag") or ""))
    finally:
        m.SITE_IS_LIVE = True
    m.PUBLIC_BASE_URL = RAILWAY
    try:
        with m.app.app_context():
            nightly = m.external_url("admin_feedback")
        s.check("nor when PUBLIC_BASE_URL is still the platform's",
                not nightly.startswith(ADMIN), detail=nightly)
        # Live with no house address, robots.txt opens the site wherever it is
        # asked -- so admin. must close itself, not lean on the www rule.
        body = guest.get("/robots.txt", base_url=ADMIN).get_data(as_text=True)
        s.check("and admin.'s robots.txt keeps crawlers out even then",
                body.strip().splitlines() == ["User-agent: *", "Disallow: /"], detail=body[:120])
    finally:
        m.PUBLIC_BASE_URL = HOUSE


if __name__ == "__main__":
    print(run().report())
