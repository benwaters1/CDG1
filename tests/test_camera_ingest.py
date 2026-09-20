"""The camera's way in, and the tray it lands in.

The GH5 sends over Wi-Fi to a shared folder on a machine at the house, because
that is the only destination it has left — Panasonic's cloud and web-service
options went when LUMIX CLUB shut down in 2022, and the remaining one needs a
phone. The app runs on Railway. So a watcher on that machine forwards what
lands, and this is what it forwards to.

THREE THINGS CARRY THIS FILE.

  SENDING TWICE IS NORMAL AND MUST BE HARMLESS. The watcher restarts and
  re-reads the folder. The card goes back in the camera. The same frame gets
  picked again at the end of a second shoot. None of those is an error and
  none of them is a second photograph, so arrival is deduplicated on the bytes
  as sent — and the answer SAYS which it was. A watcher that cannot tell
  "stored" from "you already have this" either sends everything forever or
  stops sending after its first restart.

  A PHOTOGRAPH IS NOT A POST UNTIL SOMEBODY SAYS SO. Everything arriving lands
  in a tray, used for nothing. Filing it as a draft post on arrival would turn
  the social list into a camera roll, and a list that is mostly things nobody
  chose is one people stop reading — the same failure the self-closing watch
  tasks exist to avoid.

  AND THE DOOR IS LOCKED. No login, because a script has no session; the token
  is the credential, like the supplier upload links. Which makes the check on
  that token the only thing standing between a public URL and anybody filling
  the volume with whatever they like.
"""
import hashlib
import io
import json
import os

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zzcam"


def _frame(w, h, colour=(90, 120, 70), taken=None):
    from PIL import Image
    img = Image.new("RGB", (w, h), colour)
    exif = img.getexif()
    if taken:
        exif[36867] = taken
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif, quality=90)
    return buf.getvalue()


def _cleanup():
    conn = db()
    for row in conn.execute(
            "SELECT filename FROM photo_inbox WHERE original_name LIKE ?",
            (TAG + "%",)).fetchall():
        try:
            os.remove(os.path.join(m.UPLOAD_DIR, row["filename"]))
        except OSError:
            pass
    conn.execute("DELETE FROM photo_inbox WHERE original_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _send(client, data, name, token, **extra):
    return client.post(
        "/api/photographs",
        data=dict({"photo": (io.BytesIO(data), name)}, **extra),
        headers={"X-Gudanes-Token": token} if token is not None else {},
        content_type="multipart/form-data")


def run():
    s = Suite("A photograph arriving from the camera")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    conn = db()
    token = m.camera_ingest_token(conn)
    conn.close()
    anon = m.app.test_client()

    s.section("The door is locked")
    s.check("a token was generated without anybody being asked",
            bool(token) and len(token) > 20,
            detail="a deployment with the door open and no lock on it is the "
                   "state this must never exist in")
    refused = _send(anon, _frame(200, 150), TAG + "-nope.jpg", "not-the-token")
    s.check("the wrong token is turned away", refused.status_code == 401,
            detail=f"HTTP {refused.status_code}")
    none_at_all = _send(anon, _frame(200, 150), TAG + "-nope2.jpg", None)
    s.check("and so is no token at all", none_at_all.status_code == 401,
            detail=f"HTTP {none_at_all.status_code}")
    conn = db()
    s.check("and neither of them left anything behind",
            conn.execute("SELECT COUNT(*) AS n FROM photo_inbox "
                         "WHERE original_name LIKE ?",
                         (TAG + "-nope%",)).fetchone()["n"] == 0,
            detail="a refusal that still writes the file is not a refusal")
    conn.close()

    s.section("A photograph arrives")
    frame = _frame(1200, 800, taken="2026:04:05 16:17:18")
    first = _send(anon, frame, TAG + "-P1030412.JPG", token)
    body = json.loads(first.get_data(as_text=True) or "{}")
    s.check("it is accepted", first.status_code == 200 and body.get("ok"),
            detail=f"HTTP {first.status_code} {body}")
    s.check("and the answer says it was stored", body.get("status") == "stored",
            detail=f"{body.get('status')}")
    conn = db()
    row = conn.execute("SELECT * FROM photo_inbox WHERE id = ?",
                       (body.get("id"),)).fetchone()
    conn.close()
    s.check("the tray has it", bool(row))
    s.check("filed under the day it was taken, not the day it arrived",
            row and row["taken_on"] == "2026-04-05",
            detail=f"{row['taken_on'] if row else None} — a card emptied on "
                   "Sunday is full of Saturday")
    s.check("with the camera's own name kept for it",
            row and row["original_name"] == TAG + "-P1030412.JPG",
            detail=f"{row['original_name'] if row else None}")
    s.check("and it is normalised like anything else",
            row and os.path.exists(os.path.join(m.UPLOAD_DIR, row["filename"]))
            and row["filename"].endswith(".jpg"),
            detail=f"{row['filename'] if row else None}")

    s.section("It is in a tray, not on Instagram")
    s.check("nothing has been done with it yet",
            row and not row["used_as"] and not row["dismissed"],
            detail=f"used_as {row['used_as'] if row else None} — a frame off "
                   "the camera is not a post until somebody says it is")
    conn = db()
    s.check("and it has not become a social post behind anybody's back",
            conn.execute(
                "SELECT COUNT(*) AS n FROM social_posts WHERE image_filename = ?",
                (row["filename"] if row else "",)).fetchone()["n"] == 0,
            detail="filing arrivals as drafts turns the social list into a "
                   "camera roll, and nobody reads that list twice")
    conn.close()

    s.section("Sending it again is not a second photograph")
    again = _send(anon, frame, TAG + "-P1030412.JPG", token)
    twice = json.loads(again.get_data(as_text=True) or "{}")
    s.check("the second send is accepted rather than errored",
            again.status_code == 200 and twice.get("ok"),
            detail=f"HTTP {again.status_code} — the watcher restarting is "
                   "ordinary, and an error would have it retry forever")
    s.check("but it says the photograph was already here",
            twice.get("status") == "already here",
            detail=f"{twice.get('status')} — a watcher that cannot tell the "
                   "difference either sends everything forever or stops after "
                   "its first restart")
    s.check("and it is the same row, not a new one",
            twice.get("id") == body.get("id"),
            detail=f"{twice.get('id')} against {body.get('id')}")
    conn = db()
    s.check("so the tray still holds one of it",
            conn.execute("SELECT COUNT(*) AS n FROM photo_inbox "
                         "WHERE original_name = ?",
                         (TAG + "-P1030412.JPG",)).fetchone()["n"] == 1,
            detail="the same frame twice in the tray is somebody choosing "
                   "between two identical pictures")
    conn.close()
    # A DIFFERENT frame with the SAME NAME. Cameras reuse filenames — P1030412
    # comes round every ten thousand shots, and formatting the card restarts
    # the count — so deduplicating on the name would silently swallow this one.
    other = _send(anon, _frame(640, 480, colour=(10, 10, 200)),
                  TAG + "-P1030412.JPG", token)
    other_body = json.loads(other.get_data(as_text=True) or "{}")
    s.check("a different photograph with the same name is still a new one",
            other_body.get("status") == "stored"
            and other_body.get("id") != body.get("id"),
            detail=f"{other_body} — cameras reuse filenames, so matching on "
                   "the name loses a real photograph and never says so")

    s.section("What it will not take")
    bad = _send(anon, b"II*\x00 not a photograph at all", TAG + "-raw.rw2", token)
    s.check("a file it cannot read is refused, with a reason",
            bad.status_code == 415
            and "rw2" in (json.loads(bad.get_data(as_text=True) or "{}")
                          .get("error") or "").lower(),
            detail=f"HTTP {bad.status_code} "
                   f"{bad.get_data(as_text=True)[:90]}")
    nothing = anon.post("/api/photographs", data={"source": "camera"},
                        headers={"X-Gudanes-Token": token},
                        content_type="multipart/form-data")
    s.check("and a request with no photograph in it is not a 500",
            nothing.status_code == 400, detail=f"HTTP {nothing.status_code}")

    s.section("The watcher that does the forwarding")
    # Imported rather than run: this is the piece that lives on a machine
    # nobody here can see, so the parts that can be checked should be.
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "photo_watcher", os.path.join(root, "tools", "photo_watcher.py"))
    watcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watcher)
    s.check("it will not touch a RAW file",
            ".rw2" not in watcher.PHOTO_EXTENSIONS,
            detail="sending one is a round trip to a polite refusal")
    # THE HALF-WRITTEN FILE. A camera writing ten megabytes over Wi-Fi takes
    # seconds, and a JPEG read halfway through stores as a picture with a grey
    # band across the bottom — which nothing ever retries, because the name
    # never changes again.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        growing = os.path.join(tmp, "P1030500.JPG")
        with open(growing, "wb") as fh:
            fh.write(b"half a frame")
        sizes = {}
        s.check("a file seen for the first time is left alone",
                not watcher.settled(growing, sizes),
                detail="one look cannot tell a finished file from a growing one")
        with open(growing, "ab") as fh:
            fh.write(b" and the rest of it")
        s.check("and one that is still growing is left alone too",
                not watcher.settled(growing, sizes),
                detail="a half-written JPEG stores as a picture with a grey "
                       "band across the bottom and is never retried")
        s.check("only one that has stopped changing is sent",
                watcher.settled(growing, sizes), detail="two looks, same size")
        # AND IT NEVER DELETES. The folder belongs to the camera.
        s.check("and the folder it watches is left as it found it",
                os.path.exists(growing),
                detail="a watcher that empties a folder it does not own is "
                       "one bad path away from deleting a card of work")
    # ASKED OF THE FUNCTION, not of its docstring. The first version of this
    # checked that digest_of had a docstring and ended in `or True`, which is
    # a check that cannot fail — cover, not a test.
    with tempfile.TemporaryDirectory() as tmp:
        same_a = os.path.join(tmp, "P1030412.JPG")
        same_b = os.path.join(tmp, "holiday.jpg")
        for path in (same_a, same_b):
            with open(path, "wb") as fh:
                fh.write(frame)
        clash = os.path.join(tmp, "P1030412_other.JPG")
        with open(clash, "wb") as fh:
            fh.write(_frame(320, 240, colour=(1, 2, 3)))
        s.check("the same picture under two names is one photograph",
                watcher.digest_of(same_a) == watcher.digest_of(same_b),
                detail="renaming a file does not make it a new photograph")
        s.check("and two pictures are two, whatever they are called",
                watcher.digest_of(same_a) != watcher.digest_of(clash),
                detail="P1030412 comes round every ten thousand frames, and "
                       "formatting the card restarts the count — matching on "
                       "the name loses a real photograph and never says so")
        s.check("and the digest is of the bytes the server will hash too",
                watcher.digest_of(same_a) == hashlib.sha256(frame).hexdigest(),
                detail="two different answers to 'have I sent this' is the "
                       "watcher and the château disagreeing forever")
    s.check("and there is no way to turn certificate checking off",
            "_create_unverified_context" not in
            io.open(os.path.join(root, "tools", "photo_watcher.py"),
                    encoding="utf-8").read(),
            detail="the token in that header is the credential, and an "
                   "unverified connection hands it to whoever is in the middle")

    _cleanup()
    return s
