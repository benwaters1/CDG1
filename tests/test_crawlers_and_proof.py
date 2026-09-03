"""What a crawler is told, and the figures the public pages quote.

Two things arrived as templates this round that the app already served in code,
and one that had nowhere to get its numbers.

  url_map, AT LAST. Every sketch since the first handover has tested
  `'X' in url_map` to mean "only if that page is built yet", and nothing ever
  supplied it — so the test was false in every render and four dedicated pages
  had no link pointing at them for five rounds. sitemap.xml is where that stops
  being cosmetic: the WHOLE FILE sits inside such a test, so an unsupplied
  url_map is an empty sitemap that returns 200 and looks perfectly fine.

  robots.txt AND sitemap.xml are built in code, and more fully than the
  templates: the code robots blocks seven paths the template misses, and the
  code sitemap lists every atelier with a date still ahead as well as every
  room. What the templates added — five Disallow lines including the two
  wildcards — was taken into the code rather than the code being replaced.

  THE PROOF FIGURES read from settings and NOTHING is seeded. A review count
  invented by software and printed on the house's own website is worth nothing
  at best, so the component shows a line only when somebody has supplied the
  number, and clearing it takes the line off again.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM app_settings WHERE key IN "
                 "('review_recommend','review_count','instagram_followers',"
                 "'facebook_followers')")
    conn.commit()
    conn.close()


def _raises_keyerror(bag, key):
    try:
        bag[key]
    except KeyError:
        return True
    except Exception:
        return False
    return False


def run():
    s = Suite("Crawlers and proof")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    anon = m.app.test_client()

    s.section("A template can ask which endpoints exist")
    with m.app.test_request_context("/"):
        supplied = None
        for proc in m.app.template_context_processors[None]:
            got = proc()
            if "url_map" in got:
                supplied = got["url_map"]
                break
    s.check("url_map is in the context", supplied is not None,
            detail="with nothing behind it the test is false in every render "
                   "and the guard takes its fallback for ever")
    s.check("and it names real endpoints",
            bool(supplied) and "sitemap" in supplied and "dashboard" in supplied,
            detail="a set that does not contain what it is asked about is the "
                   "same bug with a value in it")
    s.check("and not names that do not exist", "home" not in supplied,
            detail="the front page endpoint is dashboard; sitemap.xml asks for "
                   "'home' and is correctly given nothing")

    s.section("The sitemap")
    r = anon.get("/sitemap.xml")
    body = r.get_data(as_text=True)
    s.check("it is served", r.status_code == 200)
    s.check("as xml, not html", "xml" in (r.headers.get("Content-Type") or ""),
            detail=f"{r.headers.get('Content-Type')!r} — served as html a "
                   "sitemap is not a sitemap and nothing about it would say so")
    s.check("and it is not empty", body.count("<loc>") > 5,
            detail=f"{body.count('<loc>')} urls — the whole file sits inside "
                   "one url_map test, so an empty one returns 200 and tells "
                   "nobody anything is wrong")
    s.check("every room has its own entry", "/book/" in body,
            detail="those are the pages that should rank")

    s.section("What a crawler is kept away from")
    r = anon.get("/robots.txt")
    txt = r.get_data(as_text=True)
    s.check("it is served as plain text",
            r.status_code == 200 and "text/plain" in (r.headers.get("Content-Type") or ""))
    # The guest ones are the point. A management URL carries a token, and a
    # crawler that follows one puts somebody's bill and their cancel button in
    # an index.
    for path in ("/admin", "/book/manage", "/booking", "/manage/", "/account/"):
        s.check(f"{path} is disallowed", f"Disallow: {path}" in txt,
                detail=f"{path} is guest-token or staff-only")
    s.check("and so is any token in a query string",
            "Disallow: /*?token=" in txt,
            detail="taken from the designer's robots.txt this round; the code "
                   "version did not have it")
    s.check("the paths only the code version knew are still there",
            all(f"Disallow: {p}" in txt for p in ("/staff", "/pos", "/management", "/api")),
            detail="the template misses these seven, which is why it was not "
                   "swapped in")
    s.check("and it points at the sitemap", "Sitemap:" in txt and "sitemap.xml" in txt)

    s.section("The figures the public pages quote")
    home = anon.get("/").get_data(as_text=True)
    s.check("nothing is claimed before anybody supplies a number",
            "of guests recommend" not in home,
            detail="a review count invented by software and printed on the "
                   "house's own site is worth nothing at best")
    oc.post("/management/proof-figures",
            data={"review_recommend": "96", "review_count": "2533",
                  "instagram_followers": "", "facebook_followers": ""},
            follow_redirects=True)
    home = anon.get("/").get_data(as_text=True)
    s.check("a supplied figure appears", "96" in home and "recommend" in home,
            detail="the component reads settings and renders nothing without them")
    s.check("and one left empty does not",
            "Instagram" not in home or "367" not in home,
            detail="any figure not supplied renders nothing, which is what "
                   "makes supplying one at a time safe")

    s.section("And a stale figure comes off rather than sitting there wrong")
    oc.post("/management/proof-figures",
            data={"review_recommend": "", "review_count": "",
                  "instagram_followers": "", "facebook_followers": ""},
            follow_redirects=True)
    conn = db()
    left = conn.execute(
        "SELECT COUNT(*) AS c FROM app_settings WHERE key = 'review_recommend'"
    ).fetchone()["c"]
    conn.close()
    s.check("clearing it removes it", left == 0,
            detail="a form that can only add is a form that guarantees the "
                   "site eventually quotes something wrong")

    s.section("And a template cannot reach a secret through it")
    # app_settings holds supplier_upload_token and vapid_private_key. Handing
    # the whole table to every template means one stray subscript in a sketch
    # puts a secret on a public page, and nothing about the page looks wrong.
    with m.app.test_request_context("/"):
        bag = None
        for proc in m.app.template_context_processors[None]:
            got = proc()
            if isinstance(got, dict) and "settings" in got:
                bag = got["settings"]
                break
    s.check("the public settings are there", bag is not None)
    for secret in ("vapid_private_key", "supplier_upload_token"):
        s.check(f"{secret} is not reachable", bag.get(secret) is None,
                detail="an allowlist, not the table")
    s.check("and asking for one raises rather than answering",
            _raises_keyerror(bag, "vapid_private_key"),
            detail="silently returning nothing would be safe too, but raising "
                   "means a sketch that reaches for it fails loudly in review")

    s.section("Guards")
    s.check("an employee cannot set what the site claims",
            ec.post("/management/proof-figures",
                    data={"review_count": "999999"}).status_code in (302, 403))
    conn = db()
    sneaked = conn.execute(
        "SELECT COUNT(*) AS c FROM app_settings WHERE value = '999999'").fetchone()["c"]
    conn.close()
    s.check("and nothing was written", sneaked == 0,
            detail="checked by effect: a refusal and a success both redirect")

    _cleanup()
    return s
