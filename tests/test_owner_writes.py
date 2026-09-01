"""Three owner actions that write something, none of them covered until now.

A PHONE ENQUIRY. Most venue enquiries arrive by telephone, and until this route
existed the only INSERT into event_inquiries was the guest's own form — so the
owner had to fill that in pretending to be the caller. Same shape as the
walk-in booking, and checked for the same things.

A PUSH SUBSCRIPTION IS A CREDENTIAL. Turning notifications on hands the app an
endpoint at a browser vendor's push service and a key to sign for it, and both
go in the database. Nothing tested that they arrive whole, that a half-formed
one is refused, or that turning them off actually removes the row — a
subscription that survives an unsubscribe keeps somebody's phone buzzing after
they asked it to stop.

PREPPING AN ARRIVAL TWICE. It builds a checklist of tasks for a room, and the
guard is `arrival_prepped_at IS NULL` inside the UPDATE with the rowcount read
afterwards — so a second press finds nothing to update and stops. That is the
right shape and it is worth a test, because the failure is a housekeeper
opening their list to find every job on it twice with no way to tell which is
which.

SAFETY. The harness blocks the push send itself, so nothing here can reach a
real device even if a subscription row exists. The last check confirms the
block is still in place — a suite that stored a subscription and left the
sender live would push to somebody's phone on the NEXT run, not this one.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZOWRITE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint LIKE 'https://zzpush%'")
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = 'checklist' AND due_date = ?",
                 ((house_today() + timedelta(days=300)).isoformat(),))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _subs(endpoint=None):
    conn = db()
    try:
        if endpoint:
            return conn.execute(
                "SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchall()
        return conn.execute(
            "SELECT * FROM push_subscriptions WHERE endpoint LIKE 'https://zzpush%'").fetchall()
    finally:
        conn.close()


def _enquiries():
    conn = db()
    try:
        return conn.execute("SELECT * FROM event_inquiries WHERE contact_name LIKE ? "
                            "ORDER BY id DESC", (TAG + "%",)).fetchall()
    finally:
        conn.close()


def _stay(ref, offset=3):
    conn = db()
    room = _harness.ensure_room()
    arrival = house_today() + timedelta(days=offset)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'zzowrite@example.invalid', '', ?, ?, 2, 'confirmed',
           400, 0, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


PREP_DUE = (house_today() + timedelta(days=300)).isoformat()


def _prep_tasks():
    """The checklist this suite's booking produced.

    Matched on the due date, which is the fixture's arrival and far enough out
    that no other suite shares it. The first attempt filtered on
    origin='arrival_prep' and room_note=<room>, and the route writes
    origin='checklist' with the room in the TITLE — so it found nothing, and
    "a second press makes none" passed against zero having tested nothing.
    """
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM tasks WHERE origin = 'checklist' AND due_date = ?",
            (PREP_DUE,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Owner writes")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("An enquiry taken over the telephone")
    conn = db()
    types = m.known_event_types(conn)
    conn.close()
    s.check("the house has event types to choose from", bool(types),
            detail="nothing below can be filed without one")
    kind = types[0] if types else "wedding"

    oc.post("/admin/events/new",
            data={"event_type": kind, "contact_name": f"{TAG} Caller",
                  "contact_phone": "+33 5 61 00 00 00",
                  "preferred_date": (house_today() + timedelta(days=200)).isoformat(),
                  "guest_count": "80", "message": "ZZ rang about a wedding",
                  "quoted_price": "4500"},
            follow_redirects=True)
    made = _enquiries()
    s.check("it is written down", bool(made), detail=f"{len(made)}")
    if made:
        e = made[0]
        s.check("with no email, because a caller may not give one",
                not (e["contact_email"] or "").strip())
        s.check("the telephone number is kept",
                "61 00 00 00" in (e["contact_phone"] or ""), detail=f"{e['contact_phone']}")
        s.check("and the price quoted on the call",
                abs(float(e["quoted_price"] or 0) - 4500) < 0.01,
                detail=f"{e['quoted_price']}")

    s.section("What it refuses")
    before = len(_enquiries())
    r = oc.post("/admin/events/new",
                data={"event_type": "not-a-real-type", "contact_name": f"{TAG} Nope"},
                follow_redirects=True)
    s.check("an event type nobody offers is refused", len(_enquiries()) == before,
            detail=f"{flashes(r)[:1]}")
    r = oc.post("/admin/events/new", data={"event_type": kind, "contact_name": "  "},
                follow_redirects=True)
    s.check("and so is a nameless one", len(_enquiries()) == before,
            detail=f"{flashes(r)[:1]}")
    r = oc.post("/admin/events/new",
                data={"event_type": kind, "contact_name": f"{TAG} Bademail",
                      "contact_email": "not-an-address"}, follow_redirects=True)
    s.check("an email that is not one is refused rather than stored",
            len(_enquiries()) == before,
            detail=f"{flashes(r)[:1]} — the guest emails key off that field")

    s.section("Turning notifications on stores the whole subscription")
    endpoint = "https://zzpush.example.invalid/endpoint/abc123"
    r = oc.post("/notifications/subscribe",
                json={"endpoint": endpoint,
                      "keys": {"p256dh": "zzp256dh-key", "auth": "zzauth-key"}})
    s.check("it is accepted", r.status_code == 200, detail=f"HTTP {r.status_code}")
    rows = _subs(endpoint)
    s.check("and one row is stored", len(rows) == 1, detail=f"{len(rows)}")
    if rows:
        s.check("against the person who asked for it",
                rows[0]["user_id"] == owner["id"], detail=f"{rows[0]['user_id']}")
        s.check("with BOTH keys, not just the endpoint",
                rows[0]["p256dh"] == "zzp256dh-key" and rows[0]["auth"] == "zzauth-key",
                detail=f"p256dh={rows[0]['p256dh']!r} auth={rows[0]['auth']!r} — "
                       "a push cannot be signed without both, and it would fail "
                       "silently at send time rather than here")

    s.section("Subscribing twice from the same browser is still one row")
    # The endpoint IS the browser install. Two rows would push twice.
    oc.post("/notifications/subscribe",
            json={"endpoint": endpoint,
                  "keys": {"p256dh": "zzp256dh-rotated", "auth": "zzauth-rotated"}})
    rows = _subs(endpoint)
    s.check("still one row", len(rows) == 1, detail=f"{len(rows)} — the phone "
                                                    "would buzz twice for one thing")
    s.check("and the rotated keys replaced the old ones",
            bool(rows) and rows[0]["p256dh"] == "zzp256dh-rotated",
            detail=f"{rows[0]['p256dh'] if rows else None} — a rotated key stored "
                   "as a second row means half the pushes fail")

    s.section("A half-formed subscription is refused, not stored")
    for label, payload in (
        ("no endpoint", {"keys": {"p256dh": "a", "auth": "b"}}),
        ("no keys", {"endpoint": "https://zzpush.example.invalid/none"}),
        ("only one key", {"endpoint": "https://zzpush.example.invalid/half",
                          "keys": {"p256dh": "a"}}),
        ("nothing at all", {}),
    ):
        r = oc.post("/notifications/subscribe", json=payload)
        s.check(f"{label}: refused", r.status_code == 400, detail=f"HTTP {r.status_code}")
    s.check("and nothing half-formed was stored", len(_subs()) == 1,
            detail=f"{[dict(x)['endpoint'] for x in _subs()]}")

    s.section("Turning them off really removes it")
    r = oc.post("/notifications/unsubscribe", json={"endpoint": endpoint})
    s.check("it is accepted", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and the row is gone", not _subs(endpoint),
            detail="somebody's phone keeps buzzing after they asked it to stop")

    s.section("Prepping an arrival, and prepping it again")
    stay = _stay("A", offset=300)
    before_tasks = len(_prep_tasks())
    s.check("nothing is on that checklist to begin with", before_tasks == 0,
            detail=f"{before_tasks} — the counts below would be meaningless")
    oc.post(f"/admin/bookings/{stay['id']}/prepare-arrival",
            data={"assigned_to_user_id": str(emp["id"])}, follow_redirects=True)
    after_one = len(_prep_tasks())
    s.check("a checklist of tasks is created", after_one > before_tasks,
            detail=f"{before_tasks} -> {after_one}")
    s.check("assigned to the person chosen",
            after_one > 0 and all(t["assigned_to_user_id"] == emp["id"]
                                  for t in _prep_tasks()),
            detail="the tasks went to nobody, or to the wrong person")
    s.check("and every one carries the room in its title",
            after_one > 0 and all(":" in (t["title"] or "") for t in _prep_tasks()),
            detail="a housekeeper cannot tell which room a job is for")

    r = oc.post(f"/admin/bookings/{stay['id']}/prepare-arrival",
                data={"assigned_to_user_id": str(emp["id"])}, follow_redirects=True)
    s.check("a second press makes none", len(_prep_tasks()) == after_one,
            detail=f"{len(_prep_tasks())} vs {after_one} — a housekeeper "
                   "opens their list to every job on it twice")
    s.check("and says it was already done",
            any("already prepped" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("Prepping refuses what it cannot do")
    r = oc.post(f"/admin/bookings/{stay['id']}/prepare-arrival", data={},
                follow_redirects=True)
    s.check("nobody to assign to is refused",
            any("who" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")
    s.check("a booking that does not exist is a 404",
            oc.post("/admin/bookings/999999/prepare-arrival",
                    data={"assigned_to_user_id": str(emp["id"])}).status_code == 404)

    s.section("Guards")
    n_before = len(_enquiries())
    s.check("an employee cannot file an enquiry",
            ec.post("/admin/events/new",
                    data={"event_type": kind, "contact_name": f"{TAG} Sneaky"}
                    ).status_code in (302, 403))
    s.check("and none was filed", len(_enquiries()) == n_before)
    s.check("nor prep an arrival",
            ec.post(f"/admin/bookings/{stay['id']}/prepare-arrival",
                    data={"assigned_to_user_id": str(emp["id"])}
                    ).status_code in (302, 403))
    anon = m.app.test_client()
    s.check("a stranger cannot subscribe a device",
            anon.post("/notifications/subscribe",
                      json={"endpoint": "https://zzpush.example.invalid/anon",
                            "keys": {"p256dh": "a", "auth": "b"}}
                      ).status_code in (302, 401, 403))
    s.check("and nothing was stored for them",
            not _subs("https://zzpush.example.invalid/anon"))

    s.section("And a push still cannot reach a real device from in here")
    # A suite that stored a subscription and left the sender live would push to
    # somebody's phone on the NEXT run, not this one.
    raised = False
    try:
        m.webpush(subscription_info={}, data="{}", vapid_private_key="x",
                  vapid_claims={})
    except AssertionError:
        raised = True
    s.check("the push send refuses", raised,
            detail="the harness block is off and a run would notify real phones")

    _cleanup()
    return s
