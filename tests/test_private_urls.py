"""One person's private thing, reachable by a URL.

Two sets of routes that look nothing alike and have the same job. Staff
documents are reached by id and guarded by who you are signed in as; guest
statements and feedback forms are reached by an unguessable token and have no
account behind them at all. Both hand a specific person's paperwork to whoever
opens the address.

The staff side is the one with teeth. `documents` holds contracts, right-to-work
papers and sick notes, and the routes are @login_required rather than
@owner_required — deliberately, because a member of staff should be able to
fetch their own. What makes that safe is one line in each of five routes:

    if user["role"] != "owner" and user["id"] != doc["user_id"]:
        abort(403)

Five copies of a rule is five chances to leave one out, and the missing one
would not show up on any page: the owner sees everything either way, and an
employee would have to go looking to notice. Every one is checked here, in both
directions — that a stranger is refused AND that the person it belongs to is
not, because a guard that refuses everybody passes the first half on its own.

The guest side is checked for the two things a token page gets wrong: a wrong
token must be a 404 rather than an empty page that renders, and the page must
carry noindex. That second one is the trap CLAUDE.md is about — 25 templates
override a block that public_base defines, and if the base loses it every
override becomes dead markup while every page still renders perfectly.
"""
import os
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTPRIV"


def _cleanup():
    conn = db()
    for r in conn.execute(
            """SELECT filename FROM documents WHERE title LIKE ?""", (TAG + "%",)).fetchall():
        p = os.path.join(m.UPLOAD_DIR, r["filename"] or "")
        if r["filename"] and os.path.exists(p):
            os.remove(p)
    conn.execute("DELETE FROM documents WHERE title LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM guest_feedback WHERE booking_id IN
                    (SELECT id FROM bookings WHERE reference_code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _employee(name):
    conn = db()
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
           VALUES (?, ?, 'employee', ?, 'General', 'active', ?)""",
        (f"{TAG.lower()}{name}@example.invalid", "x-not-a-usable-hash",
         TAG + " " + name, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE email = ?",
                       (f"{TAG.lower()}{name}@example.invalid",)).fetchone()
    conn.close()
    return row


def _signed_in(user):
    """A client acting as one person, the way the harness does it."""
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user["id"]
    return c


def _document(user_id, title, ext="pdf"):
    conn = db()
    stored = f"{user_id}_{TAG.lower()}_{title.replace(' ', '')}.{ext}"
    with open(os.path.join(m.UPLOAD_DIR, stored), "wb") as fh:
        fh.write(b"%PDF-1.4 private")
    conn.execute(
        """INSERT INTO documents (user_id, title, filename, uploaded_at)
           VALUES (?, ?, ?, ?)""",
        (user_id, TAG + " " + title, stored, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM documents WHERE title = ?",
                       (TAG + " " + title,)).fetchone()
    conn.close()
    return row


def _booking(ref, departure_offset=-3):
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    dep = date.today() + timedelta(days=departure_offset)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           payment_status, total_price, created_at)
           VALUES (?, ?, ?, 'Private Guest', 'private@example.invalid', ?, ?, 2,
           'confirmed', 'paid', 600, ?)""",
        (room, TAG + ref, TAG + "tok" + ref, (dep - timedelta(days=2)).isoformat(),
         dep.isoformat(), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Private URLs")
    _cleanup()
    oc, _ec, owner, _emp = clients()
    anon = m.app.test_client()

    alice = _employee("Alice")
    bob = _employee("Bob")
    ac, bc = _signed_in(alice), _signed_in(bob)

    s.section("Signing the two of them in actually worked")
    # Everything below is about one of them being refused. If neither is really
    # signed in, every refusal check passes on the login redirect instead.
    # Checked against a page that REQUIRES a login. "/" is public and answers
    # 200 to a stranger, so asking it proves nothing — and a first version of
    # this precondition did exactly that while neither of them was signed in,
    # which turned every refusal below into a login redirect that passed.
    s.check("a stranger is turned away from the staff manual",
            anon.get("/manual").status_code in (302, 401, 403),
            detail=str(anon.get("/manual").status_code))
    s.check("Alice is really signed in", ac.get("/manual").status_code == 200,
            detail=str(ac.get("/manual").status_code))
    s.check("and Bob is too", bc.get("/manual").status_code == 200,
            detail=str(bc.get("/manual").status_code))

    s.section("A staff document belongs to one person")
    doc = _document(alice["id"], "Contract")
    s.check("its owner can download it",
            ac.get(f"/documents/{doc['id']}/download").status_code == 200,
            detail="a guard that refuses everybody would pass the next check "
                   "on its own")
    s.check("and view it", ac.get(f"/documents/{doc['id']}/view").status_code == 200)
    s.check("a colleague cannot download it",
            bc.get(f"/documents/{doc['id']}/download").status_code == 403,
            detail=str(bc.get(f"/documents/{doc['id']}/download").status_code))
    s.check("nor view it",
            bc.get(f"/documents/{doc['id']}/view").status_code == 403)
    s.check("and gets none of the file with the refusal",
            b"private" not in bc.get(f"/documents/{doc['id']}/download").get_data())
    s.check("somebody signed in as nobody cannot either",
            anon.get(f"/documents/{doc['id']}/download").status_code in (302, 401, 403))
    s.check("the owner of the house can", oc.get(f"/documents/{doc['id']}/download").status_code == 200)

    s.section("A colleague cannot change or delete it either")
    r = bc.post(f"/documents/{doc['id']}/edit",
                data={"title": TAG + " Hijacked", "expiry_date": ""})
    conn = db()
    still = conn.execute("SELECT title FROM documents WHERE id = ?",
                         (doc["id"],)).fetchone()["title"]
    conn.close()
    s.check("editing it is refused", r.status_code == 403, detail=str(r.status_code))
    s.check("and the title is unchanged", still == TAG + " Contract", detail=still)
    bc.post(f"/documents/{doc['id']}/delete")
    conn = db()
    alive = conn.execute("SELECT COUNT(*) c FROM documents WHERE id = ?",
                         (doc["id"],)).fetchone()["c"]
    conn.close()
    s.check("deleting it is refused", alive == 1,
            detail="a colleague deleted somebody's contract")
    s.check("and the file is still on disk",
            os.path.exists(os.path.join(m.UPLOAD_DIR, doc["filename"])))

    s.section("Uploading is to your own profile only")
    r = bc.post(f"/directory/{alice['id']}/upload", data={
        "title": TAG + " Planted", "document": (BytesIO(b"%PDF-1.4"), "planted.pdf"),
    }, content_type="multipart/form-data")
    conn = db()
    planted = conn.execute("SELECT COUNT(*) c FROM documents WHERE title = ?",
                           (TAG + " Planted",)).fetchone()["c"]
    conn.close()
    s.check("a colleague cannot attach anything to your file", planted == 0,
            detail=str(planted))
    s.check("and is told no rather than quietly ignored", r.status_code == 403,
            detail=str(r.status_code))
    bc.post(f"/directory/{bob['id']}/upload", data={
        "title": TAG + " Own", "document": (BytesIO(b"%PDF-1.4"), "own.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)
    conn = db()
    own = conn.execute("SELECT * FROM documents WHERE title = ?",
                       (TAG + " Own",)).fetchone()
    conn.close()
    s.check("while your own goes through", own is not None)
    s.check("filed against you, not whoever was in the URL",
            own and own["user_id"] == bob["id"], detail=str(own["user_id"]) if own else "")

    s.section("Only what a browser can render is served inline")
    txt = _document(alice["id"], "Notes", ext="txt")
    s.check("a .txt is not viewable inline",
            ac.get(f"/documents/{txt['id']}/view").status_code == 404,
            detail=str(ac.get(f"/documents/{txt['id']}/view").status_code))
    s.check("but its owner can still download it",
            ac.get(f"/documents/{txt['id']}/download").status_code == 200)
    s.check("and a colleague still cannot",
            bc.get(f"/documents/{txt['id']}/download").status_code == 403)

    s.section("A guest's bill, opened with the booking's own token")
    bk = _booking("STAY")
    r = anon.get(f"/booking/{bk['manage_token']}/statement")
    page = r.get_data(as_text=True)
    s.check("the token opens it without an account", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("and it is their booking on it", bk["reference_code"] in page)
    s.check("a token that is not a token is a 404",
            anon.get("/booking/not-a-real-token/statement").status_code == 404)
    s.check("and an empty one too",
            anon.get("/booking//statement").status_code in (404, 308))
    # The trap CLAUDE.md is about: this page shows one person's bill and must
    # not be indexable. Checked on what is served, not on the template source.
    s.check("the bill tells search engines to stay away", "noindex" in page,
            detail="a bill that leaks into a referrer can be indexed without "
                   "ever being crawled")

    s.section("Feedback, once, and not before they have left")
    early = _booking("FUTURE", departure_offset=+10)
    r = anon.get(f"/feedback/{early['manage_token']}")
    s.check("the form opens for a stay still to come", r.status_code == 200)
    anon.post(f"/feedback/{early['manage_token']}",
              data={"rating": "5", "comment": TAG + " too early"}, follow_redirects=True)
    conn = db()
    got = conn.execute("SELECT COUNT(*) c FROM guest_feedback WHERE booking_id = ?",
                       (early["id"],)).fetchone()["c"]
    conn.close()
    s.check("but nothing is recorded before they have been", got == 0, detail=str(got))

    anon.post(f"/feedback/{bk['manage_token']}",
              data={"rating": "4", "comment": TAG + " lovely"}, follow_redirects=True)
    conn = db()
    rows = conn.execute("SELECT * FROM guest_feedback WHERE booking_id = ?",
                        (bk["id"],)).fetchall()
    conn.close()
    s.check("a departed guest can leave it", len(rows) == 1, detail=str(len(rows)))
    s.check("with the rating they gave", rows and rows[0]["rating"] == 4,
            detail=str(rows[0]["rating"]) if rows else "")

    second = anon.post(f"/feedback/{bk['manage_token']}",
                       data={"rating": "1", "comment": TAG + " second thoughts"},
                       follow_redirects=True)
    conn = db()
    rows = conn.execute("SELECT * FROM guest_feedback WHERE booking_id = ?",
                        (bk["id"],)).fetchall()
    conn.close()
    s.check("and cannot leave a second one", len(rows) == 1,
            detail=f"{len(rows)} — one token, one review, or the same guest "
                   "can rate the house as often as they like")
    s.check("the first one stands", rows and rows[0]["rating"] == 4,
            detail=str(rows[0]["rating"]) if rows else "")
    # There are two guards here: the route checks for an existing review, and a
    # unique index on booking_id enforces it underneath. Removing the route's
    # check leaves the row count correct, which is why that control passes —
    # the invariant is what matters and it still holds. What changes is who
    # says no: the app declines politely, the index raises. Only one of those
    # is something to show a guest.
    s.check("and the second attempt is declined rather than thrown",
            second.status_code == 200, detail=f"HTTP {second.status_code}")
    s.check("a feedback token that is not a token is a 404",
            anon.get("/feedback/not-a-real-token").status_code == 404)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
