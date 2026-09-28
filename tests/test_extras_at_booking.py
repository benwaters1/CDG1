"""The crémant board, and what an extra chosen at booking has to reach.

The owner asked for one thing: a crémant and charcuterie board a guest can add
when they book, at 80 euros, only with three days' notice, and a way to say
when they want it. Building it found the thing underneath it.

  AN EXTRA TICKED ON THE BOOKING FORM REACHED NOTHING. It went into a line of
  text on the booking and nowhere else. The card was charged for it -- and
  then it was missing from the bill, the one the Pay button, the balance chase
  and the debtors list all read, and it never reached the list of what the
  house owes its guests. A 350-euro airport transfer booked with a room: a
  bill of 803.20 against 1153.20 charged, and nobody told to arrange a car.
  So the board would have been sold, paid for, and never brought.

  THE NOTICE WAS SHOWN AND NOT KEPT. The form wrote "3 days' notice" beside an
  extra and then took it for tomorrow. The manage page refused it; the booking
  form, where most extras are chosen, did not ask.

  THE DAY IS A CHOICE, THE NOTE IS FOR THE REST. A day typed into a note would
  reach no list and no calendar; somebody would have to read every note to
  find out what Saturday needs.

  AND THE MONEY IS COUNTED ONCE. An extra chosen with a stay is inside the
  stay's total AND a line now, so every reader that splits a stay into room
  and extras takes it out of the room once -- the guest's statement, the VAT
  working and the room economics -- or the same bottle is counted twice at
  two VAT rates.

Runs on the throwaway copy. Mail and Stripe are stood down by the harness; the
Stripe return is driven with a plain session dict, which is what the webhook
path accepts.
"""
import html
import re
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from _harness import Suite, clients, db, flashes, free_window
import _harness

m = _harness.m
GUEST_IP = "203.0.113.47"
EMAIL = "ztest-board-{}@example.test"
BOARD = "Crémant & charcuterie board"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM bookings WHERE guest_email LIKE 'ztest-board-%'").fetchall()]
    for bid in ids:
        conn.execute("DELETE FROM stock_movements WHERE booking_extra_id IN "
                     "(SELECT id FROM booking_extras WHERE category = 'room' "
                     "AND booking_id = ?)", (bid,))
        conn.execute("DELETE FROM booking_extras WHERE category = 'room' AND booking_id = ?",
                     (bid,))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE 'ztest-board-%'")
    conn.execute("DELETE FROM extras WHERE name LIKE 'ztest-%'")
    conn.execute("DELETE FROM app_settings WHERE key = 'added_extra_ztest_probe'")
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()
    conn.close()


def _board():
    conn = db()
    try:
        return conn.execute("SELECT * FROM extras WHERE name = ?", (BOARD,)).fetchone()
    finally:
        conn.close()


def _room():
    conn = db()
    try:
        return conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    finally:
        conn.close()


def _guest():
    client = m.app.test_client()
    client.environ_base["REMOTE_ADDR"] = GUEST_IP
    return client


def _book(room, arrival, departure, tag, extras=(), **form):
    """Post the public booking form, clear of the rate limit, as a guest would."""
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()
    conn.close()
    data = {"arrival_date": arrival.isoformat(), "departure_date": departure.isoformat(),
            "guest_name": "Ztest Board", "guest_email": EMAIL.format(tag),
            "party_size": "2", "agree_terms": "on"}
    data.update(form)
    # A MultiDict, because a form with two extras ticked sends "extras" twice.
    data = MultiDict(list(data.items()) + [("extras", str(i)) for i in extras])
    return _guest().post(f"/book/{room['id']}", data=data, follow_redirects=True)


def _booking(tag):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE guest_email = ? "
                            "ORDER BY id DESC LIMIT 1", (EMAIL.format(tag),)).fetchone()
    finally:
        conn.close()


def _lines(booking_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM booking_extras WHERE category = 'room' "
                            "AND booking_id = ? ORDER BY id", (booking_id,)).fetchall()
    finally:
        conn.close()


def _window(room, nights=2, after_days=40):
    arrival = free_window(room["id"], nights=nights, after_days=after_days,
                          clear_of_ateliers=True, clear_of_rate_overrides=True)
    return arrival, arrival + timedelta(days=nights)


def run():
    s = Suite("Extras at booking: the crémant board")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _room()
    today = m.house_today()

    s.section("The board is in the catalogue, once")
    board = _board()
    s.check("it is there", board is not None, detail="seed_added_extras did not put it in")
    s.check("at 80 euros, with three days' notice",
            board and board["price"] == 80.0 and board["lead_time_days"] == 3,
            detail=str(dict(board)) if board else "")
    s.check("for guests to add, and asking them when",
            board and board["guest_bookable"] == 1 and board["ask_when"] == 1
            and board["active"] == 1)
    probe = ("added_extra_ztest_probe", {"name": "ztest-probe extra", "price": 5.0})
    real = m.ADDED_EXTRAS
    m.ADDED_EXTRAS = [probe]
    conn = db()
    try:
        first = m.seed_added_extras(conn)
        conn.commit()
        conn.execute("DELETE FROM extras WHERE name = 'ztest-probe extra'")
        conn.commit()
        again = m.seed_added_extras(conn)
        conn.commit()
        back = conn.execute("SELECT 1 FROM extras WHERE name = 'ztest-probe extra'").fetchone()
    except Exception as e:
        # Reported by the checks below rather than let through: a crash here
        # would also hide every check after it.
        first = again = back = f"raised {type(e).__name__}: {e}"
    finally:
        conn.close()
        m.ADDED_EXTRAS = real
    s.check("an added extra goes in the first time", first == 1, detail=str(first))
    s.check("and one the owner deletes is not put back by the next deploy",
            again == 0 and back is None,
            detail="matched on a key it remembers, not on the name, so a delete "
                   "or a rename sticks")

    s.section("On the booking form")
    arrival, departure = _window(room)
    page = _guest().get(f"/book/{room['id']}?arrival={arrival.isoformat()}"
                        f"&departure={departure.isoformat()}").get_data(as_text=True)
    s.check("it is offered, at its price", BOARD.replace("&", "&amp;") in page
            and "€80.00" in page)
    s.check("with the notice it needs", "3 days' notice" in page)
    s.check("and asks when, and for a note",
            f'name="extra_when_{board["id"]}"' in page
            and f'name="extra_note_{board["id"]}"' in page)
    conn = db()
    till_only = conn.execute(
        "INSERT INTO extras (name, price, category, guest_bookable, sold_in_pos, active) "
        "VALUES ('ztest-till-only glass', 9, 'drinks', 0, 1, 1)").lastrowid
    conn.commit()
    conn.close()
    page = _guest().get(f"/book/{room['id']}").get_data(as_text=True)
    s.check("an item kept for the till is not offered to somebody booking a room",
            "ztest-till-only glass" not in page,
            detail="guest_bookable decides it everywhere else, and this form did "
                   "not ask")
    a0, d0 = _window(room, nights=2, after_days=150)
    _book(room, a0, d0, "till", extras=[till_only])
    b0 = _booking("till")
    s.check("nor taken from a form somebody wrote themselves",
            b0 is not None and not any(l["name"] == "ztest-till-only glass"
                                       for l in _lines(b0["id"]))
            and "ztest-till-only" not in (b0["extras_summary"] or ""),
            detail=f"summary {b0['extras_summary']!r}" if b0 else "no booking")

    s.section("Notice is kept, not just shown")
    soon = free_window(room["id"], nights=2, after_days=1, clear_of_ateliers=True)
    s.check("a stay starting within three days can be found to try",
            (soon - today).days < 3, detail=f"first free arrival is {soon}")
    refused = _book(room, soon, soon + timedelta(days=2), "soon", extras=[board["id"]])
    said = [html.unescape(f) for f in flashes(refused)]
    s.check("booking it for then is refused, saying why",
            any("3 days' notice" in f and BOARD in f for f in said), detail=str(said))
    s.check("and no booking is made", _booking("soon") is None)
    quote = _guest().get(
        f"/api/quote?room_id={room['id']}&arrival={soon.isoformat()}"
        f"&departure={(soon + timedelta(days=2)).isoformat()}&extras={board['id']}").get_json()
    s.check("the price beside the form names it rather than charging for it",
            any(x["id"] == board["id"] for x in quote.get("too_soon", []))
            and not any(l["label"] == BOARD for l in quote.get("lines", [])),
            detail=str(quote.get("too_soon")))
    shut = _guest().get(f"/book/{room['id']}?arrival={soon.isoformat()}"
                        f"&departure={(soon + timedelta(days=2)).isoformat()}"
                        ).get_data(as_text=True)
    s.check("and the page draws it shut, with the reason showing",
            re.search(r'id="extra_%d"[^>]*disabled' % board["id"], shut, re.S) is not None
            and 'id="extra_too_soon_%d">' % board["id"] in shut,
            detail="disabled on the box, and the hint drawn without hidden")
    edge = free_window(room["id"], nights=2, after_days=3, clear_of_ateliers=True)
    if (edge - today).days == 3:
        took = _book(room, edge, edge + timedelta(days=2), "edge", extras=[board["id"]])
        s.check("three days ahead is enough", _booking("edge") is not None,
                detail=str(flashes(took)))
    else:
        s.check("three days ahead is enough (no free stay exactly three days out)",
                True, detail=f"first free arrival is {edge}; boundary not tried")

    s.section("When, and a note")
    arrival, departure = _window(room, nights=3, after_days=45)
    second_day = arrival + timedelta(days=1)
    r = _book(room, arrival, departure, "day", extras=[board["id"]],
              **{f"extra_when_{board['id']}": second_day.isoformat(),
                 f"extra_note_{board['id']}": "  7pm,   on the terrace  "})
    b = _booking("day")
    s.check("the booking goes through", b is not None, detail=str(flashes(r)))
    lines = _lines(b["id"]) if b else []
    line = lines[0] if lines else None
    s.check("the board is a line on the booking, not a line of text",
            line is not None and line["name"] == BOARD,
            detail="it used to reach extras_summary and nothing else")
    s.check("on the day they chose", line and line["scheduled_for"] == second_day.isoformat(),
            detail=str(line["scheduled_for"]) if line else "")
    s.check("with their note, tidied", line and line["notes"] == "7pm, on the terrace",
            detail=repr(line["notes"]) if line else "")
    s.check("and marked as already inside the stay's total",
            line and line["in_booking_total"] == 1)
    conn = db()
    due = m.extras_due(conn, today)
    conn.close()
    s.check("it is on the house's list of what it owes",
            any(x["line"]["id"] == line["id"] and x["when"] == second_day.isoformat()
                for x in due["rows"]) if line else False)
    conn = db()
    with m.app.test_request_context("/"):
        cal = m.build_calendar(conn, "week", second_day)
    conn.close()
    on_day = [e for c in cal["cells"] if c["iso"] == second_day.isoformat() for e in c["events"]]
    s.check("and on the calendar, on that day",
            any(e["kind"] == "extra" and BOARD in e["title"] for e in on_day),
            detail=str([e["title"] for e in on_day]))

    arrival2, departure2 = _window(room, nights=2, after_days=60)
    _book(room, arrival2, departure2, "arrive", extras=[board["id"]],
          **{f"extra_when_{board['id']}": "arrival",
             f"extra_note_{board['id']}": "x" * 300})
    b2 = _booking("arrive")
    l2 = (_lines(b2["id"]) or [None])[0] if b2 else None
    s.check("'on arrival' is the arrival day, and says so",
            l2 and l2["scheduled_for"] == arrival2.isoformat()
            and (l2["notes"] or "").startswith("On arrival"),
            detail=f"{l2['scheduled_for']} / {l2['notes'][:30]!r}" if l2 else "")
    s.check("and a note is kept to a length somebody reads",
            l2 and len(l2["notes"]) <= len("On arrival — ") + m.EXTRA_NOTE_MAX,
            detail=str(len(l2["notes"])) if l2 else "")

    arrival3, departure3 = _window(room, nights=2, after_days=75)
    wrong = _book(room, arrival3, departure3, "outside", extras=[board["id"]],
                  **{f"extra_when_{board['id']}": departure3.isoformat()})
    s.check("a day outside the stay is refused, not guessed at",
            _booking("outside") is None
            and any("day of your stay" in f for f in flashes(wrong)),
            detail=str(flashes(wrong)))
    back = wrong.get_data(as_text=True)
    s.check("and the form comes back with what they chose still on it",
            f'data-picked="{departure3.isoformat()}"' in back)

    s.section("The bill, and the books, count it once")
    bill = m.booking_bill(db(), b["id"]) if b else None
    s.check("the bill includes it",
            bill and any(l["label"] == BOARD and l["amount"] == 80.0 for l in bill["lines"]),
            detail=str([(l["label"], l["amount"]) for l in bill["lines"]]) if bill else "")
    s.check("and the bill is what was agreed, tax on top",
            bill and abs(bill["total"] - (b["total_price"] + (b["city_tax"] or 0))) < 0.01,
            detail=f"bill {bill['total'] if bill else None} against "
                   f"{b['total_price']} + {b['city_tax']}" if b else "")
    conn = db()
    statement = m.guest_statement(conn, b)
    conn.close()
    s.check("the statement takes it out of the accommodation line",
            abs(statement["accommodation"] - (b["total_price"] - 80.0)) < 0.01,
            detail=f"{statement['accommodation']} against {b['total_price']} - 80")
    s.check("lists it as an extra",
            any(e["name"] == BOARD for e in statement["extras"]))
    s.check("and agrees with the bill to the cent",
            abs(statement["total"] - bill["total"]) < 0.01,
            detail=f"statement {statement['total']}, bill {bill['total']}")

    # The working and the economics, measured as the difference one stay makes,
    # so whatever else the copy holds cannot move the figures under the check.
    # The window opens today, the house's today: the working files an extra by
    # the house's day now, so one sold a minute ago is in it at any hour.
    arrival4, departure4 = _window(room, nights=2, after_days=90)
    start, end = today, departure4 + timedelta(days=1)

    def vat_figures():
        conn = db()
        try:
            lines = m.vat_working(conn, start, end)["lines"]
        finally:
            conn.close()
        get = lambda label: sum(l["gross"] or 0 for l in lines if l["source"] == label)
        return get("Rooms"), get("Extras")

    def economics():
        conn = db()
        try:
            out = m.room_economics(conn, months=12, today=departure4 + timedelta(days=1))
        finally:
            conn.close()
        mine = next(r for r in out["rooms"] if r["id"] == room["id"])
        return round(mine["room_revenue"], 2), round(mine["extras_revenue"], 2)

    rooms_before, extras_before = vat_figures()
    econ_before = economics()
    _book(room, arrival4, departure4, "books", extras=[board["id"]])
    b4 = _booking("books")
    rooms_after, extras_after = vat_figures()
    econ_after = economics()
    room_part = round(b4["total_price"] - 80.0, 2) if b4 else None
    s.check("the VAT working counts the room as the room",
            b4 and abs((rooms_after - rooms_before) - room_part) < 0.01,
            detail=f"rooms moved {rooms_after - rooms_before:.2f}, room part {room_part}")
    s.check("and the board once, at the extras rate",
            abs((extras_after - extras_before) - 80.0) < 0.01,
            detail=f"extras moved {extras_after - extras_before:.2f}")
    s.check("room economics credit the room with the room",
            b4 and abs((econ_after[0] - econ_before[0]) - room_part) < 0.01,
            detail=f"{econ_after[0] - econ_before[0]:.2f} against {room_part}")
    s.check("and the extras with the board", abs((econ_after[1] - econ_before[1]) - 80.0) < 0.01,
            detail=f"{econ_after[1] - econ_before[1]:.2f}")
    conn = db()
    later = conn.execute("SELECT * FROM extras WHERE name = ?", (BOARD,)).fetchone()
    with m.app.test_request_context("/"):
        m.add_booking_extra(conn, "room", b4["id"], later, 1, notes="ztest later")
    conn.commit()
    conn.close()
    econ_later = economics()
    s.check("an extra added afterwards takes nothing off the room",
            abs(econ_later[0] - econ_after[0]) < 0.01,
            detail=f"room revenue moved {econ_later[0] - econ_after[0]:.2f} — it was "
                   "never inside total_price, and taking it out anyway made the "
                   "room look poorer for every bottle charged to it later")
    conn = db()
    statement4 = m.guest_statement(conn, conn.execute(
        "SELECT * FROM bookings WHERE id = ?", (b4["id"],)).fetchone())
    bill4 = m.booking_bill(conn, b4["id"])
    conn.close()
    s.check("and the statement still agrees with the bill",
            abs(statement4["total"] - bill4["total"]) < 0.01,
            detail=f"statement {statement4['total']}, bill {bill4['total']}")

    s.section("Through the card page")
    arrival5, departure5 = _window(room, nights=2, after_days=105)
    packed = m.pack_extra_details({board["id"]: {"on": arrival5.isoformat(),
                                                 "notes": "On arrival — 6pm"}})
    session = {"id": "cs_test_ztest_board_0001", "payment_intent": "pi_ztest_board",
               "amount_total": 0, "metadata": {
                   "room_id": str(room["id"]), "guest_name": "Ztest Board",
                   "guest_email": EMAIL.format("stripe"), "guest_phone": "",
                   "arrival_date": arrival5.isoformat(),
                   "departure_date": departure5.isoformat(), "party_size": "2",
                   "guests_under_18": "0", "special_requests": "",
                   "extra_ids": str(board["id"]), "extra_when": packed}}
    conn = db()
    with m.app.test_request_context("/"):
        m.create_booking_from_stripe_session(conn, session)
    conn.commit()
    conn.close()
    b5 = _booking("stripe")
    l5 = (_lines(b5["id"]) or [None])[0] if b5 else None
    s.check("the day and the note come back through Stripe",
            l5 and l5["scheduled_for"] == arrival5.isoformat()
            and l5["notes"] == "On arrival — 6pm",
            detail=f"{l5['scheduled_for']} / {l5['notes']!r}" if l5 else "no line")
    many = {i: {"on": "2026-10-0%d" % (i % 9 + 1), "notes": "n" * 120} for i in range(1, 8)}
    squeezed = m.pack_extra_details(many)
    back = m.unpack_extra_details(squeezed)
    s.check("too much to carry is trimmed to fit Stripe's 500 characters",
            len(squeezed) <= 490, detail=str(len(squeezed)))
    s.check("and the days survive it, whole",
            all(back.get(i, {}).get("on") == many[i]["on"] for i in many),
            detail="the day is what puts it on the right list, so it is never cut")

    s.section("Added afterwards, from the manage page")
    arrival6, departure6 = _window(room, nights=3, after_days=120)
    _book(room, arrival6, departure6, "manage")
    b6 = _booking("manage")
    page = _guest().get(f"/book/manage/{b6['manage_token']}").get_data(as_text=True)
    s.check("the board is offered there too, asking when",
            BOARD.replace("&", "&amp;") in page and 'name="extra_when"' in page)
    third = arrival6 + timedelta(days=2)
    _guest().post(f"/book/manage/{b6['manage_token']}", data={
        "action": "add_extra", "extra_id": str(board["id"]), "quantity": "1",
        "extra_when": third.isoformat(), "extra_note": "anniversary"},
        follow_redirects=True)
    l6 = [l for l in _lines(b6["id"]) if l["name"] == BOARD]
    s.check("added for the day they chose, with their note",
            l6 and l6[0]["scheduled_for"] == third.isoformat()
            and "anniversary" in (l6[0]["notes"] or ""),
            detail=str([(l["scheduled_for"], l["notes"]) for l in l6]))
    s.check("and NOT marked as inside the stay's total, because it is not",
            l6 and l6[0]["in_booking_total"] == 0)

    s.section("The catalogue can say it")
    oc.post("/admin/extras/new", data={"name": "ztest-asks-when", "price": "12",
                                       "category": "food", "ask_when": "on",
                                       "guest_bookable": "on"})
    conn = db()
    made = conn.execute("SELECT * FROM extras WHERE name = 'ztest-asks-when'").fetchone()
    conn.close()
    s.check("an extra can be set to ask when", made and made["ask_when"] == 1)
    oc.post(f"/admin/extras/{made['id']}/edit", data={"name": "ztest-asks-when",
                                                      "price": "12", "category": "food"})
    conn = db()
    edited = conn.execute("SELECT * FROM extras WHERE id = ?", (made["id"],)).fetchone()
    conn.close()
    s.check("and unticking it stops asking", edited["ask_when"] == 0)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
