"""The whole guest, once: guest_dossier, which the Outlook panel, the reply
drafter and the guest page in the dashboard all read.

The lookup it replaced matched one address exactly against three tables and
missed every merge, so a guest who wrote from a different address, or whose
two profiles had been folded into one, came up as a stranger beside their own
email. This holds the new one to:

  - any case, any address they are known by: their profile's, a merged
    profile's, one a stay was linked under;
  - everything booked: stays, extras, dinners, ateliers, events, vouchers,
    and for the owner what was spent at the till;
  - the warnings first: a caution, a request to stop using their details, a
    poor review nobody answered, an email waiting, money owed (owner only);
  - money only for the owner, in the panel exactly as on the dashboard;
  - and a decision for every column on a profile and every table that names
    a guest: shown, or left out with a reason. A column added next month is
    a red run until somebody decides.
"""
import re
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZGD"

# A table "names a guest" if it has one of these columns. Kept wide on
# purpose: a false alarm costs one line in GUEST_DOSSIER_TABLES with a reason.
GUEST_COLUMNS = re.compile(
    r"^(guest_id|linked_guest_id|guest_email|guest_name|guest_contact|contact_email|"
    r"purchaser_email|recipient_email|to_address)$")


def undecided(conn, columns_decided, tables_decided):
    """(columns of guests with no decision, guest tables with no decision,
    decisions about things that no longer exist)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(guests)").fetchall()]
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    naming = [t for t in tables if t != "guests" and any(
        GUEST_COLUMNS.match(r[1]) for r in conn.execute(f"PRAGMA table_info({t})").fetchall())]
    stale = ([c for c in columns_decided if c not in cols]
             + [t for t in tables_decided if t not in tables])
    return ([c for c in cols if c not in columns_decided],
            [t for t in naming if t not in tables_decided], stale)


def _cleanup(conn):
    gids = [r[0] for r in conn.execute("SELECT id FROM guests WHERE name LIKE ?", (TAG + "%",))]
    marks = ",".join("?" * len(gids)) or "NULL"
    for table in ("guest_notes", "guest_contacts", "guest_tags", "guest_profile_changes",
                  "consent_events"):
        conn.execute(f"DELETE FROM {table} WHERE guest_id IN ({marks})", gids)
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guest_feedback WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM gift_vouchers WHERE code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_flags WHERE graph_message_id LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM payment_disputes WHERE stripe_dispute_id LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM addin_sessions WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ? OR LOWER(email) LIKE ?",
                 (TAG + "%", TAG.lower() + "%"))
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'guest_lookup%' "
                 "OR action LIKE 'addin_%'")
    conn.commit()


def run():
    s = Suite("the whole guest")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = m.house_today()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()

    s.section("Every column and every table is decided")
    cols, tables, stale = undecided(conn, m.GUEST_DOSSIER_COLUMNS, m.GUEST_DOSSIER_TABLES)
    s.check("every column on a guest profile is shown or left out with a reason",
            not cols, detail=f"undecided: {cols} — add each to GUEST_DOSSIER_COLUMNS")
    s.check("every table that names a guest is shown or left out with a reason",
            not tables, detail=f"undecided: {tables} — add each to GUEST_DOSSIER_TABLES")
    s.check("and nothing is decided about a column or table that is gone",
            not stale, detail=f"stale: {stale}")
    s.check("every reason says which, and why if not",
            all(v.startswith(("shown", "followed", "left out: ")) for v in
                list(m.GUEST_DOSSIER_COLUMNS.values()) + list(m.GUEST_DOSSIER_TABLES.values())))
    # The negative control: the same check, told about a column nobody decided.
    fake_cols = dict(m.GUEST_DOSSIER_COLUMNS)
    fake_cols.pop("phone")
    s.check("(the check does see a column nobody decided)",
            undecided(conn, fake_cols, m.GUEST_DOSSIER_TABLES)[0] == ["phone"])

    s.section("Any address they are known by, in any case")
    ana = conn.execute(
        """INSERT INTO guests (name, email, phone, dietary_notes, preferences, vip, notes,
             name_pronunciation, birthday, anniversary, access_needs, usual_arrival_time,
             language, photo_consent, caution, caution_level, created_at)
           VALUES (?, 'zzgd.ana@example.invalid', '+33 6 99 88 77 66', 'nut allergy', 'quiet side',
                   1, 'old note', 'AH-na', '06-03', '09-14', 'no stairs', '18:00', 'fr', 'no',
                   'shouted at the night porter', 'care', ?)""",
        (f"{TAG} Ana", now)).lastrowid
    old = conn.execute(
        "INSERT INTO guests (name, email, merged_into_id, created_at) VALUES (?, ?, ?, ?)",
        (f"{TAG} Ana (old)", "zzgd.ana.old@example.invalid", ana, now)).lastrowid
    m.add_guest_tag(conn, ana, "repeat")
    m.add_guest_note(conn, ana, f"{TAG} likes the tower room")
    conn.execute(
        """INSERT INTO guest_contacts (guest_id, channel, direction, summary, follow_up_on,
             created_at) VALUES (?, 'phone', 'in', ?, ?, ?)""",
        (ana, f"{TAG} asked about a transfer", (today + timedelta(days=2)).isoformat(), now))

    def stay(ref, email, arrive, nights, status="confirmed", linked=None):
        return conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
                 arrival_date, departure_date, party_size, status, special_requests,
                 linked_guest_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 'late arrival', ?, ?)""",
            (room["id"], f"{TAG}{ref}", f"{TAG}t{ref}", f"{TAG} Ana", email,
             (today + timedelta(days=arrive)).isoformat(),
             (today + timedelta(days=arrive + nights)).isoformat(), status, linked, now)).lastrowid

    soon = stay("S1", "zzgd.ana.old@example.invalid", 3, 2)
    past = stay("S2", "zzgd.ana.work@example.invalid", -90, 3, linked=ana)
    conn.execute("""INSERT INTO booking_extras (booking_id, category, name, quantity, unit_price,
                      status, scheduled_for, created_at)
                    VALUES (?, 'room', ?, 2, 35, 'confirmed', ?, ?)""",
                 (soon, f"{TAG} picnic", (today + timedelta(days=4)).isoformat(), now))
    conn.execute("""INSERT INTO guest_feedback (booking_id, guest_name, rating, comment, submitted_at)
                    VALUES (?, ?, 2, 'cold room', ?)""", (past, f"{TAG} Ana", now))
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
             party_size, dinner_date, status, total_price, created_at)
           VALUES (?, ?, ?, 'zzgd.ana.work@example.invalid', 2, ?, 'confirmed', 110, ?)""",
        (f"{TAG}D1", f"{TAG}dt", f"{TAG} Ana", (today + timedelta(days=4)).isoformat(), now))
    ws = conn.execute("SELECT id FROM workshops ORDER BY id LIMIT 1").fetchone()["id"]
    session = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 8, ?, ?)""",
        (ws, (today + timedelta(days=60)).isoformat(), (today + timedelta(days=64)).isoformat(),
         TAG, now)).lastrowid
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
             guest_email, party_size, status, created_at)
           VALUES (?, ?, ?, ?, 'ZZGD.Ana@Example.invalid', 1, 'confirmed', ?)""",
        (session, f"{TAG}W1", f"{TAG}wt", f"{TAG} Ana", now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
             contact_email, preferred_date, guest_count, status, created_at)
           VALUES (?, ?, 'private_party', ?, 'zzgd.ana@example.invalid', ?, 40, 'new', ?)""",
        (f"{TAG}E1", f"{TAG}et", f"{TAG} Ana", (today + timedelta(days=120)).isoformat(), now))
    conn.execute(
        """INSERT INTO gift_vouchers (code, original_amount, purchaser_name, purchaser_email,
             recipient_name, status, created_at)
           VALUES (?, 200, ?, 'zzgd.ana@example.invalid', 'her sister', 'active', ?)""",
        (f"{TAG}V1", f"{TAG} Ana", now))
    conn.execute(
        """INSERT INTO pos_orders (table_label, room_booking_id, status, settled_total, opened_at,
             closed_at, service_date) VALUES (?, ?, 'paid', 64.5, ?, ?, ?)""",
        (f"{TAG} bar", past, now, now, (today - timedelta(days=89)).isoformat()))
    conn.execute(
        """INSERT INTO email_flags (graph_message_id, from_address, subject, received_at, status,
             mailbox, created_at, updated_at)
           VALUES (?, 'zzgd.ana@example.invalid', 'Our arrival', ?, 'open',
                   'reservations@example.invalid', ?, ?)""",
        (f"{TAG}F1", now, now, now))
    conn.execute(
        """INSERT INTO payment_disputes (stripe_dispute_id, amount, status, guest_email,
             reference_code, opened_at, updated_at)
           VALUES (?, 120, 'needs_response', 'zzgd.ana@example.invalid', ?, ?, ?)""",
        (f"{TAG}DP1", f"{TAG}S2", now, now))
    conn.commit()

    with m.app.test_request_context("/"):
        for address in ("ZZGD.ANA@EXAMPLE.INVALID", "zzgd.ana.old@example.invalid",
                        "zzgd.ana.work@example.invalid"):
            gid = m.guest_id_for_address(conn, address)
            s.check(f"{address} is Ana", gid == ana, detail=f"found {gid}")
        s.check("a merged profile's address follows to the profile it lives in",
                m.guest_id_for_address(conn, "zzgd.ana.old@example.invalid") == ana)
        owner_view = m.guest_dossier_for_address(conn, "zzgd.ana.old@example.invalid",
                                                 money=True, letters=True)
        staff_view = m.guest_dossier_for_address(conn, "zzgd.ana@example.invalid",
                                                 money=False, letters=True)

    s.section("Everything booked, under every address")
    kinds = [i["kind"] for i in owner_view["items"]]
    for kind in ("Stay", "Extra", "Dinner", "Atelier", "Event", "Gift voucher", "At the till"):
        s.check(f"{kind.lower()} is there", kind in kinds, detail=str(kinds))
    s.check("both stays, the one under the old address and the one linked under another",
            kinds.count("Stay") == 2)
    s.check("coming up first, soonest first",
            owner_view["items"][0]["upcoming"] and owner_view["items"][0]["ref"] == f"{TAG}S1",
            detail=str([(i["kind"], i["ref"]) for i in owner_view["items"][:3]]))
    s.check("a request they made comes with it",
            any(i["requests"] == "late arrival" for i in owner_view["items"]))
    s.check("every address is listed on the profile",
            set(owner_view["profile"]["other_emails"]) >= {"zzgd.ana.old@example.invalid",
                                                          "zzgd.ana.work@example.invalid"},
            detail=str(owner_view["profile"]["other_emails"]))

    s.section("What they are like")
    p = owner_view["profile"]
    for key, value in (("pronunciation", "AH-na"), ("phone", "+33 6 99 88 77 66"),
                       ("dietary", "nut allergy"), ("access_needs", "no stairs"),
                       ("preferences", "quiet side"), ("usual_arrival_time", "18:00"),
                       ("language", "fr"), ("note", "old note"), ("birthday", "3 June"),
                       ("anniversary", "14 September")):
        s.check(f"{key} is shown", p.get(key) == value, detail=f"{p.get(key)!r}")
    s.check("VIP is shown", p["vip"] is True)
    s.check("tags are shown", "repeat" in p["tags"])
    s.check("photographs, in words", p["photo_consent"] == m.PHOTO_CONSENT["no"])
    s.check("staff notes are there", any(TAG in n["body"] for n in owner_view["notes"]))
    s.check("and the follow-up still to do",
            any(TAG in f["what"] for f in owner_view["follow_ups"]))
    s.check("the key to their account page is not",
            "portal_token" not in str(m.dossier_json(owner_view)))

    s.section("The warnings come first")
    titles = [w["title"] for w in owner_view["warnings"]]
    s.check("the caution", "Handle with care" in titles, detail=str(titles))
    s.check("the poor review nobody answered",
            any("unanswered poor review" in t for t in titles))
    s.check("the email waiting for a reply", "Waiting for a reply" in titles)
    s.check("that they arrive this week", any(t.startswith("Arrives ") for t in titles))
    s.check("the open dispute, for the owner",
            "A card payment disputed with their bank" in titles)
    s.check("in order: what not to do and what to handle before the rest",
            [w["level"] for w in owner_view["warnings"]] ==
            sorted([w["level"] for w in owner_view["warnings"]],
                   key=lambda lv: {"stop": 0, "care": 1, "money": 2, "note": 3}[lv]))

    s.section("Money is the owner's")
    s.check("the owner sees the account", owner_view["account"] is not None)
    s.check("and what was spent at the till",
            any(i["kind"] == "At the till" and i["amount"] == 64.5 for i in owner_view["items"]))
    s.check("staff see no account", staff_view["account"] is None)
    s.check("no amounts on anything they booked",
            all(i["amount"] is None and i["owed"] is None for i in staff_view["items"]))
    s.check("no till", "At the till" not in [i["kind"] for i in staff_view["items"]])
    s.check("and no money warnings",
            not [w for w in staff_view["warnings"] if w["level"] == "money"])
    s.check("but the dietary note, which they need",
            staff_view["profile"]["dietary"] == "nut allergy")

    s.section("Asked us to stop using their details")
    conn.execute("UPDATE guests SET restricted_at = ?, restricted_how = 'by email' WHERE id = ?",
                 (now, ana))
    conn.commit()
    with m.app.test_request_context("/"):
        restricted = m.guest_dossier_for_address(conn, "zzgd.ana@example.invalid")
    s.check("said at the top, as something not to do",
            restricted["warnings"][0]["title"] == "Asked us to stop using their details")
    s.check("and the dossier knows", restricted["restricted"] is True)
    conn.execute("UPDATE guests SET restricted_at = NULL, restricted_how = NULL WHERE id = ?", (ana,))
    conn.commit()

    s.section("Somebody with no profile is not a stranger")
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
             party_size, dinner_date, status, created_at)
           VALUES (?, ?, 'Walk In', 'zzgd.walkin@example.invalid', 2, ?, 'confirmed', ?)""",
        (f"{TAG}D2", f"{TAG}dt2", (today + timedelta(days=9)).isoformat(), now))
    conn.execute(
        """INSERT INTO guests (name, email, caution, caution_level, created_at)
           VALUES (?, 'zzgd.someone.else@example.invalid', 'never paid', 'refuse', ?)""",
        (f"{TAG} Refused", now))
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
             party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, 'zzgd.new.address@example.invalid', 2, ?, 'pending', ?)""",
        (f"{TAG}D3", f"{TAG}dt3", f"{TAG} Refused", (today + timedelta(days=9)).isoformat(), now))
    conn.commit()
    with m.app.test_request_context("/"):
        walkin = m.guest_dossier_for_address(conn, "zzgd.walkin@example.invalid")
        renamed = m.guest_dossier_for_address(conn, "zzgd.new.address@example.invalid")
    s.check("their dinner is there", walkin["known"] and not walkin["has_profile"]
            and walkin["items"][0]["kind"] == "Dinner")
    s.check("with the name it was booked under", walkin["profile"]["name"] == "Walk In")
    s.check("a caution is found by name when the address is new",
            renamed["warnings"] and renamed["warnings"][0]["level"] == "stop"
            and "matched by name" in renamed["warnings"][0]["text"],
            detail=str(renamed["warnings"])[:200])

    s.section("The panel, as each person sees it")
    owner_key, _exp = m.addin_issue_session(conn, owner["id"], "test", label=f"{TAG} owner")
    staff_key, _exp = m.addin_issue_session(conn, emp["id"], "test", label=f"{TAG} staff")
    conn.commit()
    anon = m.app.test_client()
    r = anon.post("/api/guest-lookup", data={"email": "ZZGD.Ana@example.invalid"},
                  headers={"Authorization": f"Bearer {owner_key}"})
    got = r.get_json() or {}
    s.check("the owner's panel answers", r.status_code == 200, detail=str(r.status_code))
    s.check("with the whole guest", (got.get("dossier") or {}).get("guest_id") == ana)
    s.check("money included", (got.get("dossier") or {}).get("account") is not None)
    s.check("and says who is looking", (got.get("who") or {}).get("level") == "owner")
    r = anon.post("/api/guest-lookup", data={"email": "zzgd.ana@example.invalid"},
                  headers={"Authorization": f"Bearer {staff_key}"})
    got = r.get_json() or {}
    s.check("a colleague's panel answers", r.status_code == 200)
    s.check("without the money", (got.get("dossier") or {}).get("account") is None
            and "A card payment disputed with their bank" not in str(got))
    r = anon.post("/api/guest-lookup", data={"email": "zzgd.ana@example.invalid"},
                  headers={"Authorization": "Bearer not-a-key"})
    s.check("a key nobody issued is a 404", r.status_code == 404)

    s.section("A profile made from the panel")
    r = anon.post("/api/addin/create-profile", data={"email": "zzgd.fresh@example.invalid",
                                                     "name": f"{TAG} Fresh"},
                  headers={"Authorization": f"Bearer {staff_key}"})
    s.check("a colleague cannot make one", r.status_code == 403)
    r = anon.post("/api/addin/create-profile", data={"email": "zzgd.fresh@example.invalid",
                                                     "name": f"{TAG} Ana"},
                  headers={"Authorization": f"Bearer {owner_key}"})
    got = r.get_json() or {}
    s.check("the same name is offered before a second profile is made",
            got.get("created") is False and got.get("possible"), detail=str(got)[:200])
    r = anon.post("/api/addin/create-profile", data={"email": "zzgd.fresh@example.invalid",
                                                     "name": f"{TAG} Ana", "confirm": "1"},
                  headers={"Authorization": f"Bearer {owner_key}"})
    got = r.get_json() or {}
    s.check("and made when the owner says it is somebody else",
            got.get("created") is True and conn.execute(
                "SELECT 1 FROM guests WHERE email = 'zzgd.fresh@example.invalid'").fetchone())
    r = anon.post("/api/addin/create-profile", data={"email": "zzgd.fresh@example.invalid",
                                                     "name": f"{TAG} Again", "confirm": "1"},
                  headers={"Authorization": f"Bearer {owner_key}"})
    s.check("never twice for one address", r.status_code == 409)

    s.section("The guest page reads the same dossier")
    page = oc.get(f"/guests/{ana}").get_data(as_text=True)
    s.check("the warnings are at the top", "Before anything else" in page
            and "shouted at the night porter" in page)
    s.check("the extras, vouchers and till are there",
            f"{TAG} picnic" in page and f"{TAG}V1" in page and "At the till" in page)
    s.check("and what is still to do", "Still to do for them" in page)
    page = ec.get(f"/guests/{ana}").get_data(as_text=True)
    s.check("a colleague sees the warnings too", "Before anything else" in page)
    s.check("but not the till or the money", "At the till" not in page
            and "A card payment disputed" not in page)
    page = oc.get(f"/guests/{old}").get_data(as_text=True)
    s.check("a merged profile still opens as itself",
            f"{TAG} Ana (old)" in page)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
