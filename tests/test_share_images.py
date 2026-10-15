"""A link preview's photograph is a full address.

Facebook, WhatsApp and iMessage fetch og:image from wherever the page says,
with nothing to resolve a path against, so "/mirrored-photo/..." and
"/static/img/..." gave every shared link a blank card (the design side, 15
October 2026). share_images_in_full makes them absolute as the page goes out.
"""
import re

from _harness import Suite
import _harness

m = _harness.m
RAILWAY = "https://cdg1-production-9186.up.railway.app"
HOUSE = "https://www.chateaugudanes.com"


def _shares(body):
    return re.findall(
        r'<meta[^>]*(?:property|name)="(?:og:image|twitter:image)"[^>]*content="([^"]*)"', body)


def run():
    s = Suite("Link previews carry their photograph")
    c = m.app.test_client()

    s.section("On every public page")
    relative = {}
    for path in ("/", "/story", "/book", "/workshops", "/events/weddings", "/gallery"):
        body = c.get(path, base_url=RAILWAY).get_data(as_text=True)
        shares = _shares(body)
        bad = [u for u in shares if not u.startswith("https://")]
        if not shares or bad:
            relative[path] = shares
    s.check("each page's preview image is a full https address", not relative,
            detail=f"{relative} — a path here is a blank card on every shared link")

    s.section("Whatever the page wrote")
    page = ('<html><head><meta property="og:image" content="/static/img/x.jpg">'
            '<meta name="twitter:image" content="/mirrored-photo/ab.jpg">'
            '<meta content="/static/y.jpg" property="og:image">'
            '<meta property="og:image" content="https://elsewhere.example/z.jpg">'
            '<link rel="icon" href="/static/icon.png"></head><body class="g"></body></html>')
    with m.app.test_request_context("/", base_url=RAILWAY):
        resp = m.app.response_class(page, mimetype="text/html")
        out = m.share_images_in_full(resp).get_data(as_text=True)
    s.check("a static path is made full", f'content="{RAILWAY}/static/img/x.jpg"' in out, detail=out)
    s.check("and so is the house's own copy of a photograph",
            f'content="{RAILWAY}/mirrored-photo/ab.jpg"' in out)
    s.check("whichever order the attributes come in", f'content="{RAILWAY}/static/y.jpg"' in out)
    s.check("a full address is left as it is", 'content="https://elsewhere.example/z.jpg"' in out)
    s.check("and nothing that is not a preview is touched", 'href="/static/icon.png"' in out)

    s.section("Once live, on the house's own address")
    was = (m.SITE_IS_LIVE, m.PUBLIC_BASE_URL)
    try:
        m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = True, HOUSE
        body = c.get("/story", base_url=HOUSE).get_data(as_text=True)
    finally:
        m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = was
    shares = _shares(body)
    s.check("the preview points at www.chateaugudanes.com, never at Railway",
            shares and all(u.startswith(HOUSE + "/") or not u.startswith("https://cdg1")
                           for u in shares), detail=str(shares))
    return s


if __name__ == "__main__":
    print(run().report())
