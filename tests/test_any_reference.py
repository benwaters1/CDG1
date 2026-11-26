"""Any reference, on any find page.

A guest with a confirmed place on an atelier, Belinda Down, wrote on 26
November 2026 that her reference "didn't exist" when she looked it up. It did:
WRK-TFM3I5. Every "Manage your booking" link on the site opened /book/manage,
which looked among room bookings only, so an atelier's WRK- reference -- and a
dinner's DIN- and an event's EVT- -- was "No booking found". The atelier's own
find page, which nothing on the site linked to, matched the email exactly, so
an address typed with a capital failed there too.

What is checked: each of the four find pages takes each of the four kinds,
the prefix decides which booking it is, the address matches however it is
capitalised, a reference typed in lower case or without its dash still
works, the surname of anybody named on an atelier place opens it, and nothing
opens on the reference alone, a wrong address, or one letter of a name. The
page a guest lands on says the four prefixes, from REFERENCE_PREFIXES.
"""
from datetime import datetime, timedelta, timezone
from itertools import count
from urllib.parse import urlsplit

from _harness import Suite, db, house_today
import _harness

m = _harness.m
TAG = "ZZREF"
_ips = count(1)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (f"%{TAG}%",))
    sids = [r["id"] for r in conn.execute(
        "SELECT workshop_sessions.id FROM workshop_sessions JOIN workshops "
        "ON workshops.id = workshop_sessions.workshop_id WHERE workshops.title LIKE ?",
        (TAG + "%",))]
    for sid in sids:
        conn.execute("DELETE FROM workshop_booking_guests WHERE workshop_booking_id IN "
                     "(SELECT id FROM workshop_bookings WHERE session_id = ?)", (sid,))
        conn.execute("DELETE FROM workshop_bookings WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM workshop_sessions WHERE id = ?", (sid,))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE ip_address LIKE '10.77.%'")
    conn.commit()


def _ref(kind, tail):
    return m.REFERENCE_PREFIXES[kind] + tail


def _post(page, reference, email="", surname="", ip=None):
    """One attempt from its own address, so the hourly limit is not what answers."""
    client = m.app.test_client()
    addr = ip or f"10.77.{next(_ips) // 250}.{next(_ips) % 250 + 1}"
    return client.post(page, data={"reference_code": reference, "email": email,
                                   "surname": surname},
                       environ_base={"REMOTE_ADDR": addr})


def _lands(r):
    return urlsplit(r.headers.get("Location", "")).path if r.status_code in (302, 303) else ""


def run():
    s = Suite("Any reference, on any find page")
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn):
    now, day = _now(), house_today() + timedelta(days=200)
    room = _harness.ensure_room()
    tokens = {k: f"tok{TAG}{k}{_harness.secrets_token()}".lower()
              for k in ("room", "workshop", "restaurant", "event")}
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               booked_by_name, booked_by_email, arrival_date, departure_date, party_size, status,
               total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 500, 0, 0, ?)""",
        (room["id"], _ref("room", "R00M01"), tokens["room"], f"Clara Rossi {TAG}",
         "clara.zzref@example.invalid", f"Marco Rossi {TAG}", "marco.zzref@example.invalid",
         day.isoformat(), (day + timedelta(days=2)).isoformat(), now))
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity, active,
               sort_order, created_at) VALUES (?, '', 3800, 10, 1, 98, ?)""", (f"{TAG} Cooking", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Cooking",)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
                        created_at) VALUES (?, ?, ?, 10, ?, ?)""",
                 (wid, day.isoformat(), (day + timedelta(days=5)).isoformat(), TAG, now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
               guest_email, party_size, status, created_at)
           VALUES (?, ?, ?, ?, ?, 2, 'confirmed', ?)""",
        # Stored as she typed it when she booked, capitals and all: matched
        # exactly, the address she types now in lower case would not open it.
        (sid, _ref("workshop", "TFM3I5"), tokens["workshop"], f"Belinda Down {TAG}",
         "Bindi.ZZREF@example.invalid", now))
    wbid = conn.execute("SELECT id FROM workshop_bookings WHERE manage_token = ?",
                        (tokens["workshop"],)).fetchone()["id"]
    conn.execute("INSERT INTO workshop_booking_guests (workshop_booking_id, guest_name, is_lead, "
                 "created_at) VALUES (?, ?, 0, ?)", (wbid, f"Jenny Lynch {TAG}", now))
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
               party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?)""",
        (_ref("restaurant", "D1NN3R"), tokens["restaurant"], f"Anne Morel {TAG}",
         "anne.zzref@example.invalid", day.isoformat(), now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, status, created_at)
           VALUES (?, ?, 'wedding', ?, ?, 'new', ?)""",
        (_ref("event", "W3DD1N"), tokens["event"], f"Lucie Garnier {TAG}",
         "lucie.zzref@example.invalid", now))
    conn.commit()
    pages = {"room": f"/book/manage/{tokens['room']}",
             "workshop": f"/workshops/manage/{tokens['workshop']}",
             "restaurant": f"/restaurant/manage/{tokens['restaurant']}",
             "event": f"/events/manage/{tokens['event']}"}
    refs = {"room": _ref("room", "R00M01"), "workshop": _ref("workshop", "TFM3I5"),
            "restaurant": _ref("restaurant", "D1NN3R"), "event": _ref("event", "W3DD1N")}
    emails = {"room": "clara.zzref@example.invalid", "workshop": "bindi.zzref@example.invalid",
              "restaurant": "anne.zzref@example.invalid", "event": "lucie.zzref@example.invalid"}

    s.section("Belinda's case: an atelier's reference on the page the site links to")
    r = _post("/book/manage", refs["workshop"], email=emails["workshop"])
    s.check("Manage your booking opens her atelier place", _lands(r) == pages["workshop"],
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = _post("/book/manage", refs["workshop"], email="Bindi.ZZREF@Example.Invalid")
    s.check("with the address in whatever capitals she types", _lands(r) == pages["workshop"])
    r = _post("/book/manage", "wrk tfm3i5", email=emails["workshop"])
    s.check("and the reference typed in lower case, with a space for the dash",
            _lands(r) == pages["workshop"])
    r = _post("/book/manage", "WRKTFM3I5", email=emails["workshop"])
    s.check("or with no dash at all", _lands(r) == pages["workshop"])
    r = _post("/book/manage", refs["workshop"], surname="LYNCH")
    s.check("the friend named on the place opens it with her surname", _lands(r) == pages["workshop"])
    page = m.app.test_client().get("/book/manage").get_data(as_text=True)
    s.check("the page says every reference is found there, by its prefix",
            all(p in page for p in m.REFERENCE_PREFIXES.values()), detail=page[:0])

    s.section("Every kind, from every find page")
    for find in ("/book/manage", "/workshops/find", "/restaurant/find", "/events/find"):
        for kind in refs:
            r = _post(find, refs[kind], email=emails[kind].upper())
            s.check(f"{find} opens a {kind} booking", _lands(r) == pages[kind],
                    detail=f"{r.status_code} {r.headers.get('Location')}")
    r = _post("/book/manage", refs["room"], email="marco.zzref@example.invalid")
    s.check("a stay's payer opens it with their own address", _lands(r) == pages["room"])
    r = _post("/book/manage", refs["room"], surname="rossi")
    s.check("and the surname still opens a stay, as it did", _lands(r) == pages["room"])

    s.section("Nothing opens without the second detail")
    for label, kw in (("on the reference alone", {}),
                      ("with somebody else's address", {"email": emails["room"]}),
                      ("with one letter of the name", {"surname": "d"}),
                      ("with a name that is not on it", {"surname": "Smith"})):
        r = _post("/book/manage", refs["workshop"], **kw)
        s.check(f"not {label}", _lands(r) == "" and r.status_code == 200,
                detail=f"{r.status_code} {r.headers.get('Location')}")
    r = _post("/book/manage", refs["workshop"], email="someone@example.invalid")
    s.check("and the refusal says nothing about whether the reference exists",
            "No booking found with that reference and those details" in r.get_data(as_text=True))
    r = _post("/book/manage", "CDG-ABC123", email=emails["room"])
    s.check("a reference that is none of ours is told what ours look like",
            all(p in r.get_data(as_text=True) for p in m.REFERENCE_PREFIXES.values())
            and "does not look like one of ours" in r.get_data(as_text=True))

    s.section("Still limited per hour")
    ip = "10.77.250.250"
    answers = [_post("/workshops/find", refs["workshop"], email="wrong@example.invalid", ip=ip)
               for _ in range(m.BOOKING_RATE_LIMIT_PER_HOUR)]
    r = _post("/workshops/find", refs["workshop"], email=emails["workshop"], ip=ip)
    s.check("past the limit, even the right details wait", _lands(r) == ""
            and "Too many attempts" in r.get_data(as_text=True) and all(a.status_code == 200
                                                                         for a in answers))


if __name__ == "__main__":
    print(run().report())
