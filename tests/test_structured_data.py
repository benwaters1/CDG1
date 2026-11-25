"""What search engines read about the house: the schema.org blocks.

The public templates carry them (the house on every page, each atelier sitting
on /workshops, the FAQ, the restaurant). Three things undid much of it on the
live site, 23 November 2026, and none of them showed on any page:

  addresses without a host -- "/static/img/..." from the handovers and
  "/mirrored-photo/..." from the photo swap, written into the JSON;

  the bare domain where every canonical link says www.;

  an atelier's own page with no event on it, only the list.

structured_data_for_search mends all three at the response. This file holds
it to that on real pages, as if the site were on its own address.
"""
import json
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m
BLOCK = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
HOME = ("https", "www.chateaugudanes.com")
BASE = "https://www.chateaugudanes.com"


def _blocks(html):
    out = []
    for raw in BLOCK.findall(html):
        try:
            out.append(json.loads(raw))
        except ValueError as exc:
            out.append({"__broken__": str(exc)})
    return out


def _walk(value, key=None):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, key)
    else:
        yield key, value


def _items(blocks):
    for b in blocks:
        for it in (b if isinstance(b, list) else b.get("@graph", [b])):
            if isinstance(it, dict):
                yield it


def _addresses(blocks):
    return [(k, v) for b in blocks for k, v in _walk(b)
            if k in m._LD_ADDRESS_KEYS and isinstance(v, str)]


def run():
    s = Suite("What search engines read")
    _oc, ec, _owner, _emp = clients()
    pub = m.app.test_client()
    real_home = m.house_address
    m.house_address = lambda: HOME
    try:
        _the_mending(s)
        _real_pages(s, pub, ec)
    finally:
        m.house_address = real_home
    return s


def _the_mending(s):
    s.section("Every address made full, on the house's own host")
    data = {"@id": "https://chateaugudanes.com/#chateau", "url": "https://chateaugudanes.com/",
            "image": ["/mirrored-photo/abc.jpg", "/static/img/x.jpg"],
            "sameAs": ["https://www.instagram.com/chateaugudanes/"],
            "description": "/ a sentence that happens to start with a slash",
            "location": {"@id": "https://chateaugudanes.com/#chateau",
                         "url": "http://chateaugudanes.com/workshops/7"}}
    roots = ["https://chateaugudanes.com", "http://chateaugudanes.com"]
    out = m.structured_data_in_full(data, BASE, roots)
    s.check("a path becomes a full address", out["image"] ==
            [BASE + "/mirrored-photo/abc.jpg", BASE + "/static/img/x.jpg"], detail=str(out["image"]))
    s.check("the bare domain becomes www., as the canonical link says",
            out["@id"] == BASE + "/#chateau" and out["url"] == BASE + "/"
            and out["location"]["url"] == BASE + "/workshops/7", detail=str(out))
    s.check("somebody else's address is left alone",
            out["sameAs"] == ["https://www.instagram.com/chateaugudanes/"])
    s.check("and a sentence is not an address", out["description"] == data["description"])
    s.check("an Event and an EducationEvent are both events; a venue is not",
            m._ld_has_event(['{"@type": "EducationEvent"}'])
            and m._ld_has_event(['{"@type":"Event"}'])
            and not m._ld_has_event(['{"@type": "EventVenue"}']))


def _real_pages(s, pub, ec):
    s.section("The public pages, as Google fetches them")
    for path in ("/", "/workshops", "/book", "/events", "/contact", "/restoration", "/history"):
        r = pub.get(path + "?lang=en", base_url=BASE)
        html = r.get_data(as_text=True)
        blocks = _blocks(html)
        s.check(f"{path}: every block is JSON", r.status_code == 200 and blocks and
                not any("__broken__" in b for b in blocks if isinstance(b, dict)),
                detail=f"HTTP {r.status_code}, {len(blocks)} block(s)")
        bad = [(k, v) for k, v in _addresses(blocks)
               if v.startswith("/") or "://chateaugudanes.com" in v]
        s.check(f"{path}: every address is full and on www.", not bad, detail=str(bad[:3]))

    s.section("An atelier's own page says when it is on")
    conn = db()
    try:
        row = None
        for w in conn.execute(f"SELECT * FROM workshops WHERE active = 1 AND {m.WORKSHOP_IS_PUBLIC}"):
            if m.public_sittings(conn, w["id"], m.house_today()):
                row = w
                break
    finally:
        conn.close()
    if not s.check("there is an atelier with dates to come", row is not None,
                   detail="nothing to check the page against"):
        return
    r = pub.get(m.retreat_path(row['id']) + "?lang=en", base_url=BASE)
    html = r.get_data(as_text=True)
    events = [it for it in _items(_blocks(html)) if str(it.get("@type", "")).endswith("Event")]
    with m.app.test_request_context(base_url=BASE):
        page = m.workshop_page_url(row)
    s.check("its page carries its dates as events", r.status_code == 200 and events,
            detail=f"HTTP {r.status_code}")
    ev = events[0] if events else {}
    s.check("each with a start, an end and the house as the place",
            ev.get("startDate") and ev.get("endDate")
            and (ev.get("location") or {}).get("address", {}).get("streetAddress") == "2 Route de Beille")
    s.check("its own page as the address, in full",
            ev.get("url") == BASE + page, detail=f"{ev.get('url')} vs {BASE + page}")
    if row["price_per_person"]:
        s.check("and the price, in euros, with whether places are left",
                (ev.get("offers") or {}).get("price") == row["price_per_person"]
                and (ev.get("offers") or {}).get("priceCurrency") == "EUR"
                and str((ev.get("offers") or {}).get("availability", "")).startswith("https://schema.org/"))
    images = ev.get("image") or []
    s.check("and the page's own picture, as a full address",
            images and all(str(i).startswith("https://") for i in images), detail=str(images))
    s.check("one set of dates, not two", len({e.get("startDate") for e in events}) == len(events),
            detail=str([e.get("startDate") for e in events]))


if __name__ == "__main__":
    print(run().report())
