"""Who receives a promo blast.

The segments are built from confirmed bookings. Only the workshop segment ever
looked at do_not_email; a room or restaurant guest had no equivalent, and
email_optouts — which is where "unsubscribe" in a campaign email actually
writes — was not consulted by this path at all. A guest could unsubscribe and
still be sent the next promo code.

The checks that matter are therefore about who is left OUT, and that the count
the owner is shown before sending is the same set that actually gets mailed. A
preview that over-counts is its own bug: it is the number somebody decides to
press send on.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zzblast"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG.upper() + "%",))
    conn.commit()
    conn.close()


def _guest(email, name, room_id, offset):
    """A confirmed past room stay, which is what the room segment selects."""
    arrival = datetime.now(timezone.utc).date() - timedelta(days=offset)
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', ?)""",
        (room_id, f"{TAG[:4].upper()}{offset}", _harness.secrets_token(), name, email,
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _recipients():
    conn = db()
    try:
        return m.promo_blast_recipients(conn, ["room"], None)
    finally:
        conn.close()


def run():
    s = Suite("Promo blast")
    _cleanup()
    oc, ec, owner, emp = clients()

    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    conn.close()
    if not room:
        s.check("a room exists to attach a past stay to", False)
        return s

    happy = f"{TAG}.happy@example.invalid"
    gone = f"{TAG}.unsubscribed@example.invalid"
    _guest(happy, f"{TAG} Happy", room["id"], 40)
    _guest(gone, f"{TAG} Gone", room["id"], 50)

    s.section("Both past guests are in the audience to begin with")
    everyone = _recipients()
    s.check("the guest who never unsubscribed is included", happy in everyone)
    s.check("and so is the other one, for now", gone in everyone)

    s.section("Unsubscribing takes a guest out of the blast")
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
        (gone, "test", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    after = _recipients()
    s.check("the unsubscribed guest is gone — this is the whole point",
            gone not in after, detail="an opted-out guest is still in the audience")
    s.check("and the other one is untouched", happy in after)

    s.section("An address opted out in a different case still matches")
    mixed = f"{TAG}.MiXeD@Example.Invalid"
    _guest(mixed.lower(), f"{TAG} Mixed", room["id"], 60)
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
        (mixed, "test", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    s.check("case does not let an opt-out slip through",
            mixed.lower() not in _recipients(),
            detail="a differently-cased opt-out was ignored")

    s.section("The count the owner is shown matches what would be sent")
    conn = db()
    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, discount_value,
           applies_to, active, redemption_count, created_at)
           VALUES (?, 'test', 'percent', 10, 'all', 1, 0, ?)""",
        (TAG.upper() + "1", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    promo = conn.execute("SELECT id FROM promo_codes WHERE code = ?",
                         (TAG.upper() + "1",)).fetchone()
    conn.close()
    page = oc.get(f"/admin/promo-codes/{promo['id']}/blast?segment=room")
    html = page.get_data(as_text=True)
    s.check("the blast page loads", page.status_code == 200, page)
    # The preview renders promo_blast_recipients too, so the opted-out guests
    # must not be countable there either.
    s.check("the preview does not name an opted-out guest", gone not in html)
    s.check("nor the mixed-case one", mixed.lower() not in html)

    s.section("Guards")
    s.check("an employee cannot open the blast page",
            ec.get(f"/admin/promo-codes/{promo['id']}/blast").status_code in (302, 403))
    s.check("an unknown code is a 404",
            oc.get("/admin/promo-codes/999999/blast").status_code == 404)

    _cleanup()
    return s
