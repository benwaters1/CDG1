"""Replacing a photograph on the public site, without touching a template.

Every picture on the site was a Squarespace URL typed into the markup, so
changing one meant a handover. The slots page names each place in plain
language — "the first thing on the home page" rather than home.hero — and the
key is the PLACE, so swapping the picture is one upload and no template moves.

The checks worth having are the refusals, and specifically what a refusal
LEAVES BEHIND. A rejected upload has to leave the old photograph exactly where
it was: a page that half-replaces a picture shows a blank where a photograph
used to be, on the public site, and the person who did it has already closed
the tab.
"""
import io as _io
import os
from datetime import datetime, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-img-"
SLOT = "home.hero"


def _cleanup(conn):
    for row in conn.execute("SELECT filename FROM site_images").fetchall():
        if (row["filename"] or "").startswith("site_"):
            try:
                os.remove(os.path.join(m.ROOM_PHOTO_DIR, row["filename"]))
            except OSError:
                pass
    conn.execute("DELETE FROM site_images")
    conn.execute("DELETE FROM audit_log WHERE action = 'site_image_replaced'")
    conn.commit()


def _png(size=64):
    """A tiny real PNG, padded to whatever size the check needs."""
    head = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"IHDR" + b"\x00" * 16)
    return head + b"\x00" * max(0, size - len(head))


def _stored(conn, slot=SLOT):
    return conn.execute("SELECT * FROM site_images WHERE slot = ?", (slot,)).fetchone()


def run():
    s = Suite("site images")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    def upload(name, data, slot=SLOT):
        return oc.post("/admin/images/upload",
                       data={"slot": slot, "image": (_io.BytesIO(data), name)},
                       content_type="multipart/form-data")

    s.section("A picture goes into a named place")
    r = upload("hero.png", _png())
    s.check("the upload is accepted", r.status_code == 200, detail="HTTP %s" % r.status_code)
    s.check("and it answers with the new address",
            r.is_json and r.get_json().get("ok") and r.get_json().get("url"),
            detail=str(r.get_json() if r.is_json else r.data[:80]))
    row = _stored(conn)
    s.check("the slot now points at a file", row is not None,
            detail="nothing stored")
    s.check("and the file is really on disk",
            row and os.path.exists(os.path.join(m.ROOM_PHOTO_DIR, row["filename"])),
            detail=None if not row else row["filename"])
    logged = conn.execute(
        """SELECT target FROM audit_log WHERE action = 'site_image_replaced'
             ORDER BY id DESC LIMIT 1""").fetchone()
    # A photograph on the public site is a change to what guests see.
    s.check("the change is recorded against the place",
            logged is not None and logged["target"] == SLOT,
            detail=None if logged is None else logged["target"])

    s.section("Replacing one keeps a single slot, not a pile")
    first = _stored(conn)["filename"]
    upload("second.png", _png())
    rows = conn.execute("SELECT COUNT(*) AS c FROM site_images WHERE slot = ?",
                        (SLOT,)).fetchone()["c"]
    s.check("one place still means one row", rows == 1, detail=str(rows))
    s.check("and it is the new picture", _stored(conn)["filename"] != first,
            detail=_stored(conn)["filename"])

    s.section("A refusal leaves the old photograph exactly where it was")
    before = _stored(conn)["filename"]

    bad_type = upload("notes.txt", b"this is not a picture")
    s.check("a file that is not a picture is refused",
            bad_type.status_code == 400, detail="HTTP %s" % bad_type.status_code)
    s.check("it says so rather than failing silently",
            bad_type.is_json and not bad_type.get_json().get("ok")
            and bad_type.get_json().get("error"),
            detail=str(bad_type.get_json() if bad_type.is_json else "")[:90])
    s.check("and the picture on the site is untouched",
            _stored(conn)["filename"] == before, detail=_stored(conn)["filename"])

    too_big = upload("huge.png", _png(m.SITE_IMAGE_MAX_BYTES + 1024))
    s.check("a picture over the limit is refused", too_big.status_code == 400,
            detail="HTTP %s" % too_big.status_code)
    s.check("the limit is named in the refusal",
            too_big.is_json and "MB" in (too_big.get_json().get("error") or ""),
            detail=str(too_big.get_json() if too_big.is_json else "")[:90])
    s.check("and again the old picture stands",
            _stored(conn)["filename"] == before, detail=_stored(conn)["filename"])

    nowhere = upload("hero.png", _png(), slot="not.a.real.place")
    s.check("a place that does not exist is refused", nowhere.status_code == 400,
            detail="HTTP %s" % nowhere.status_code)
    s.check("and nothing was written for it",
            _stored(conn, "not.a.real.place") is None)

    empty = oc.post("/admin/images/upload", data={"slot": SLOT},
                    content_type="multipart/form-data")
    s.check("sending no picture at all is refused", empty.status_code == 400,
            detail="HTTP %s" % empty.status_code)
    s.check("with the old one still in place",
            _stored(conn)["filename"] == before, detail=_stored(conn)["filename"])

    s.section("The page shows every place, filled or not")
    page = oc.get("/admin/images").get_data(as_text=True)
    s.check("every slot in the catalogue appears",
            all(k in page for k in m.IMAGE_SLOT_KEYS),
            detail="%d slot(s) declared" % len(m.IMAGE_SLOT_KEYS))
    s.check("the filled one shows its picture", before in page,
            detail="the stored filename is not on the page")

    s.section("A template can ask for a slot without knowing if it is filled")
    with m.app.test_request_context():
        filled = m.site_image(SLOT, "FALLBACK")
        unfilled = m.site_image("restaurant.kitchen", "FALLBACK")
    s.check("a filled slot returns its own picture",
            filled and filled != "FALLBACK" and before in filled, detail=str(filled))
    # This is what lets a hardcoded photograph be moved into the house's hands
    # one line at a time: swapping a template over changes nothing that day.
    s.check("an empty one returns the fallback, not a broken image",
            unfilled == "FALLBACK", detail=str(unfilled))

    s.section("Only the owner can change what guests see")
    denied = ec.post("/admin/images/upload",
                     data={"slot": SLOT, "image": (_io.BytesIO(_png()), "x.png")},
                     content_type="multipart/form-data")
    s.check("an employee is refused", denied.status_code in (302, 403),
            detail="HTTP %s" % denied.status_code)
    s.check("and the picture did not move",
            _stored(conn)["filename"] == before, detail=_stored(conn)["filename"])

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
