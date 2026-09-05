"""A photograph in, landing on a real date, without anybody opening a calendar.

A social post was a caption with nothing attached. This page takes the picture,
puts it against a plan, and works out the next slot that plan has free.

THREE THINGS CARRY THIS FILE.

  THE HEIC REFUSAL. Every iPhone shoots HEIC by default and no browser here
  will display one, so the choice was an imaging dependency or an honest
  refusal -- and the refusal has to SAY WHAT TO DO, because the person
  uploading is standing at their phone and can change one setting in ten
  seconds. Storing it silently would leave a broken picture on the page and
  nobody able to work out why.

  A BLANK CAPTION IS AN IDEA, NOT A DRAFT. The page deliberately does not
  write the caption: a generated sentence about a French chateau reads like
  every other generated sentence. So dropping a photograph in with nothing
  written leaves an honest thing to finish rather than a post that looks done.

  AND UPLOADS ARE NOT A PUBLIC DIRECTORY. The route that serves the picture
  serves out of UPLOAD_DIR, which also holds signed contracts, expense
  receipts and doctors' notes. It is behind a login and it checks the name it
  is given, and both of those are checked here rather than assumed.
"""
from _harness import Suite, clients, db, flashes

import io
import os

import _harness

m = _harness.m
TAG = "ZZINTAKE"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def _cleanup(conn):
    for r in conn.execute(
        "SELECT image_filename FROM social_posts WHERE alt_text LIKE ?",
            (TAG + "%",)).fetchall():
        try:
            os.remove(os.path.join(m.UPLOAD_DIR, r["image_filename"] or ""))
        except OSError:
            pass
    conn.execute("DELETE FROM social_posts WHERE alt_text LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM social_posts WHERE plan_id IN "
                 "(SELECT id FROM social_plans WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM social_plans WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("photographs in")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    def upload(name, data=PNG, **extra):
        return oc.post("/admin/photos",
                       data=dict({"photo": (io.BytesIO(data), name)}, **extra),
                       content_type="multipart/form-data", follow_redirects=True)

    s.section("What counts as a photograph")

    for name, ok in (("a.jpg", True), ("a.jpeg", True), ("a.png", True),
                     ("a.webp", True), ("a.gif", True),
                     ("a.docx", False), ("a.pdf", False), ("noextension", False)):
        got, _why = m.allowed_image(name)
        s.check("%s is %s" % (name, "taken" if ok else "refused"), got == ok)

    # The one that matters, because it is what a phone hands you.
    heic_ok, heic_why = m.allowed_image("IMG_4021.HEIC")
    s.check("an iPhone HEIC is refused", not heic_ok)
    s.check("and the refusal says how to fix it",
            "Most Compatible" in heic_why or "JPEG" in heic_why,
            detail="somebody standing at their phone can change one setting; "
                   "a bare 'not allowed' leaves them stuck")
    s.check("upper case makes no difference",
            m.allowed_image("IMG.HEIC")[0] == m.allowed_image("img.heic")[0])

    s.section("A photograph with nothing written is an idea")

    r = upload("salon.png", alt_text=TAG + " the salon")
    s.check("it is accepted", r.status_code == 200, r)
    row = conn.execute(
        "SELECT * FROM social_posts WHERE alt_text = ? ORDER BY id DESC LIMIT 1",
        (TAG + " the salon",)).fetchone()
    s.check("and kept", row is not None, detail="; ".join(flashes(r)[:1]))
    if row:
        s.check("filed as an idea rather than a draft", row["status"] == "idea",
                detail="a post that looks finished and says nothing is worse "
                       "than an honest thing to finish")
        s.check("the file is really on disk",
                os.path.exists(os.path.join(m.UPLOAD_DIR, row["image_filename"] or "")))
        s.check("and its stored name is not the one that was uploaded",
                (row["image_filename"] or "").startswith("photo_")
                and row["image_filename"] != "salon.png",
                detail="two people uploading salon.png must not be one file")

    r = upload("terrace.png", caption="Lunch under the limes",
               alt_text=TAG + " the terrace")
    row = conn.execute(
        "SELECT * FROM social_posts WHERE alt_text = ? ORDER BY id DESC LIMIT 1",
        (TAG + " the terrace",)).fetchone()
    s.check("with a caption it is a draft", row and row["status"] == "drafted",
            detail=str(row["status"]) if row else "nothing stored")

    s.section("A plan gives it a date without anybody opening a calendar")

    conn.execute(
        """INSERT INTO social_plans (name, platform, cadence, weekday, post_time,
             active, created_at)
           VALUES (?, 'instagram', 'weekly', 2, '09:00', 1, ?)""",
        (TAG + " Wednesdays", now))
    conn.commit()
    plan = conn.execute("SELECT * FROM social_plans WHERE name = ?",
                        (TAG + " Wednesdays",)).fetchone()

    first = m.next_free_slot(conn, plan["id"])
    s.check("the plan offers a next free date", bool(first), detail=str(first))
    s.check("and it is a Wednesday, which is what the plan says",
            first and m.parse_date(first).weekday() == 2, detail=str(first))

    r = upload("one.png", plan_id=str(plan["id"]), alt_text=TAG + " one")
    got = conn.execute(
        "SELECT * FROM social_posts WHERE alt_text = ? ORDER BY id DESC LIMIT 1",
        (TAG + " one",)).fetchone()
    s.check("a photograph against that plan takes the date",
            got and str(got["scheduled_date"])[:10] == first,
            detail="%s vs %s" % (got["scheduled_date"] if got else None, first))
    s.check("and the platform comes from the plan rather than a guess",
            got and got["platform"] == "instagram")

    # The whole point: the SECOND one does not land on top of the first.
    second = m.next_free_slot(conn, plan["id"])
    s.check("the next one is offered a different date", second and second != first,
            detail="%s then %s — two photographs on one slot is one of them "
                   "never going out" % (first, second))
    r = upload("two.png", plan_id=str(plan["id"]), alt_text=TAG + " two")
    got2 = conn.execute(
        "SELECT * FROM social_posts WHERE alt_text = ? ORDER BY id DESC LIMIT 1",
        (TAG + " two",)).fetchone()
    s.check("and takes it", got2 and str(got2["scheduled_date"])[:10] == second,
            detail=str(got2["scheduled_date"]) if got2 else "nothing stored")

    # A date typed by hand beats the suggestion, because somebody typing one
    # has a reason the calendar does not know about.
    when = (m.house_today() + m.timedelta(days=200)).isoformat()
    upload("three.png", plan_id=str(plan["id"]), scheduled_date=when,
           alt_text=TAG + " three")
    got3 = conn.execute(
        "SELECT * FROM social_posts WHERE alt_text = ? ORDER BY id DESC LIMIT 1",
        (TAG + " three",)).fetchone()
    s.check("a date typed by hand is kept",
            got3 and str(got3["scheduled_date"])[:10] == when,
            detail=str(got3["scheduled_date"]) if got3 else "nothing stored")

    s.section("What it refuses, and what it says")

    before = conn.execute("SELECT COUNT(*) c FROM social_posts").fetchone()["c"]
    r = upload("holiday.HEIC")
    s.check("a HEIC writes nothing",
            conn.execute("SELECT COUNT(*) c FROM social_posts").fetchone()["c"] == before)
    s.check("and the page says what to do about it",
            any("Most Compatible" in f for f in flashes(r)),
            detail="; ".join(flashes(r)[:1]))
    r = oc.post("/admin/photos", data={"caption": "no file"},
                content_type="multipart/form-data", follow_redirects=True)
    s.check("no photograph at all is refused too",
            conn.execute("SELECT COUNT(*) c FROM social_posts").fetchone()["c"] == before,
            detail="; ".join(flashes(r)[:1]))

    s.section("Uploads are not a public directory")

    stored = conn.execute(
        "SELECT image_filename FROM social_posts WHERE alt_text = ?",
        (TAG + " one",)).fetchone()
    if stored:
        name = stored["image_filename"]
        s.check("the owner can fetch the picture",
                oc.get("/uploads/%s" % name).status_code == 200)
        # UPLOAD_DIR also holds signed contracts, expense receipts and
        # doctors' notes. This route must never be the open door it looks like.
        anon = m.app.test_client()
        landed = anon.get("/uploads/%s" % name, follow_redirects=False)
        s.check("a stranger is not", landed.status_code in (301, 302, 401, 403),
                detail="HTTP %s — the same directory holds contracts and "
                       "medical notes" % landed.status_code)
    # A file that is genuinely THERE and is not one this app named. Asking for
    # a name that does not exist proves nothing -- it is a 404 whether the
    # check is present or not, which is how the first version of this passed
    # with the guard deleted.
    odd = os.path.join(m.UPLOAD_DIR, "notes to self.txt")
    os.makedirs(m.UPLOAD_DIR, exist_ok=True)
    with io.open(odd, "wb") as fh:
        fh.write(b"not a photograph")
    try:
        served = oc.get("/uploads/notes%20to%20self.txt")
        s.check("a file in there that this app did not name is refused",
                served.status_code == 404, detail="HTTP %s" % served.status_code)
        served.close()
    finally:
        try:
            os.remove(odd)
        except OSError:
            pass
    s.check("a name that is not there is refused too",
            oc.get("/uploads/..%2f..%2fapp.py").status_code in (301, 308, 404))
    s.check("and so is one with a slash in it",
            oc.get("/uploads/sub/dir.png").status_code == 404)

    s.section("The page itself")

    r = oc.get("/admin/photos")
    s.check("the owner can open it", r.status_code == 200, r)
    body = r.get_data(as_text=True)
    s.check("the plan is offered by name", TAG + " Wednesdays" in body)
    s.check("and the recent strip shows what has been put in",
            "/uploads/" in body, detail="the strip is how somebody sees it landed")
    s.check("an employee cannot", ec.get("/admin/photos").status_code != 200)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
