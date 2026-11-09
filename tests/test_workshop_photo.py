"""A retreat's own photograph, set on the workshop form (9 Nov 2026).

workshops.photo_filename had been a column since the table was made, and
nothing could write it: the form had no field, and neither route read one.
/workshops was taught to show an uploaded photo the same day, so this is the
way in. Held here:

  - adding a retreat with a photograph, and editing one in, stores it beside
    the room photographs, where room_photo serves it
  - through photo_master: a JPEG at most the master edge, with nothing of the
    camera's EXIF left (no GPS, no maker), a PNG flattened to a JPEG
  - its card on /workshops and the top of its own page show it
  - a new file replaces it and the old file goes; saving without a file keeps
    it; the box takes it off, and the house's photographs come back
  - a file that is not a photograph is refused with a reason, and nothing is
    stored or changed
  - only files this form made are ever deleted, and only the owner can set it
"""
import io
import os
import re
import shutil
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZWPH"


def _jpeg_from_a_camera(size=(4000, 3000)):
    """A camera-sized JPEG carrying the EXIF a phone or a GH5 writes."""
    from PIL import Image
    img = Image.new("RGB", size, (90, 120, 160))
    exif = Image.Exif()
    exif[0x010F] = "Panasonic"       # Make
    exif[0x0110] = "DC-GH5"          # Model
    gps = exif.get_ifd(0x8825)       # GPSInfo
    gps[1], gps[2] = "N", (42.0, 47.0, 30.0)
    gps[3], gps[4] = "E", (1.0, 43.0, 10.0)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif, quality=60)
    return buf.getvalue()


def _png_with_transparency():
    from PIL import Image
    img = Image.new("RGBA", (600, 400), (200, 40, 40, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _form(title, **extra):
    data = {"title": title, "description": "", "price_per_person": "1500",
            "default_capacity": "15", "deposit_percent": "30", "active": "on"}
    data.update(extra)
    return data


def _row(wid):
    conn = db()
    row = conn.execute("SELECT * FROM workshops WHERE id = ?", (wid,)).fetchone()
    conn.close()
    return row


def _hero(page):
    hit = re.search(r'<img[^>]*class="g-hero__img[^"]*" src="([^"]*)"', page)
    return hit.group(1) if hit else ""


def _card(page, title):
    for block in re.findall(r'<article class="g-wscard">(.*?)</article>', page, re.S):
        if '<h3 class="g-wscard__name">%s</h3>' % title in block:
            hit = re.search(r'<img\b[^>]*\ssrc="([^"]*)"[^>]*class="g-wscard__img"', block)
            return hit.group(1) if hit else ""
    return None


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("A retreat's own photograph")
    oc, ec, _owner, _emp = clients()
    anon = m.app.test_client()
    real_dir = m.ROOM_PHOTO_DIR
    scratch = os.path.join(m.UPLOAD_DIR, "zwph_room_photos")
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch)
    m.ROOM_PHOTO_DIR = scratch
    _cleanup()
    try:
        _run(s, oc, ec, anon, scratch)
    finally:
        m.ROOM_PHOTO_DIR = real_dir
        _cleanup()
        shutil.rmtree(scratch, ignore_errors=True)
    return s


def _run(s, oc, ec, anon, scratch):
    from PIL import Image

    s.section("Added with a photograph")
    camera = _jpeg_from_a_camera()
    s.check("(the test photograph does carry a maker and a position)",
            b"Panasonic" in camera and Image.open(io.BytesIO(camera)).getexif().get_ifd(0x8825))
    title = TAG + " Painting Week"
    r = oc.post("/admin/workshops/new", data=_form(title, photo=(io.BytesIO(camera), "P1000123.JPG")),
                content_type="multipart/form-data")
    conn = db()
    row = conn.execute("SELECT * FROM workshops WHERE title = ?", (title,)).fetchone()
    conn.close()
    if not s.check("the form adds the retreat", row is not None and r.status_code == 302,
                   detail=str(r.status_code)):
        return
    wid, name = row["id"], row["photo_filename"] or ""
    s.check("with the photograph named on it, under a name of the form's own",
            re.fullmatch(r"workshop_[0-9a-f]{16}\.jpg", name) is not None, detail=repr(name))
    path = os.path.join(scratch, name)
    s.check("stored beside the room photographs", os.path.isfile(path), detail=path)
    if not os.path.isfile(path):
        return
    with open(path, "rb") as f:
        stored = f.read()
    with Image.open(io.BytesIO(stored)) as im:
        fmt, size, exif = im.format, im.size, im.getexif()
    s.check("as a JPEG brought down to the master edge",
            fmt == "JPEG" and max(size) == m.PHOTO_MASTER_EDGE, detail=f"{fmt} {size}")
    s.check("with no EXIF left: no position, no maker, no model",
            not exif and b"Panasonic" not in stored and b"GH5" not in stored, detail=repr(dict(exif)))

    s.section("Where guests see it")
    start = m.house_today() + timedelta(days=150)
    conn = db()
    conn.execute(
        "INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at) "
        "VALUES (?, ?, ?, 15, ?, ?)",
        (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), TAG, _harness.datetime_now()))
    conn.commit()
    conn.close()
    with m.app.test_request_context():
        src = m.url_for("room_photo", filename=name)
    listing = anon.get("/workshops").get_data(as_text=True)
    s.check("its card on /workshops", _card(listing, title) == src, detail=repr(_card(listing, title)))
    own = anon.get("/workshops/%d" % wid).get_data(as_text=True)
    s.check("and the top of its own page", _hero(own) == src, detail=repr(_hero(own)))
    served = anon.get(src)
    s.check("served to anybody, as the photograph stored",
            served.status_code == 200 and served.data == stored, detail=str(served.status_code))
    # A file response holds its file open until closed, and Windows will not delete an open file.
    served.close()

    s.section("The form itself")
    # The posts here are multipart whatever the page says. A browser sends a file only if the form asks it to.
    for path_, what in (("/admin/workshops/new", "the Add form"), ("/admin/workshops/%d/edit" % wid, "the Edit form")):
        form = oc.get(path_).get_data(as_text=True)
        tag = re.search(r'<form method="post"[^>]*>', form)
        s.check(f"{what} sends files, and has the field",
                tag and 'enctype="multipart/form-data"' in tag.group(0)
                and re.search(r'<input[^>]*type="file"[^>]*name="photo"', form), detail=tag.group(0) if tag else "")
    s.check("the Edit form shows the photograph it has, and the box to take it off",
            ('src="%s"' % src) in form and 'name="remove_photo"' in form)

    s.section("Saved again without a file")
    r = oc.post("/admin/workshops/%d/edit" % wid, data=_form(title, description="Now with words"),
                content_type="multipart/form-data")
    after = _row(wid)
    s.check("the description is saved", after["description"] == "Now with words", detail=str(r.status_code))
    s.check("and the photograph kept: the commonest edit must not lose it",
            after["photo_filename"] == name and os.path.isfile(path), detail=repr(after["photo_filename"]))

    s.section("Replaced")
    oc.post("/admin/workshops/%d/edit" % wid,
            data=_form(title, photo=(io.BytesIO(_png_with_transparency()), "card.png")),
            content_type="multipart/form-data")
    after = _row(wid)
    new = after["photo_filename"] or ""
    new_path = os.path.join(scratch, new)
    s.check("a new file takes its place", new and new != name and os.path.isfile(new_path), detail=repr(new))
    if os.path.isfile(new_path):
        with Image.open(new_path) as im:
            s.check("a PNG lands as a JPEG, its clear background white, not black",
                    im.format == "JPEG" and im.getpixel((10, 10))[0] > 240, detail=f"{im.format} {im.getpixel((10, 10))}")
    s.check("and the old file is gone", not os.path.exists(path), detail=path)
    conn = db()
    logged = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'workshop_photo_set' AND target = ?",
                          (str(wid),)).fetchone()[0]
    conn.close()
    s.check("each change written in the audit log", logged >= 2, detail=f"{logged} row(s)")
    listing = anon.get("/workshops").get_data(as_text=True)
    with m.app.test_request_context():
        new_src = m.url_for("room_photo", filename=new)
    s.check("the card shows the new one", _card(listing, title) == new_src, detail=repr(_card(listing, title)))

    s.section("A file that is not a photograph")
    before = sorted(os.listdir(scratch))
    words = _row(wid)["description"]
    r = oc.post("/admin/workshops/%d/edit" % wid,
                data=_form(title, description="Should not save", photo=(io.BytesIO(b"hello"), "notes.txt")),
                content_type="multipart/form-data", follow_redirects=True)
    page = r.get_data(as_text=True)
    after = _row(wid)
    s.check("a .txt is refused, saying why", "is not a photograph this can store" in page,
            detail="the reason allowed_image gives")
    s.check("and nothing changes: not the photograph, not the words, nothing stored",
            after["photo_filename"] == new and after["description"] == words
            and sorted(os.listdir(scratch)) == before,
            detail=f"{after['photo_filename']!r}, {after['description']!r}, {sorted(os.listdir(scratch))}")
    r = oc.post("/admin/workshops/%d/edit" % wid,
                data=_form(title, photo=(io.BytesIO(b"\xff\xd8 not really a jpeg"), "IMG_0001.jpg")),
                content_type="multipart/form-data", follow_redirects=True)
    page = r.get_data(as_text=True)
    s.check("a file named .jpg that is not one is refused too",
            "could not be read as a photograph" in page and _row(wid)["photo_filename"] == new
            and sorted(os.listdir(scratch)) == before)
    r = oc.post("/admin/workshops/new", data=_form(TAG + " Refused", photo=(io.BytesIO(b"x"), "a.docx")),
                content_type="multipart/form-data")
    conn = db()
    added = conn.execute("SELECT 1 FROM workshops WHERE title = ?", (TAG + " Refused",)).fetchone()
    conn.close()
    s.check("and a new retreat with one is not added half-made",
            added is None and "is not a photograph this can store" in r.get_data(as_text=True),
            detail=str(r.status_code))

    s.section("Only the owner")
    r = ec.post("/admin/workshops/%d/edit" % wid,
                data=_form(title, photo=(io.BytesIO(_jpeg_from_a_camera((800, 600))), "mine.jpg")),
                content_type="multipart/form-data")
    s.check("staff cannot change it", _row(wid)["photo_filename"] == new
            and sorted(os.listdir(scratch)) == before, detail=str(r.status_code))

    s.section("Taken off")
    oc.post("/admin/workshops/%d/edit" % wid, data=_form(title, remove_photo="1"),
            content_type="multipart/form-data")
    after = _row(wid)
    s.check("the box takes it off the retreat", after["photo_filename"] is None, detail=repr(after["photo_filename"]))
    s.check("and the file goes", not os.path.exists(new_path), detail=new_path)
    listing = anon.get("/workshops").get_data(as_text=True)
    card = _card(listing, title) or ""
    s.check("its card goes back to the house's photographs", card and "/room-photos/" not in card,
            detail=repr(card))
    own = anon.get("/workshops/%d" % wid).get_data(as_text=True)
    s.check("and so does the top of its page", _hero(own) and "/room-photos/" not in _hero(own),
            detail=repr(_hero(own)))

    s.section("Only its own files are ever deleted")
    keep = "room_zzwph_keep.jpg"
    with open(os.path.join(scratch, keep), "wb") as f:
        f.write(_jpeg_from_a_camera((80, 60)))
    conn = db()
    conn.execute("UPDATE workshops SET photo_filename = ? WHERE id = ?", (keep, wid))
    conn.commit()
    conn.close()
    oc.post("/admin/workshops/%d/edit" % wid, data=_form(title, remove_photo="1"),
            content_type="multipart/form-data")
    s.check("a name set by hand to a room's photograph comes off the retreat",
            _row(wid)["photo_filename"] is None)
    s.check("but the room's file stays where it was", os.path.isfile(os.path.join(scratch, keep)))


if __name__ == "__main__":
    print(run().report())
