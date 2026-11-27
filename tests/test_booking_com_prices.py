"""An occasion's prices, carried to Booking.com by hand (owner's brief, 25 Nov 2026).

Booking.com takes prices only from a certified channel manager, and the
cheapest costs more than a dozen price changes a year are worth. So the app
prepares the list and a person types it into the extranet under the owner's own
sign-in: the app never signs in to Booking.com and holds no password for it.

What this holds, each through the routes the owner uses:

  - A LIVE occasion makes one line per room it prices above (or below) that
    room's usual rate on those nights; a room at its usual price makes none, and
    a draft makes nothing at all.
  - A line not yet done FOLLOWS the occasion: a new price rewrites it in place.
    A line done is what Booking.com now shows, so a change after it is a NEW
    line, from the price entered to the new one.
  - Taking an occasion off sale after its prices were entered puts the nights
    BACK to the usual price; before they were entered, its lines are cancelled.
    Dates moved after entering: the nights left behind go back, the nights
    gained get the price, and the nights kept need nothing.
  - ONE TASK, "Booking.com prices to update", while any line is to do -- on the
    owner home, on the calendar, opening the list -- and it closes itself when
    the last line is marked done. The morning's watch run keeps it, too.
  - Mark done refuses a line that changed since the page was drawn, by name.
  - The list as JSON is the owner's: staff are refused and a visitor is sent to
    sign in.

And since the follow-up of 26 Nov 2026 (the owner, against the real extranet):

  - A line gives BOOKING.COM'S two prices, not the house's: Non-Refundable and
    the Standard Rate, each the room's Booking.com usual plus what the occasion
    adds on the house site. Christmas at €320 in a €270 room is +€50, so the
    King Room's €290 / €330 on Booking.com become €340 / €380, each with the
    one it replaces. The copy block and the JSON carry both.
  - Changing a room's Booking.com usual price rewrites every line not yet done,
    from the price list and from the room's own form.
  - A room whose Booking.com prices are not given still gets its line, saying
    what to add, and the page asks for the prices.

And since the owner read "the usual +€50" as describing the €50 (26 Nov 2026):

  - Every line shows the price to type, in euros, on both rates, with the
    room's Booking.com price on other nights beneath it -- never "the usual".
  - A line marked done before the room's Booking.com prices were given has its
    figures worked out from them now, marked "worked out after it was entered".
  - Each line says where its amount comes from: "+€50 because Christmas is
    €320 against the house's €270". Nothing calls the €50 a rule or a default.
  - The extranet's name for a room is its own field. channel_name, the name
    guests see on Booking.com and the one its mail is matched on, is never
    written by the price list.
  - The lines entered before any of this (no uplift stored) are given the
    uplift they carried and raise nothing new.

No suite reaches Booking.com: nothing in this feature can.
"""
import json
import re
from datetime import timedelta

from _harness import Suite, clients, db, flashes, free_window, keep_rows, secrets_token, visible_text
import _harness

m = _harness.m
TAG = "ZZBCP"
KING = TAG + " King Room"          # priced above usual, extranet name and Booking.com prices given
QUEEN = TAG + " Queen Room"        # priced above usual, no extranet name or Booking.com prices yet
TWIN = TAG + " Twin Room"          # priced AT its usual rate: no line
KING_ON_BCOM = TAG + " King Room with Mountain View"   # what guests see: mail is matched on it
KING_EXTRANET = TAG + " King Room (extranet)"          # what the extranet calls it
USUAL = 270.0
BCOM_NR, BCOM_STD = 290.0, 330.0   # the King Room on Booking.com, as the brief has it
TASK = "Booking.com prices to update"


def _cleanup(conn):
    conn.execute("DELETE FROM room_rate_overrides WHERE label = ?", (TAG,))
    conn.execute("DELETE FROM channel_price_pushes WHERE room_id IN "
                 "(SELECT id FROM rooms WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM channel_price_pushes WHERE occasion_name LIKE ?", (TAG + "%",))
    for table in ("stay_occasion_dinners", "stay_occasion_prices"):
        conn.execute(f"DELETE FROM {table} WHERE occasion_id IN "
                     "(SELECT id FROM stay_occasions WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stay_occasions WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _room(conn, name, channel_name=None, extranet=None, nr=None, std=None):
    conn.execute(
        """INSERT INTO rooms (name, channel_name, booking_extranet_name, booking_nr_price,
               booking_std_price, export_token, active, max_occupancy, max_adults,
               price_per_night, sort_order) VALUES (?, ?, ?, ?, ?, ?, 1, 2, 2, ?, 98)""",
        (name, channel_name, extranet, nr, std, secrets_token(), USUAL))
    conn.commit()
    return conn.execute("SELECT * FROM rooms WHERE name = ?", (name,)).fetchone()


def _rates(line):
    """A line's Booking.com figures: (nr, std, the nr it replaces, the std it replaces)."""
    return (line["nr_price"], line["std_price"], line["previous_nr_price"], line["previous_std_price"])


def _form(name, first, last, prices, status):
    f = {"name": name, "first_night": first.isoformat(), "last_night": last.isoformat(),
         "kind": "stay", "chef_on_site": "0", "status": status, "description": "", "photo": ""}
    for room_id, price in prices.items():
        f[f"price_{room_id}"] = str(price)
    return f


def _occasion(conn, name):
    return conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()


def _make(oc, conn, name, first, last, prices, status="live"):
    """Drafted through the new-occasion form, then saved with the status."""
    oc.post("/admin/stay-occasions/new", data=_form(name, first, last, prices, "draft"))
    occ = _occasion(conn, name)
    r = _save(oc, occ, first, last, prices, status) if occ else None
    return _occasion(conn, name), r


def _save(oc, occ, first, last, prices, status):
    return oc.post(f"/admin/stay-occasions/{occ['id']}",
                   data=_form(occ["name"], first, last, prices, status), follow_redirects=True)


def _lines(conn, room_id, state=None):
    sql = "SELECT * FROM channel_price_pushes WHERE room_id = ?"
    args = [room_id]
    if state:
        sql += " AND state = ?"
        args.append(state)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def _task(conn):
    return conn.execute("SELECT * FROM tasks WHERE origin = ? AND title = ? AND status != 'done' "
                        "ORDER BY id DESC LIMIT 1", (m.WATCH_TASK_ORIGIN, TASK)).fetchone()


def _mark_done(oc, rows, stamps=None):
    data = {"line": [str(r["id"]) for r in rows]}
    for r in rows:
        data[f"stamp_{r['id']}"] = (stamps or {}).get(r["id"], r["updated_at"])
    return oc.post("/admin/channels/booking-prices/done", data=data, follow_redirects=True)


def _copy_block(html):
    if "data-copy-text>" not in html:
        return ""
    return html.split('data-copy-text>', 1)[1].split("</textarea>", 1)[0].replace("&amp;", "&")


def run():
    s = Suite("Booking.com prices, entered by hand")
    oc, ec, owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "stay_occasions", "status = 'live'"),
                keep_rows(conn, "tasks", "origin = ? AND title = ?", (m.WATCH_TASK_ORIGIN, TASK)),
                keep_rows(conn, "channel_price_pushes", "1 = 1")]
    # Only this suite's occasions are on sale, and only its lines are on the
    # list, for the run: the list is worked out across the whole house.
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE status = 'live'")
    conn.execute("DELETE FROM channel_price_pushes")
    conn.commit()
    _cleanup(conn)
    try:
        _run(s, oc, ec, owner, conn)
        _the_rooms_of_26_november(s, conn)
    finally:
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, ec, owner, conn):
    today = m.house_today()
    king = _room(conn, KING, KING_ON_BCOM, KING_EXTRANET, BCOM_NR, BCOM_STD)
    queen = _room(conn, QUEEN)
    twin = _room(conn, TWIN, extranet=TAG + " Twin", nr=BCOM_NR, std=BCOM_STD)
    owner_row = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()
    n0 = free_window(king["id"], 24, after_days=150, clear_of_ateliers=True,
                     clear_of_rate_overrides=True)
    a1, b1 = n0 + timedelta(days=2), n0 + timedelta(days=4)     # the first occasion: three nights
    prices = {king["id"]: 320, queen["id"]: 330, twin["id"]: USUAL}
    name1 = TAG + " Midsummer"

    s.section("A draft changes no price, so it makes no line")
    occ, r = _make(oc, conn, name1, a1, b1, prices, status="draft")
    s.check("the occasion is drafted", occ is not None and occ["status"] == "draft",
            response=r)
    s.check("and nothing is on the list for any of its rooms",
            not any(_lines(conn, x["id"]) for x in (king, queen, twin)))
    s.check("so there is no task", _task(conn) is None)

    s.section("On sale: one line a room priced off its usual rate, none for one at it")
    r = _save(oc, occ, a1, b1, prices, "live")
    occ = _occasion(conn, name1)
    s.check("it goes on sale", occ["status"] == "live", response=r)
    k, q, t = _lines(conn, king["id"]), _lines(conn, queen["id"]), _lines(conn, twin["id"])
    s.check("the King Room has one line to do", len(k) == 1 and k[0]["state"] == "to_do",
            detail=str([dict(x) for x in k]))
    s.check("the Queen Room has one line to do", len(q) == 1 and q[0]["state"] == "to_do",
            detail=str([dict(x) for x in q]))
    s.check("the Twin Room, at its usual price, has none", not t,
            detail="a line saying 'change €290 to €290' is a job for nobody: "
                   + str([dict(x) for x in t]))
    if k:
        s.check("the line is the occasion's nights, first and last included",
                (k[0]["first_night"], k[0]["last_night"]) == (a1.isoformat(), b1.isoformat()),
                detail=f"{k[0]['first_night']}..{k[0]['last_night']}")
        s.check("it keeps the house's figures, the occasion's price for the usual one",
                k[0]["price"] == 320 and k[0]["previous_price"] == USUAL and k[0]["kind"] == "occasion",
                detail=f"{k[0]['price']} for {k[0]['previous_price']}")
        s.check("and it knows its occasion", k[0]["occasion_id"] == occ["id"]
                and k[0]["occasion_name"] == name1 and k[0]["channel"] == "booking.com")
    said = " ".join(flashes(r))
    s.check("the save says so", "Booking.com: 2 prices to enter" in said, detail=said)

    s.section("Booking.com's two prices: its usual on each rate, plus what the occasion adds (+€50)")
    if k:
        s.check("the occasion adds €50 on the house site, so the line carries +€50",
                k[0]["uplift"] == 50 and k[0]["previous_uplift"] == 0,
                detail=f"uplift {k[0]['uplift']}, was {k[0]['previous_uplift']}")
        s.check("King Room: €340 Non-Refundable and €380 Standard Rate, for €290 and €330",
                _rates(k[0]) == (340, 380, BCOM_NR, BCOM_STD), detail=str(_rates(k[0])))
    if q:
        s.check("a room with no Booking.com prices given still has its line, at +€60, with no figure made up",
                q[0]["uplift"] == 60 and _rates(q[0]) == (None, None, None, None),
                detail=f"uplift {q[0]['uplift']}, rates {_rates(q[0])}")

    s.section("One task while anything is to do")
    task = _task(conn)
    s.check("'Booking.com prices to update' is open", task is not None)
    if task:
        s.check("due today, so it is on today's list", task["due_date"] == today.isoformat())
        s.check("its note names the occasion and how many",
                "2 prices to enter" in task["notes"] and name1 in task["notes"], detail=task["notes"])
        s.check("and the room with no extranet name yet, and not the one with",
                QUEEN in task["notes"] and KING not in task["notes"], detail=task["notes"])
        s.check("and asks for its Booking.com prices", "prices for " + QUEEN in task["notes"],
                detail=task["notes"])
        with m.app.test_request_context("/"):
            subject = m.task_subject(task, owner_row)
        s.check("it opens the price list", subject and subject["url"] == "/admin/channels/booking-prices",
                detail=str(subject))
        with m.app.test_request_context("/"):
            day = m.owner_home_day(conn, today, owner_row)
        mine = [x for x in day if x["title"] == TASK]
        s.check("on the owner home's day, opening the list",
                len(mine) == 1 and mine[0].get("href") == "/admin/channels/booking-prices",
                detail=str(mine))
        home = oc.get("/").get_data(as_text=True)
        s.check("which the owner home draws", TASK in home)
        cal = visible_text(oc.get(f"/calendar?view=day&date={today.isoformat()}").get_data(as_text=True))
        s.check("and on the calendar", TASK in cal)
        found, _dropped = m.watch_task_findings(conn, today)
        s.check("the morning's watch run wants it too, so does not close it",
                any(f[1] == TASK for f in found))
        m.generate_watch_tasks(conn, today)
        s.check("and after that run it is still open", _task(conn) is not None)

    s.section("The page: grouped by occasion, both rates, ready to copy")
    page = oc.get("/admin/channels/booking-prices")
    html = page.get_data(as_text=True)
    text = visible_text(html)
    s.check("the page opens for the owner", page.status_code == 200)
    s.check("headed by the occasion", name1 in text)
    s.check("naming the room as the extranet does", KING_EXTRANET in text)
    s.check("and saying where that name is missing", "Extranet name not given" in text)
    s.check("a column for each rate plan", "Non-Refundable" in text and "Standard Rate" in text)
    s.check("the King Room's line shows €340 and €380",
            "€340" in text and "€380" in text, detail=text[text.find(KING_EXTRANET):][:400])
    s.check("each with the room's Booking.com price on other nights beneath it",
            "€290 on other nights" in text and "€330 on other nights" in text,
            detail=text[text.find(KING_EXTRANET):][:400])
    s.check("and where the amount comes from",
            f"+€50 because {name1} is €320 against the house's €270" in text,
            detail=text[text.find(name1):][:600])
    s.check("the room with no Booking.com prices says what to add, with no figure made up",
            "+€60 on Booking.com's price for this room" in text, detail=text[text.find(QUEEN):][:300])
    s.check("and the page asks for its prices",
            f"Booking.com's prices for {QUEEN} on other nights are not given yet" in text)
    s.check("under the table, what to do where Booking.com already charges something else",
            "Where Booking.com already charges something else on these dates (the summer is dearer), "
            "add the same amount to what is there instead." in text)
    s.check("the Standard Rate is described with its free cancellation, and nothing says to remove it",
            "free cancellation until 3 days before" in text
            and not any(w in text.lower() for w in ("remove the standard", "drop the standard",
                                                     "turn off free cancellation",
                                                     "remove free cancellation")))
    copy = _copy_block(html)
    want = " | ".join((KING_EXTRANET, m.format_date_human(a1.isoformat()),
                       m.format_date_human(b1.isoformat()), "340", "380", "+50"))
    s.check("the copy block has the line as room | from | to | Non-Refundable | Standard Rate | change",
            want in copy, detail=copy[:500])
    s.check("and names the two rates in its heading",
            "Non-Refundable | Standard Rate" in copy, detail=copy[:300])
    s.check("with the room that has no name flagged, not dropped, saying what to add",
            f"{QUEEN} (extranet name not given)" in copy and "not given, +60 | not given, +60 | +60" in copy,
            detail=copy[:500])
    s.check("the house's €320 is not offered as a price to type",
            " | 320" not in copy, detail=copy[:500])
    s.check("and ends with what to do on a date at another price",
            "add the same amount (the change) to what is there" in copy)
    s.check("and no line for the room at its usual price", TWIN not in copy)
    # While a room's Booking.com prices are missing, as well as at the end.
    _nothing_says_the_usual(s, oc, conn, "a room's prices not given")
    s.check("Mark done beside each line, and for the whole occasion",
            html.count(">Mark done</button>") >= 2 and "Mark all 2 done" in html)

    s.section("The list as data, for the owner's assistant")
    js = oc.get("/admin/channels/booking-prices.json")
    s.check("the owner reads it", js.status_code == 200 and js.mimetype == "application/json")
    data = js.get_json() or {}
    ours = [x for x in data.get("to_do", []) if x["house_room"].startswith(TAG)]
    s.check("both lines to do, and only those", sorted(x["house_room"] for x in ours) == [KING, QUEEN],
            detail=json.dumps(ours)[:300])
    kj = next((x for x in ours if x["house_room"] == KING), {})
    s.check("each with its extranet room and nights",
            kj.get("booking_com_room") == KING_EXTRANET and kj.get("first_night") == a1.isoformat()
            and kj.get("last_night") == b1.isoformat(), detail=str(kj))
    s.check("and both of Booking.com's rates, each with the one it replaces and its price on other nights",
            kj.get("rates") == {"nr": {"name": "Non-Refundable", "price": 340, "replaces": BCOM_NR,
                                       "on_other_nights": BCOM_NR},
                                "std": {"name": "Standard Rate", "price": 380, "replaces": BCOM_STD,
                                        "on_other_nights": BCOM_STD}},
            detail=str(kj.get("rates")))
    s.check("and the change on each", kj.get("change") == 50, detail=str(kj.get("change")))
    s.check("with no bare 'price' to type by mistake: the house's is named as the house's",
            "price" not in kj and kj.get("house_price") == 320 and kj.get("house_replaces") == USUAL,
            detail=str(kj))
    s.check("the guest-facing name is given apart, as what guests see",
            kj.get("booking_com_guest_name") == KING_ON_BCOM, detail=str(kj))
    s.check("the rate plans and where the change comes from are stated",
            data.get("rate_plans") == {"nr": "Non-Refundable", "std": "Standard Rate"}
            and "already charges something else" in (data.get("change") or "")
            and kj.get("because") == f"+€50 because {name1} is €320 against the house's €270"
            and "free cancellation" in (data.get("standard_rate_terms") or ""), detail=str(data)[:300])
    qj = next((x for x in ours if x["house_room"] == QUEEN), {})
    s.check("and the missing name and prices said as missing, not as the house's",
            QUEEN in data.get("missing_booking_com_names", [])
            and QUEEN in data.get("missing_booking_com_prices", [])
            and qj.get("booking_com_room") is None
            and qj.get("rates", {}).get("nr", {}).get("price") is None and qj.get("change") == 60,
            detail=str(qj))
    staff = ec.get("/admin/channels/booking-prices.json")
    s.check("staff are refused it", staff.status_code == 403, detail=str(staff.status_code))
    s.check("and staff cannot open the page either",
            ec.get("/admin/channels/booking-prices").status_code == 403)
    nobody = m.app.test_client().get("/admin/channels/booking-prices.json")
    s.check("a visitor is sent to sign in", nobody.status_code in (301, 302)
            and "login" in (nobody.headers.get("Location") or ""),
            detail=f"{nobody.status_code} {nobody.headers.get('Location')}")

    s.section("Booking.com's usual prices changed on the price list: lines not yet done follow")
    before = _lines(conn, king["id"])[0]
    r = oc.post("/admin/channels/booking-names",
                data={f"booking_nr_{king['id']}": "300", f"booking_std_{king['id']}": "€345"},
                follow_redirects=True)
    got = conn.execute("SELECT * FROM rooms WHERE id = ?", (king["id"],)).fetchone()
    s.check("the room keeps the two new figures", (got["booking_nr_price"], got["booking_std_price"])
            == (300, 345), detail=str((got["booking_nr_price"], got["booking_std_price"])), response=r)
    k = _lines(conn, king["id"])
    s.check("the King Room's line is rewritten in place, the same line",
            len(k) == 1 and k[0]["id"] == before["id"] and k[0]["state"] == "to_do",
            detail=str([dict(x) for x in k]))
    s.check("now €350 and €395, for €300 and €345", k and _rates(k[0]) == (350, 395, 300, 345),
            detail=str(k and _rates(k[0])))
    s.check("and the save says the line changed to match",
            any("changed to match" in f for f in flashes(r)), detail=str(flashes(r)))
    s.check("the guest-facing name, which mail is matched on, is untouched",
            got["channel_name"] == KING_ON_BCOM, detail=repr(got["channel_name"]))
    r = oc.post("/admin/channels/booking-names",
                data={f"booking_nr_{king['id']}": "a lot", f"booking_std_{king['id']}": "345"},
                follow_redirects=True)
    got = conn.execute("SELECT booking_nr_price FROM rooms WHERE id = ?", (king["id"],)).fetchone()
    s.check("a price that is not a price is refused by name, and the old one kept",
            got["booking_nr_price"] == 300
            and any("Non-Refundable" in f and "is not a price" in f for f in flashes(r)),
            detail=f"{got['booking_nr_price']} {flashes(r)}")

    s.section("And from the room's own form")
    form = oc.get(f"/admin/rooms/{king['id']}/edit").get_data(as_text=True)
    s.check("the room's form carries the extranet name and both prices",
            'name="booking_extranet_name"' in form and 'name="booking_nr_price"' in form
            and 'name="booking_std_price"' in form and 'value="300"' in form
            and f'value="{KING_EXTRANET}"' in form.replace("&#39;", "'"))
    edit = {"name": KING, "channel_name": KING_ON_BCOM, "price_per_night": str(USUAL),
            "max_adults": "2", "max_occupancy": "2", "min_nights": "1", "active": "on",
            "booking_extranet_name": KING_EXTRANET, "booking_nr_price": "290", "booking_std_price": "330"}
    r = oc.post(f"/admin/rooms/{king['id']}/edit", data=edit, follow_redirects=True)
    k = _lines(conn, king["id"])
    s.check("saving it at €290 and €330 rewrites the line back to €340 and €380",
            len(k) == 1 and k[0]["id"] == before["id"] and _rates(k[0]) == (340, 380, 290, 330),
            detail=str(k and _rates(k[0])), response=r)
    s.check("and says so", any("Booking.com:" in f and "changed to match" in f for f in flashes(r)),
            detail=str(flashes(r)))
    no_booking = {x: y for x, y in edit.items() if not x.startswith("booking_")}
    oc.post(f"/admin/rooms/{king['id']}/edit", data=no_booking)
    got = conn.execute("SELECT * FROM rooms WHERE id = ?", (king["id"],)).fetchone()
    s.check("a save that does not carry them keeps them, rather than wiping every line's figures",
            (got["booking_extranet_name"], got["booking_nr_price"], got["booking_std_price"])
            == (KING_EXTRANET, 290, 330), detail=str(dict(got)))

    s.section("A new price before it is entered rewrites the line")
    before = _lines(conn, king["id"])[0]
    prices[king["id"]] = 330
    r = _save(oc, occ, a1, b1, prices, "live")
    k = _lines(conn, king["id"])
    s.check("still one line for the King Room, the same one",
            len(k) == 1 and k[0]["id"] == before["id"], detail=str([dict(x) for x in k]))
    s.check("now +€60: €350 and €390", k and k[0]["price"] == 330 and k[0]["state"] == "to_do"
            and k[0]["previous_price"] == USUAL and _rates(k[0]) == (350, 390, 290, 330),
            detail=str(k and _rates(k[0])))
    s.check("the save says it changed to match", "changed to match" in " ".join(flashes(r)),
            detail=" ".join(flashes(r)))

    s.section("Mark done refuses a line that changed since the page was drawn")
    q = _lines(conn, queen["id"])[0]
    r = _mark_done(oc, [q], stamps={q["id"]: "drawn-before-it-changed"})
    s.check("the Queen line is not ticked", _lines(conn, queen["id"])[0]["state"] == "to_do")
    s.check("and the refusal names it", any(QUEEN in f and "changed since" in f for f in flashes(r)),
            detail=str(flashes(r)))

    s.section("Marked done: who and when")
    k = _lines(conn, king["id"])[0]
    r = _mark_done(oc, [k])
    k = _lines(conn, king["id"])[0]
    s.check("the King line is done", k["state"] == "done", response=r)
    s.check("by the owner, with the moment", k["done_by_user_id"] == owner["id"] and k["done_at"])
    logged = conn.execute("SELECT details FROM audit_log WHERE action = 'booking_prices_done' "
                          "ORDER BY id DESC LIMIT 1").fetchone()
    s.check("the audit log records both prices entered",
            logged is not None and "€350 Non-Refundable, €390 Standard Rate" in (logged["details"] or ""),
            detail=logged and logged["details"])
    s.check("the page says who", (owner["name"] or "") in visible_text(
        oc.get("/admin/channels/booking-prices?state=done").get_data(as_text=True)))
    task = _task(conn)
    s.check("the task stays while the Queen line is to do",
            task is not None and "1 price to enter" in task["notes"], detail=task and task["notes"])

    s.section("A new price after it is entered is a new line")
    prices[king["id"]] = 350
    _save(oc, occ, a1, b1, prices, "live")
    done = _lines(conn, king["id"], "done")
    todo = _lines(conn, king["id"], "to_do")
    s.check("the line done is left as it was entered",
            len(done) == 1 and done[0]["price"] == 330 and _rates(done[0])[:2] == (350, 390))
    s.check("and a new one is to do, from the prices entered to the new ones (+€80)",
            len(todo) == 1 and todo[0]["price"] == 350 and todo[0]["previous_price"] == 330
            and _rates(todo[0]) == (370, 410, 350, 390),
            detail=str([dict(x) for x in todo]))

    s.section("Every line of an occasion at once, and the task closes itself")
    allof = _lines(conn, king["id"], "to_do") + _lines(conn, queen["id"], "to_do")
    r = _mark_done(oc, allof)
    s.check("both ticked", all(x["state"] == "done" for x in
                               _lines(conn, king["id"]) + _lines(conn, queen["id"])), response=r)
    s.check("said as two", "Ticked off 2 prices" in " ".join(flashes(r)), detail=str(flashes(r)))
    s.check("nothing is left to do, so the task is closed", _task(conn) is None)
    s.check("and the copy block is gone with it",
            "data-copy-text" not in oc.get("/admin/channels/booking-prices").get_data(as_text=True))

    s.section("Booking.com's usual prices changed after a line is entered: a new line")
    oc.post("/admin/channels/booking-names",
            data={f"booking_nr_{king['id']}": "295", f"booking_std_{king['id']}": "335"})
    todo = _lines(conn, king["id"], "to_do")
    s.check("the occasion's nights get €375 and €415 (still +€80), replacing €370 and €410",
            len(todo) == 1 and _rates(todo[0]) == (375, 415, 370, 410) and todo[0]["uplift"] == 80,
            detail=str([dict(x) for x in todo]))
    page = visible_text(oc.get("/admin/channels/booking-prices").get_data(as_text=True))
    s.check("and the page says why: the same amount, on the room's new Booking.com prices",
            f"+€80 because {name1} is €350 against the house's €270, on Booking.com's new prices "
            "for the room" in page, detail=page[page.find(name1):][:600])
    oc.post("/admin/channels/booking-names",
            data={f"booking_nr_{king['id']}": "290", f"booking_std_{king['id']}": "330"})
    s.check("put back as it was, the line is no longer needed",
            not _lines(conn, king["id"], "to_do"), detail=str([dict(x) for x in _lines(conn, king["id"])]))

    s.section("Off sale after it was entered: the nights go back to the usual price")
    r = _save(oc, occ, a1, b1, prices, "dismissed")
    s.check("it is dismissed", _occasion(conn, name1)["status"] == "dismissed", response=r)
    back = _lines(conn, king["id"], "to_do")
    s.check("King Room: a line putting its nights back to €290 and €330, from €370 and €410",
            len(back) == 1 and back[0]["kind"] == "usual" and back[0]["price"] == USUAL
            and back[0]["previous_price"] == 350 and _rates(back[0]) == (290, 330, 370, 410)
            and (back[0]["first_night"], back[0]["last_night"]) == (a1.isoformat(), b1.isoformat()),
            detail=str([dict(x) for x in back]))
    back = _lines(conn, queen["id"], "to_do")
    s.check("Queen Room: back to its usual, taking off the €60, with no figure made up",
            len(back) == 1 and back[0]["kind"] == "usual" and back[0]["price"] == USUAL
            and back[0]["previous_price"] == 330 and back[0]["uplift"] == 0
            and back[0]["previous_uplift"] == 60 and _rates(back[0]) == (None, None, None, None),
            detail=str([dict(x) for x in back]))
    page = visible_text(oc.get("/admin/channels/booking-prices").get_data(as_text=True))
    s.check("the page says the change on each, and to take it off where the price is something else",
            f"Back to Booking.com's own prices: {name1} no longer prices these nights, −€80 on each" in page
            and "Where a line goes down, take it off." in page,
            detail=page[page.find(name1):][:600])
    s.check("so the task is back", _task(conn) is not None)
    _mark_done(oc, _lines(conn, king["id"], "to_do") + _lines(conn, queen["id"], "to_do"))
    s.check("and closes again once they are entered", _task(conn) is None)

    s.section("Off sale before it was entered: its line is cancelled")
    a2, b2 = n0 + timedelta(days=8), n0 + timedelta(days=9)
    name2 = TAG + " Harvest"
    occ2, r = _make(oc, conn, name2, a2, b2, {king["id"]: 300})
    line = [x for x in _lines(conn, king["id"]) if x["occasion_id"] == (occ2 or {"id": -1})["id"]]
    s.check("on sale, it has its line", len(line) == 1 and line[0]["state"] == "to_do", response=r)
    _save(oc, occ2, a2, b2, {king["id"]: 300}, "draft")
    line = [x for x in _lines(conn, king["id"]) if x["occasion_id"] == occ2["id"]]
    s.check("back to a draft, the line is cancelled and not reversed",
            len(line) == 1 and line[0]["state"] == "cancelled", detail=str([dict(x) for x in line]))
    s.check("saying why", line and "taken off sale" in (line[0]["note"] or ""))
    s.check("and no task is left behind", _task(conn) is None)

    s.section("Dates moved after it was entered")
    a3, b3 = n0 + timedelta(days=12), n0 + timedelta(days=14)
    name3 = TAG + " Festival"
    occ3, _r = _make(oc, conn, name3, a3, b3, {king["id"]: 250})
    first3 = [x for x in _lines(conn, king["id"], "to_do") if x["occasion_id"] == occ3["id"]]
    s.check("an occasion BELOW the usual takes the same off Booking.com's: €270 and €310",
            len(first3) == 1 and _rates(first3[0]) == (270, 310, 290, 330) and first3[0]["uplift"] == -20,
            detail=str([dict(x) for x in first3]))
    _mark_done(oc, first3)
    moved_a, moved_b = a3 + timedelta(days=1), b3 + timedelta(days=1)
    _save(oc, occ3, moved_a, moved_b, {king["id"]: 250}, "live")
    todo = [x for x in _lines(conn, king["id"], "to_do") if x["occasion_id"] == occ3["id"]]
    back = [x for x in todo if x["kind"] == "usual"]
    gained = [x for x in todo if x["kind"] == "occasion"]
    s.check("the night left behind goes back to the usual prices",
            len(back) == 1 and back[0]["first_night"] == back[0]["last_night"] == a3.isoformat()
            and back[0]["price"] == USUAL and _rates(back[0]) == (290, 330, 270, 310),
            detail=str([dict(x) for x in todo]))
    s.check("the night gained gets the occasion's",
            len(gained) == 1 and gained[0]["first_night"] == gained[0]["last_night"] == moved_b.isoformat()
            and gained[0]["price"] == 250 and _rates(gained[0]) == (270, 310, 290, 330))
    s.check("and the nights kept need nothing", len(todo) == 2)

    s.section("Not done after all")
    first = gained[0] if gained else None
    if first:
        _mark_done(oc, [first])
        r = oc.post(f"/admin/channels/booking-prices/{first['id']}/undo", follow_redirects=True)
        again = conn.execute("SELECT * FROM channel_price_pushes WHERE id = ?", (first["id"],)).fetchone()
        s.check("a line marked done by mistake goes back on the list",
                again["state"] == "to_do" and again["done_at"] is None, response=r)
        s.check("and says so", any("back on the list" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("A night whose usual rate is the occasion's price needs nothing")
    conn.execute("INSERT INTO room_rate_overrides (room_id, start_date, end_date, price_per_night, "
                 "label, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (queen["id"], (n0 + timedelta(days=17)).isoformat(),
                  (n0 + timedelta(days=17)).isoformat(), 310, TAG, m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    a4, b4 = n0 + timedelta(days=16), n0 + timedelta(days=18)
    occ4, r = _make(oc, conn, TAG + " Fair", a4, b4, {queen["id"]: 310})
    mine = [x for x in _lines(conn, queen["id"], "to_do") if x["occasion_id"] == (occ4 or {"id": -1})["id"]]
    s.check("the Queen Room gets the nights either side, not the middle one",
            [(x["first_night"], x["last_night"]) for x in mine]
            == [(a4.isoformat(), a4.isoformat()), (b4.isoformat(), b4.isoformat())],
            detail=str([(x["first_night"], x["last_night"], x["price"]) for x in mine]))

    s.section("Deleted after it was entered: back to the usual price")
    _mark_done(oc, mine)
    s.check("its two lines are entered", all(
        conn.execute("SELECT state FROM channel_price_pushes WHERE id = ?", (x["id"],)).fetchone()["state"]
        == "done" for x in mine) and len(mine) == 2)
    r = oc.post(f"/admin/stay-occasions/{occ4['id']}/delete", follow_redirects=True)
    back = [x for x in _lines(conn, queen["id"], "to_do") if x["occasion_name"] == TAG + " Fair"]
    s.check("both of its nights go back, still named for it",
            len(back) == 2 and all(x["kind"] == "usual" and x["price"] == USUAL for x in back),
            detail=str([dict(x) for x in back]))
    s.check("and the deletion says so", any("Booking.com: 2 prices to enter" in f for f in flashes(r)),
            detail=str(flashes(r)))
    conn.execute("DELETE FROM room_rate_overrides WHERE room_id = ?", (queen["id"],))
    conn.commit()

    s.section("The rooms as the extranet has them: names and prices")
    r = oc.post("/admin/channels/booking-names",
                data={f"booking_name_{queen['id']}": f"  {TAG}   Queen Room  ",
                      f"booking_nr_{queen['id']}": "300", f"booking_std_{queen['id']}": "340,50",
                      f"booking_name_{king['id']}": KING_EXTRANET}, follow_redirects=True)
    got = conn.execute("SELECT * FROM rooms WHERE id = ?", (queen["id"],)).fetchone()
    s.check("the extranet name is saved, its spaces tidied",
            got["booking_extranet_name"] == f"{TAG} Queen Room",
            detail=repr(got["booking_extranet_name"]), response=r)
    s.check("and the two prices, a comma read as the decimal point",
            (got["booking_nr_price"], got["booking_std_price"]) == (300, 340.5),
            detail=str((got["booking_nr_price"], got["booking_std_price"])))
    s.check("and NOT into channel_name, which Booking.com's mail is matched on",
            got["channel_name"] is None, detail=repr(got["channel_name"]))
    back = [x for x in _lines(conn, queen["id"], "to_do") if x["occasion_name"] == TAG + " Fair"]
    s.check("the Queen Room's lines not yet done now carry figures", back and all(
        (x["nr_price"], x["std_price"]) == (300, 340.5) for x in back), detail=str([dict(x) for x in back]))
    task = _task(conn)
    s.check("and the task stops asking for the name or the prices",
            task is not None and QUEEN not in task["notes"], detail=task and task["notes"])
    oc.post("/admin/channels/booking-names", data={f"booking_name_{queen['id']}": "   "})
    s.check("blank stores nothing, as the room's own form does",
            conn.execute("SELECT booking_extranet_name FROM rooms WHERE id = ?",
                         (queen["id"],)).fetchone()["booking_extranet_name"] is None)

    s.section("A line entered before lines carried an uplift raises nothing new")
    _mark_done(oc, _lines(conn, queen["id"], "to_do"))
    a5, b5 = n0 + timedelta(days=20), n0 + timedelta(days=21)
    occ5, _r = _make(oc, conn, TAG + " Christmas", a5, b5, {king["id"]: 320})
    line = [x for x in _lines(conn, king["id"], "to_do") if x["occasion_id"] == occ5["id"]]
    # As the 36 lines of 25-26 November were: entered, with the house's price
    # only -- no uplift, no Booking.com figures.
    conn.execute("""UPDATE channel_price_pushes SET state = 'done', done_at = ?, uplift = NULL,
                        previous_uplift = NULL, nr_price = NULL, std_price = NULL,
                        previous_nr_price = NULL, previous_std_price = NULL WHERE id = ?""",
                 (m.datetime.now(m.timezone.utc).isoformat(), line[0]["id"]))
    conn.commit()
    s.check("it is given the uplift it carried, +€50 over the €270", m.backfill_booking_price_uplift(conn) == 1
            and tuple(conn.execute("SELECT uplift, previous_uplift FROM channel_price_pushes WHERE id = ?",
                                   (line[0]["id"],)).fetchone()) == (50, 0))
    m.sync_booking_prices(conn, today)
    conn.commit()
    s.check("and the list asks for nothing more on those nights",
            not [x for x in _lines(conn, king["id"], "to_do") if x["occasion_id"] == occ5["id"]],
            detail=str([dict(x) for x in _lines(conn, king["id"], "to_do")]))
    done_html = oc.get("/admin/channels/booking-prices?state=done").get_data(as_text=True)
    row = done_html.split(f'id="line-{line[0]["id"]}"', 1)[-1].split("</tr>", 1)[0]
    row_text = visible_text(row)
    s.check("on the page it shows euro figures, worked out from the room's Booking.com prices now",
            "€340" in row_text and "€380" in row_text and "€290 on other nights" in row_text,
            detail=row_text[:400])
    s.check("marked as worked out after it was entered, so nobody takes it for a record",
            "worked out after it was entered" in row_text and "data-worked-out" in row, detail=row_text[:400])
    s.check("and says where the €50 comes from",
            f"+€50 because {TAG} Christmas is €320 against the house's €270" in row_text, detail=row_text[:400])
    stored = conn.execute("SELECT nr_price, std_price FROM channel_price_pushes WHERE id = ?",
                          (line[0]["id"],)).fetchone()
    s.check("while the line itself still records nothing it was not given",
            tuple(stored) == (None, None), detail=str(tuple(stored)))

    s.section("No page under /admin/channels says \"the usual\", or calls the €50 a rule")
    _nothing_says_the_usual(s, oc, conn, "every line entered")

    s.section("A line whose nights pass before it is entered falls off")
    gone_a, gone_b = today - timedelta(days=5), today - timedelta(days=3)
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO channel_price_pushes (occasion_id, occasion_name, channel, room_id, kind,
               first_night, last_night, price, previous_price, uplift, previous_uplift,
               state, created_at, updated_at)
           VALUES (NULL, ?, 'booking.com', ?, 'occasion', ?, ?, 333, 270, 63, 0, 'to_do', ?, ?)""",
        (TAG + " Long Ago", twin["id"], gone_a.isoformat(), gone_b.isoformat(), now, now))
    m.sync_booking_prices(conn, today)
    conn.commit()
    old = conn.execute("SELECT * FROM channel_price_pushes WHERE occasion_name = ?",
                       (TAG + " Long Ago",)).fetchone()
    s.check("it is cancelled, and says its nights passed",
            old["state"] == "cancelled" and "passed" in (old["note"] or ""), detail=dict(old))


def _nothing_says_the_usual(s, oc, conn, when):
    """Every page under /admin/channels, the JSON and the task, read for
    "the usual" and for the €50 called a rule, a default or a standard."""
    with m.app.test_request_context("/"):
        paths = sorted(r.rule for r in m.app.url_map.iter_rules()
                       if r.rule.startswith("/admin/channels") and "GET" in r.methods and not r.arguments)
    s.check(f"the pages are found ({when})", "/admin/channels/booking-prices" in paths, detail=str(paths))
    said = {}
    for path in paths:
        for q in ("", "?state=all", "?state=done", "?state=cancelled"):
            body = oc.get(path + q).get_data(as_text=True)
            # The page's own words: the staff menu around it has "Standard Weeks".
            said[path + q] = (body if path.endswith(".json") else
                              visible_text(body.split('<main class="page">', 1)[-1].split("</main>", 1)[0]))
    task = conn.execute("SELECT notes FROM tasks WHERE origin = ? AND title = ? ORDER BY id DESC LIMIT 1",
                        (m.WATCH_TASK_ORIGIN, TASK)).fetchone()
    if task:
        said["the task"] = task["notes"] or ""
    usual = [k for k, v in said.items() if "the usual" in v.lower()]
    s.check(f"none says \"the usual\" ({when})", not usual, detail=str(usual))
    ruled = [k for k, v in said.items()
             if re.search(r"\b(rules?|defaults?)\b", v, re.I) or re.search(r"\bstandard\b(?! rate)", v, re.I)]
    s.check(f"and none calls it a rule, a default or a standard ({when}; the Standard Rate is a rate's name)",
            not ruled, detail=str([(k, re.findall(r".{30}\b(?:rules?|defaults?|standard)\b(?! rate).{30}",
                                                   said[k], re.I)[:2]) for k in ruled]))


def _the_rooms_of_26_november(s, conn):
    """The owner's figures for the six rooms, written once where a room has none."""
    s.section("Booking.com's names and prices for the house's rooms (owner, 26 Nov)")
    by_name = {}
    for name, extranet, nr, std in m.BOOKING_COM_ROOMS_2026_11_26:
        rows = conn.execute("SELECT * FROM rooms WHERE name = ?", (name,)).fetchall()
        if len(rows) == 1:
            by_name[name] = (rows[0], extranet, nr, std)
    s.check("the house's rooms are in this database to check", len(by_name) >= 5,
            detail=str(sorted(by_name)))
    for name, (room, extranet, nr, std) in by_name.items():
        s.check(f"{name}: '{extranet}' in the extranet, €{nr:g} / €{std:g}",
                (room["booking_extranet_name"], room["booking_nr_price"], room["booking_std_price"])
                == (extranet, nr, std),
                detail=str((room["booking_extranet_name"], room["booking_nr_price"],
                            room["booking_std_price"])))
    king = by_name.get("The King Suite")
    s.check("the King Suite's guest-facing name is left alone: mail is matched on it",
            king is not None and king[0]["channel_name"] != "King Suite",
            detail=king and repr(king[0]["channel_name"]))

    # Once, and only where none is set: a figure the owner typed since stays.
    put_back = keep_rows(conn, "app_settings", "key = 'booking_com_rooms_2026_11_26'")
    if king:
        room = king[0]
        try:
            conn.execute("DELETE FROM app_settings WHERE key = 'booking_com_rooms_2026_11_26'")
            conn.execute("UPDATE rooms SET booking_nr_price = 399, booking_std_price = NULL, "
                         "booking_extranet_name = '' WHERE id = ?", (room["id"],))
            conn.commit()
            m.apply_booking_com_rooms_2026_11_26(conn)
            got = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
            s.check("run again, it fills only what is missing and keeps the owner's own figure",
                    (got["booking_extranet_name"], got["booking_nr_price"], got["booking_std_price"])
                    == ("King Suite", 399, 425),
                    detail=str((got["booking_extranet_name"], got["booking_nr_price"],
                                got["booking_std_price"])))
            conn.execute("UPDATE rooms SET booking_nr_price = NULL WHERE id = ?", (room["id"],))
            conn.commit()
            m.apply_booking_com_rooms_2026_11_26(conn)
            s.check("and once run, it does not run again",
                    conn.execute("SELECT booking_nr_price FROM rooms WHERE id = ?",
                                 (room["id"],)).fetchone()[0] is None)
        finally:
            conn.execute("UPDATE rooms SET booking_extranet_name = ?, booking_nr_price = ?, "
                         "booking_std_price = ? WHERE id = ?",
                         (room["booking_extranet_name"], room["booking_nr_price"],
                          room["booking_std_price"], room["id"]))
            conn.commit()
            put_back()
