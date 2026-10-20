"""Mail about the software goes to the technical address, not to accounts@.

The owner's address is accounts@, the money inbox: invoices, suppliers,
Pennylane. The nightly backup zip and every provider test landed in it as
well and buried the invoices. A mailbox for the internet side (website@) was
made on 15 Oct 2026, and `tech_email` is the setting that points the
machine mail at it.

What this holds:
  - the backup job sends to the setting when there is one, and to the owner
    when it is empty -- nothing goes silent because a box was left blank;
  - the provider test does the same, falling back to whoever pressed it;
  - the setting refuses something that is not an address, and only the
    owner can change it;
  - the Guest email page says where each kind of mail goes, worked out by the
    same helper the sender calls;
  - money that needs the owner, and a poor review, stay with the owner and
    never ask the technical address (the daily summary moved to the technical
    address on 19 Oct 2026: test_summary_to_website);
  - every notice about a booking goes to the inbox for that kind of booking
    (reservations@ for stays and dinners, experience@ for ateliers and
    events) and never to the owner's sign-in address, which is accounts@ --
    the owner did not want accounts receiving every booking (15 Oct 2026).

Every send is a stand-in. Nothing reaches a provider.
"""
import inspect

import _harness
from _harness import Suite

import app as m

TECH = "website@example.invalid"


def _set(conn, value):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (m.TECH_EMAIL_KEY, value))
    conn.commit()


def _run_backup(conn):
    """Run the real job with the zip and the transport stood in. Returns the
    address it sent to, or None."""
    sent = {}
    was_build, was_send = m.build_backup_zip, m.send_backup_email
    try:
        m.build_backup_zip = lambda include_media=True, skipped_out=None: b"x" * 50

        def fake_send(to, data, filename, note=""):
            sent["to"] = to
            sent["filename"] = filename
            return True, None
        m.send_backup_email = fake_send
        with m.app.test_request_context("/"):
            m.run_backup_email_job(conn)
        conn.commit()
    finally:
        m.build_backup_zip, m.send_backup_email = was_build, was_send
    return sent.get("to"), sent.get("filename")


def _press_provider_test(client):
    """Press the provider test with a provider standing in. Returns who it
    was sent to, or None."""
    sent = []
    was_send, was_resend = m.send_email, m.resend_enabled

    def fake_send(to, subject, body, *a, **k):
        sent.append(to)
        return True
    try:
        m.send_email = fake_send
        m.resend_enabled = lambda: True
        client.post("/admin/email-outbox/test")
    finally:
        m.send_email, m.resend_enabled = was_send, was_resend
    return sent[0] if sent else None


def run():
    s = Suite("tech_email")
    conn = m.get_db()
    put_back = [
        _harness.keep_rows(conn, "app_settings", "key = ?", (m.TECH_EMAIL_KEY,)),
        _harness.keep_rows(conn, "audit_log", _harness.BACKUP_RECORD),
    ]
    try:
        _checks(s, conn)
    finally:
        conn.execute("DELETE FROM audit_log WHERE action = 'tech_email_changed' "
                     "AND details LIKE ?", ("%example.invalid%",))
        conn.commit()
        for restore in put_back:
            restore()
        conn.close()
    return s


def _checks(s, conn):
    oc, ec, owner, _emp = _harness.clients()
    owner_to = m.owner_email(conn)
    signed_in = conn.execute("SELECT email FROM users WHERE id = ?",
                             (owner["id"],)).fetchone()["email"]

    # ====================================================== the setting
    s.section("The owner sets it on the Guest email page")
    _set(conn, "")
    r = oc.post("/admin/emails/tech-address", data={"tech_email": "  Website@Example.INVALID "},
                follow_redirects=True)
    s.check("the save answers", r.status_code == 200, r)
    s.check("it is stored, trimmed and lower-cased",
            m.tech_email_setting(conn) == TECH, detail=repr(m.tech_email_setting(conn)))
    s.check("and the page says where backups now go",
            any(TECH in f for f in _harness.flashes(r)), detail=str(_harness.flashes(r)))
    s.check("the change is in the audit log, old and new",
            conn.execute("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'tech_email_changed' "
                         "AND details LIKE ?", ("%" + TECH + "%",)).fetchone()["c"] >= 1)

    s.section("Something that is not an address is refused")
    r = oc.post("/admin/emails/tech-address", data={"tech_email": "website at the house"},
                follow_redirects=True)
    s.check("it says so", any("not an email address" in f for f in _harness.flashes(r)),
            detail=str(_harness.flashes(r)))
    s.check("and keeps what was there", m.tech_email_setting(conn) == TECH)

    s.section("A row edited by hand to nonsense falls back rather than mailing a typo")
    _set(conn, "website@")
    s.check("the setting reads as unset", m.tech_email_setting(conn) == "")
    s.check("so the helper answers the owner", m.tech_email(conn) == owner_to)
    _set(conn, TECH)

    s.section("Only the owner can change it")
    r = ec.post("/admin/emails/tech-address", data={"tech_email": "staff@example.invalid"})
    s.check("an employee is turned away", r.status_code in (302, 403), r)
    s.check("and the setting is unchanged", m.tech_email_setting(conn) == TECH)
    # The page is in comms, and comms is a preset people hold. The backup is
    # the whole database, so the box that decides where it goes must not be
    # reachable through the page's area.
    s.check("it is owner-only, not in the page's area",
            "update_tech_email" in m.OWNER_ONLY_ENDPOINTS
            and "update_tech_email" not in m.ENDPOINT_AREA)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO access_presets (slug, name, areas, created_at) "
                 "VALUES ('ztech-comms', 'ztech comms', 'comms', ?)", (now,))
    conn.execute("INSERT INTO users (email, password_hash, role, name, job_role, status, "
                 "access_preset, account_claimed, created_at) VALUES "
                 "('ztech.comms@example.invalid', 'x', 'employee', 'ztech Comms', 'General', "
                 "'active', 'ztech-comms', 1, ?)", (now,))
    conn.commit()
    comms_id = conn.execute("SELECT id FROM users WHERE email = 'ztech.comms@example.invalid'"
                            ).fetchone()["id"]
    cc = m.app.test_client()
    with cc.session_transaction() as sess:
        sess["user_id"] = comms_id
    try:
        page = cc.get("/admin/emails")
        s.check("somebody with comms can open the Guest email page", page.status_code == 200, page)
        s.check("but is not shown the box",
                "update_tech_email" not in page.get_data(as_text=True)
                and "/admin/emails/tech-address" not in page.get_data(as_text=True))
        r = cc.post("/admin/emails/tech-address", data={"tech_email": "mine@example.invalid"})
        s.check("and is refused if they post it anyway", r.status_code == 403, r)
        s.check("the backup still goes where the owner said", m.tech_email_setting(conn) == TECH)
    finally:
        conn.execute("DELETE FROM users WHERE id = ?", (comms_id,))
        conn.execute("DELETE FROM access_presets WHERE slug = 'ztech-comms'")
        conn.commit()

    # ====================================================== the backup
    s.section("The backup goes to the technical address when one is set")
    to, filename = _run_backup(conn)
    s.check("sent to the technical address", to == TECH,
            detail=f"{to!r} -- the zip a day that buried the invoices in accounts@")
    s.check("not to the owner", to != owner_to, detail=f"owner is {owner_to!r}")
    s.check("named for the house's day, not UTC's",
            filename == f"gudanes-backup-{m.house_today_iso()}.zip", detail=filename)

    s.section("Cleared, it goes back to the owner rather than nowhere")
    r = oc.post("/admin/emails/tech-address", data={"tech_email": ""}, follow_redirects=True)
    s.check("clearing answers", r.status_code == 200, r)
    s.check("the setting is empty", m.tech_email_setting(conn) == "")
    to, _ = _run_backup(conn)
    s.check("the backup falls back to the owner", bool(owner_to) and to == owner_to,
            detail=f"{to!r} vs owner {owner_to!r} -- an empty box must not mean "
                   "no backup")

    # ====================================================== the provider test
    s.section("The provider test goes to the technical address")
    _set(conn, TECH)
    to = _press_provider_test(oc)
    s.check("sent to the technical address", to == TECH, detail=repr(to))
    _set(conn, "")
    to = _press_provider_test(oc)
    s.check("and with none set, to whoever pressed it", to == signed_in,
            detail=f"{to!r} vs {signed_in!r}")

    # ====================================================== the page
    s.section("The Guest email page says where each kind of mail goes")
    _set(conn, TECH)
    html = oc.get("/admin/emails").get_data(as_text=True)
    rows = dict(m.email_health(conn)["house_mail"])
    s.check("backups are shown going to the technical address",
            rows.get("Backups by email") == TECH, detail=str(rows))
    s.check("the daily summary is shown going to the technical address",
            rows.get("The daily summary and the morning note") == TECH, detail=str(rows))
    stays = m.house_address_for(conn, "rooms")
    s.check("stays are shown going to the rooms inbox, not the owner",
            rows.get("Stays: booked, waiting on you, changed, cancelled, extras, the waitlist")
            == stays and stays != owner_to, detail=str(rows))
    s.check("ateliers and events to theirs",
            rows.get("Ateliers: cancelled, moved, the waitlist") == m.house_address_for(conn, "workshops")
            and rows.get("Events: enquiries, changes, quotes accepted, holds that lapsed")
            == m.house_address_for(conn, "events"), detail=str(rows))
    s.check("the table is on the page", "Where the house's own mail goes" in html
            and TECH in html)
    form = next((f for f in _harness.forms_on(html)
                 if (f["action"] or "").endswith("/admin/emails/tech-address")), None)
    box = next((x for x in (form or {}).get("fields", ()) if x["name"] == "tech_email"), None)
    s.check("with the box to change it, holding the saved address",
            box is not None and box["value"] == TECH and box["type"] == "email",
            detail=str(form and form["fields"]))

    # ====================================================== the code
    # The page's table is a claim about the senders. Hold each "tech" line to
    # the function that sends it, and the business mail to the owner -- so a
    # sender moved without the table, or the table without the sender, is red.
    s.section("Money and a poor review stay with the owner")
    for fn in ("send_autocharge_failed_email", "create_booking_from_stripe_session",
               "create_restaurant_booking_from_stripe_session",
               "tell_the_house_about_a_review"):
        src = inspect.getsource(getattr(m, fn))
        s.check(f"{fn} writes to the owner", "owner_email(conn)" in src
                and "tech_email" not in src and "house_address_for" not in src)

    # The owner, 15 Oct 2026: "I don't want accounts to receive every booking".
    # Every notice about a booking goes to the inbox for that kind of booking --
    # and NONE of these may still write to owner_email anywhere, because one
    # branch left behind is one kind of booking still landing among invoices.
    s.section("Every booking notice goes to its own inbox, none to the owner")
    for fn, area in (("create_booking", "rooms"), ("manage_booking", "rooms"),
                     ("join_waitlist", "rooms"), ("guest_account_message", "rooms"),
                     ("contact_page", "rooms"), ("contact_page", "events"),
                     ("restaurant_manage", "restaurant"), ("join_restaurant_waitlist", "restaurant"),
                     ("workshop_manage", "workshops"), ("join_workshop_waitlist", "workshops"),
                     ("submit_event_inquiry", "events"), ("event_manage", "events"),
                     ("event_quote", "events"), ("lapse_event_holds", "events")):
        src = inspect.getsource(getattr(m, fn))
        s.check(f"{fn} writes to the {area} inbox",
                f'house_address_for(conn, "{area}")' in src and "owner_email(" not in src)

    s.section("A stay's notice reaches the rooms inbox, not accounts")
    sent = []
    was_send = m.send_email
    conn.execute("DELETE FROM submission_log WHERE action = 'join_waitlist'")
    conn.commit()
    try:
        m.send_email = lambda to, subject, body, *a, **k: (sent.append((to, subject)), True)[1]
        m.app.test_client().post("/waitlist/join", data={
            "name": "ztech Waiter", "email": "ztech.waiter@example.invalid",
            "desired_arrival": "2027-03-09", "desired_departure": "2027-03-10",
            "party_size": "2"})
    finally:
        m.send_email = was_send
        conn.execute("DELETE FROM waitlist_entries WHERE email = 'ztech.waiter@example.invalid'")
        conn.execute("DELETE FROM submission_log WHERE action = 'join_waitlist'")
        conn.commit()
    notice = [to for to, subject in sent if subject == "New waitlist request"]
    s.check("the house hears about it, once", len(notice) == 1, detail=str(sent))
    s.check("at the rooms inbox", notice == [stays], detail=f"{notice} vs {stays!r}")
    s.check("and not at the owner's sign-in address", owner_to not in notice,
            detail=f"owner is {owner_to!r}")

    s.section("And the machine mail asks for the technical address")
    s.check("run_backup_email_job", "tech_email(conn)" in inspect.getsource(m.run_backup_email_job)
            and "owner_email(" not in inspect.getsource(m.run_backup_email_job))
    s.check("test_email_provider", "tech_email_setting(conn)"
            in inspect.getsource(m.test_email_provider))
    s.check("every route on the page is a helper the senders call",
            {kind for _label, kind in m.HOUSE_MAIL_ROUTES}
            <= {"tech", "owner", "rooms", "restaurant", "workshops", "events"})
