"""Who changed a guest's profile, and every yes and no they have given.

What there was: editing a profile wrote over the old name, address and number
with nothing kept, so an address typed wrong -- which moves every letter to
somebody else -- could not be seen, let alone put back, and nobody could say
who had done it. The lists of who may be written to hold only where somebody
stands now: confirming the newsletter DELETES the earlier opt-out, so "when did
they say no, and how" was gone the moment they said yes. Texts and photographs
the same. And the record showed the marketing state and nothing about texts or
photographs at all.

What this holds:

  - An edit writes down what changed and who changed it: the value for a name,
    an address, a number; for a dietary or access note, or anything typed
    freely, only that it changed. A save that changes nothing writes nothing.
  - The audit line names the fields, never the values.
  - Every yes and no is kept with how it was said -- the link, the form, the
    person who wrote it down -- once, and only when it changes something:
    campaign email, the newsletter from asking to confirming to leaving, texts,
    marketing texts, photographs.
  - What was said before any of this is put into the history at startup, once.
  - The record says where they stand on texts and photographs, and leads to
    the history of it. A colleague sees neither the changes nor the consent.
  - A request for their data carries both, and erasing them takes both.
  - The privacy notice says so, and says the health notes are never kept.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, visible_text
import _harness

m = _harness.m
TAG = "ZZCP"
WHO = f"{TAG.lower()}@example.invalid"
NEW = f"{TAG.lower()}.new@example.invalid"
LETTERS = f"{TAG.lower()}.letters@example.invalid"
PHONE = "+33 6 44 55 66 77"


def _cleanup():
    conn = db()
    try:
        ids = "(SELECT id FROM guests WHERE name LIKE ?)"
        conn.execute(f"DELETE FROM guest_profile_changes WHERE guest_id IN {ids}", (TAG + "%",))
        conn.execute(f"DELETE FROM consent_events WHERE guest_id IN {ids}", (TAG + "%",))
        conn.execute("DELETE FROM consent_events WHERE LOWER(email) LIKE ?", (TAG.lower() + "%",))
        number = m.normalise_phone(PHONE)
        for table in ("consent_events", "sms_optouts", "sms_consents"):
            conn.execute(f"DELETE FROM {table} WHERE phone = ?", (number,))
        conn.execute("DELETE FROM email_optouts WHERE LOWER(email) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM newsletter_subscribers WHERE LOWER(email) LIKE ?",
                     (TAG.lower() + "%",))
        conn.execute("DELETE FROM campaign_sends WHERE LOWER(recipient_email) LIKE ?",
                     (TAG.lower() + "%",))
        conn.execute("DELETE FROM email_outbox WHERE LOWER(to_address) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM guest_messages WHERE LOWER(to_address) LIKE ?",
                     (TAG.lower() + "%",))
        conn.execute("DELETE FROM audit_log WHERE action = 'guest_profile_edited' AND target IN "
                     f"(SELECT 'guest ' || id FROM guests WHERE name LIKE ?)", (TAG + "%",))
        conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
        conn.commit()
    finally:
        conn.close()


# A CLOCK THAT MOVES ON BETWEEN ANY TWO READINGS, as a real one does. This
# machine's reads the same instant for sixteen milliseconds, so a yes or no
# stamped a moment apart from the list it belongs to -- which the startup would
# then write a second time -- looked, here, as if it shared the list's moment.
_REAL_DATETIME = []


def _moving_clock_on():
    real = m.datetime
    _REAL_DATETIME.append(real)
    step = [0]

    class Moving(real):
        @classmethod
        def now(cls, tz=None):
            step[0] += 1
            t = real.now(tz) + timedelta(microseconds=step[0])
            return cls(t.year, t.month, t.day, t.hour, t.minute, t.second, t.microsecond,
                       t.tzinfo)

    m.datetime = Moving


def _moving_clock_off():
    while _REAL_DATETIME:
        m.datetime = _REAL_DATETIME.pop(0)


def _tagged_events(gid):
    """Every yes and no the suite has written, by any way it could be filed."""
    return len(_rows(
        """SELECT id FROM consent_events
            WHERE LOWER(email) IN (?, ?) OR phone = ? OR guest_id = ?""",
        NEW, LETTERS, m.normalise_phone(PHONE), gid))


def _rows(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _said(**where):
    key, value = next(iter(where.items()))
    if key == "email":
        return _rows("SELECT * FROM consent_events WHERE LOWER(email) = ? ORDER BY id", value)
    if key == "phone":
        return _rows("SELECT * FROM consent_events WHERE phone = ? ORDER BY id",
                     m.normalise_phone(value))
    return _rows("SELECT * FROM consent_events WHERE guest_id = ? ORDER BY id", value)


def _history(gid):
    conn = db()
    try:
        return m.guest_timeline(conn, gid)
    finally:
        conn.close()


def _form(gid, **changes):
    g = _rows("SELECT * FROM guests WHERE id = ?", gid)[0]
    form = {"name": g["name"], "email": g["email"] or "", "phone": g["phone"] or "",
            "dietary_notes": g["dietary_notes"] or "", "preferences": g["preferences"] or "",
            "notes": g["notes"] or "", "access_needs": g["access_needs"] or "",
            "photo_consent": g["photo_consent"] or "unknown", "language": g["language"] or ""}
    if g["vip"]:
        form["vip"] = "1"
    form.update(changes)
    return {k: v for k, v in form.items() if v is not None}


def run():
    s = Suite("Profile changes and consent")
    oc, ec, owner, _emp = clients()
    _cleanup()
    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, *a, **k: sent.append((to, subject, body)) or True
    try:
        _run(s, oc, ec, owner, sent)
    finally:
        _moving_clock_off()
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, oc, ec, owner, sent):
    s.section("An edit says what changed, and who changed it")
    conn = db()
    conn.execute("""INSERT INTO guests (name, email, phone, dietary_notes, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                 (f"{TAG} Guest", WHO, PHONE, "no shellfish", _harness.datetime_now()))
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} Guest",)).fetchone()["id"]
    conn.commit()
    conn.close()
    oc.post(f"/guests/{gid}/edit", data=_form(gid, email=NEW, dietary_notes="no nuts either",
                                              vip="1"))
    changes = {r["field"]: r for r in _rows(
        "SELECT * FROM guest_profile_changes WHERE guest_id = ?", gid)}
    s.check("a new address is kept, from and to, with who changed it",
            "email" in changes and changes["email"]["old_value"] == WHO
            and changes["email"]["new_value"] == NEW
            and changes["email"]["changed_by_user_id"] == owner["id"],
            detail=str({k: dict(v) for k, v in changes.items()}))
    s.check("and being made a VIP, as a yes",
            "vip" in changes and (changes["vip"]["old_value"], changes["vip"]["new_value"])
            == ("no", "yes"))
    s.check("a dietary note says it changed, and never what it said",
            "dietary_notes" in changes and changes["dietary_notes"]["old_value"] is None
            and changes["dietary_notes"]["new_value"] is None,
            detail=str(dict(changes["dietary_notes"])) if "dietary_notes" in changes else "")
    s.check("and nothing that did not change is written down",
            set(changes) == {"email", "vip", "dietary_notes"}, detail=str(sorted(changes)))
    audit = _rows("SELECT details FROM audit_log WHERE action = 'guest_profile_edited' "
                  "AND target = ?", f"guest {gid}")
    s.check("the audit line names what changed, never the values",
            len(audit) == 1 and "Email" in audit[0][0] and NEW not in audit[0][0]
            and "nuts" not in audit[0][0], detail=str([tuple(a) for a in audit]))
    lines = [x for x in _history(gid) if x["kind"] == "Profile"]
    s.check("it is on their history, with who did it",
            any(NEW in x["title"] and owner["name"] in x["title"] for x in lines)
            and any("Dietary notes changed" in x["title"] for x in lines)
            and not any("nuts" in x["title"] or "shellfish" in x["title"] for x in lines),
            detail=str([x["title"] for x in lines]))
    oc.post(f"/guests/{gid}/edit", data=_form(gid))
    s.check("a save that changed nothing writes nothing",
            len(_rows("SELECT id FROM guest_profile_changes WHERE guest_id = ?", gid)) == 3
            and len(_rows("SELECT id FROM audit_log WHERE action = 'guest_profile_edited' "
                          "AND target = ?", f"guest {gid}")) == 1)
    page = oc.get(f"/admin/history/guest/{gid}").get_data(as_text=True)
    s.check("and the record's own history of changes has it",
            "Guest profile edited" in page and "Email" in page)
    record = oc.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("the record leads to that history", f"/admin/history/guest/{gid}" in record)

    s.section("Photographs")
    _moving_clock_on()
    oc.post(f"/guests/{gid}/edit", data=_form(gid, photo_consent="no"))
    photos = [r for r in _said(guest_id=gid) if r["channel"] == "photos"]
    s.check("saying no to photographs is kept, with how and who wrote it down",
            len(photos) == 1 and photos[0]["granted"] == 0
            and photos[0]["recorded_by_user_id"] == owner["id"]
            and photos[0]["how"] == "recorded on their profile",
            detail=str([dict(r) for r in photos]))
    record = visible_text(ec.get(f"/guests/{gid}").get_data(as_text=True))
    s.check("and the record says so, to whoever is holding the camera",
            m.PHOTO_CONSENT["no"] in record)

    s.section("What they say about being written to is kept, with how")
    conn = db()
    conn.execute("""INSERT INTO campaign_sends (recipient_email, recipient_name, subject, status,
                    unsubscribe_token, created_at) VALUES (?, ?, 'A spring letter', 'sent', ?, ?)""",
                 (NEW, f"{TAG} Guest", f"{TAG.lower()}unsub", _harness.datetime_now()))
    conn.commit()
    conn.close()
    guest = m.app.test_client()
    guest.post(f"/unsubscribe/{TAG.lower()}unsub")
    guest.post(f"/unsubscribe/{TAG.lower()}unsub")
    no = [r for r in _said(email=NEW) if r["channel"] == "marketing_email"]
    s.check("unsubscribing from a campaign is kept, as said by the link, once",
            len(no) == 1 and no[0]["granted"] == 0 and no[0]["recorded_by_user_id"] is None
            and "unsubscribe link" in no[0]["how"] and no[0]["guest_id"] == gid,
            detail=str([dict(r) for r in no]))
    conn = db()
    conn.execute("DELETE FROM submission_log")
    conn.commit()
    conn.close()
    guest.post("/newsletter/subscribe", data={"email": NEW})
    token = _rows("SELECT token FROM newsletter_subscribers WHERE email = ?", NEW)
    token = token[0][0] if token else "none"
    guest.get(f"/newsletter/confirm/{token}")
    said = [(r["channel"], r["granted"]) for r in _said(email=NEW)]
    s.check("joining the newsletter is kept from asking to confirming",
            ("newsletter", None) in said and ("newsletter", 1) in said, detail=str(said))
    s.check("and the no it lifted is still in the history, with the yes after it",
            said.count(("marketing_email", 0)) == 1 and ("marketing_email", 1) in said
            and not _rows("SELECT 1 FROM email_optouts WHERE email = ?", NEW),
            detail="confirming deletes the opt-out; the history is where it must stay: "
                   + str(said))
    guest.post(f"/newsletter/unsubscribe/{token}")
    guest.post(f"/newsletter/unsubscribe/{token}")
    said = [(r["channel"], r["granted"]) for r in _said(email=NEW)]
    s.check("and leaving it, once",
            said.count(("newsletter", 0)) == 1 and said.count(("marketing_email", 0)) == 2,
            detail=str(said))
    oc.post("/admin/emails/optout", data={"email": LETTERS})
    by_hand = _said(email=LETTERS)
    s.check("a no written down by hand says who wrote it",
            len(by_hand) == 1 and by_hand[0]["recorded_by_user_id"] == owner["id"],
            detail=str([dict(r) for r in by_hand]))

    s.section("And about texts")
    oc.post("/management/texting/stop", data={"phone": PHONE, "reason": "asked us on the phone"})
    optout = _rows("SELECT id FROM sms_optouts WHERE phone = ?", m.normalise_phone(PHONE))
    oc.post(f"/management/texting/allow/{optout[0][0] if optout else 0}")
    oc.post("/management/texting/consent", data={"phone": PHONE, "source": "at the desk"})
    said = [(r["channel"], r["granted"], r["how"]) for r in _said(phone=PHONE)]
    s.check("a stop, the stop taken off, and a yes to marketing texts, each with how",
            [(c, g) for c, g, _h in said] == [("texts", 0), ("texts", 1), ("marketing_texts", 1)]
            and said[0][2] == "asked us on the phone" and said[2][2] == "at the desk",
            detail=str(said))
    s.check("and they are the guest's, by their number",
            all(r["guest_id"] == gid for r in _said(phone=PHONE)))
    # Given before there was a profile to file it under -- a stop texted in,
    # a newsletter joined -- and so filed by nothing but the number or address.
    conn = db()
    conn.execute("""INSERT INTO consent_events (channel, granted, how, phone, created_at)
                    VALUES ('texts', 0, ?, ?, '2025-01-01T10:00:00+00:00')""",
                 (TAG + " before a profile, by number", m.normalise_phone(PHONE)))
    conn.execute("""INSERT INTO consent_events (channel, granted, how, email, created_at)
                    VALUES ('newsletter', 1, ?, ?, '2025-01-02T10:00:00+00:00')""",
                 (TAG + " before a profile, by address", NEW))
    conn.commit()
    conn.close()
    titles = [x["title"] for x in _history(gid)]
    s.check("one given before they had a profile is theirs, by the number",
            any(TAG + " before a profile, by number" in t for t in titles))
    s.check("and by the address",
            any(TAG + " before a profile, by address" in t for t in titles))
    kinds = [x["title"] for x in _history(gid) if x["kind"] == "Consent"]
    s.check("all of it on their history, newest first",
            len(kinds) >= 8 and kinds[0].startswith("Marketing texts: yes"), detail=str(kinds))
    record = visible_text(oc.get(f"/guests/{gid}").get_data(as_text=True))
    s.check("the record says where they stand on texts",
            "Said yes to marketing texts" in record)
    s.check("and leads to how and when they said it",
            f"/guests/{gid}/history?kind=Consent" in oc.get(f"/guests/{gid}").get_data(as_text=True))
    colleague = visible_text(ec.get(f"/guests/{gid}/history").get_data(as_text=True))
    s.check("a colleague sees neither the changes nor the consent",
            "Marketing texts" not in colleague and NEW not in colleague
            and "Dietary notes changed" not in colleague)

    _moving_clock_off()

    s.section("What was said before any of this")
    already = _tagged_events(gid)
    before = "2025-03-01T09:00:00+00:00"
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
                 (f"{TAG.lower()}.old@example.invalid", None, before))
    conn.commit()
    conn.close()
    m.init_db()
    m.init_db()
    old = _said(email=f"{TAG.lower()}.old@example.invalid")
    s.check("a no already on the list is put into the history at startup, once",
            len(old) == 1 and old[0]["created_at"] == before and old[0]["granted"] == 0
            and old[0]["how"] == m.CONSENT_HOW_UNKNOWN, detail=str([dict(r) for r in old]))
    s.check("and what was written as it happened is not written twice",
            _tagged_events(gid) == already,
            detail=f"{already} before the startup, {_tagged_events(gid)} after: a yes or no "
                   "not stamped with its list's own moment is written again")

    s.section("A request for their data carries both, and erasing takes both")
    conn = db()
    export = m.guest_data_export(conn, NEW)
    conn.close()
    held = (export or {}).get("tables", {})
    s.check("the export carries what changed on their profile",
            any(r.get("field") == "email" for r in held.get("guest_profile_changes", [])),
            detail=str(sorted(held)))
    s.check("and every yes and no, texts included",
            {r.get("channel") for r in held.get("consent_events", [])}
            >= {"marketing_email", "newsletter", "texts", "photos"},
            detail=str(sorted({r.get("channel") for r in held.get("consent_events", [])})))
    conn = db()
    m.guest_data_erase(conn, NEW)
    conn.commit()
    conn.close()
    s.check("and erasing them takes both",
            not _rows("SELECT id FROM guest_profile_changes WHERE guest_id = ?", gid)
            and not _said(phone=PHONE) and not _said(email=NEW))

    s.section("The notice says so")
    notice = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    s.check("that every yes and no is kept, and how",
            "What you have told us about hearing from us" in notice)
    s.check("and that a health note's change is kept without what it said",
            "Changes to your details" in notice and "never what it said" in notice)
