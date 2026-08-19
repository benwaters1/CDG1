"""The public gallery, and the five menu links that pointed at nothing.

Every page's header links /gallery#inside, #grounds, #restoration, #life and
#views. The route rendered a hardcoded empty list and the template had no
anchors at all, so all five landed on the same blank page. Five broken links,
shipped, on every page of the site.

The check that matters is therefore not "does the gallery work" but "does
every anchor the header links to actually exist on the page" — and it reads
the anchors out of public_base.html rather than repeating the list, so adding
a sixth menu link without a section fails here rather than in front of a guest.
"""
import io
import os
import re

from _harness import Suite, clients, db, ROOT
import _harness

m = _harness.m


def _order(section_id):
    """Photographs in the order the public page renders them."""
    conn = db()
    try:
        return conn.execute(
            """SELECT id, sort_order FROM gallery_photos WHERE section_id = ?
               ORDER BY sort_order, id""", (section_id,)).fetchall()
    finally:
        conn.close()


def _menu_anchors():
    """The #anchors the shipped header links to, straight from the template."""
    with open(f"{ROOT}/templates/public_base.html", encoding="utf-8") as fh:
        html = fh.read()
    return sorted(set(re.findall(r'href="/gallery#([a-z0-9-]+)"', html)))


def run():
    s = Suite("Gallery")
    pub = m.app.test_client()
    oc, ec, owner, emp = clients()

    s.section("Every menu link lands on a real anchor")
    anchors = _menu_anchors()
    s.check("the header does link to gallery anchors at all", len(anchors) > 0,
            detail=f"found {anchors}")
    page = pub.get("/gallery")
    html = page.get_data(as_text=True)
    s.check("the gallery page loads", page.status_code == 200, page)
    missing = [a for a in anchors if f'id="{a}"' not in html]
    s.check("every anchor the header links to exists on the page", not missing,
            detail=f"missing: {missing}")

    s.section("Sections render even with no photographs")
    conn = db()
    n_sections = conn.execute("SELECT COUNT(*) AS c FROM gallery_sections").fetchone()["c"]
    conn.close()
    s.check("the five sections are seeded", n_sections >= 5, detail=f"{n_sections} section(s)")
    s.check("an empty section still shows its heading, not a bare page",
            "Instagram" in html and 'id="inside"' in html)

    s.section("Adding a photograph")
    conn = db()
    section = conn.execute(
        "SELECT * FROM gallery_sections WHERE slug = 'inside'").fetchone()
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM gallery_photos WHERE section_id = ?",
        (section["id"],)).fetchone()["c"]
    conn.close()

    # A 1x1 PNG is enough: the route cares about the extension and that a file
    # arrived, not about the pixels.
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
           b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    r = oc.post(f"/admin/gallery/{section['id']}/photos",
                data={"photos": (io.BytesIO(png), "zzgal_test.png")},
                content_type="multipart/form-data", follow_redirects=True)
    conn = db()
    rows = conn.execute(
        "SELECT * FROM gallery_photos WHERE section_id = ? ORDER BY id",
        (section["id"],)).fetchall()
    conn.close()
    s.check("the photograph is recorded against the section", len(rows) == before + 1, r)

    if len(rows) > before:
        photo = rows[-1]
        page2 = pub.get("/gallery").get_data(as_text=True)
        s.check("it appears on the public page", photo["filename"] in page2)
        s.check("and is served by the photo route",
                pub.get(f"/room-photos/{photo['filename']}").status_code == 200)

        s.section("Removing it")
        r2 = oc.post(f"/admin/gallery/photo/{photo['id']}/delete", follow_redirects=True)
        conn = db()
        still = conn.execute(
            "SELECT 1 FROM gallery_photos WHERE id = ?", (photo["id"],)).fetchone()
        conn.close()
        s.check("the row is gone", still is None, r2)
        s.check("and it is off the public page",
                photo["filename"] not in pub.get("/gallery").get_data(as_text=True))

    s.section("Captions and order")
    # Two photographs, so there is something to reorder.
    oc.post(f"/admin/gallery/{section['id']}/photos",
            data={"photos": [(io.BytesIO(png), "zzgal_a.png"), (io.BytesIO(png), "zzgal_b.png")]},
            content_type="multipart/form-data", follow_redirects=True)
    conn = db()
    pair = conn.execute(
        """SELECT * FROM gallery_photos WHERE section_id = ?
           ORDER BY sort_order, id DESC LIMIT 2""", (section["id"],)).fetchall()
    conn.close()

    if len(pair) == 2:
        first, second = pair[0], pair[1]
        oc.post(f"/admin/gallery/photo/{first['id']}",
                data={"caption": "Bare plaster, north wall"}, follow_redirects=True)
        conn = db()
        cap = conn.execute("SELECT caption FROM gallery_photos WHERE id = ?",
                           (first["id"],)).fetchone()["caption"]
        conn.close()
        s.check("a caption saves", cap == "Bare plaster, north wall", detail=repr(cap))
        s.check("and shows on the public page",
                "Bare plaster, north wall" in pub.get("/gallery").get_data(as_text=True))

        # Blanking it must give NULL, not "", or the template's `{% if %}`
        # renders an empty caption element.
        oc.post(f"/admin/gallery/photo/{first['id']}", data={"caption": "   "},
                follow_redirects=True)
        conn = db()
        cap2 = conn.execute("SELECT caption FROM gallery_photos WHERE id = ?",
                            (first["id"],)).fetchone()["caption"]
        conn.close()
        s.check("blanking a caption stores NULL, not an empty string", cap2 is None,
                detail=repr(cap2))

        before = [p["id"] for p in _order(section["id"])]
        oc.post(f"/admin/gallery/photo/{before[-1]}", data={"move": "up"},
                follow_redirects=True)
        after = [p["id"] for p in _order(section["id"])]
        s.check("moving a photograph earlier swaps it with its neighbour",
                after[-2] == before[-1] and after[-1] == before[-2],
                detail=f"{before} -> {after}")
        s.check("and nothing else in the section moved",
                sorted(after) == sorted(before) and after[:-2] == before[:-2])

        oc.post(f"/admin/gallery/photo/{after[0]}", data={"move": "up"},
                follow_redirects=True)
        s.check("moving the first one earlier is a no-op, not an error",
                [p["id"] for p in _order(section["id"])] == after)

    s.section("Guards")
    s.check("an employee cannot reach the gallery admin",
            ec.get("/admin/gallery").status_code in (302, 403))
    s.check("uploading to a section that does not exist is a 404",
            oc.post("/admin/gallery/999999/photos",
                    data={"photos": (io.BytesIO(png), "x.png")},
                    content_type="multipart/form-data").status_code == 404)
    r3 = oc.post(f"/admin/gallery/{section['id']}", data={"title": ""},
                 follow_redirects=True)
    conn = db()
    title_now = conn.execute(
        "SELECT title FROM gallery_sections WHERE id = ?", (section["id"],)).fetchone()["title"]
    conn.close()
    s.check("a section cannot be retitled to nothing", title_now.strip() != "", r3)

    # The slug is the header's link target, so it must not be editable at all.
    oc.post(f"/admin/gallery/{section['id']}",
            data={"title": "Renamed", "blurb": "", "slug": "somethingelse"},
            follow_redirects=True)
    conn = db()
    slug_now = conn.execute(
        "SELECT slug FROM gallery_sections WHERE id = ?", (section["id"],)).fetchone()["slug"]
    conn.execute("UPDATE gallery_sections SET title = 'Inside the Château' WHERE id = ?",
                 (section["id"],))
    conn.commit()
    conn.close()
    s.check("the slug cannot be changed, even if posted", slug_now == "inside",
            detail=f"slug is now {slug_now}")

    # Uploads land beside the throwaway database rather than in real storage
    # (DATA_DIR follows DB_PATH), so nothing here can touch a guest's photo —
    # but the files would still pile up in that directory run after run.
    conn = db()
    leftovers = conn.execute(
        "SELECT id, filename FROM gallery_photos WHERE filename LIKE '%zzgal%'").fetchall()
    conn.execute("DELETE FROM gallery_photos WHERE filename LIKE '%zzgal%'")
    conn.commit()
    conn.close()
    for row in leftovers:
        path = os.path.join(m.ROOM_PHOTO_DIR, row["filename"])
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    return s
