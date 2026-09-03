"""The delete that actually deletes.

Eight destructive routes the suite reached and never got an answer out of.
Each was already covered on paper: a test posts to it as somebody who is not
allowed and gets a 403, or posts an id that does not exist and gets a 404.
Both are worth proving. Neither runs the delete.

So for every one of them the coverage figure said "tested" about a route
whose working branch had never executed once, and the figure was 100%.

WHAT IS CHECKED, beyond the row going:

  A NEIGHBOUR SURVIVES. Every one of these is `DELETE FROM x WHERE id = ?`,
  and the failure mode of that line is the day somebody edits it and the
  WHERE goes. A test that only checks the target is gone passes just as
  happily when the table has been emptied.

  THE FILE GOES WITH IT, for the two that hold one -- and another row's file
  does not. A receipt deleted from the database while the scan stays on disk
  is the half-delete nobody notices until an audit.

  AND THE TWO THAT REFUSE STILL REFUSE. A dish on a published card and a
  promo code somebody has already used are both kept deliberately: one so the
  printed card still matches the kitchen, the other so the redemption history
  stays intact. Those branches are the reason the delete is not a delete, and
  they had never run either.
"""
import os

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZDEL"


def _cleanup(conn):
    conn.execute("DELETE FROM cash_bankings WHERE reference LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM documents WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_dishes WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menus WHERE notes LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM police_register WHERE surname LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_photos WHERE filename LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rota_template_shifts WHERE role_note LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM rota_templates WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _mkfile(name):
    """A real file in the scratch uploads folder, not a name in a column.

    The harness asserts UPLOAD_DIR is the scratch copy at import, so this
    cannot reach the château's own receipts.
    """
    path = os.path.join(m.UPLOAD_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("test receipt")
    return path


def run():
    s = Suite("deletes that actually delete")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = house_today().isoformat()

    def rowid():
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def gone(table, row_id):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id = ?",
                            (row_id,)).fetchone()[0] == 0

    # ---------------------------------------------------------------- money
    s.section("A cash banking, and only that one")
    ids = []
    for n in ("one", "two"):
        conn.execute(
            "INSERT INTO cash_bankings (banked_on, amount, reference, created_at) "
            "VALUES (?, ?, ?, ?)", (today, 120.0, TAG + n, now))
        ids.append(rowid())
    conn.commit()
    oc.post(f"/management/cash-banking/{ids[0]}/delete", follow_redirects=True)
    s.check("the banking is gone", gone("cash_bankings", ids[0]))
    s.check("and the other one is not", not gone("cash_bankings", ids[1]),
            detail="the WHERE clause is the whole safety of this route")

    s.section("An expense, and the receipt with it")
    keep_file, go_file = TAG + "-keep.txt", TAG + "-go.txt"
    _mkfile(keep_file)
    _mkfile(go_file)
    exp = []
    for fn in (go_file, keep_file):
        conn.execute(
            "INSERT INTO expenses (kind, description, amount, filename, "
            "submitted_at) VALUES ('supplier_invoice', ?, 40.0, ?, ?)",
            (TAG + fn, fn, now))
        exp.append(rowid())
    conn.commit()
    oc.post(f"/expenses/{exp[0]}/delete", follow_redirects=True)
    s.check("the expense row is gone", gone("expenses", exp[0]))
    s.check("and its receipt is off the disk",
            not os.path.exists(os.path.join(m.UPLOAD_DIR, go_file)),
            detail="a row deleted while the scan stays is the half-delete "
                   "nobody notices until somebody audits the folder")
    s.check("the other expense survives", not gone("expenses", exp[1]))
    s.check("and so does its receipt",
            os.path.exists(os.path.join(m.UPLOAD_DIR, keep_file)))

    # ------------------------------------------------------------- people
    s.section("A document, by its owner and by nobody else")
    doc_file = TAG + "-doc.txt"
    _mkfile(doc_file)
    conn.execute(
        "INSERT INTO documents (user_id, title, filename, uploaded_at) "
        "VALUES (?, ?, ?, ?)", (emp["id"], TAG + " contract", doc_file, now))
    doc_id = rowid()
    conn.commit()
    r = ec.post(f"/documents/{doc_id}/delete", follow_redirects=True)
    s.check("the person it belongs to may delete it", gone("documents", doc_id),
            detail=f"status {r.status_code}")
    s.check("and the file goes too",
            not os.path.exists(os.path.join(m.UPLOAD_DIR, doc_file)))

    other_file = TAG + "-other.txt"
    _mkfile(other_file)
    conn.execute(
        "INSERT INTO documents (user_id, title, filename, uploaded_at) "
        "VALUES (?, ?, ?, ?)", (owner["id"], TAG + " owner doc", other_file, now))
    other_doc = rowid()
    conn.commit()
    r = ec.post(f"/documents/{other_doc}/delete", follow_redirects=False)
    s.check("somebody else's is refused", r.status_code == 403,
            detail=f"status {r.status_code}")
    s.check("and it is still there", not gone("documents", other_doc))
    s.check("with its file untouched",
            os.path.exists(os.path.join(m.UPLOAD_DIR, other_file)),
            detail="the refusal happens before the unlink, and the order is "
                   "the whole of it")

    s.section("A police register entry")
    booking = conn.execute(
        "SELECT id FROM bookings ORDER BY id DESC LIMIT 1").fetchone()
    if booking:
        conn.execute(
            "INSERT INTO police_register (booking_id, surname, first_names, "
            "nationality, recorded_at) VALUES (?, ?, 'A', 'FR', ?)",
            (booking["id"], TAG + "Fiche", now))
        fiche = rowid()
        conn.execute(
            "INSERT INTO police_register (booking_id, surname, first_names, "
            "nationality, recorded_at) VALUES (?, ?, 'B', 'FR', ?)",
            (booking["id"], TAG + "Keep", now))
        fiche_keep = rowid()
        conn.commit()
        oc.post(f"/admin/register/{fiche}/delete", follow_redirects=True)
        s.check("the entry is gone", gone("police_register", fiche))
        s.check("and the rest of the register is not",
                not gone("police_register", fiche_keep))
    else:
        s.check("a booking exists to attach a fiche to", False,
                detail="reported rather than skipped: the checks below would "
                       "pass on nothing")

    # ------------------------------------------------------------- refusals
    s.section("A dish comes off an unpublished card, and not a published one")
    conn.execute(
        "INSERT INTO menus (service_date, service, status, notes, created_at) "
        "VALUES (?, 'dinner', 'draft', ?, ?)", (today, TAG + " draft", now))
    draft_menu = rowid()
    conn.execute(
        "INSERT INTO menus (service_date, service, status, notes, created_at) "
        "VALUES (?, 'lunch', 'published', ?, ?)", (today, TAG + " live", now))
    live_menu = rowid()
    conn.execute("INSERT INTO menu_dishes (menu_id, name, created_at) "
                 "VALUES (?, ?, ?)", (draft_menu, TAG + " draft dish", now))
    draft_dish = rowid()
    conn.execute("INSERT INTO menu_dishes (menu_id, name, created_at) "
                 "VALUES (?, ?, ?)", (live_menu, TAG + " live dish", now))
    live_dish = rowid()
    conn.commit()

    oc.post(f"/admin/restaurant/menu/dish/{draft_dish}/delete",
            follow_redirects=True)
    s.check("the draft dish goes", gone("menu_dishes", draft_dish))

    r = oc.post(f"/admin/restaurant/menu/dish/{live_dish}/delete",
                follow_redirects=True)
    s.check("the published one does not", not gone("menu_dishes", live_dish),
            detail="the printed card and the kitchen have to agree")
    s.check("and it says to take it off instead",
            "take the dish off instead" in r.get_data(as_text=True),
            detail="a refusal with no instruction is a page somebody retries")

    s.section("A promo code goes only while nobody has used it")
    conn.execute(
        "INSERT INTO promo_codes (code, discount_value, redemption_count, "
        "created_at) VALUES (?, 10, 0, ?)", (TAG + "FRESH", now))
    fresh = rowid()
    conn.execute(
        "INSERT INTO promo_codes (code, discount_value, redemption_count, "
        "created_at) VALUES (?, 10, 3, ?)", (TAG + "USED", now))
    used = rowid()
    conn.commit()
    oc.post(f"/admin/promo-codes/{fresh}/delete", follow_redirects=True)
    s.check("an unused code goes", gone("promo_codes", fresh))
    r = oc.post(f"/admin/promo-codes/{used}/delete", follow_redirects=True)
    s.check("a redeemed one stays", not gone("promo_codes", used),
            detail="deleting it takes the discount off three bookings that "
                   "were charged with it")
    s.check("and it says to deactivate instead",
            "deactivate it instead" in r.get_data(as_text=True))

    # --------------------------------------------------------------- rooms
    s.section("A room photo, and the room it belongs to")
    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 2").fetchall()
    if len(room) >= 2:
        a_room, b_room = room[0]["id"], room[1]["id"]
        conn.execute("INSERT INTO room_photos (room_id, filename, created_at) "
                     "VALUES (?, ?, ?)", (a_room, TAG + "photo.jpg", now))
        photo = rowid()
        conn.commit()
        r = oc.post(f"/admin/rooms/{b_room}/photos/{photo}/delete",
                    follow_redirects=False)
        s.check("asking under the wrong room is refused",
                r.status_code == 404, detail=f"status {r.status_code}")
        s.check("and the photo is still there", not gone("room_photos", photo),
                detail="the route takes both ids and the SELECT uses both; "
                       "dropping the room from it would delete another "
                       "room's photograph on a guessed number")
        oc.post(f"/admin/rooms/{a_room}/photos/{photo}/delete",
                follow_redirects=True)
        s.check("under its own room it goes", gone("room_photos", photo))
    else:
        s.check("two rooms exist to tell apart", False,
                detail="reported rather than skipped")

    s.section("A line off a rota template")
    conn.execute("INSERT INTO rota_templates (name, created_at) VALUES (?, ?)",
                 (TAG + " template", now))
    tpl = rowid()
    line_ids = []
    for day in (1, 2):
        conn.execute(
            "INSERT INTO rota_template_shifts (template_id, user_id, weekday, "
            "role_note, created_at) VALUES (?, ?, ?, ?, ?)",
            (tpl, emp["id"], day, TAG + "line", now))
        line_ids.append(rowid())
    conn.commit()
    oc.post(f"/admin/rota-templates/line/{line_ids[0]}/delete",
            follow_redirects=True)
    s.check("the line goes", gone("rota_template_shifts", line_ids[0]))
    s.check("and the rest of the template stands",
            not gone("rota_template_shifts", line_ids[1]),
            detail="deleting a line is not deleting the template")

    for name in (keep_file, go_file, doc_file, other_file):
        try:
            os.remove(os.path.join(m.UPLOAD_DIR, name))
        except OSError:
            pass
    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
