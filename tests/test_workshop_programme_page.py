"""The workshop manager: what is coming up first, a page per programme, and a
sitting changed on its own page.

The owner, 19 October 2026: the workshop and event manager was "impossible to
use". Every programme was a card holding its description, its sittings, the
stock it uses and its registration questions, so what was coming up meant
reading every card in turn. A sitting's dates could not be changed from
anywhere, nor its minimum or the day it is decided once it was made; its
places, its teaching link and calling it off were folds on the workshops page,
not on the sitting's own; and every one of those actions threw the owner back
to the top of the workshops page.

Held here:

  - the workshops page leads with every programme's sittings in one list by
    date, and keeps each programme's setup off it;
  - each programme has a page, with its sittings, its stock and its questions,
    and the actions taken there come back there;
  - a sitting's dates, places, minimum, decision day and note are changed on
    its own page, checked, and the page comes back to itself; a move names
    what else is on the new dates;
  - the places box on the workshops page still leaves the minimum alone.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZPROG"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_bookings WHERE session_id IN (SELECT id FROM workshop_sessions "
                 "WHERE workshop_id IN (SELECT id FROM workshops WHERE title LIKE ?))", (TAG + "%",))
    conn.execute("DELETE FROM workshop_materials WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_custom_fields WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _workshop(conn, name):
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                    active, created_at) VALUES (?, 'x', 1500, 8, 1, ?)""", (f"{TAG} {name}", _now()))
    return conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} {name}",)).fetchone()["id"]


def _sitting(conn, wid, start, days=3, minimum=0):
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
                    min_participants, decide_by_days, created_at) VALUES (?, ?, ?, 8, ?, 21, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=days)).isoformat(), minimum, _now()))
    return conn.execute("SELECT MAX(id) AS i FROM workshop_sessions WHERE workshop_id = ?",
                        (wid,)).fetchone()["i"]


def _session(sid):
    conn = db()
    try:
        return conn.execute("SELECT * FROM workshop_sessions WHERE id = ?", (sid,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("the workshop manager: coming up first, a page per programme")
    oc, ec, _owner, _emp = clients()
    _cleanup()
    today = house_today()
    try:
        conn = db()
        a = _workshop(conn, "Plaster")
        b = _workshop(conn, "Gilding")
        a_late = _sitting(conn, a, today + timedelta(days=300))
        b_mid = _sitting(conn, b, today + timedelta(days=250))
        a_soon = _sitting(conn, a, today + timedelta(days=200))
        past = _sitting(conn, a, today - timedelta(days=60))
        conn.execute("""INSERT INTO stock_items (name, category, unit, active, created_at)
                        VALUES (?, 'other', 'each', 1, ?)""", (f"{TAG} Aprons", _now()))
        conn.commit()
        conn.close()

        s.section("The workshops page leads with what is coming up")
        page = oc.get("/admin/workshops").get_data(as_text=True)
        coming = page[page.find("Coming up"):page.find("Programmes</h2>")]
        at = [coming.find(f"/admin/workshops/sessions/{sid}\"") for sid in (a_soon, b_mid, a_late)]
        s.check("every programme's sittings in one list, in date order",
                all(x > 0 for x in at) and at == sorted(at), detail=str(at))
        s.check("a sitting already run is not in it",
                f"/admin/workshops/sessions/{past}\"" not in coming)
        s.check("each programme opens its own page", f'href="/admin/workshops/{a}"' in page
                and f'href="/admin/workshops/{b}"' in page)
        s.check("and its stock and questions are not on the workshops page",
                f"/admin/workshops/{a}/materials" not in page
                and f"/admin/workshops/{a}/custom-fields/new" not in page)
        s.check("adding a sitting still is, folded", f"/admin/workshops/{a}/sessions/new" in page)

        s.section("A programme's page")
        r = oc.get(f"/admin/workshops/{a}")
        prog = r.get_data(as_text=True)
        s.check("it opens for the owner", r.status_code == 200, r)
        s.check("staff are turned away", ec.get(f"/admin/workshops/{a}").status_code in (302, 403))
        s.check("a programme that is not there is a 404", oc.get("/admin/workshops/999999").status_code == 404)
        s.check("it lists its sittings to come, and keeps the past ones under a fold",
                f"/admin/workshops/sessions/{a_soon}" in prog and f"/admin/workshops/sessions/{a_late}" in prog
                and "Past sittings (1)" in prog and f"/admin/workshops/sessions/{b_mid}" not in prog)
        s.check("and carries its stock and its questions",
                f"/admin/workshops/{a}/materials" in prog and f"/admin/workshops/{a}/custom-fields/new" in prog
                and f"{TAG} Aprons" in prog)
        conn = db()
        item = conn.execute("SELECT id FROM stock_items WHERE name = ?", (f"{TAG} Aprons",)).fetchone()["id"]
        conn.close()
        r = oc.post(f"/admin/workshops/{a}/materials", data={
            "stock_item_id": str(item), "qty_per_person": "1", "back_workshop": str(a)})
        s.check("stock added there comes back there",
                r.headers.get("Location", "").endswith(f"/admin/workshops/{a}"), detail=r.headers.get("Location"))
        r = oc.post(f"/admin/workshops/{a}/custom-fields/new", data={
            "label": "Shoe size", "field_type": "text", "back_workshop": str(a)})
        s.check("and so does a question", r.headers.get("Location", "").endswith(f"/admin/workshops/{a}"),
                detail=r.headers.get("Location"))
        r = oc.post(f"/admin/workshops/{a}/sessions/new", data={
            "start_date": (today + timedelta(days=400)).isoformat(), "back_workshop": str(a)})
        s.check("and a sitting added there", r.headers.get("Location", "").endswith(f"/admin/workshops/{a}"),
                detail=r.headers.get("Location"))
        r = oc.post(f"/admin/workshops/{a}/sessions/new", data={
            "start_date": (today + timedelta(days=401)).isoformat()})
        s.check("added from the workshops page, it goes back there",
                r.headers.get("Location", "").endswith("/admin/workshops"), detail=r.headers.get("Location"))
        r = oc.post(f"/admin/workshops/{a}/edit", data={"title": f"{TAG} Plaster", "price_per_person": "1500",
                                                        "active": "on"})
        s.check("saving its details comes back to its page",
                r.headers.get("Location", "").endswith(f"/admin/workshops/{a}"), detail=r.headers.get("Location"))
        r = oc.post("/admin/workshops/new", data={"title": f"{TAG} Fresh"})
        conn = db()
        fresh = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Fresh",)).fetchone()
        conn.close()
        s.check("a new programme opens on its own page, to add its first sitting",
                fresh and r.headers.get("Location", "").endswith(f"/admin/workshops/{fresh['id']}"),
                detail=r.headers.get("Location"))

        s.section("A sitting is changed on its own page")
        sit_page = oc.get(f"/admin/workshops/sessions/{a_soon}").get_data(as_text=True)
        s.check("it has the form for its dates, places, minimum and decision day",
                "Change this sitting" in sit_page
                and f'action="/admin/workshops/sessions/{a_soon}/edit"' in sit_page
                and 'name="min_participants"' in sit_page and 'name="decide_by_days"' in sit_page)
        s.check("and the teaching link, calling it off and removing it",
                f"/admin/workshops/session/{a_soon}/instructor-link" in sit_page
                and f"/admin/workshops/session/{a_soon}/call-off" in sit_page
                and f"/admin/workshops/sessions/{a_soon}/delete" in sit_page)
        start = today + timedelta(days=210)
        r = oc.post(f"/admin/workshops/sessions/{a_soon}/edit", data={
            "start_date": start.isoformat(), "end_date": (start + timedelta(days=2)).isoformat(),
            "capacity": "10", "min_participants": "4", "decide_by_days": "30", "notes": "Bring old clothes",
            "back_session": str(a_soon)})
        row = _session(a_soon)
        s.check("its dates, places, minimum, decision day and note all change",
                row["start_date"] == start.isoformat() and row["capacity"] == 10
                and row["min_participants"] == 4 and row["decide_by_days"] == 30
                and row["notes"] == "Bring old clothes", detail=str(dict(row)))
        s.check("and the page comes back to itself",
                r.headers.get("Location", "").endswith(f"/admin/workshops/sessions/{a_soon}"),
                detail=r.headers.get("Location"))
        oc.post(f"/admin/workshops/sessions/{a_soon}/edit", data={
            "start_date": start.isoformat(), "end_date": (start + timedelta(days=2)).isoformat(),
            "capacity": "6", "min_participants": "9", "back_session": str(a_soon)})
        s.check("a minimum above the places is held to the places",
                _session(a_soon)["min_participants"] == 6, detail=str(_session(a_soon)["min_participants"]))
        said = " ".join(flashes(oc.post(f"/admin/workshops/sessions/{a_soon}/edit", data={
            "start_date": start.isoformat(), "capacity": "6", "min_participants": "lots",
            "back_session": str(a_soon)}, follow_redirects=True)))
        s.check("a minimum that is not a number is refused, and nothing changes",
                "whole number" in said and _session(a_soon)["min_participants"] == 6, detail=said)
        oc.post(f"/admin/workshops/sessions/{a_soon}/edit", data={
            "start_date": start.isoformat(), "end_date": (start + timedelta(days=2)).isoformat(),
            "capacity": "7", "notes": "Bring old clothes"})
        row = _session(a_soon)
        s.check("the places box on the workshops page changes the places and leaves the minimum alone",
                row["capacity"] == 7 and row["min_participants"] == 6 and row["decide_by_days"] == 30,
                detail=str(dict(row)))

        s.section("A move names what else is on the new dates")
        conn = db()
        room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
        clash = today + timedelta(days=500)
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 500, ?)""",
            (room["id"], f"{TAG}-STAY", f"tok{TAG}stay".lower(), f"{TAG} Staying Guest",
             f"{TAG.lower()}@example.invalid", clash.isoformat(),
             (clash + timedelta(days=2)).isoformat(), _now()))
        conn.commit()
        conn.close()
        said = " ".join(flashes(oc.post(f"/admin/workshops/sessions/{a_soon}/edit", data={
            "start_date": clash.isoformat(), "end_date": (clash + timedelta(days=1)).isoformat(),
            "capacity": "7", "back_session": str(a_soon)}, follow_redirects=True)))
        s.check("moving it onto a stay says whose", f"{TAG} Staying Guest" in said, detail=said)

        s.section("Calling off and removing, from the sitting's page")
        r = oc.post(f"/admin/workshops/session/{b_mid}/call-off",
                    data={"reason": "Not enough takers", "back_session": str(b_mid)})
        s.check("calling it off comes back to the sitting",
                r.headers.get("Location", "").endswith(f"/admin/workshops/sessions/{b_mid}")
                and _session(b_mid)["cancelled_at"], detail=r.headers.get("Location"))
        r = oc.post(f"/admin/workshops/sessions/{a_late}/delete", data={"back_workshop": str(a)})
        s.check("removing it goes to its programme, the sitting being gone",
                r.headers.get("Location", "").endswith(f"/admin/workshops/{a}") and _session(a_late) is None,
                detail=r.headers.get("Location"))
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
