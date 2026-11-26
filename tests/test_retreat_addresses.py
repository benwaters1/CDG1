"""Each retreat at its name: /retreats/cooking-in-the-cuisine (site review, 25 November 2026).

The four retreats lived at /workshops/7 to /workshops/10. The review: "A name in
the address would serve guests and Google better"; the owner chose it. This
holds:

  - every public retreat has a name, made from its title (accents off, "&" as
    "and"), and its page answers there;
  - its old numbered address, and /retreats/<number>, move there for good (301);
  - every link the site writes to it uses the name -- the list, the front page,
    the sitemap;
  - retitling a retreat does NOT move its page: the name is given once and kept;
  - a name it used to have still finds it, and moves to the current one;
  - a private stay has no name and no page; a name that never was is not found.
"""
import re

from _harness import Suite, db
import _harness

m = _harness.m


def run():
    s = Suite("Retreat addresses")
    c = m.app.test_client()
    conn = db()
    try:
        today = m.house_today()
        live = [w for w in conn.execute(
            f"SELECT * FROM workshops WHERE active = 1 AND {m.WORKSHOP_IS_PUBLIC} ORDER BY id").fetchall()
            if m.public_sittings(conn, w["id"], today)]
    finally:
        conn.close()
    if not s.check("there are retreats with dates to try", bool(live)):
        return s

    s.section("Every public retreat has a name, from its title")
    for w in live:
        s.check(f"{w['title']} is {w['slug']}", bool(w["slug"])
                and w["slug"].startswith(m.retreat_slug_text(w["title"])), detail=str(w["slug"]))
    s.check("accents come off and & is 'and'",
            m.retreat_slug_text("Noël at Gudanes") == "noel-at-gudanes"
            and m.retreat_slug_text("Antique & French Finds") == "antique-and-french-finds")

    w = live[0]
    named = "/retreats/" + w["slug"]
    s.section(f"{w['title']} answers at {named}")
    r = c.get(named)
    s.check("its page answers at its name", r.status_code == 200, detail=str(r.status_code))
    s.check("retreat_path says the same", m.retreat_path(w["id"]) == named, detail=m.retreat_path(w["id"]))
    for old in (f"/workshops/{w['id']}", f"/retreats/{w['id']}"):
        r = c.get(old + "?utm_source=letter")
        s.check(f"{old} moves there for good, the rest of the address kept",
                r.status_code == 301 and r.headers.get("Location", "") == named + "?utm_source=letter",
                detail=f"{r.status_code} {r.headers.get('Location')}")
    listing = c.get("/workshops").get_data(as_text=True)
    s.check("the list links to it by name", f'href="{named}"' in listing)
    s.check("and to no retreat by number", re.search(r'href="/workshops/\d+"', listing) is None)
    xml = c.get("/sitemap.xml").get_data(as_text=True)
    s.check("the sitemap lists it by name", named + "</loc>" in xml)

    s.section("A new title does not move the page")
    conn = db()
    try:
        conn.execute("UPDATE workshops SET title = ? WHERE id = ?", (w["title"] + " (renamed)", w["id"]))
        m.ensure_workshop_slug(conn, w["id"])
        conn.commit()
        kept = conn.execute("SELECT slug FROM workshops WHERE id = ?", (w["id"],)).fetchone()["slug"]
    finally:
        conn.close()
    s.check("the name stays as it was", kept == w["slug"], detail=str(kept))
    m._RETREAT_SLUGS["at"] = 0.0
    s.check("and the page is still there", c.get(named).status_code == 200)

    s.section("A name it used to have still finds it")
    conn = db()
    try:
        conn.execute("INSERT INTO workshop_slugs (slug, workshop_id, created_at) VALUES (?, ?, ?)",
                     ("an-older-name-for-it", w["id"], "2026-11-25T00:00:00+00:00"))
        conn.commit()
    finally:
        conn.close()
    m._RETREAT_SLUGS["at"] = 0.0
    r = c.get("/retreats/an-older-name-for-it")
    s.check("and moves to the current one for good", r.status_code == 301
            and r.headers.get("Location", "") == named, detail=f"{r.status_code} {r.headers.get('Location')}")

    s.section("What has no page")
    r = c.get("/retreats/never-a-retreat-here")
    s.check("a name that never was is not found", r.status_code == 404, detail=str(r.status_code))
    conn = db()
    try:
        private = conn.execute(
            "INSERT INTO workshops (title, price_per_person, active, private_token, created_at) "
            "VALUES ('A private stay (test)', 100, 1, 'tok-retreat-addresses', '2026-11-25T00:00:00+00:00')").lastrowid
        conn.commit()
        given = m.ensure_workshop_slug(conn, private)
        slug = conn.execute("SELECT slug FROM workshops WHERE id = ?", (private,)).fetchone()["slug"]
    finally:
        conn.close()
    s.check("a private stay is given no name", given is None and not slug, detail=str(slug))
    r = c.get(f"/retreats/{private}")
    s.check("and has no page at /retreats/<its number>", r.status_code == 404, detail=str(r.status_code))

    conn = db()
    try:
        conn.execute("UPDATE workshops SET title = ? WHERE id = ?", (w["title"], w["id"]))
        conn.execute("DELETE FROM workshop_slugs WHERE slug = 'an-older-name-for-it'")
        conn.execute("DELETE FROM workshops WHERE id = ?", (private,))
        conn.commit()
    finally:
        conn.close()
    m._RETREAT_SLUGS["at"] = 0.0
    return s
