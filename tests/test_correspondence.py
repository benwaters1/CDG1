"""Everything the house has said to a guest, kept against their stay.

A guest rings and says "you told us we could arrive at ten" and there was no
way to check. Everything went out through send_email and vanished unless it
FAILED — in which case a copy sat in the outbox, so the only messages the house
could still read were the ones the guest never got.

Four things carry this file.

  A CREDENTIAL IS NEVER KEPT. send_email already has a flag for a body that is
  itself a key — keep=False, used by password resets and staff invitations, so
  they are not queued either. Nothing with that flag is filed here, and there
  is a check for it: a table of working reset links that the whole owner side
  can read is a worse outcome than never having built this.

  STAFF MAIL IS NOT CORRESPONDENCE. A letter to an address that belongs to no
  booking is a letter to the accountant, a supplier, or another member of
  staff. It is not filed.

  IT IS FILED WHETHER IT WENT OR NOT, and marked with which. "We wrote and it
  bounced" is a different fact from "we never wrote", and being able to say
  which is the point of keeping any of it.

  IT IS NOT KEPT FOR EVER. Two years past the stay, deleted by the same daily
  pass as everything else the privacy notice promises to delete — and the
  notice says so, because it is a set of testable claims about this code.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZCO"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM guest_messages WHERE booking_id IN
                    (SELECT id FROM bookings WHERE guest_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM guest_messages WHERE to_address LIKE ?", ("zzco.%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", ("zzco.%",))
    conn.execute("DELETE FROM sms_outbox WHERE phone LIKE ?", ("%99887766%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, room_id, arrival, nights=2, phone=""):
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 'unpaid', 500, 0, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"zzco.{ref}@example.invalid".lower(), phone, arrival.isoformat(),
         (arrival + timedelta(days=nights)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _filed(booking_id=None, address=None):
    conn = db()
    try:
        if booking_id:
            return conn.execute(
                "SELECT * FROM guest_messages WHERE booking_id = ? ORDER BY id",
                (booking_id,)).fetchall()
        return conn.execute(
            "SELECT * FROM guest_messages WHERE to_address = ? ORDER BY id",
            (address,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Correspondence kept with the stay")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    soon = m.house_today() + timedelta(days=30)

    # The REAL send_email, deliberately. Every other suite mocks it; this one
    # is about what send_email itself does, and mocking it would test nothing.
    # Mail transports are pinned off in _harness, so nothing leaves.
    b = _stay("A", room_id=room["id"], arrival=soon, phone="+33612998877 66")

    s.section("A letter to a guest is kept against their stay")
    with m.app.test_request_context("/"):
        m.send_email(b["guest_email"], "Your arrival", "Come any time after two.")
    filed = _filed(b["id"])
    s.check("it is filed", len(filed) == 1, detail=f"{len(filed)}")
    s.check("with what was actually said",
            filed and "after two" in filed[0]["body"],
            detail="a subject line alone does not settle an argument about "
                   "what somebody was told")
    s.check("and against the right stay",
            filed and filed[0]["booking_id"] == b["id"])
    s.check("marked as not gone, because it did not",
            filed and not filed[0]["delivered"],
            detail="no provider is configured in here, and 'we wrote and it "
                   "bounced' is a different fact from 'we never wrote'")

    s.section("A credential is never kept")
    # THE ONE THAT WOULD BE WORSE THAN NOT BUILDING THIS. keep=False already
    # means "this body is itself a key" -- it is why resets are not queued.
    with m.app.test_request_context("/"):
        m.send_email(b["guest_email"], "Reset your password",
                     "https://example.invalid/reset/SECRETTOKEN", keep=False)
    filed = _filed(b["id"])
    s.check("no second row appears", len(filed) == 1, detail=f"{len(filed)}")
    s.check("and the token is nowhere in the table",
            not [r for r in filed if "SECRETTOKEN" in (r["body"] or "")],
            detail="a table of working reset links the whole owner side can "
                   "read is worse than never having built this")

    s.section("Mail that is not to a guest is not correspondence")
    with m.app.test_request_context("/"):
        m.send_email("zzco.accountant@example.invalid", "Your invoice", "Attached.")
    s.check("nothing is filed for them",
            not _filed(address="zzco.accountant@example.invalid"),
            detail="a supplier, the accountant, another member of staff — this "
                   "is a guest record, not a copy of the whole mailbox")

    s.section("Which stay, when there is more than one")
    old = _stay("OLD", room_id=room["id"], arrival=m.house_today() - timedelta(days=200))
    conn = db()
    conn.execute("UPDATE bookings SET guest_email = ? WHERE id = ?",
                 (b["guest_email"], old["id"]))
    conn.commit()
    conn.close()
    with m.app.test_request_context("/"):
        m.send_email(b["guest_email"], "See you soon", "Two weeks to go.")
    s.check("the one they are coming for, not the one they left",
            len(_filed(b["id"])) == 2 and not _filed(old["id"]),
            detail="a message between stays is about the next one")

    s.section("A text is kept the same way")
    conn = db()
    with m.app.test_request_context("/"):
        m.send_sms(conn, "+33 6 12 99 88 77 66", "Your room is ready.")
    conn.commit()
    conn.close()
    texts = [r for r in _filed(b["id"]) if r["channel"] == "sms"]
    s.check("it is filed as a text", len(texts) == 1, detail=f"{len(texts)}")
    s.check("held rather than sent, and marked so",
            texts and not texts[0]["delivered"],
            detail="no texting provider in here either")

    s.section("The page")
    page = oc.get(f"/admin/bookings/{b['id']}/correspondence").get_data(as_text=True)
    s.check("it opens", b["reference_code"] in page)
    s.check("with the letter on it", "after two" in page)
    s.check("and the text", "Your room is ready" in page)
    s.check("it says one of them did not go", "did not go" in page,
            detail="the distinction the outbox already makes, kept here")
    s.check("and how long any of it is kept", "24 months" in page,
            detail="somebody reading a guest's letters should be able to see "
                   "the retention rule without opening the privacy notice")
    s.check("it is reachable from the booking",
            f"/admin/bookings/{b['id']}/correspondence"
            in oc.get(f"/admin/bookings/{b['id']}/edit").get_data(as_text=True),
            detail="a page nobody can get to is a page nobody uses")

    s.section("It does not outlive the stay by much")
    ancient = _stay("ANCIENT", room_id=room["id"],
                    arrival=m.house_today() - timedelta(days=1000))
    conn = db()
    conn.execute(
        """INSERT INTO guest_messages (booking_id, channel, to_address, subject,
           body, delivered, created_at) VALUES (?, 'email', ?, 'Old', 'Old', 1, ?)""",
        (ancient["id"], ancient["guest_email"],
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    with m.app.test_request_context("/"):
        cleared = m.purge_guest_messages(conn)
    conn.close()
    s.check("the old one goes", not _filed(ancient["id"]), detail=f"{cleared}")
    s.check("the recent one stays", len(_filed(b["id"])) == 3)
    s.check("and it says what it cleared",
            cleared.get("old guest correspondence", 0) >= 1, detail=f"{cleared}")

    s.section("The daily pass runs it")
    conn = db()
    src = open(m.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    conn.close()
    s.check("the retention job calls the purge",
            "purge_guest_messages(conn)" in src.split("def run_health_notes_purge_job")[1][:1200],
            detail="a purge nothing runs is a promise nothing keeps")

    s.section("And the privacy notice says so")
    notice = oc.get("/privacy").get_data(as_text=True)
    # Each claim on its own, matched on the words that carry it. An earlier
    # version of this accepted either of two phrases, so renaming the whole
    # clause still passed on the leftover sentence underneath.
    s.check("it says we keep what we have written to them",
            "What we have written to you" in notice,
            detail="the notice is a set of testable claims about this code, "
                   "not marketing copy")
    s.check("and for how long", "two years after the stay" in notice,
            detail=f"the code keeps it {m.GUEST_MESSAGE_KEEP_MONTHS} months")
    s.check("and that a credential is never among it",
            "never kept at all" in notice,
            detail="the code honours keep=False; the notice has to say so, or "
                   "it overstates what the software does in the other "
                   "direction and understates the protection")

    s.section("Guards")
    s.check("an employee cannot read a guest's letters",
            ec.get(f"/admin/bookings/{b['id']}/correspondence").status_code in (302, 403))
    s.check("an unknown booking is a 404",
            oc.get("/admin/bookings/999999/correspondence").status_code == 404)

    _cleanup()
    return s
