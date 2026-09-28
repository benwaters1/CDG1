"""Tags on a person, many at once, the guest list as a file, and one figure for
what somebody has spent.

What there was: the words the house used for people -- "wine", "press",
"comes every June" -- lived as prose in the notes, which no list could filter
by. Doing anything to several guests meant opening each. The guest list could
not be taken away as a file. And the record added up what somebody had spent
by a second rule of its own: it left out what a cancellation kept and what the
house owed back on a stay it called off, so the record could say nothing was
owed while the statement said the house owed them; and it counted a table's
deposit twice, once on the statement and again in "at the table".

What this holds:

  - A tag goes on and comes off a profile, kept plainly and once, on the
    history with who did it; a colleague cannot.
  - The list filters by tag -- a guest with two tags is under both chips --
    and finds a tag by search.
  - Many ticked are tagged at once, and anything not done is named with why.
  - The list downloads as a file of exactly what is on screen, with what each
    has spent and owes from their statement; on the audit trail by how many,
    never by who; and not for a colleague.
  - The record's figures are the statement's, and "at the table" is the rest
    of the evening, beyond what was paid towards it.
  - A merge carries the tags; a request for their data carries them; erasing
    them takes them.
"""
import csv
import io
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZTG"


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        for table in ("guest_tags", "guest_profile_changes", "consent_events", "guest_notes"):
            conn.execute(f"DELETE FROM {table} WHERE guest_id IN "
                         "(SELECT id FROM guests WHERE name LIKE ?)", like)
        conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                     "(SELECT id FROM bookings WHERE reference_code LIKE ?)", like)
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM audit_log WHERE action = 'guests_exported' AND details LIKE ?",
                     ("%tag=" + TAG.lower() + "%",))
        conn.execute("UPDATE guests SET merged_into_id = NULL WHERE name LIKE ?", like)
        conn.execute("DELETE FROM guests WHERE name LIKE ?", like)
        conn.commit()
    finally:
        conn.close()


def _one(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _guest(name, email=None):
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} {name}", email, _harness.datetime_now()))
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return gid


def _tags(gid):
    conn = db()
    try:
        return [r[0] for r in conn.execute(
            "SELECT tag FROM guest_tags WHERE guest_id = ? ORDER BY tag", (gid,)).fetchall()]
    finally:
        conn.close()


def run():
    s = Suite("Tags and the guest list")
    oc, ec, owner, _emp = clients()
    _cleanup()
    try:
        _run(s, oc, ec, owner)
    finally:
        _cleanup()
    return s


def _run(s, oc, ec, owner):
    wine = f"{TAG.lower()} wine"
    press = f"{TAG.lower()} press"
    a = _guest("Alice", f"{TAG.lower()}.alice@example.invalid")
    b = _guest("Bruno", f"{TAG.lower()}.bruno@example.invalid")
    c = _guest("Chloe", f"{TAG.lower()}.chloe@example.invalid")

    s.section("A tag on a person")
    oc.post(f"/guests/{a}/tags", data={"tag": f"  {TAG}   WINE "})
    s.check("it goes on, kept plainly", _tags(a) == [wine], detail=str(_tags(a)))
    r = oc.post(f"/guests/{a}/tags", data={"tag": wine}, follow_redirects=True)
    s.check("once, however often it is put on",
            _tags(a) == [wine] and "already" in " ".join(flashes(r)), detail=str(flashes(r)))
    conn = db()
    lines = [x["title"] for x in m.guest_timeline(conn, a) if x["kind"] == "Profile"]
    conn.close()
    s.check("and it is on their history, with who put it there",
            any(t.startswith(f"Tag: nothing → {wine}") and owner["name"] in t for t in lines),
            detail=str(lines))
    # By the chip itself: the record's history names the tag too, so a check
    # that read the whole page passed with the chips gone.
    record = oc.get(f"/guests/{a}").get_data(as_text=True)
    s.check("the record shows it, as a tag that can be taken off",
            f'name="remove" value="{wine}"' in record)
    oc.post(f"/guests/{a}/tags", data={"tag": press})
    oc.post(f"/guests/{a}/tags", data={"remove": press})
    s.check("and it comes off", _tags(a) == [wine], detail=str(_tags(a)))
    r = ec.post(f"/guests/{a}/tags", data={"tag": "colleague"})
    s.check("a colleague cannot tag anybody",
            r.status_code in (302, 403) and _tags(a) == [wine])

    s.section("Many at once")
    merged = _guest("Merged away")
    conn = db()
    conn.execute("UPDATE guests SET merged_into_id = ? WHERE id = ?", (c, merged))
    conn.commit()
    conn.close()
    r = oc.post("/guests/bulk-tag", data={"tag": wine, "back": "/guests",
                                          "guest_ids": [str(a), str(b), str(merged), "99999999"]},
                follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("the ticked are tagged", _tags(b) == [wine], detail=said)
    s.check("and what was not done is named, with why",
            "already tagged" in said and f"{TAG} Alice" in said
            and "merged into another profile" in said and "no such profile" in said,
            detail=said)
    oc.post(f"/guests/{b}/tags", data={"tag": press})

    s.section("The list, by tag")
    page = visible_text(oc.get("/guests", query_string={"q": TAG}).get_data(as_text=True))
    s.check("the chips count everybody under each tag",
            f"{wine} 2" in page and f"{press} 1" in page, detail=page[:600])
    only = visible_text(oc.get("/guests", query_string={"tag": press}).get_data(as_text=True))
    s.check("choosing a tag shows only who carries it",
            f"{TAG} Bruno" in only and f"{TAG} Alice" not in only)
    found = visible_text(oc.get("/guests", query_string={"q": press}).get_data(as_text=True))
    s.check("and a search finds a tag", f"{TAG} Bruno" in found and f"{TAG} Chloe" not in found)

    s.section("The list as a file")
    room = _harness.ensure_room()
    arrive = house_today() + timedelta(days=640)
    conn = db()
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    created_at, linked_guest_id, cancel_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'cancelled', 600, ?, ?, 'house_could_not')""",
                 (room["id"], f"{TAG}HC", f"tokhc{TAG}".lower(), f"{TAG} Bruno",
                  f"{TAG.lower()}.bruno@example.invalid", arrive.isoformat(),
                  (arrive + timedelta(days=2)).isoformat(), _harness.datetime_now(), b))
    hc = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}HC",)).fetchone()["id"]
    conn.execute("INSERT INTO booking_payments (booking_id, amount, method, created_at) "
                 "VALUES (?, 300, 'cash', ?)", (hc, _harness.datetime_now()))
    conn.execute("UPDATE bookings SET amount_paid = 300 WHERE id = ?", (hc,))
    conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                    guest_email, dinner_date, party_size, status, total_price, deposit_amount,
                    payment_status, created_at)
                    VALUES (?, ?, ?, ?, ?, 4, 'confirmed', 200, 50, 'paid', ?)""",
                 (f"{TAG}D", f"tokd{TAG}".lower(), f"{TAG} Bruno",
                  f"{TAG.lower()}.bruno@example.invalid",
                  (house_today() - timedelta(days=3)).isoformat(), _harness.datetime_now()))
    conn.commit()
    with m.app.test_request_context("/"):
        st = m.guest_account_statement(conn, b)
        rec = m.guest_record(conn, b)
    conn.close()
    r = oc.get("/admin/guests/export.csv", query_string={"tag": press})
    rows = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
    s.check("it is exactly the view on screen",
            [x["name"] for x in rows] == [f"{TAG} Bruno"], detail=str([x["name"] for x in rows]))
    s.check("with what they have spent and owe, from their statement",
            rows and float(rows[0]["spent_with_us"]) == st["lifetime_charged"]
            and float(rows[0]["balance"]) == round(st["balance"], 2)
            and press in rows[0]["tags"], detail=str(rows[:1]))
    audit = _one("SELECT details FROM audit_log WHERE action = 'guests_exported' "
                 "ORDER BY id DESC LIMIT 1")
    s.check("on the audit trail by how many, never by who",
            audit and "1 profile" in audit[0] and "@" not in audit[0], detail=str(audit and audit[0]))
    s.check("and not for a colleague", ec.get("/admin/guests/export.csv").status_code in (302, 403))
    page = oc.get("/guests", query_string={"tag": press}).get_data(as_text=True)
    s.check("the page's own button downloads the view it is on",
            "/admin/guests/export.csv?tag=" in page)

    s.section("One figure for what somebody has spent")
    s.check("the record's spent is the statement's",
            abs(rec["spent"] - st["lifetime_charged"]) < 0.005,
            detail=f"record {rec['spent']}, statement {st['lifetime_charged']}")
    s.check("and so is what is owed, credit and all -- a stay the house called off owes "
            "them what they paid",
            abs(rec["owed"] - st["balance"]) < 0.005 and rec["owed"] <= -299.99,
            detail=f"record {rec['owed']}, statement {st['balance']}")
    s.check("the rest of the evening at the table is counted once, beyond the deposit",
            abs(rec["at_table"] - 150) < 0.005, detail=str(rec["at_table"]))
    page = visible_text(oc.get(f"/guests/{b}").get_data(as_text=True))
    s.check("and the record says they are in credit", "In credit" in page)

    s.section("A merge carries them, and so does a request for their data")
    keep = _guest("Keeper", f"{TAG.lower()}.keeper@example.invalid")
    oc.post(f"/guests/{keep}/tags", data={"tag": wine})
    oc.post(f"/guests/{keep}/merge", data={"merge_id": str(b)})
    s.check("the survivor carries both, once each", _tags(keep) == sorted([press, wine]),
            detail=str(_tags(keep)))
    s.check("and the absorbed profile none", _tags(b) == [])
    conn = db()
    export = m.guest_data_export(conn, f"{TAG.lower()}.keeper@example.invalid")
    conn.close()
    s.check("a request for their data carries their tags",
            {r.get("tag") for r in (export or {}).get("tables", {}).get("guest_tags", [])}
            == {press, wine})
    conn = db()
    m.guest_data_erase(conn, f"{TAG.lower()}.keeper@example.invalid")
    conn.commit()
    conn.close()
    s.check("and erasing them takes them",
            not _one("SELECT id FROM guest_tags WHERE guest_id = ?", keep))
