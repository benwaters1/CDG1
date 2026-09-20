"""One master, every size made from it, and three things that fail in silence.

A GH5 frame is 5184x3888 and ten megabytes. Nothing downstream wants that: the
website wants something that paints on a train, Instagram refuses anything
wider than 1440 outright, and the caption model throws away everything past
1568 before it looks at it. So the frame is normalised once at the door.

WHAT THIS FILE IS REALLY GUARDING is that none of the three things done at
that door announce themselves when they stop happening.

  ROTATION. A camera held upright writes a sideways frame and an EXIF tag
  saying "turn this". Browsers honour the tag. PIL does not, Meta's fetcher
  does not, and neither does anything that re-encodes -- so the picture that
  looked right in the admin page goes out on Instagram on its side, and the
  first anybody knows is the post. The pixels are turned once and the tag is
  dropped, and BOTH halves are checked: a file that is turned and still
  carries the tag gets turned twice by the next thing that reads it, which is
  the same bug wearing the other shoe.

  METADATA. A camera frame carries the lens, the body's serial number and, on
  a body with location on, where the photographer was standing. Publishing the
  house's photographs is the job. Publishing its coordinates is not, and it
  would never show up on any page. The check proves the GPS was THERE in the
  source first -- a test that the output has no GPS passes beautifully against
  an input that never had any.

  THE DAY. Taken from the shutter, not from when the file arrived, because a
  card emptied on Sunday is full of Saturday.
"""
import io
import os

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zzsize"


def _frame(w, h, colour=(180, 40, 40), orientation=None, taken=None,
           gps=False, make=None):
    """A JPEG carrying whatever EXIF the check needs, built in memory."""
    from PIL import Image
    img = Image.new("RGB", (w, h), colour)
    exif = img.getexif()
    if orientation:
        exif[274] = orientation
    if taken:
        exif[36867] = taken
    if make:
        exif[271] = make
    if gps:
        ifd = exif.get_ifd(0x8825)
        # The château, near enough. Floats rather than rational pairs: Pillow
        # builds the rationals itself and chokes on pre-made ones.
        ifd[1] = "N"
        ifd[2] = (42.0, 46.0, 0.0)
        ifd[3] = "E"
        ifd[4] = (1.0, 38.0, 0.0)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif, quality=92)
    return buf.getvalue()


def _open(data):
    from PIL import Image
    return Image.open(io.BytesIO(data))


def _cleanup():
    dirs = [m.UPLOAD_DIR] + [os.path.join(m.PHOTO_DERIVED_DIR, size)
                             for size in m.PHOTO_SIZES]
    for folder in dirs:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if name.startswith(TAG):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass


def run():
    s = Suite("A photograph off the camera")
    _cleanup()

    s.section("The camera was held upright")
    # Orientation 6 is a portrait frame written sideways -- the commonest tag
    # there is, and the one that puts a post out on its side.
    sideways = _frame(400, 200, orientation=6)
    s.check("the frame really is stored sideways, tag and all",
            _open(sideways).size == (400, 200)
            and _open(sideways).getexif().get(274) == 6,
            detail="if this stops being true the two checks below prove nothing")
    upright, _taken = m.photo_master(sideways)
    s.check("the pixels are turned the right way up",
            _open(upright).size == (200, 400),
            detail=f"{_open(upright).size} - a browser honours the tag and PIL "
                   "does not, so the admin page looked right while Instagram "
                   "would have had it on its side")
    s.check("and the tag goes with them, so nothing turns it twice",
            not _open(upright).getexif().get(274),
            detail=f"orientation {_open(upright).getexif().get(274)} - a turned "
                   "frame that still says 'turn me' is the same bug the other "
                   "way round")

    s.section("What the camera knew about where it was standing")
    located = _frame(300, 200, gps=True, make="Panasonic")
    s.check("the source really is carrying coordinates",
            bool(_open(located).getexif().get_ifd(0x8825)),
            detail="checking that the output has no GPS is a check that passes "
                   "perfectly against an input that never had any")
    stripped, _t = m.photo_master(located)
    out = _open(stripped).getexif()
    s.check("they do not survive the door",
            not out.get_ifd(0x8825),
            detail=f"{dict(out.get_ifd(0x8825))} - the house's photographs are "
                   "for publishing. Where it was standing is not, and no page "
                   "would ever have shown that it was going out")
    s.check("and neither does the kit list",
            not out.get(271), detail=f"{out.get(271)}")

    s.section("The day it was taken")
    dated = _frame(200, 150, taken="2026:03:04 07:08:09")
    _bytes, taken = m.photo_master(dated)
    s.check("comes off the shutter, not off the clock",
            taken and taken.isoformat() == "2026-03-04",
            detail=f"{taken} - a card emptied on Sunday is full of Saturday")
    _b2, none_taken = m.photo_master(_frame(120, 120))
    s.check("and a frame that does not say is not guessed at",
            none_taken is None, detail=f"{none_taken}")

    s.section("Ten megabytes is nobody's idea of a web page")
    big, _t = m.photo_master(_frame(5184, 3888))
    s.check("the master comes down to a size a volume can hold",
            max(_open(big).size) == m.PHOTO_MASTER_EDGE,
            detail=f"{_open(big).size} from 5184x3888")
    s.check("without being stretched on the way",
            abs((_open(big).size[0] / _open(big).size[1]) - (5184 / 3888)) < 0.01,
            detail=f"{_open(big).size}")

    s.section("Every size has somebody it is for")
    name = TAG + "_master.jpg"
    os.makedirs(m.UPLOAD_DIR, exist_ok=True)
    with open(os.path.join(m.UPLOAD_DIR, name), "wb") as fh:
        fh.write(big)
    made = {}
    for size in m.PHOTO_SIZES:
        path = m.photo_rendition(name, size)
        with open(path, "rb") as fh:
            made[size] = _open(fh.read()).size
    s.check("Instagram's ceiling is respected rather than discovered",
            max(made["social"]) <= 1440,
            detail=f"{made['social']} - Meta refuses anything wider outright, "
                   "and the refusal lands at the scheduled hour, hours after "
                   "whoever chose the picture went to bed")
    s.check("the caption model is not sent pixels it throws away",
            max(made["vision"]) <= 1568, detail=f"{made['vision']}")
    s.check("a tray of forty thumbnails is forty small files",
            max(made["thumb"]) <= 400, detail=f"{made['thumb']}")

    s.section("Made once, and made again when the picture changes")
    first = m.photo_rendition(name, "thumb")
    stamp = os.path.getmtime(first)
    m.photo_rendition(name, "thumb")
    s.check("asking twice does not resize twice",
            os.path.getmtime(first) == stamp,
            detail="every page that shows a tray asks for all of them at once")
    # The website swap: the same name, a different picture behind it.
    replacement, _t = m.photo_master(_frame(900, 900, colour=(20, 90, 200)))
    with open(os.path.join(m.UPLOAD_DIR, name), "wb") as fh:
        fh.write(replacement)
    os.utime(os.path.join(m.UPLOAD_DIR, name), (stamp + 10, stamp + 10))
    with open(m.photo_rendition(name, "thumb"), "rb") as fh:
        again = _open(fh.read())
    s.check("but replacing the picture replaces every size of it",
            again.size[0] == again.size[1],
            detail=f"{again.size} - swapping a website photograph while every "
                   "size still showed the old one would leave the admin page "
                   "and the public site disagreeing about what is on it")

    s.section("Serving one, at the size the page asked for")
    oc, _ec, _owner, _emp = clients()
    shown = oc.get(f"/photo/thumb/{name}")
    s.check("the page gets its thumbnail", shown.status_code == 200,
            detail=f"HTTP {shown.status_code}")
    # MEASURED IN PIXELS, NOT BYTES. Bytes was the first way this was written
    # and it could not fail: by this point the master on disk has been swapped
    # for the smaller replacement above, so "fewer bytes than the master" was
    # true whether the route served the thumbnail or the master itself. The
    # size served is the actual claim, so it is the thing to ask about.
    served = _open(shown.data).size
    on_disk = _open(open(os.path.join(m.UPLOAD_DIR, name), "rb").read()).size
    s.check("and it is the small one, not the master",
            max(served) <= 400 < max(on_disk),
            detail=f"served {served}, master on disk {on_disk} - a tray that "
                   "serves masters is forty full-size frames on one page")
    s.check("a size nobody defined is not invented",
            oc.get(f"/photo/enormous/{name}").status_code == 404,
            detail=f"HTTP {oc.get(f'/photo/enormous/{name}').status_code}")
    # UPLOAD_DIR holds signed contracts and doctors' notes beside the
    # photographs, so this route being open would be a door onto all of it.
    anon = _harness.m.app.test_client()
    s.check("and a stranger is not served one at all",
            anon.get(f"/photo/thumb/{name}").status_code in (302, 401, 403),
            detail=f"HTTP {anon.get(f'/photo/thumb/{name}').status_code} - "
                   "uploads are not a public directory")

    s.section("What the page showed is what gets saved")
    # THE EDITOR HAD NEVER ONCE BEEN OBEYED. The intake page straightens the
    # exposure, warms it, lifts the contrast and crops, draws the result to a
    # canvas and posts it as a data URL beside the original file. The route
    # read request.files and nothing else, so every adjustment and every crop
    # was discarded in silence -- the page showed the corrected picture, the
    # button said "Save the photograph", and the untouched frame is what
    # landed. The only way to catch it was to compare the stored file against
    # what you remembered doing.
    import base64
    original = _frame(600, 400, colour=(200, 30, 30), taken="2026:05:06 09:10:11")
    # Unmistakably different: a square crop, and a colour the original has none
    # of. "The file changed" would pass on a re-encode; this cannot.
    edited_bytes = _frame(300, 300, colour=(10, 60, 220))
    posted = oc.post("/admin/photos", data={
        "photo": (io.BytesIO(original), "salon.jpg"),
        "edited": "data:image/jpeg;base64," + base64.b64encode(edited_bytes).decode(),
        "alt_text": TAG + " edited",
    }, content_type="multipart/form-data", follow_redirects=True)
    conn = db()
    row = conn.execute(
        "SELECT * FROM social_posts WHERE alt_text = ? ORDER BY id DESC LIMIT 1",
        (TAG + " edited",)).fetchone()
    conn.close()
    s.check("the photograph lands", bool(row),
            detail=f"HTTP {posted.status_code}")
    if row:
        with open(os.path.join(m.UPLOAD_DIR, row["image_filename"]), "rb") as fh:
            stored = _open(fh.read())
        px = stored.convert("RGB").getpixel((stored.size[0] // 2, stored.size[1] // 2))
        s.check("and it is the edited one, not the frame as shot",
                stored.size[0] == stored.size[1] and px[2] > px[0],
                detail=f"{stored.size} and {px} - a 600x400 red frame means "
                       "the crop and every slider were thrown away, which is "
                       "what happened every time until this was written")
        # A canvas carries no EXIF, so this is the half that breaks the moment
        # the edited picture is preferred, and nothing would say so.
        s.check("and the day it was taken survives being edited",
                row["taken_on"] == "2026-05-06",
                detail=f"{row['taken_on']} - read off the frame as shot, which "
                       "is posted alongside and still has it")
        conn = db()
        conn.execute("DELETE FROM social_posts WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        try:
            os.remove(os.path.join(m.UPLOAD_DIR, row["image_filename"]))
        except OSError:
            pass

    s.section("A file that is not a photograph")
    try:
        m.photo_master(b"II*\x00 not really a raw file")
        refused = ""
    except ValueError as e:
        refused = str(e)
    s.check("is refused with a sentence somebody can act on",
            "rw2" in refused.lower() or "jpeg" in refused.lower(),
            detail=f"{refused!r} - the GH5 shoots RAW as well, and the person "
                   "reading this is standing at the camera")
    _cleanup()
    return s
