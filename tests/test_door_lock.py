"""The front door: a guest opens it from their phone, for their stay and no longer.

The lock (a Nivian NVS-SMARTBOLT) has no keypad, so there is no code to give
anybody. The site asks Tuya -- the cloud behind the Smart Life app -- to open it,
from a page that belongs to one stay. So the things held here are the things
that decide whether a stranger can get in, and whether a guest is left outside:

  THE WINDOW IS THE STAY. Before it the page says when; after it, or for a
  cancelled stay, a checked-out one, or one the owner has switched off, it
  says no -- and in none of those does anything reach the lock.

  A VISIT DOES NOT OPEN IT. Only a POST does, so a link previewed by a
  messaging app or fetched by a crawler cannot open the house.

  EVERY OPENING IS RECORDED, and the owner hears the first time a guest lets
  themselves in and every time the door does not open, because that is
  somebody standing outside.

  THE LOCK'S HEALTH IS SAID BEFORE IT MATTERS, on the owner's home and as a
  task that closes itself, and the panel is quiet when the lock is well.

  THE SIGNATURE IS TUYA'S, checked against the worked examples in Tuya's own
  documentation rather than against a second copy of the same code.

Nothing reaches Tuya: tuya_request is stood down by the harness, and stood in
for here by a fake that answers like Tuya and records what it was asked.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "ZZDOOR"
DEVICE = "bfzzdoor0123456789ab"


def _now():
    return datetime.now(timezone.utc).isoformat()


class _Tuya:
    """Tuya, as far as the site can tell: answers by path, and remembers.

    `fail` is {path fragment: TuyaError message} for calls that should be
    refused; `battery`, `online` and `remote_on` shape what the lock reports.
    """

    def __init__(self, *, fail=None, battery=80, online=True, remote_on=True, token_fails=None):
        self.fail = dict(fail or {})
        self.battery, self.online, self.remote_on = battery, online, remote_on
        self.token_fails = token_fails
        self.calls = []

    def __call__(self, method, path, *, params=None, body=None, access_token=""):
        self.calls.append((method, path, body, access_token))
        if path == "/v1.0/token":
            if self.token_fails:
                raise m.TuyaError(self.token_fails, code=28841004)
            return {"access_token": f"tok{len(self.calls)}", "expire_time": 7200}
        for fragment, message in list(self.fail.items()):
            if fragment in path:
                code = None
                if message == "token expired":
                    code = 1010
                    self.fail.pop(fragment)       # once; the retry succeeds
                raise m.TuyaError(message, code=code)
        if path.endswith("/password-ticket"):
            return {"ticket_id": "TKT42", "ticket_key": "x", "expire_time": 360}
        if path.endswith("/password-free/open-door"):
            return True
        if path.endswith("/door-lock/remote-unlocks"):
            return {"remote_unlock_type": "remoteUnlockWithoutPwd", "open": self.remote_on}
        if path == f"/v1.0/devices/{DEVICE}":
            return {"online": self.online, "name": "Front door",
                    "status": [{"code": "residual_electricity", "value": self.battery},
                               {"code": "doorcontact_state", "value": False}]}
        raise m.TuyaError(f"unexpected path {path}")

    def opened(self):
        return [c for c in self.calls if c[1].endswith("/password-free/open-door")]

    def __enter__(self):
        self.saved = (m.tuya_request, m.TUYA_ACCESS_ID, m.TUYA_ACCESS_SECRET, m.urlopen)
        m.tuya_request = self
        m.TUYA_ACCESS_ID, m.TUYA_ACCESS_SECRET = "zzdoor-test-id", "zzdoor-test-secret"
        m.urlopen = _harness._refuse("the network", "a stand-in missed a path")
        m._tuya_token_cache.update(token=None, expires_at=0.0)
        return self

    def __exit__(self, *exc):
        m.tuya_request, m.TUYA_ACCESS_ID, m.TUYA_ACCESS_SECRET, m.urlopen = self.saved
        m._tuya_token_cache.update(token=None, expires_at=0.0)
        return False


def _booking(conn, suffix, arrive, leave, status="confirmed", checked_out=False):
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, created_at, checked_out_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?)""",
        (room, f"{TAG}-{suffix}", f"{TAG.lower()}-{suffix.lower()}-token", f"{suffix} {TAG} Guest",
         f"{suffix.lower()}.{TAG.lower()}@example.invalid", arrive.isoformat(), leave.isoformat(),
         status, _now(), _now() if checked_out else None))
    conn.commit()
    return conn.execute("SELECT * FROM bookings WHERE reference_code = ?", (f"{TAG}-{suffix}",)).fetchone()


SETTINGS = ("door_lock_device_id", "door_opens_at", "door_closes_at", "door_guest_access",
            "door_lock_health")


def _cleanup(conn):
    conn.execute("""DELETE FROM door_openings WHERE booking_id IN
                    (SELECT id FROM bookings WHERE reference_code LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM door_openings WHERE ota_reservation_id IN
                    (SELECT id FROM ota_reservations WHERE guest_name LIKE ?)""", (f"%{TAG}%",))
    conn.execute("DELETE FROM door_openings WHERE detail LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM door_openings WHERE via = 'staff' AND booking_id IS NULL "
                 "AND ota_reservation_id IS NULL AND opened_at >= ?",
                 ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM ota_reservations WHERE guest_name LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM notifications WHERE kind = 'door'")
    conn.commit()


def run():
    s = Suite("The front door")
    oc, ec, owner, _emp = clients()
    conn = db()
    kept = {k: conn.execute("SELECT value FROM app_settings WHERE key = ?", (k,)).fetchone()
            for k in SETTINGS}
    for k in SETTINGS:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (k,))
    conn.commit()
    _cleanup(conn)
    try:
        _run(s, oc, ec, owner, conn)
    finally:
        _cleanup(conn)
        for k, row in kept.items():
            conn.execute("DELETE FROM app_settings WHERE key = ?", (k,))
            if row is not None:
                conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (k, row["value"]))
        conn.commit()
        with m.app.test_request_context("/"):
            m.generate_watch_tasks(conn)
        conn.commit()
        conn.close()
    return s


def _run(s, oc, ec, owner, conn):
    today = m.house_today()

    # ------------------------------------------------------------------
    s.section("The signature is Tuya's own")
    heads = ("area_id:29a33e8796834b1efa6\ncall_id:8afdb70ab2ed11eb85290242ac130003\n")
    token_sign = m.tuya_sign("1KAD46OrT9HafiKdsXeg", "4OHBOnWOqaEC1mWXOpVL3yV50s0qGSRC", "GET",
                             "/v1.0/token?grant_type=1", b"", t="1588925778000",
                             nonce="5138cc3a9033d69856923fd07b491173", signed_headers=heads)
    s.check("the token request is signed as Tuya's worked example is",
            token_sign == "9E48A3E93B302EEECC803C7241985D0A34EB944F40FB573C7B5C2A82158AF13E",
            detail=token_sign)
    business = m.tuya_sign("1KAD46OrT9HafiKdsXeg", "4OHBOnWOqaEC1mWXOpVL3yV50s0qGSRC", "GET",
                           "/v2.0/apps/schema/users?page_no=1&page_size=50", b"", t="1588925778000",
                           nonce="5138cc3a9033d69856923fd07b491173",
                           access_token="3f4eda2bdec17232f67c0b188af3eec1", signed_headers=heads)
    s.check("and so is a call with the access token",
            business == "AE4481C692AA80B25F3A7E12C3A5FD9BBF6251539DD78E565A1A72A508A88784", detail=business)
    s.check("the harness has Tuya stood down, so no test can open the real door",
            m.tuya_request.__name__ == "_blocked" and not m.tuya_configured())

    # ------------------------------------------------------------------
    s.section("Not connected: nobody is offered a button that cannot work")
    here = _booking(conn, "Here", today, today + timedelta(days=2))
    page = oc.get(f"/book/manage/{here['manage_token']}/door").get_data(as_text=True)
    s.check("the door page says it cannot be opened yet, and to ring",
            "cannot be opened from this page yet" in visible_text(page)
            and "Open the front door</button>" not in page)
    booking_page = oc.get(f"/book/manage/{here['manage_token']}").get_data(as_text=True)
    s.check("and the booking page shows no door card", f"/book/manage/{here['manage_token']}/door" not in booking_page)
    s.check("the Front Door page says what to connect",
            "Not connected yet" in visible_text(oc.get("/admin/door").get_data(as_text=True)))
    s.check("the half-hourly check does nothing, and says why",
            m.run_door_lock_check_job(conn) == "no door lock connected")
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, today)
    s.check("and the owner's home says nothing about a lock nobody has fitted",
            not [w for w in warnings if "front door" in w["title"].lower()])

    # ------------------------------------------------------------------
    s.section("Connecting it")
    r = oc.post("/admin/door/settings", data={"device_id": "no spaces!", "opens": "12:00",
                                               "closes": "12:00", "guest_access": "1"},
                follow_redirects=True)
    s.check("a device ID that is not one is refused", any("does not look like" in f for f in flashes(r)))
    r = oc.post("/admin/door/settings", data={"device_id": DEVICE, "opens": "25:00",
                                               "closes": "12:00", "guest_access": "1"},
                follow_redirects=True)
    s.check("and a time that is not one", any("hours and minutes" in f for f in flashes(r)))
    # The whole day, for the checks below: the window is the stay's own days,
    # whatever the hour this suite happens to run at.
    r = oc.post("/admin/door/settings", data={"device_id": DEVICE, "opens": "00:00",
                                               "closes": "23:59", "guest_access": "1"},
                follow_redirects=True)
    s.check("the lock and the hours are saved", m.door_lock_device_id(conn) == DEVICE
            and any("Saved" in f for f in flashes(r)))
    r = ec.post("/admin/door/settings", data={"device_id": "", "opens": "00:00", "closes": "23:59"})
    s.check("not by an employee", r.status_code in (302, 403) and m.door_lock_device_id(conn) == DEVICE)

    with _Tuya() as tuya:
        page = visible_text(oc.get("/admin/door").get_data(as_text=True))
        s.check("the Front Door page shows what the lock says about itself",
                "Online Yes" in page and "Batteries 80%" in page and "Door shut" in page
                and "Opening without a code switched on" in page, detail=page[:400])
        s.check("asking it never opened it", not tuya.opened())

    # ------------------------------------------------------------------
    s.section("A guest opens the door, in their stay")
    with _Tuya() as tuya:
        page = oc.get(f"/book/manage/{here['manage_token']}/door").get_data(as_text=True)
        s.check("the door page offers the button", "Open the front door</button>" in page)
        s.check("and visiting it opened nothing -- only a POST can", not tuya.opened())
        booking_page = oc.get(f"/book/manage/{here['manage_token']}").get_data(as_text=True)
        s.check("the booking page leads to it",
                f'href="/book/manage/{here["manage_token"]}/door"' in booking_page)
        r = oc.post(f"/book/manage/{here['manage_token']}/door", follow_redirects=True)
        s.check("pressing it opens the door", len(tuya.opened()) == 1
                and any("The door is open" in f for f in flashes(r)), detail=str(flashes(r)))
        s.check("with a fresh ticket from Tuya, for this lock",
                tuya.opened()[0][1] == f"/v1.0/devices/{DEVICE}/door-lock/password-free/open-door"
                and tuya.opened()[0][2] == {"ticket_id": "TKT42"})
        rows = conn.execute("SELECT * FROM door_openings WHERE booking_id = ?", (here["id"],)).fetchall()
        s.check("it is recorded against the stay", len(rows) == 1 and rows[0]["ok"] == 1
                and rows[0]["via"] == "guest")
        told = conn.execute("SELECT * FROM notifications WHERE kind = 'door' AND title = ?",
                            (f"Here {TAG} Guest has let themselves in",)).fetchall()
        s.check("and the owner hears they have arrived", len(told) == 1 and told[0]["user_id"] == owner["id"])
        oc.post(f"/book/manage/{here['manage_token']}/door")
        told = conn.execute("SELECT * FROM notifications WHERE kind = 'door' AND title = ?",
                            (f"Here {TAG} Guest has let themselves in",)).fetchall()
        s.check("but not again the second time -- that is a list, not news", len(told) == 1
                and len(tuya.opened()) == 2)
        for _ in range(3):
            oc.post(f"/book/manage/{here['manage_token']}/door")
        before = len(tuya.opened())
        r = oc.post(f"/book/manage/{here['manage_token']}/door", follow_redirects=True)
        s.check("five in ten minutes, and it rests -- without asking the lock",
                before == 5 and len(tuya.opened()) == 5 and any("resting" in f for f in flashes(r)),
                detail=f"{before} {len(tuya.opened())} {flashes(r)}")

    conn.execute("DELETE FROM door_openings WHERE booking_id = ?", (here["id"],))
    conn.commit()
    with _Tuya(fail={"/password-free/open-door": f"device offline {TAG}"}) as tuya:
        r = oc.post(f"/book/manage/{here['manage_token']}/door", follow_redirects=True)
        s.check("when the lock does not open, the guest is told what to do instead",
                any("did not open" in f and "remote" in f for f in flashes(r)), detail=str(flashes(r)))
        row = conn.execute("SELECT * FROM door_openings WHERE booking_id = ? ORDER BY id DESC",
                           (here["id"],)).fetchone()
        s.check("it is recorded as not opening, with Tuya's reason",
                row is not None and row["ok"] == 0 and TAG in (row["detail"] or ""))
        told = conn.execute("SELECT * FROM notifications WHERE kind = 'door' AND title = ?",
                            (f"The front door did not open for Here {TAG} Guest",)).fetchall()
        s.check("and the owner hears at once -- somebody is outside", len(told) == 1)
    with _Tuya(fail={"/v1.0/smart-lock/devices/": "uri path invalid"}) as tuya:
        r = oc.post(f"/book/manage/{here['manage_token']}/door", follow_redirects=True)
        s.check("a lock that answers only at Tuya's other ticket path still opens",
                len(tuya.opened()) == 1 and any(c[1] == f"/v1.0/devices/{DEVICE}/door-lock/password-ticket"
                                                for c in tuya.calls), detail=str([c[1] for c in tuya.calls]))
    with _Tuya(fail={"/password-ticket": "token expired"}) as tuya:
        oc.post(f"/book/manage/{here['manage_token']}/door")
        s.check("a token Tuya says has run out is fetched again, and the door still opens",
                len(tuya.opened()) == 1 and sum(1 for c in tuya.calls if c[1] == "/v1.0/token") == 2)

    # ------------------------------------------------------------------
    s.section("Outside the stay, nothing reaches the lock")
    later = _booking(conn, "Later", today + timedelta(days=3), today + timedelta(days=5))
    gone = _booking(conn, "Gone", today - timedelta(days=4), today - timedelta(days=1))
    dropped = _booking(conn, "Dropped", today, today + timedelta(days=2), status="cancelled")
    left = _booking(conn, "Left", today - timedelta(days=2), today + timedelta(days=1), checked_out=True)
    with _Tuya() as tuya:
        for b, want in ((later, "can be opened from here from"), (gone, "Your stay is over"),
                        (dropped, "not confirmed"), (left, "You have checked out")):
            r = oc.post(f"/book/manage/{b['manage_token']}/door", follow_redirects=True)
            s.check(f"{b['guest_name'].split()[0]}: {want}", any(want in f for f in flashes(r)),
                    detail=str(flashes(r)))
        s.check("and not one of them asked the lock", not tuya.opened())
        s.check("a stay yet to start sees the door card, saying when",
                f"/book/manage/{later['manage_token']}/door" in
                oc.get(f"/book/manage/{later['manage_token']}").get_data(as_text=True))
        s.check("one that is over does not",
                f"/book/manage/{gone['manage_token']}/door" not in
                oc.get(f"/book/manage/{gone['manage_token']}").get_data(as_text=True))
        oc.post("/admin/door/stay-access", data={"kind": "booking", "id": here["id"], "off": "1"})
        r = oc.post(f"/book/manage/{here['manage_token']}/door", follow_redirects=True)
        s.check("a stay the owner has switched off cannot open it",
                any("switched off for this stay" in f for f in flashes(r)) and not tuya.opened())
        oc.post("/admin/door/stay-access", data={"kind": "booking", "id": here["id"], "off": "0"})
        conn.execute("DELETE FROM door_openings WHERE booking_id = ?", (here["id"],))
        conn.commit()
        oc.post(f"/book/manage/{here['manage_token']}/door")
        s.check("and switched back on, it can", len(tuya.opened()) == 1)
        r = ec.post("/admin/door/stay-access", data={"kind": "booking", "id": here["id"], "off": "1"})
        s.check("an employee cannot switch a stay off", r.status_code in (302, 403))
        oc.post("/admin/door/settings", data={"device_id": DEVICE, "opens": "00:00", "closes": "23:59"})
        r = oc.post(f"/book/manage/{here['manage_token']}/door", follow_redirects=True)
        s.check("with guests' access switched off altogether, nobody can",
                any("switched off at the moment" in f for f in flashes(r)) and len(tuya.opened()) == 1)
        oc.post("/admin/door/settings", data={"device_id": DEVICE, "opens": "00:00", "closes": "23:59",
                                              "guest_access": "1"})
    s.check("an unknown stay is not a door", oc.get("/book/manage/zz-no-such-token/door").status_code == 404)

    # The window's edges, to the minute, at the house's clock.
    conn.execute("UPDATE app_settings SET value = '12:00' WHERE key IN ('door_opens_at', 'door_closes_at')")
    conn.commit()
    opens = datetime.combine(m.parse_date(later["arrival_date"]),
                             m.dtime(12, 0), tzinfo=m.LOCAL_TZ).astimezone(timezone.utc)
    with _Tuya():
        just_before = m.door_access(conn, booking=later, now=opens - timedelta(minutes=1))
        at = m.door_access(conn, booking=later, now=opens)
    s.check("it opens at noon on the day they arrive, by the house's clock, not a minute before",
            just_before["state"] == "before" and at["can_open"], detail=f"{just_before['state']} {at['state']}")
    s.check("and says so as a guest here reads a date -- day first",
            f"from {m.format_date_short(later['arrival_date'])} at 12:00." in just_before["message"],
            detail=just_before["message"])
    closes = datetime.combine(m.parse_date(later["departure_date"]), m.dtime(12, 0),
                              tzinfo=m.LOCAL_TZ).astimezone(timezone.utc)
    with _Tuya():
        last = m.door_access(conn, booking=later, now=closes - timedelta(minutes=1))
        after = m.door_access(conn, booking=later, now=closes)
    s.check("and shuts at noon on the day they leave", last["can_open"] and after["state"] == "after")
    conn.execute("UPDATE app_settings SET value = '00:00' WHERE key = 'door_opens_at'")
    conn.execute("UPDATE app_settings SET value = '23:59' WHERE key = 'door_closes_at'")
    conn.commit()

    # ------------------------------------------------------------------
    s.section("A Booking.com guest gets a door link of their own")
    conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name, arrival_date,
               departure_date, room_id, guests, first_seen_at, last_event_at, updated_at)
           VALUES ('booking.com', '9876700001', 'confirmed', ?, ?, ?, NULL, 2, ?, ?, ?)""",
        (f"Mia {TAG} Berg", today.isoformat(), (today + timedelta(days=2)).isoformat(),
         _now(), _now(), _now()))
    conn.commit()
    mia = conn.execute("SELECT * FROM ota_reservations WHERE reservation_number = '9876700001'").fetchone()
    with _Tuya() as tuya:
        page = oc.get("/admin/door").get_data(as_text=True)
        s.check("the Front Door page lists them, with a button to make the link",
                f"Mia {TAG} Berg" in page and f"/admin/door/link/{mia['id']}" in page)
        r = oc.post(f"/admin/door/link/{mia['id']}", follow_redirects=True)
        token = conn.execute("SELECT door_token FROM ota_reservations WHERE id = ?", (mia["id"],)).fetchone()[0]
        page = r.get_data(as_text=True)
        s.check("the link is made once asked for, and shown to copy",
                bool(token) and f"/door/{token}" in page, detail=str(flashes(r)))
        s.check("with the warning that Booking.com strips links it has not been told to allow",
                "approved links in the extranet" in visible_text(page))
        oc.post(f"/admin/door/link/{mia['id']}")
        s.check("asked again, it is the same link",
                conn.execute("SELECT door_token FROM ota_reservations WHERE id = ?", (mia["id"],)).fetchone()[0] == token)
        page = oc.get(f"/door/{token}").get_data(as_text=True)
        s.check("their door page offers the button", "Open the front door</button>" in page
                and f"Mia {TAG} Berg" in page)
        s.check("and tells search engines to keep out", 'content="noindex' in page)
        r = oc.post(f"/door/{token}", follow_redirects=True)
        row = conn.execute("SELECT * FROM door_openings WHERE ota_reservation_id = ?", (mia["id"],)).fetchone()
        s.check("it opens the door, recorded against their stay",
                len(tuya.opened()) == 1 and row is not None and row["via"] == "booking_com_guest")
        s.check("and the owner hears they are in",
                conn.execute("SELECT 1 FROM notifications WHERE kind = 'door' AND title = ?",
                             (f"Mia {TAG} Berg has let themselves in",)).fetchone() is not None)
        conn.execute("UPDATE ota_reservations SET status = 'cancelled' WHERE id = ?", (mia["id"],))
        conn.commit()
        r = oc.post(f"/door/{token}", follow_redirects=True)
        s.check("a cancelled Booking.com stay's link opens nothing",
                len(tuya.opened()) == 1 and any("not confirmed" in f for f in flashes(r)))
    s.check("a link nobody made is not a door", oc.get("/door/zz-not-a-link").status_code == 404)
    s.check("only the owner makes one", ec.post(f"/admin/door/link/{mia['id']}").status_code in (302, 403))

    # ------------------------------------------------------------------
    s.section("From the office")
    with _Tuya() as tuya:
        r = oc.post("/admin/door/open", follow_redirects=True)
        row = conn.execute("SELECT * FROM door_openings WHERE via = 'staff' ORDER BY id DESC").fetchone()
        s.check("the owner can open it from the Front Door page, for a delivery",
                len(tuya.opened()) == 1 and row is not None and row["user_id"] == owner["id"]
                and any("The door is open" in f for f in flashes(r)))
        r = ec.post("/admin/door/open")
        s.check("an employee without the page cannot", r.status_code in (302, 403) and len(tuya.opened()) == 1)
        log = visible_text(oc.get("/admin/door").get_data(as_text=True))
        s.check("and every opening is on the page's record", "Staff, from the Door page" in log
                and f"Here {TAG} Guest" in log, detail=log[-300:])
    s.check("an employee cannot see the page at all", ec.get("/admin/door").status_code in (302, 403))

    # ------------------------------------------------------------------
    s.section("The confirmation letter carries the link")
    with _Tuya(), m.app.test_request_context("/"):
        context, _card = m.room_confirmation_context(conn, later, "The Twin Room")
    s.check("the door link is in the letter's stay details",
            f"/book/manage/{later['manage_token']}/door" in context["stay_details"]
            and "The front door opens from your phone" in context["stay_details"])
    s.check("and can be put anywhere in the letter as {door_url}",
            context.get("door_url", "").endswith(f"/book/manage/{later['manage_token']}/door")
            and "door_url" in m.EMAIL_TEMPLATE_TAGS["room_confirmed"])
    conn.execute("UPDATE app_settings SET value = '' WHERE key = 'door_lock_device_id'")
    conn.commit()
    with m.app.test_request_context("/"):
        context, _card = m.room_confirmation_context(conn, later, "The Twin Room")
    s.check("but not while no lock is connected", "front door" not in context["stay_details"])
    conn.execute("UPDATE app_settings SET value = ? WHERE key = 'door_lock_device_id'", (DEVICE,))
    conn.commit()

    # ------------------------------------------------------------------
    s.section("The lock's health, before it matters")
    for kind, tuya_kw, want in (("batteries", {"battery": 15}, "batteries are low (15%)"),
                                ("offline", {"online": False}, "offline"),
                                ("Tuya's plan", {"token_fails": "subscription expired"}, "cannot reach")):
        with _Tuya(**tuya_kw), m.app.test_request_context("/"):
            m.run_door_lock_check_job(conn)
            warnings = [w for w in m.owner_home_warnings(conn, today) if "front door" in w["title"].lower()]
            found, _dropped = m.watch_task_findings(conn)
        door = [f for f in found if f[0] == "door"]
        s.check(f"{kind}: the owner's home says so", len(warnings) == 1 and want in warnings[0]["detail"],
                detail=str(warnings))
        s.check(f"{kind}: and it is a task", len(door) == 1 and door[0][1] == "Look at the front door lock")
    with _Tuya(), m.app.test_request_context("/"):
        said = m.run_door_lock_check_job(conn)
        warnings = [w for w in m.owner_home_warnings(conn, today) if "front door" in w["title"].lower()]
        found, _dropped = m.watch_task_findings(conn)
    s.check("a well lock: the panel is quiet and the task goes",
            said == "the lock is fine" and not warnings and not [f for f in found if f[0] == "door"])
    s.check("the check runs by itself, every half hour",
            any(j[0] == "door_lock_check" and j[3] == 1800 and j[4] is m.run_door_lock_check_job
                for j in m.AUTOMATION_JOBS))
    for status, want in (({"battery_state": "low"}, (None, "low", True, None)),
                         ({"residual_electricity": 55, "closed_opened": "open"}, (55, None, False, True)),
                         ({}, (None, None, False, None))):
        s.check(f"a lock reporting {status or 'nothing'} is read as {want}",
                m.door_lock_reading(status) == want, detail=str(m.door_lock_reading(status)))

    # ------------------------------------------------------------------
    s.section("The record goes when the notice says")
    for age, label in ((400, "old"), (300, "recent")):
        conn.execute("""INSERT INTO door_openings (opened_at, via, ok, detail)
                        VALUES (?, 'staff', 1, ?)""",
                     ((datetime.now(timezone.utc) - timedelta(days=age)).isoformat(), f"{TAG} {label}"))
    conn.commit()
    cleared = m.purge_door_openings(conn)
    left_rows = {r["detail"] for r in conn.execute("SELECT detail FROM door_openings WHERE detail LIKE ?",
                                                   (f"{TAG} %",))}
    s.check("an opening over twelve months old is deleted, a recent one kept",
            left_rows == {f"{TAG} recent"} and cleared.get("door openings", 0) >= 1, detail=str(left_rows))
    src = open(m.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    s.check("by the daily retention pass",
            "purge_door_openings(conn)" in src.split("def run_health_notes_purge_job")[1][:1500])
    notice = visible_text(oc.get("/privacy").get_data(as_text=True))
    s.check("the privacy notice says what is kept and for how long",
            "If you open the front door from your phone" in notice
            and "Kept for twelve months" in notice and m.DOOR_LOG_KEEP_MONTHS == 12)

    s.section("The door card survives a new version of the booking page")
    src = open(_harness.ROOT + "/templates/manage_booking.html", encoding="utf-8").read()
    s.check("manage_booking.html still includes the door card",
            '{% include "_door_card.html" %}' in src,
            detail="the booking page arrives from the design side as a whole-file replacement; "
                   "without this line nothing on it leads a guest to their door")


if __name__ == "__main__":
    print(run().report())
