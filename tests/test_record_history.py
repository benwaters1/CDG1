"""What changed on this one record, and who changed it.

The audit log has been written to faithfully for a long time -- a hundred and
seventy-odd call sites -- and read back exactly one way: a single page of
everything the house has ever done, searchable. That answers "who revealed a
bank detail in March". It does not answer the question people actually ask,
which is asked while looking at ONE booking: who moved these dates, and when.
The answer was to open the whole log and search for the reference code, which
is a thing you only do if you already know the log exists.

  THE KEYS ARE DECLARED, NOT GUESSED. `target` is free text and no two areas
  agreed: a booking is its reference code, a person is their name, a sitting is
  its id, and a refund is "room booking GD-1042". HISTORY_KINDS says which
  columns are keys for each kind.

  AND THE MATCH IS NARROW. Exactly the key, or the key as a whole word inside
  the target. A substring match would put "Marie"'s history on "Marie-Claire"'s
  page, and on a record somebody is going to be answerable for, attributing one
  person's changes to another is worse than showing nothing at all.

  THE DRIFT IS THE POINT. If somebody changes a log_audit target from the
  reference code to the id, nothing errors, no other test goes red, and this
  page silently empties. So this suite does a real audited action through a
  real route and then asks the record's own history for it.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZRH"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM audit_log WHERE target LIKE ? OR details LIKE ?",
                 (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _log(target, action=TAG + "_thing_happened", details=None):
    conn = db()
    conn.execute(
        """INSERT INTO audit_log (actor_user_id, action, target, details, created_at)
           VALUES (NULL, ?, ?, ?, ?)""",
        (action, target, details, m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def m_history_no_about(key):
    conn = db()
    try:
        return m.record_history(conn, [key])
    finally:
        conn.close()


def _history(keys):
    conn = db()
    try:
        return m.record_history(conn, keys)
    finally:
        conn.close()


def run():
    s = Suite("What changed, and who changed it")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()

    arrival = m.house_today() + timedelta(days=155)
    departure = arrival + timedelta(days=2)
    try:
        with m.app.test_request_context("/"):
            ref, _token = m.create_booking(
                conn, room, f"{TAG} Guest", "zzrh@example.invalid", "",
                arrival, departure, 2, "", [], payment_status="unpaid")
        conn.commit()
        bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (ref,)).fetchone()["id"]
        conn.close()

        s.section("A record finds its own lines")
        _log(ref, TAG + "_dates_moved", "moved by two nights")
        found = _history([ref])
        s.check("the line logged under the key is found",
                any(e["action"] == TAG + "_dates_moved" for e in found),
                detail=str([e["action"] for e in found]))
        s.check("and it carries who and what",
                found and found[0]["details"] == "moved by two nights")

        s.section("And the lines that were logged another way round")
        # refund_issued writes "room booking GD-1042", not the bare code, and
        # a match that only took the whole target would miss every refund the
        # house has ever made -- which is the line most worth finding.
        _log(f"room booking {ref}", "refund_issued", "EUR 120.00")
        found = _history([ref])
        s.check("a key inside the target is still this record",
                any(e["action"] == "refund_issued" for e in found),
                detail=str([e["target"] for e in found]))

        s.section("And nobody else's")
        # THE ONE THAT MATTERS. Attributing one record's changes to another is
        # worse than an empty page, because an empty page does not accuse
        # anybody.
        _log(ref + "9", TAG + "_someone_elses_booking")
        _log("Marie-Claire", TAG + "_someone_elses_name")
        found = _history([ref])
        s.check("a longer reference code is a different booking",
                not any(e["action"] == TAG + "_someone_elses_booking" for e in found),
                detail=str([e["target"] for e in found]))
        s.check("and a longer name is a different person",
                not any(e["action"] == TAG + "_someone_elses_name"
                        for e in _history(["Marie"])),
                detail=str([e["target"] for e in _history(["Marie"])]))
        _log("None", TAG + "_filed_under_the_word_none")
        s.section("A number is not a key on its own")
        # Ids repeat across tables — vendor 47, session 47 and expense 47 are
        # three different things — and target is free text, some of which is
        # str(id). This one was found by a full run rather than by reading:
        # an untouched supplier came up with somebody else's history against
        # it, which is the accusation this whole file is built to avoid.
        vid = "987654"
        _log(vid, TAG + "_workshop_session_called_off")
        _log(vid, TAG + "_vendor_edited")
        conn = db()
        try:
            got = [e["action"] for e in
                   m.record_history(conn, [vid], about="vendor")]
        finally:
            conn.close()
        s.check("an action about this kind of thing counts",
                TAG + "_vendor_edited" in got, detail=str(got))
        s.check("and one about something else with the same id does not",
                TAG + "_workshop_session_called_off" not in got,
                detail="vendor %s and workshop session %s are different "
                       "things: %s" % (vid, vid, got))
        s.check("and with no kind named, a bare number matches nothing",
                not m_history_no_about(vid),
                detail="a number with nothing to say what it is an id OF is "
                       "not something to put on somebody's record")

        conn = db()
        try:
            sid = conn.execute(
                "SELECT id FROM workshop_sessions ORDER BY id").fetchone()
            sid = sid["id"] if sid else None
        finally:
            conn.close()
        if sid:
            _log(str(sid), "workshop_session_called_off", TAG + " weather")
            conn = db()
            try:
                sitting = m.history_for(conn, "workshop_session", sid)
            finally:
                conn.close()
            s.check("and a sitting, which is keyed by nothing but its id, "
                    "still finds its own",
                    any(e["details"] == TAG + " weather"
                        for e in sitting["entries"]),
                    detail="if the kind stops saying what its actions are "
                           "called, or history_for stops passing it down, "
                           "this page empties without erroring: "
                           + str([e["action"] for e in sitting["entries"]][:5]))
        s.check("and every kind says what its actions are called",
                all(bool(spec[3]) for spec in m.HISTORY_KINDS.values()),
                detail="a kind with no word can never match its own numeric "
                       "key: " + str({k: v[3] for k, v in
                                      m.HISTORY_KINDS.items()}))

        s.check("asking with no keys at all answers nothing",
                _history([]) == []
                and not any(e["action"] == TAG + "_filed_under_the_word_none"
                            for e in _history([None, ""])),
                detail="a record with nothing to be found under must not match "
                       "every line in the log, and str(None) is the word None: "
                       + str([e["target"] for e in _history([None, ""])]))

        s.section("A real audited action lands on the record's own page")
        # THE DRIFT GUARD. Nothing here reaches into the audit table: it marks
        # a booking as a no-show through the route the owner uses, and then
        # asks the booking's history for it. Change the target that route logs
        # under and this is what goes red.
        conn = db()
        conn.execute("UPDATE bookings SET status = 'confirmed', arrival_date = ? WHERE id = ?",
                     ((m.house_today() - timedelta(days=1)).isoformat(), bid))
        conn.commit()
        conn.close()
        oc.post(f"/admin/bookings/{bid}/no-show", data={"note": TAG + " never arrived"},
                follow_redirects=True)
        conn = db()
        try:
            data = m.history_for(conn, "booking", bid)
        finally:
            conn.close()
        s.check("the no-show is on the booking's history",
                any(e["action"] == "booking_no_show" for e in data["entries"]),
                detail="if this is empty, the route's log_audit target no "
                       "longer matches the key: "
                       + str([e["action"] for e in data["entries"]]))
        s.check("with a name against it, not 'the system'",
                any(e["action"] == "booking_no_show" and e["actor_name"]
                    for e in data["entries"]),
                detail=str([(e["action"], e["actor_name"]) for e in data["entries"]]))

        s.section("The page itself")
        page = oc.get(f"/admin/history/booking/{bid}").get_data(as_text=True)
        s.check("it renders", "What changed" in page)
        s.check("and names the action", "Booking no show" in page,
                detail="the underscores are the database's, not the reader's")
        s.check("and says what the record is", ref in page)
        row = " ".join(page.split())
        cut = row.find("Booking no show")
        s.check("and who did it, by name, in that row",
                cut >= 0 and (_owner["name"] or "no-name-on-the-owner") in row[cut:cut + 220],
                detail="the column is the whole point of the page; the owner's "
                       "own name is in the header of every admin page, so this "
                       "has to read the row: " + repr(row[cut:cut + 220]))
        s.check("a kind nobody has heard of is a 404",
                oc.get(f"/admin/history/pelican/{bid}").status_code == 404)
        s.check("and so is a record that does not exist",
                oc.get("/admin/history/booking/99999999").status_code == 404)
        s.check("an employee cannot read it",
                ec.get(f"/admin/history/booking/{bid}").status_code in (302, 403),
                detail="who changed what is the owner's question")

        s.section("An empty history is a real answer")
        # It has to be able to be empty. A panel that can never be empty is
        # furniture, and this one is empty on almost every record in the house.
        conn = db()
        conn.execute(
            """INSERT INTO vendors (name, contact_person, active, created_at)
               VALUES (?, ?, 1, ?)""",
            (TAG + " Untouched Supplier", TAG + " Nobody",
             m.datetime.now(m.timezone.utc).isoformat()))
        conn.commit()
        vid = conn.execute("SELECT id FROM vendors WHERE name = ?",
                           (TAG + " Untouched Supplier",)).fetchone()["id"]
        conn.close()
        blank = oc.get(f"/admin/history/vendor/{vid}").get_data(as_text=True)
        s.check("the page still renders", "What changed" in blank)
        s.check("and says so plainly",
                "Nothing audited has happened" in blank,
                detail="an empty table with a header is not an answer")

        s.section("And every record page can be got to it")
        booking_page = oc.get(f"/admin/bookings/{bid}/edit").get_data(as_text=True)
        s.check("from the booking",
                f"/admin/history/booking/{bid}" in booking_page,
                detail="a page nobody can reach from the thing it is about "
                       "is a page nobody opens")
        vendors_page = oc.get("/management/vendors").get_data(as_text=True)
        s.check("from the supplier list",
                f"/admin/history/vendor/{vid}" in vendors_page)
    finally:
        _cleanup()
    return s
