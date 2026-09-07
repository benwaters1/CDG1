# -*- coding: utf-8 -*-
"""The camera roll: what comes off the card, and what becomes of it.

The house has photographed its own restoration since 2013 and almost none of
it has ever been seen. The bottleneck was never the camera — it is that
choosing, captioning and posting four hundred frames is an evening's work
nobody has, every week, forever. So a frame is looked at once on arrival and
the ordinary case goes all the way through on its own.

Which means the checks here are mostly about the cases that must NOT go
through on their own, because those are the ones automation gets wrong:

  A PERSON IN THE FRAME IS A GATE, NOT A SCORE. A guest's face on a public
  page unasked is the one mistake on this page that cannot be taken back —
  the page can be changed in a minute, a screenshot cannot — and the privacy
  notice makes a promise about it. So it is checked twice: the assessment
  holds it, and publish_media refuses it again at the moment of publishing,
  because the row could have been written by a model that was wrong or an
  owner ticking quickly. The second check is the one tested hardest here: it
  is the one that has no reason to exist until the day it does.

  THE SAME CARD, READ AGAIN, COSTS NOTHING. It will be plugged in every week
  with the same four hundred frames on it and forty new ones. Keyed on the
  bytes, so re-reading imports only what is new — and cannot put the same
  photograph on the public page twice or bill the same frame twice.

  NOTHING IS POSTED. The best frames become DRAFTS in the social queue that
  already exists, with the words written. Publishing to somebody else's
  audience on the owner's behalf is theirs to press.

  A FAILURE HOLDS, NEVER REJECTS. A photograph quietly binned because a
  request timed out is a photograph nobody knows they lost.

  AND A CLIP NOBODY HAS WATCHED GETS NO CAPTION. Without ffmpeg there is no
  frame to look at, so the clip is held saying exactly that rather than
  described from its filename.
"""
import io as _io
import os

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "rolltest-"


def _png(colour=(90, 70, 50), size=(48, 32)):
    """A real image, because the code opens it with Pillow and reads its size."""
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def _cleanup(conn):
    rows = conn.execute(
        "SELECT * FROM media_intake WHERE original_name LIKE ?",
        (TAG + "%",)).fetchall()
    for r in rows:
        if r["restoration_photo_id"]:
            conn.execute("DELETE FROM restoration_photos WHERE id = ?",
                         (r["restoration_photo_id"],))
        if r["social_post_id"]:
            conn.execute("DELETE FROM social_posts WHERE id = ?",
                         (r["social_post_id"],))
        for name in (r["filename"], r["poster"]):
            if name:
                path = os.path.join(m.ROOM_PHOTO_DIR, name)
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
    conn.execute("DELETE FROM media_intake WHERE original_name LIKE ?",
                 (TAG + "%",))
    conn.commit()


def _verdict(**over):
    got = {"verdict": "publish", "score": 90, "shows": "A stripped reveal",
           "has_people": False, "site_caption": "A window reveal, back to stone",
           "social_caption": "The render came off this week.",
           "hold_reason": None}
    got.update(over)
    return got


def run():
    s = Suite("The camera roll: off the card, onto the page")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    # ---- what it takes, and what it refuses ------------------------------
    s.section("Reading a card")
    s.check("RAW is not taken, because a browser cannot show one",
            m.media_kind_of("P1000123.RW2") is None,
            detail="the GH5 writes .RW2 alongside its JPEGs; shoot RAW+JPEG "
                   "and the JPEG is what arrives")
    s.check("a clip is recognised as a clip",
            m.media_kind_of("P1000123.MP4") == "video")

    # A clean card first, on its own. Read together with the raw file below
    # this answered with an ERROR flash -- correctly, because bulk_message is
    # an error the moment anything is skipped -- and the whole successful
    # path was therefore never once observed succeeding.
    up = oc.post("/admin/camera/upload", data={
        "media": [(_io.BytesIO(_png()), TAG + "one.png"),
                  (_io.BytesIO(_png((30, 40, 60))), TAG + "two.png")],
        "source": "test"},
        content_type="multipart/form-data")
    s.check("a card can be read", up.status_code == 302)
    s.check("and a clean card says so plainly",
            "Took in 2 files" in oc.get("/admin/camera").get_data(as_text=True),
            detail="nothing was skipped, so nothing should read as a problem")

    raw = oc.post("/admin/camera/upload", data={
        "media": [(_io.BytesIO(b"not an image at all"), TAG + "three.rw2")]},
        content_type="multipart/form-data")
    s.check("a raw file is refused, and the refusal is reported",
            raw.status_code == 302
            and "RAW" in oc.get("/admin/camera").get_data(as_text=True),
            detail="a photograph that vanishes without a word is how "
                   "somebody finds out in March that October never arrived")

    rows = conn.execute(
        "SELECT * FROM media_intake WHERE original_name LIKE ? "
        "ORDER BY original_name", (TAG + "%",)).fetchall()
    s.check("the photographs came in and the raw file did not",
            len(rows) == 2, detail="%d row(s): %s"
            % (len(rows), [r["original_name"] for r in rows]))
    if len(rows) != 2:
        _cleanup(conn)
        conn.close()
        return s


    s.check("and each is stored under the fingerprint of its own bytes",
            all(len(r["sha256"]) == 64 for r in rows)
            and rows[0]["sha256"] != rows[1]["sha256"])
    s.check("with the file actually on the volume",
            all(os.path.exists(os.path.join(m.ROOM_PHOTO_DIR, r["filename"]))
                for r in rows))

    # ---- the same card again ---------------------------------------------
    s.section("The same card, next week")
    again = oc.post("/admin/camera/upload", data={
        "media": [(_io.BytesIO(_png()), TAG + "one.png"),
                  (_io.BytesIO(_png((10, 90, 10))), TAG + "four.png")]},
        content_type="multipart/form-data")
    s.check("reading it again is not an error", again.status_code == 302)
    s.check("the one already here is not taken in twice",
            conn.execute(
                "SELECT COUNT(*) AS c FROM media_intake WHERE sha256 = ?",
                (rows[0]["sha256"],)).fetchone()["c"] == 1,
            detail="a card is plugged in every week with the same four "
                   "hundred frames on it; keyed on the bytes, that costs "
                   "nothing")
    s.check("and the new one is",
            conn.execute(
                "SELECT COUNT(*) AS c FROM media_intake "
                "WHERE original_name = ?",
                (TAG + "four.png",)).fetchone()["c"] == 1)

    # ---- the assessment ---------------------------------------------------
    s.section("Looking at one frame")
    good, person = rows[0]["id"], rows[1]["id"]

    # Two stand-ins, not one. assess_media_row asks claude_configured()
    # BEFORE it calls out, so that the owner is told "no key is set" rather
    # than "it did not come back" -- two different problems with two
    # different fixes. Under test the key really is unset, so both have to be
    # stood in or the fake below is never reached.
    real_configured = m.claude_configured
    m.claude_configured = lambda: True
    m.assess_media_with_claude = lambda *_a, **_k: _verdict()
    outcome = m.assess_media_row(conn, good)
    conn.commit()
    s.check("a good frame goes up on its own", outcome == "published",
            detail=str(outcome))
    row = conn.execute("SELECT * FROM media_intake WHERE id = ?",
                       (good,)).fetchone()
    s.check("it is filed against a piece of work rather than nowhere",
            row["work_id"] is not None and row["restoration_photo_id"],
            detail=str(dict(row))[:160])
    photo = conn.execute("SELECT * FROM restoration_photos WHERE id = ?",
                         (row["restoration_photo_id"],)).fetchone()
    s.check("and the photograph carries the caption that was written for it",
            photo and photo["caption"] == "A window reveal, back to stone",
            detail=str(dict(photo)) if photo else "no photograph row")
    s.check("with a taken-on date, so it can be read in order",
            bool(photo and photo["taken_on"]),
            detail=str(photo["taken_on"]) if photo else "no photograph row")

    # ---- the gate ---------------------------------------------------------
    s.section("Somebody in the frame")
    m.assess_media_with_claude = lambda *_a, **_k: _verdict(
        has_people=True, verdict="publish", score=97,
        hold_reason="there is a guest at the window")
    outcome = m.assess_media_row(conn, person)
    conn.commit()
    s.check("a frame with somebody in it is held, whatever else was decided",
            outcome == "held",
            detail="%s — the assessment said publish and scored it 97; the "
                   "gate is not a score to be weighed against sharpness"
                   % outcome)
    held = conn.execute("SELECT * FROM media_intake WHERE id = ?",
                        (person,)).fetchone()
    s.check("and it is on none of the public pages",
            held["status"] == "held" and not held["restoration_photo_id"])
    s.check("with the reason written down, so it can be dealt with",
            held["hold_reason"], detail=str(held["hold_reason"]))

    ok, why = m.publish_media(conn, person)
    conn.commit()
    s.check("and publishing it outright is refused at the last moment too",
            ok is False,
            detail="%s — the row was written by something that could have "
                   "been wrong, and this is the last place it can be caught"
                   % why)
    s.check("the refusal says why rather than failing quietly",
            "somebody in it" in (why or ""), detail=str(why))
    forced = oc.post("/admin/camera/%d/publish" % person)
    s.check("the button on the page cannot get round it either",
            forced.status_code == 302
            and not conn.execute(
                "SELECT restoration_photo_id FROM media_intake WHERE id = ?",
                (person,)).fetchone()["restoration_photo_id"],
            detail="one owner, in a hurry, ticking quickly")

    s.check("it is kept off the public strip",
            all(r["id"] != person for r in m.media_strip(conn)))

    # ---- the buttons, not only the functions behind them ------------------
    #
    # Everything above drove assess_media_row and publish_media directly.
    # That tests the rule and not the page, and the coverage report said so:
    # these three routes had been reached by the empty-form sweep and had
    # never once answered yes to anybody.
    s.section("The buttons on the page")
    fresh = conn.execute(
        "SELECT id FROM media_intake WHERE original_name = ?",
        (TAG + "four.png",)).fetchone()["id"]
    m.assess_media_with_claude = lambda *_a, **_k: _verdict(
        score=70, social_caption=None,
        site_caption=TAG + "scaffolding on the south front")
    pressed = oc.post("/admin/camera/assess", data={"id": str(fresh)})
    s.check("the button that looks at what nothing has seen works",
            pressed.status_code == 302, detail=str(pressed.status_code))
    row = conn.execute("SELECT * FROM media_intake WHERE id = ?",
                       (fresh,)).fetchone()
    s.check("and the frame it looked at went up",
            row["status"] == "published" and row["restoration_photo_id"],
            detail="score 70, over the %d bar" % m.MEDIA_PUBLISH_SCORE)
    s.check("but a 70 is not offered as a post",
            row["social_post_id"] is None,
            detail="a picture good enough for the record is not "
                   "automatically good enough to put in front of people who "
                   "did not ask for it — that bar is %d"
                   % m.MEDIA_SOCIAL_SCORE)

    oc.post("/admin/camera/%d/set-aside" % fresh)
    aside = conn.execute("SELECT * FROM media_intake WHERE id = ?",
                         (fresh,)).fetchone()
    s.check("setting one aside takes it off the page",
            aside["status"] == "rejected"
            and aside["restoration_photo_id"] is None)
    s.check("and keeps the file, so re-reading the card does not ask again",
            os.path.exists(os.path.join(m.ROOM_PHOTO_DIR, aside["filename"])),
            detail="the hash is the whole reason a card can be plugged in "
                   "every week for nothing")

    back = oc.post("/admin/camera/%d/publish" % fresh)
    s.check("and it can be put back up by hand", back.status_code == 302
            and conn.execute(
                "SELECT status FROM media_intake WHERE id = ?",
                (fresh,)).fetchone()["status"] == "published")

    # ---- what a failure does ----------------------------------------------
    s.section("When nothing could judge it")
    spare = conn.execute(
        "SELECT id FROM media_intake WHERE original_name = ?",
        (TAG + "four.png",)).fetchone()["id"]
    m.assess_media_with_claude = lambda *_a, **_k: None
    outcome = m.assess_media_row(conn, spare)
    conn.commit()
    s.check("a frame nothing could judge is held, never binned",
            outcome == "held",
            detail="a photograph quietly dropped because a request timed out "
                   "is one nobody knows they lost")
    row = conn.execute("SELECT * FROM media_intake WHERE id = ?",
                       (spare,)).fetchone()
    s.check("and it says so rather than sitting there blank",
            row["hold_reason"] and row["assessed_at"],
            detail=str(row["hold_reason"]))

    # ---- posting is not automatic -----------------------------------------
    s.section("Offered as a post, never posted")
    post = conn.execute(
        "SELECT sp.* FROM social_posts sp JOIN media_intake mi "
        "ON mi.social_post_id = sp.id WHERE mi.id = ?", (good,)).fetchone()
    s.check("a frame worth posting is drafted into the queue that exists",
            post is not None,
            detail="score 90, over the %d bar" % m.MEDIA_SOCIAL_SCORE)
    s.check("as an idea, not as something that has gone out",
            post and post["status"] == "idea",
            detail="%s — publishing to somebody else's audience on the "
                   "owner's behalf is theirs to press"
                   % (post["status"] if post else "none"))
    s.check("with the words already written",
            post and "render came off" in (post["caption"] or ""),
            detail=str(post["caption"]) if post else "")

    # ---- the owner's words win --------------------------------------------
    s.section("Your words instead of its")
    oc.post("/admin/camera/%d/caption" % good, data={
        "site_caption": TAG + "the north reveal",
        "social_caption": TAG + "a post in my own words"})
    photo = conn.execute("SELECT caption FROM restoration_photos WHERE id = ?",
                         (row and conn.execute(
                             "SELECT restoration_photo_id FROM media_intake "
                             "WHERE id = ?", (good,)).fetchone()[0],)).fetchone()
    s.check("editing the caption reaches the published photograph as well",
            photo and photo["caption"] == TAG + "the north reveal",
            detail="%s — otherwise the page still shows the machine's words "
                   "while the roll shows yours, which is two answers to one "
                   "question" % (photo["caption"] if photo else "none"))

    # ---- the machine door -------------------------------------------------
    s.section("The card reader's door")
    s.check("it does not exist while no key is set",
            oc.post("/ingest/known", json={"sha256": []}).status_code == 404,
            detail="a 404 rather than a 401 — an unconfigured door should "
                   "not advertise itself")
    m.MEDIA_INGEST_KEY = "zz-test-key"
    try:
        anon = m.app.test_client()
        s.check("and refuses a request without the key",
                anon.post("/ingest/known", json={"sha256": []}).status_code == 403)
        told = anon.post("/ingest/known",
                         json={"sha256": [rows[0]["sha256"], "0" * 64]},
                         headers={"X-Ingest-Key": "zz-test-key"})
        s.check("with the key, it says which frames it already has",
                told.status_code == 200
                and told.get_json()["known"] == [rows[0]["sha256"]],
                detail="%s — asked before anything is sent, so the house does "
                       "not push nine megabytes a frame up a rural line to be "
                       "told each time that it had it" % told.get_json())
        sent = anon.post(
            "/ingest/media",
            data={"media": (_io.BytesIO(_png((5, 5, 90))), TAG + "card.png")},
            headers={"X-Ingest-Key": "zz-test-key"},
            content_type="multipart/form-data")
        s.check("and a card can be put through it without a browser",
                sent.status_code == 200 and sent.get_json()["taken"] == 1,
                detail="%s — this is the door tools/ingest_media.py uses, and "
                       "it is the one that decides whether any of this gets "
                       "used in November" % sent.get_json())
        s.check("filed with where it came from, not as a page upload",
                conn.execute(
                    "SELECT source FROM media_intake WHERE original_name = ?",
                    (TAG + "card.png",)).fetchone()["source"] == "card")
    finally:
        m.MEDIA_INGEST_KEY = ""

    # ---- the pages --------------------------------------------------------
    s.section("What the pages say")
    page = oc.get("/admin/camera").get_data(as_text=True)
    s.check("the roll opens", "Camera roll" in page)
    s.check("and says plainly that nothing is posted by the app",
            "nothing is ever posted" in page.lower(),
            detail="the one thing somebody must not be surprised by")

    public = m.app.test_client()
    strip = public.get("/restoration/work-in-progress")
    s.check("anybody may read the work-in-progress page",
            strip.status_code == 200)
    # Whitespace-flattened: the sentence wraps across three lines in the
    # template, so a raw substring search tests the line breaks rather than
    # the prose and goes red the first time somebody reflows a paragraph.
    body = strip.get_data(as_text=True)
    flat = " ".join(body.split())
    s.check("and it explains what it is before showing anything",
            "one approved intervention at a time" in flat,
            detail="a strip of scaffolding with no explanation reads as a "
                   "building site somebody forgot to tidy")
    s.check("the published frame is on it",
            TAG + "the north reveal" in flat)
    s.check("an employee cannot open the roll",
            ec.get("/admin/camera").status_code == 403)

    m.assess_media_with_claude = _harness.REAL_ASSESS_MEDIA
    m.claude_configured = real_configured
    _cleanup(conn)
    conn.close()
    return s
