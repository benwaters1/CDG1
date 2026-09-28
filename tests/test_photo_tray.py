"""The tray: where an arrival stops being a file and becomes a decision.

The camera's own screen is the first sieve — the keepers picked out of forty
near-identical frames. This is the second, and the only one that can tell a
picture for the website from a picture for Instagram from one nobody wants,
because that is a judgement rather than a rule.

WHAT THIS FILE IS GUARDING.

  AN ARRIVAL IS NOT A POST. Nothing here writes a caption or schedules
  anything. A photograph becomes a post only when somebody presses the button,
  and it becomes an IDEA even then, because the words are the reason the site
  works and a generated sentence about a French château reads like every other
  generated sentence.

  IT CANNOT BECOME TWO. Pressing twice, a double-tap on a phone, a page
  restored from the back button -- all of them would make a second post of the
  same photograph, and the house would put the same picture out twice a
  fortnight apart. Nobody would see that here. Everybody would see it there.

  SET ASIDE IS NOT DELETED. The file stays on the volume and the row stays in
  the tray under its own chip, because a photograph dismissed by a mis-tap
  while standing up holding a camera should not be gone.
"""
import io
import os

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zztray"


def _frame(w=600, h=400, colour=(70, 110, 90)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _cleanup():
    conn = db()
    rows = conn.execute("SELECT filename, used_ref FROM photo_inbox "
                        "WHERE original_name LIKE ?", (TAG + "%",)).fetchall()
    for row in rows:
        conn.execute("DELETE FROM social_posts WHERE image_filename = ?",
                     (row["filename"],))
        try:
            os.remove(os.path.join(m.UPLOAD_DIR, row["filename"]))
        except OSError:
            pass
    conn.execute("DELETE FROM photo_inbox WHERE original_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _arrive(name, colour=(70, 110, 90)):
    """One photograph in the tray, by the same door the camera uses."""
    conn = db()
    try:
        arrival_id, _what = m.store_arriving_photograph(
            conn, _frame(colour=colour), name, source="camera")
        conn.commit()
        return arrival_id
    finally:
        conn.close()


def _row(arrival_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM photo_inbox WHERE id = ?",
                            (arrival_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Photographs waiting to be decided about")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    first = _arrive(TAG + "-P1040001.JPG")
    second = _arrive(TAG + "-P1040002.JPG", colour=(150, 60, 40))

    s.section("The page")
    page = oc.get("/admin/photos/tray")
    body = page.get_data(as_text=True)
    s.check("the owner can open it", page.status_code == 200,
            detail=f"HTTP {page.status_code}")
    s.check("and what has arrived is on it",
            TAG + "-P1040001.JPG" in body and TAG + "-P1040002.JPG" in body,
            detail="the tray is how somebody sees the camera got through")
    s.check("shown at thumbnail size, not full frames",
            "/photo/thumb/" in body and "/uploads/" not in body,
            detail="a tray of forty masters is forty ten-megabyte downloads "
                   "to draw a grid of small squares")
    s.check("an employee cannot", ec.get("/admin/photos/tray").status_code != 200,
            detail=f"HTTP {ec.get('/admin/photos/tray').status_code}")

    s.section("Nothing has been decided for you")
    s.check("an arrival is waiting, not used",
            not _row(first)["used_as"] and not _row(first)["dismissed"])
    conn = db()
    s.check("and no post exists for it yet",
            conn.execute("SELECT COUNT(*) AS n FROM social_posts "
                         "WHERE image_filename = ?",
                         (_row(first)["filename"],)).fetchone()["n"] == 0,
            detail="an arrival that files itself as a draft turns the social "
                   "list into a camera roll")
    conn.close()

    s.section("Making one into a post")
    made = oc.post(f"/admin/photos/tray/{first}/make-post", follow_redirects=True)
    s.check("it is accepted", made.status_code == 200,
            detail=f"HTTP {made.status_code}")
    conn = db()
    post = conn.execute("SELECT * FROM social_posts WHERE image_filename = ?",
                        (_row(first)["filename"],)).fetchone()
    conn.close()
    s.check("a post now exists with that photograph on it", bool(post))
    s.check("as an idea, with no words put in its mouth",
            post and post["status"] == "idea" and not (post["caption"] or "").strip(),
            detail=f"{post['status'] if post else None} / "
                   f"{post['caption'] if post else None!r} — the writing is "
                   "the reason the site works")
    s.check("and the day it was taken came with it",
            post and post["taken_on"] == _row(first)["taken_on"],
            detail=f"{post['taken_on'] if post else None}")
    s.check("the tray records what it became",
            _row(first)["used_as"] == "social"
            and _row(first)["used_ref"] == (post["id"] if post else None),
            detail=f"{_row(first)['used_as']} / {_row(first)['used_ref']}")

    # PRESSING IT AGAIN. A double tap, a back button, a refreshed POST.
    oc.post(f"/admin/photos/tray/{first}/make-post", follow_redirects=True)
    conn = db()
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM social_posts WHERE image_filename = ?",
        (_row(first)["filename"],)).fetchone()["n"]
    conn.close()
    s.check("and pressing it twice does not make a second post",
            count == 1,
            detail=f"{count} posts — the house would put the same picture out "
                   "twice a fortnight apart, which nobody sees here and "
                   "everybody sees there")

    s.section("Setting one aside")
    oc.post(f"/admin/photos/tray/{second}/set-aside", follow_redirects=True)
    aside = _row(second)
    # ASKED FIRST, AND ON ITS OWN. Reading a column straight off the row makes
    # "set aside deletes it" a crash rather than a failure, and a crash takes
    # every other check in this file down with it and reports 0/0 -- which
    # says nothing about the nineteen things that were fine.
    s.check("it still exists at all", aside is not None,
            detail="set aside must never mean deleted")
    s.check("it is out of the way", aside and aside["dismissed"] == 1)
    s.check("but the photograph is still on the volume",
            aside and os.path.exists(os.path.join(m.UPLOAD_DIR, aside["filename"])),
            detail="set aside by a mis-tap, standing up, holding a camera")
    conn = db()
    s.check("and still in the tray to be found",
            conn.execute("SELECT COUNT(*) AS n FROM photo_inbox WHERE id = ?",
                         (second,)).fetchone()["n"] == 1)
    conn.close()
    oc.post(f"/admin/photos/tray/{second}/bring-back", follow_redirects=True)
    back = _row(second)
    s.check("and it comes back", back is not None and back["dismissed"] == 0,
            detail=f"{back['dismissed'] if back else 'the row is gone'}")

    s.section("The chips count what is there")
    page = oc.get("/admin/photos/tray?state=Used")
    s.check("filtering to the used ones finds the one that was used",
            TAG + "-P1040001.JPG" in page.get_data(as_text=True),
            detail="a chip that yields nothing is worse than no chip")
    waiting = oc.get("/admin/photos/tray?state=Waiting").get_data(as_text=True)
    s.check("and the waiting ones do not include it",
            TAG + "-P1040001.JPG" not in waiting and TAG + "-P1040002.JPG" in waiting,
            detail="a photograph already made into a post is not waiting")

    s.section("One that is not there")
    s.check("is a 404 rather than a page about nothing",
            oc.post("/admin/photos/tray/99999123/make-post").status_code == 404)

    _cleanup()
    return s
