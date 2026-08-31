"""Everything the house holds about one person, and taking it away again.

The privacy notice has said this since it was written: "You may ask for a copy
of what we hold, ask us to correct it, ask us to delete it, ask us to stop
using it, or ask for it in a portable form." There were ZERO lines of code
behind that sentence. Keeping the promise meant somebody querying SQLite
across a dozen tables by hand and hoping they had them all.

THE HARD CLAIM IS COMPLETENESS, and it is the one this file works at. An
export that misses a table is worse than no export, because it says "this is
everything" and is not. So:

  - the tables are DERIVED from the schema, not listed, and the test adds a
    table at runtime and requires the sweep to find it;
  - and the rows that carry no address of their own — a review, a fiche, a
    line added to a stay — are followed through the booking, because searching
    for an email address would silently miss the most personal rows there are.

ERASURE IS HONEST ABOUT WHAT IT CANNOT DO. A booking is not deleted: French
accounting law requires the record of a sale, and deleting invoices to honour
a privacy request breaks one law to keep another. Those rows are anonymised —
the money stays, the person goes — and the difference is reported rather than
glossed as "we deleted everything".
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztdsr"
WHO = f"{TAG}person@example.invalid"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    for sql in (
        "DELETE FROM guest_feedback WHERE booking_id IN (SELECT id FROM bookings WHERE reference_code LIKE ?)",
        "DELETE FROM police_register WHERE booking_id IN (SELECT id FROM bookings WHERE reference_code LIKE ?)",
        "DELETE FROM booking_extras WHERE booking_id IN (SELECT id FROM bookings WHERE reference_code LIKE ?)",
    ):
        try:
            conn.execute(sql, (TAG.upper() + "%",))
        except sqlite3.OperationalError:
            pass
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG.upper() + "%",))
    for t, col in (("newsletter_subscribers", "email"), ("email_optouts", "email"),
                   ("email_outbox", "to_address"), ("guest_sessions", "email"),
                   ("restaurant_bookings", "guest_email"), ("guests", "email")):
        try:
            conn.execute(f"DELETE FROM {t} WHERE {col} = ?", (WHO,))
        except sqlite3.OperationalError:
            pass
    conn.execute("DROP TABLE IF EXISTS zzdsr_new_table")
    conn.execute("DELETE FROM audit_log WHERE action LIKE 'guest_data_%'")
    conn.commit()
    conn.close()


def _seed():
    """One person, spread across the app the way a real guest is."""
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    today = datetime.now(m.LOCAL_TZ).date()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, created_at)
           VALUES (?, ?, ?, 'Amelie Fontaine', ?, '+33611111111', ?, ?, 2,
                   'confirmed', 400, ?)""",
        (room, TAG.upper() + "STAY", TAG + "tok", WHO, (today - timedelta(days=9)).isoformat(),
         (today - timedelta(days=6)).isoformat(), now))
    conn.execute(
        "INSERT INTO newsletter_subscribers (email, token, created_at, confirmed_at) "
        "VALUES (?, ?, ?, ?)", (WHO, TAG + "nl", now, now))
    conn.execute(
        "INSERT INTO email_outbox (to_address, subject, body, reason, created_at) "
        "VALUES (?, 'Your stay', 'body', 'no provider', ?)", (WHO, now))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (TAG.upper() + "STAY",)).fetchone()["id"]
    # Rows with no address of their own — the ones a naive export misses.
    conn.execute(
        """INSERT INTO guest_feedback (booking_id, guest_name, rating, comment, submitted_at)
           VALUES (?, 'Amelie Fontaine', 4, 'Lovely, if cold', ?)""", (bid, now))
    conn.execute(
        """INSERT INTO police_register (booking_id, surname, first_names, nationality,
           recorded_at) VALUES (?, 'Fontaine', 'Amelie', 'Australian', ?)""", (bid, now))
    conn.commit()
    conn.close()
    return bid


def run():
    s = Suite("What we hold about you")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    bid = _seed()

    s.section("Where it looks is read from the schema, not from a list")
    conn = db()
    with m.app.test_request_context():
        before = set(m.guest_data_tables(conn))
    # A table added at runtime. A hand-kept list would not know about it, and
    # a table added next year is exactly the case that makes an export a lie.
    conn.execute("CREATE TABLE zzdsr_new_table (id INTEGER PRIMARY KEY, email TEXT, note TEXT)")
    conn.execute("INSERT INTO zzdsr_new_table (email, note) VALUES (?, 'something new')",
                 (WHO,))
    conn.commit()
    with m.app.test_request_context():
        after = set(m.guest_data_tables(conn))
    conn.close()
    s.check("it already searches the tables that exist", len(before) > 8,
            detail=f"{len(before)} tables")
    s.check("and a table added afterwards is found without anybody listing it",
            "zzdsr_new_table" in after and "zzdsr_new_table" not in before,
            detail="a list is right on the day it is written and wrong the "
                   "first time somebody adds a table")
    s.check("staff are not answered by a guest's request",
            "users" not in after,
            detail="a staff record is a different process with different rules")
    s.check("nor are job applicants or suppliers",
            "candidates" not in after and "vendors" not in after)

    s.section("A copy of what we hold")
    conn = db()
    with m.app.test_request_context():
        export = m.guest_data_export(conn, WHO)
    conn.close()
    tables = export["tables"]
    s.check("the stay is in it", "bookings" in tables)
    s.check("the newsletter subscription is in it", "newsletter_subscribers" in tables)
    s.check("mail we sent them is in it", "email_outbox" in tables)
    s.check("and the table added a moment ago is in it", "zzdsr_new_table" in tables,
            detail=str(sorted(tables))[:120])

    # The half a naive export misses. None of these carries an email address.
    s.check("their review is in it, which carries no address of its own",
            "guest_feedback" in tables,
            detail="followed through the booking, because searching for an "
                   "email would miss the most personal rows there are")
    s.check("and their police fiche", "police_register" in tables,
            detail="date and place of birth, home address, nationality")

    s.check("it says how many rows", export["row_count"] >= 6,
            detail=str(export["row_count"]))
    s.check("and names where it looked, rather than implying it looked everywhere",
            len(export["searched"]) == len(after),
            detail="'we searched for your email' and 'we found everything about "
                   "you' are different claims")

    s.section("Somebody else's data is not in it")
    other = _one("SELECT guest_email FROM bookings WHERE guest_email IS NOT NULL "
                 "AND guest_email != '' AND guest_email != ? LIMIT 1", (WHO,))
    if other:
        conn = db()
        with m.app.test_request_context():
            theirs = m.guest_data_export(conn, other["guest_email"])
        conn.close()
        addresses = {str(v).lower() for rows in theirs["tables"].values()
                     for row in rows for v in row.values()}
        s.check("a request for one person returns only theirs",
                WHO not in addresses,
                detail="an export is sent to the person who asked; another "
                       "guest's address in it is a breach, not a bug")

    s.section("The portable form the notice promises")
    r = oc.get(f"/admin/data-requests/export.json?email={WHO}")
    s.check("it downloads", r.status_code == 200, detail=str(r.status_code))
    s.check("as a file, not a page",
            "attachment" in r.headers.get("Content-Disposition", ""),
            detail=r.headers.get("Content-Disposition", ""))
    s.check("and it is JSON a machine can read",
            r.headers.get("Content-Type", "").startswith("application/json"))
    payload = json.loads(r.get_data(as_text=True))
    s.check("carrying the same rows the page showed",
            payload["row_count"] == export["row_count"],
            detail=f"{payload['row_count']} vs {export['row_count']}")
    s.check("and the download is on the record",
            _one("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'guest_data_exported' "
                 "AND target = ?", (WHO,))["c"] == 1,
            detail="handing somebody a copy of everything is worth writing down")

    s.section("Erasure keeps what the law says to keep")
    conn = db()
    with m.app.test_request_context():
        result = m.guest_data_erase(conn, WHO)
    conn.commit()
    conn.close()
    s.check("the newsletter subscription is deleted",
            "newsletter_subscribers" in result["deleted"],
            detail=str(result["deleted"]))
    s.check("and so is the table with no legal reason to keep it",
            "zzdsr_new_table" in result["deleted"])
    s.check("the booking is NOT deleted", "bookings" not in result["deleted"],
            detail="deleting the record of a sale to honour a privacy request "
                   "breaks one law to keep another")
    s.check("it is anonymised instead", "bookings" in result["anonymised"],
            detail=str(result["anonymised"]))

    row = _one("SELECT * FROM bookings WHERE reference_code = ?", (TAG.upper() + "STAY",))
    s.check("the stay is still there", row is not None)
    s.check("with the money on it", row and abs((row["total_price"] or 0) - 400) < 0.01,
            detail="the books have to still add up afterwards")
    s.check("and no name", row and row["guest_name"] == m.ERASED_MARKER,
            detail=str(row["guest_name"]) if row else "")
    s.check("no address", row and not row["guest_email"])
    s.check("and no telephone number", row and not row["guest_phone"])

    s.check("the fiche is gone entirely",
            _one("SELECT COUNT(*) AS c FROM police_register WHERE booking_id = ?",
                 (bid,))["c"] == 0,
            detail="it is held under its own obligation with its own clock, "
                   "not under the accounting rules")

    s.section("And it says which is which rather than 'all done'")
    s.check("it reports what was deleted", bool(result["deleted"]))
    s.check("and what was kept", bool(result["anonymised"]))
    s.check("and why it was kept", "accounting law" in result["kept_because"],
            detail="'we have deleted everything' would not be true, and the "
                   "person is entitled to know what remains")

    conn = db()
    with m.app.test_request_context():
        nothing_left = m.guest_data_export(conn, WHO)
    conn.close()
    s.check("a second look finds nothing under that address",
            nothing_left["row_count"] == 0,
            detail=f"{nothing_left['row_count']} rows still answer to the address")

    s.section("Erasing is not something to do by accident")
    conn = db()
    conn.execute(
        "INSERT INTO newsletter_subscribers (email, token, created_at, confirmed_at) "
        "VALUES (?, ?, ?, ?)",
        (WHO, TAG + "nl2", datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    r = oc.post("/admin/data-requests/erase",
                data={"email": WHO, "confirm_email": "something-else@example.invalid"},
                follow_redirects=True)
    s.check("a mistyped confirmation stops it",
            any("type the address again" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("and nothing was erased",
            _one("SELECT COUNT(*) AS c FROM newsletter_subscribers WHERE email = ?",
                 (WHO,))["c"] == 1,
            detail="the confirmation is retyping the address rather than a "
                   "checkbox, because a checkbox is ticked while reading on")

    r = oc.post("/admin/data-requests/erase",
                data={"email": WHO, "confirm_email": WHO}, follow_redirects=True)
    s.check("typing it correctly does erase", _one(
        "SELECT COUNT(*) AS c FROM newsletter_subscribers WHERE email = ?",
        (WHO,))["c"] == 0, detail=str(flashes(r)))
    s.check("and it says what happened in plain numbers",
            any("deleted" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    # The audit row outlives the data it is about, so writing the address into
    # it would undo the erasure by hand.
    logged = _one("SELECT * FROM audit_log WHERE action = 'guest_data_erased' "
                  "ORDER BY id DESC LIMIT 1")
    s.check("the erasure is on the record", logged is not None)
    s.check("without the address in it",
            logged and WHO not in ((logged["target"] or "") + (logged["details"] or "")),
            detail="an audit row is never purged, so a name in it survives the "
                   "erasure it is recording")

    s.section("Who may ask")
    r = ec.get(f"/admin/data-requests?email={WHO}", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    r = anon.get(f"/admin/data-requests/export.json?email={WHO}", follow_redirects=False)
    s.check("nor can a stranger download somebody's data",
            r.status_code in (302, 303, 401, 403), detail=f"HTTP {r.status_code}")
    r = ec.post("/admin/data-requests/erase",
                data={"email": WHO, "confirm_email": WHO}, follow_redirects=False)
    s.check("nor erase anybody", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    r = oc.get("/admin/data-requests")
    s.check("the owner can", r.status_code == 200, detail=str(r.status_code))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
