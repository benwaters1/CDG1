"""The Outlook add-in keeps its own address, whatever name it is asked on (19 Oct 2026).

Everybody's Outlook has the add-in at the Railway address. Since the staff side
moved to admin.chateaugudanes.com, the owner opens the add-in page there -- and
both that page and the manifest used to build every address from the name the
request came in on. The page offered admin.chateaugudanes.com/outlook-addin/
manifest.xml; installing it moves the add-in off Railway for the whole house,
where its sign-in and its send check live. Nothing would error. The add-in
would simply be somewhere else on the next update.

So the add-in's address is one setting, ADDIN_BASE_URL, with Railway's own
RAILWAY_PUBLIC_DOMAIN standing in only when it holds a *.railway.app name:
Railway documents it as "the public service or customer domain", and on the
house's deployment it may well hold one of the house's names.

WHAT IS CHECKED

  The manifest, fetched on admin., www, the bare domain and Railway, names
  the Railway address at every URL in it, and not one on the name asked.
  The add-in page, opened on admin., links the Railway manifest and gives the
  Railway redirect URI for the Microsoft sign-in.
  With nothing set at all, the house's names still name Railway: the address
  is built in for them (22 Oct 2026), so losing the variables moves nothing.
  A laptop or a staging copy still serves its own add-in, as it always did.
  And /outlook-addin/* still answers on every name -- this moves no request.
"""
import re

from _harness import Suite, clients, db

import _harness

m = _harness.m

ADMIN = "https://admin.chateaugudanes.com"
HOUSE = "https://www.chateaugudanes.com"
APEX = "https://chateaugudanes.com"
RAILWAY = "https://cdg1-production-9186.up.railway.app"
RAILWAY_NAME = "cdg1-production-9186.up.railway.app"
LABEL = "The Outlook add-in's address"


def _signed_in(user_id, base_url):
    c = m.app.test_client()
    with c.session_transaction(base_url=base_url) as sess:
        sess["user_id"] = user_id
    return c


def _manifest_urls(base_url):
    """(status, every address in the manifest as served on base_url, AppDomain)."""
    r = m.app.test_client().get("/outlook-addin/manifest.xml", base_url=base_url)
    body = r.get_data(as_text=True)
    # Microsoft's own schema namespaces are addresses too, and are nobody's host.
    urls = [u for u in re.findall(r'https?://[^"<\s]+', body)
            if "schemas.microsoft.com" not in u and "www.w3.org" not in u]
    domains = re.findall(r"<AppDomain>([^<]*)</AppDomain>", body)
    return r.status_code, urls, domains


def _readiness_line(base_url):
    conn = db()
    try:
        with m.app.test_request_context(base_url=base_url):
            checks = m.readiness_checks(conn, include_slow=False)
    finally:
        conn.close()
    return next((c for c in checks if c["label"] == LABEL), None)


def run():
    s = Suite("The Outlook add-in keeps its own address")
    _oc, _ec, owner, _emp = clients()
    was = (m.SITE_IS_LIVE, m.PUBLIC_BASE_URL, m.ADMIN_HOST, m.SHARED_COOKIE_DOMAIN,
           m.ADDIN_BASE_URL, m.RAILWAY_PUBLIC_DOMAIN)
    try:
        m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = True, HOUSE
        m.ADMIN_HOST, m.SHARED_COOKIE_DOMAIN = "admin.chateaugudanes.com", ".chateaugudanes.com"

        # As Railway runs it: no setting, Railway's variable holding its own name.
        m.ADDIN_BASE_URL, m.RAILWAY_PUBLIC_DOMAIN = "", RAILWAY_NAME
        _manifest_everywhere(s, "with Railway's own name to go on")
        _page_on_admin(s, owner)
        _still_answers(s)

        # Railway's variable holding the custom domain, and the setting saying it.
        m.ADDIN_BASE_URL, m.RAILWAY_PUBLIC_DOMAIN = RAILWAY + "/", "admin.chateaugudanes.com"
        _manifest_everywhere(s, "with ADDIN_BASE_URL set and Railway's variable on admin.")

        _nothing_to_go_on(s, owner)
        _a_copy_serves_its_own(s)
    finally:
        (m.SITE_IS_LIVE, m.PUBLIC_BASE_URL, m.ADMIN_HOST, m.SHARED_COOKIE_DOMAIN,
         m.ADDIN_BASE_URL, m.RAILWAY_PUBLIC_DOMAIN) = was
    return s


def _manifest_everywhere(s, how):
    s.section(f"The manifest names Railway on every name, {how}")
    for base in (ADMIN, HOUSE, APEX, RAILWAY):
        status, urls, domains = _manifest_urls(base)
        off = [u for u in urls if not (u == RAILWAY or u.startswith(RAILWAY + "/"))]
        s.check(f"fetched on {base}: every address in it is on Railway",
                status == 200 and len(urls) >= 10 and not off,
                detail=f"status {status}, {len(urls)} addresses; elsewhere: {off[:3]}")
        s.check(f"fetched on {base}: its AppDomain is Railway", domains == [RAILWAY],
                detail=str(domains))
    # Every address the manifest is known to carry, by name, so a new one
    # built from the request cannot hide behind the count above.
    _status, urls, _domains = _manifest_urls(ADMIN)
    for path in ("/outlook-addin/taskpane", "/outlook-addin/compose",
                 "/outlook-addin/launchevent.html", "/outlook-addin/launchevent.js",
                 "/static/addin/icon-80.png"):
        s.check(f"fetched on admin.: {path} is Railway's", RAILWAY + path in urls,
                detail=str([u for u in urls if path in u]))


def _page_on_admin(s, owner):
    s.section("The add-in page, opened on admin., offers the Railway manifest")
    r = _signed_in(owner["id"], ADMIN).get("/admin/outlook-addin", base_url=ADMIN)
    body = r.get_data(as_text=True)
    s.check("the page answers on admin.", r.status_code == 200, response=r)
    s.check("its manifest link is Railway's",
            f'href="{RAILWAY}/outlook-addin/manifest.xml"' in body)
    s.check("the address to paste into Microsoft 365 admin is Railway's",
            f"<code>{RAILWAY}/outlook-addin/manifest.xml</code>" in body)
    s.check("the sign-in redirect URI is Railway's",
            f"brk-multihub://{RAILWAY_NAME}</code>" in body)
    s.check("and nothing on the page offers the add-in on admin.",
            "admin.chateaugudanes.com/outlook-addin" not in body
            and "brk-multihub://admin." not in body)
    s.check("it says where the address comes from", "RAILWAY_PUBLIC_DOMAIN" in body)
    line = _readiness_line(ADMIN)
    s.check("readiness counts the add-in's address as known",
            line is not None and line["ok"] and RAILWAY in line["detail"], detail=str(line))


def _still_answers(s):
    s.section("/outlook-addin/* answers where it is asked, on every name")
    for base in (ADMIN, HOUSE, APEX, RAILWAY):
        for path in ("/outlook-addin/manifest.xml", "/outlook-addin/taskpane",
                     "/outlook-addin/launchevent.js"):
            r = m.app.test_client().get(path, base_url=base)
            s.check(f"{path} on {base} answers itself, no redirect", r.status_code == 200,
                    detail=f"{r.status_code} {r.headers.get('Location') or ''}")


def _nothing_to_go_on(s, owner):
    # The variables lost: neither set, and Railway's holding the custom domain
    # (or nothing). On the house's names the built-in address still holds.
    for railway_var in ("admin.chateaugudanes.com", ""):
        m.ADDIN_BASE_URL, m.RAILWAY_PUBLIC_DOMAIN = "", railway_var
        _manifest_everywhere(
            s, "with no ADDIN_BASE_URL and RAILWAY_PUBLIC_DOMAIN "
               + (repr(railway_var) if railway_var else "unset"))

    s.section("With nothing set, the page on admin. still offers Railway's manifest")
    m.ADDIN_BASE_URL, m.RAILWAY_PUBLIC_DOMAIN = "", "admin.chateaugudanes.com"
    s.check("the built-in address is the house's Railway one",
            m.HOUSE_ADDIN_ORIGIN == RAILWAY, detail=m.HOUSE_ADDIN_ORIGIN)
    r = _signed_in(owner["id"], ADMIN).get("/admin/outlook-addin", base_url=ADMIN)
    body = r.get_data(as_text=True)
    s.check("the page answers", r.status_code == 200, response=r)
    s.check("its manifest link is Railway's",
            f'href="{RAILWAY}/outlook-addin/manifest.xml"' in body)
    s.check("the sign-in redirect URI is Railway's",
            f"brk-multihub://{RAILWAY_NAME}</code>" in body)
    s.check("nothing on the page offers the add-in on admin.",
            "admin.chateaugudanes.com/outlook-addin" not in body
            and "brk-multihub://admin." not in body)
    s.check("it says the address is the built-in one, and why Railway's variable was not used",
            "the address built into the app" in body
            and "<code>admin.chateaugudanes.com</code>, which is not a Railway address" in body)
    line = _readiness_line(ADMIN)
    s.check("readiness notes it, as optional rather than worth fixing",
            line is not None and not line["ok"] and line["severity"] == "info"
            and RAILWAY in line["detail"] and "ADDIN_BASE_URL" in line["detail"],
            detail=str(line))
    # Opened on Railway itself, the address it was opened on is the right one.
    r = _signed_in(owner["id"], RAILWAY).get("/admin/outlook-addin", base_url=RAILWAY)
    s.check("opened on Railway, the page offers Railway's manifest",
            f'href="{RAILWAY}/outlook-addin/manifest.xml"' in r.get_data(as_text=True),
            response=r)

    m.ADDIN_BASE_URL = RAILWAY_NAME            # pasted without https://
    line = _readiness_line(RAILWAY)
    s.check("an ADDIN_BASE_URL that is not an address is not used, and readiness says so",
            m.addin_base_source(from_request=False) == (None, "unknown")
            and line is not None and not line["ok"] and "not an address" in line["detail"],
            detail=str(line))


def _a_copy_serves_its_own(s):
    s.section("A laptop or a staging copy still serves its own add-in")
    m.ADDIN_BASE_URL, m.RAILWAY_PUBLIC_DOMAIN = "", ""
    m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = False, ""
    status, urls, domains = _manifest_urls("http://localhost")
    s.check("on localhost the manifest names localhost, as it always did",
            status == 200 and urls and all(u == "http://localhost" or u.startswith("http://localhost/") for u in urls)
            and domains == ["http://localhost"], detail=f"{domains} {urls[:2]}")
    staging = "https://cdg1-production.up.railway.app"
    status, urls, domains = _manifest_urls(staging)
    s.check("a second Railway service names itself, not the house's",
            status == 200 and domains == [staging]
            and all(u == staging or u.startswith(staging + "/") for u in urls), detail=str(domains))
    line = _readiness_line("http://localhost")
    s.check("and with no staff address, readiness only notes it",
            line is not None and line["severity"] == "info", detail=str(line))


if __name__ == "__main__":
    print(run().report())
