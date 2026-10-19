"""The calendar, read at a glance and opened with a click -- and Outlook the same.

The owner, 17 October 2026: the extras on the calendar were not easy, the
events needed to be clickable, and the bookings needed to show clearer:
arrival, night 1, and so on. Then: the same in Outlook.

What was wrong, each found on the page before this was written:

  A STAY counted from its second night. The arrival said "↓ Name", the next
  day "Name · night 2", so the first night read as somebody else's, and in a
  month cell a long name ellipsed the count away altogether.

  NOTHING OPENED ANYTHING WORTH OPENING. A booking went to the bookings list
  filtered by reference; a dinner to the whole restaurant page; the owner's
  own tasks, and the staff's, went nowhere at all.

  EXTRAS were in the bookings' colour, said only what they were -- "Board",
  for whom? -- never showed as delivered or late, and one with no day chosen
  was on no day of the calendar.

What this holds:

  - Every day of a stay says where it is: arrives (night 1 of 3), nights 2 and
    3 of 3, departs by check-out -- the count in a badge of its own.
  - Every chip opens a card, one per thing, shared by all its chips: the
    stay's dates, guests, nights one by one, its extras with a way to tick
    each off, and the ways to its pages. The owner's tasks open too.
  - What an employee's card offers is what an employee may open.
  - Extras are their own lane: what, how many, for whom; delivered, to
    confirm, overdue; one with no day on the stay's first, "for the stay".
  - Outlook: the stay's nights and extras in the event, never a price or a
    note, and a link that opens the app's calendar with that very card.
"""
import html as _html
from datetime import timedelta
from urllib.parse import urlsplit

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZCARD"


def _cleanup(conn):
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def _all_events(cal):
    return [(c["iso"], e) for c in cal["cells"] for e in c["events"]]


def run():
    s = Suite("the calendar: clearer stays, cards on click, extras, and Outlook")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = m.house_today()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()

    def stay(ref, arrive, nights, name):
        return conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
                 guest_phone, arrival_date, departure_date, party_size, status, special_requests,
                 total_price, created_at)
               VALUES (?, ?, ?, ?, ?, '+33 6 11 22 33 44', ?, ?, 2, 'confirmed',
                       'ZZCARD feather-free pillows', 1234, ?)""",
            (room["id"], f"{TAG}{ref}", f"{TAG}tok{ref}", name, f"{ref.lower()}@example.invalid",
             (today + timedelta(days=arrive)).isoformat(),
             (today + timedelta(days=arrive + nights)).isoformat(), now)).lastrowid

    def extra(booking, name, *, qty=1, status="confirmed", day=None, notes=None):
        return conn.execute(
            """INSERT INTO booking_extras (category, booking_id, name, unit_price, quantity, notes,
                 status, scheduled_for, created_at)
               VALUES ('room', ?, ?, 87.5, ?, ?, ?, ?, ?)""",
            (booking, name, qty, notes, status,
             day.isoformat() if day else None, now)).lastrowid

    arrive = today + timedelta(days=40)
    guest = f"Ines {TAG}Moreau"
    bid = stay("A", 40, 3, guest)
    picnic = extra(bid, f"{TAG} Picnic hamper", qty=2, day=arrive + timedelta(days=1),
                   notes="ZZCARD no nuts please")
    flowers = extra(bid, f"{TAG} Flowers")                        # no day chosen
    cake = extra(bid, f"{TAG} Cake", status="delivered", day=arrive)
    wine = extra(bid, f"{TAG} Wine", status="requested", day=arrive + timedelta(days=2))
    gone = extra(bid, f"{TAG} Cancelled thing", status="cancelled", day=arrive)
    here = stay("B", -1, 3, f"Otto {TAG}Here")
    late = extra(here, f"{TAG} Transfer", day=today - timedelta(days=1))
    task = conn.execute(
        "INSERT INTO tasks (title, due_date, status, assigned_to_user_id, created_at) "
        "VALUES (?, ?, 'open', ?, ?)",
        (f"{TAG} ring the notaire", arrive.isoformat(), owner["id"], now)).lastrowid
    conn.commit()

    # The calendar asks a viewer's role; clients() hands back id and name.
    as_owner = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()
    as_staff = conn.execute("SELECT * FROM users WHERE id = ?", (emp["id"],)).fetchone()
    with m.app.test_request_context("/"):
        cal = m.build_calendar(conn, "month", arrive, viewer=as_owner)
        # The week holding YESTERDAY, where the late extra is. Anchored on
        # today it went red every Monday: yesterday is last week's Sunday.
        week = m.build_calendar(conn, "week", today - timedelta(days=1), viewer=as_owner)
        staff = m.build_calendar(conn, "month", arrive, viewer=as_staff)
    events = _all_events(cal)
    mine = {iso: e for iso, e in events if e["kind"] == "booking" and e["id"] == bid}

    s.section("A stay says which night it is, every day")
    d = [(arrive + timedelta(days=i)).isoformat() for i in range(4)]
    first = mine.get(d[0])
    s.check("the arrival is night 1 of 3",
            first and first["title"].startswith(f"{guest} arrives") and "night 1 of 3" in first["title"],
            detail=first and first["title"])
    s.check("with the count in a badge, out of reach of the ellipsis",
            first and first["badge"] == "1/3", detail=first and first.get("badge"))
    s.check("the next two days are nights 2 and 3 of 3",
            all(mine.get(d[k]) and f"night {k + 1} of 3" in mine[d[k]]["title"]
                and mine[d[k]]["badge"] == f"{k + 1}/3" for k in (1, 2)),
            detail=str([(mine[x]["title"], mine[x].get("badge")) for x in d[1:3] if x in mine]))
    s.check("and a night's short label is the guest, the count being in the badge",
            mine.get(d[1]) and mine[d[1]]["short"] == guest, detail=mine.get(d[1], {}).get("short"))
    last = mine.get(d[3])
    s.check("the departure says out, and by when",
            last and last["badge"] == "out" and f"by {m.CHECK_OUT_TIME}" in last["title"],
            detail=last and last["title"])

    s.section("Every chip opens one card for the thing")
    cards = {c["uid"]: c for c in cal["cards"]}
    uid = f"booking-{bid}"
    s.check("all four days of the stay open the same card",
            all(mine.get(x) and mine[x]["card"] == uid for x in d) and uid in cards,
            detail=str([mine[x].get("card") for x in d if x in mine]))
    card = cards.get(uid, {})
    facts = dict(card.get("facts", []))
    s.check("the card has the dates and how many nights",
            "3 nights" in facts.get("Dates", ""), detail=str(facts))
    s.check("and the guests", facts.get("Guests") == 2)
    s.check("its nights one by one, the arrival first and the departure last",
            [w for _d, w in card.get("timeline", [])]
            == [f"Arrives, from {m.CHECK_IN_TIME} · night 1", "Night 2", "Night 3",
                f"Leaves, by {m.CHECK_OUT_TIME}"], detail=str(card.get("timeline")))
    lines = {x["id"]: x for x in card.get("extras", [])}
    s.check("its extras, every one but the cancelled one",
            set(lines) == {picnic, flowers, cake, wine}, detail=str(sorted(lines)))
    s.check("each with how many, and when",
            lines.get(picnic, {}).get("what") == f"{TAG} Picnic hamper ×2"
            and lines.get(flowers, {}).get("when") == "for the stay", detail=str(lines.get(picnic)))
    s.check("one still to do can be ticked off from the card",
            lines.get(picnic, {}).get("tick") and not lines.get(picnic, {}).get("undo"))
    s.check("one delivered can be un-ticked, and one only asked for cannot be ticked",
            lines.get(cake, {}).get("undo") and not lines.get(wine, {}).get("tick")
            and lines.get(wine, {}).get("state") == "to confirm")
    actions = dict(card.get("actions", []))
    with m.app.test_request_context("/"):
        edit = m.url_for("edit_booking", booking_id=bid)
        tasks_page = m.url_for("admin_tasks")
    s.check("the owner's card goes to the booking, and to edit it",
            "Open the booking" in actions and actions.get("Edit") == edit, detail=str(actions))
    own = [e for _iso, e in events if e["kind"] == "task" and e["id"] == task]
    s.check("the owner's own task opens something now",
            own and (own[0]["link"] or "").startswith(tasks_page), detail=own and own[0]["link"])
    staff_cards = {c["uid"]: c for c in staff["cards"]}
    sc = staff_cards.get(uid, {})
    s.check("an employee gets the same stay's card",
            sc and dict(sc["facts"]).get("Dates") == facts.get("Dates"))
    s.check("without the owner's pages on it",
            sc and not sc["actions"] and not any(edit in str(a) for a in sc["actions"]),
            detail=str(sc.get("actions")))
    s.check("but with the ticking off, which is theirs to do",
            sc and any(x.get("tick") for x in sc["extras"]))

    s.section("Extras are their own lane, and say whose and how they stand")
    xs = {e["id"]: (iso, e) for iso, e in events if e["kind"] == "extra"}
    s.check("in a lane of their own",
            xs.get(picnic) and xs[picnic][1]["lane"] == "extra", detail=str(xs.get(picnic)))
    s.check("on their day, saying what, how many and for whom",
            xs.get(picnic) and xs[picnic][0] == d[1]
            and xs[picnic][1]["short"] == f"{TAG} Picnic hamper ×2 · {guest}",
            detail=str(xs.get(picnic)))
    s.check("one with no day chosen is on the stay's first day, for the stay",
            xs.get(flowers) and xs[flowers][0] == d[0] and "for the stay" in xs[flowers][1]["title"],
            detail=str(xs.get(flowers)))
    s.check("one delivered is ticked",
            xs.get(cake) and xs[cake][1]["status"] == "delivered" and xs[cake][1]["badge"] == "✓")
    s.check("one only asked for says so",
            xs.get(wine) and "to confirm" in xs[wine][1]["title"])
    s.check("a cancelled one is not there", gone not in xs)
    late_row = [e for _iso, e in _all_events(week) if e["kind"] == "extra" and e["id"] == late]
    s.check("one whose day has passed undelivered is overdue",
            late_row and late_row[0]["overdue"] and "overdue" in late_row[0]["title"],
            detail=str([e["title"] for e in late_row]))

    s.section("The page")
    page = oc.get(f"/calendar?view=week&date={arrive.isoformat()}").get_data(as_text=True)
    s.check("each chip names its card", f'data-card="{uid}"' in page)
    s.check("and the card is on the page to open", f'data-card-for="{uid}"' in page
            and "Night by night" in page and "<dialog" in page)
    with m.app.test_request_context("/"):
        tick = m.url_for("mark_extra_delivered", line_id=picnic)
    s.check("with the form that ticks an extra off", f'action="{tick}"' in page)
    s.check("and Extras is a filter of its own", 'data-filter-lane="extra"' in page)
    staff_page = ec.get(f"/calendar?view=week&date={arrive.isoformat()}").get_data(as_text=True)
    s.check("an employee's page has the card too, without the booking's edit page",
            f'data-card-for="{uid}"' in staff_page and edit not in staff_page)
    r = ec.post(tick, headers={"Referer": f"/calendar?view=week&date={arrive.isoformat()}"})
    state = conn.execute("SELECT status FROM booking_extras WHERE id = ?", (picnic,)).fetchone()
    s.check("ticking it off from the card works, for whoever carried it",
            state["status"] == "delivered", detail=state["status"])
    s.check("and lands back on the calendar", r.status_code == 302
            and "/calendar" in r.headers.get("Location", ""), detail=r.headers.get("Location"))
    conn.execute("UPDATE booking_extras SET status = 'confirmed', delivered_at = NULL, "
                 "delivered_by_user_id = NULL WHERE id = ?", (picnic,))
    conn.commit()

    s.section("Outlook has the same")
    with m.app.test_request_context("/"):
        items = {i["key"]: i for i in m.house_calendar_items(
            conn, today - timedelta(days=5), today + timedelta(days=60))}
        st = items.get(f"stay:{bid}", {})
        body = m.graph_event_body(st) if st else {}
        ics = m.house_calendar_ics_text([st], "test") if st else ""
    s.check("the stay's event says how many nights",
            "3 nights" in st.get("subject", ""), detail=st.get("subject"))
    s.check("and which they are, arrival and departure",
            "3 nights: " in st.get("body", "") and f"by {m.CHECK_OUT_TIME}" in st.get("body", ""),
            detail=st.get("body"))
    s.check("and its extras",
            f"{TAG} Picnic hamper ×2" in st.get("body", "") and f"{TAG} Flowers (for the stay)"
            in st.get("body", ""), detail=st.get("body"))
    sent = str(body)
    s.check("never an extra's price or its note, nor the guest's requests",
            "87.5" not in sent and "no nuts" not in sent and "feather-free" not in sent
            and "1234" not in sent, detail=sent[:300])
    s.check("sent as HTML, with a link Outlook can open",
            body.get("body", {}).get("contentType") == "html"
            and '<a href="' in body.get("body", {}).get("content", ""))
    link = st.get("link", "")
    s.check("the link is the app's calendar on the arrival day, with this card",
            f"/calendar?view=day&date={d[0]}" in link and link.endswith(f"#ev-{uid}"), detail=link)
    parts = urlsplit(link)
    landing = oc.get(f"{parts.path}?{parts.query}")
    s.check("and that page has the card the link names",
            f'data-card-for="{uid}"' in landing.get_data(as_text=True), response=landing)
    s.check("the .ics feed carries the link as well", f"URL:{link}" in ics.replace("\r\n ", ""))
    xi = items.get(f"extra:{picnic}", {})
    s.check("an extra's own event says it is an extra, and whose",
            xi.get("subject", "").endswith(f"Extra: {TAG} Picnic hamper ×2 · {guest} · {room['name']}"),
            detail=xi.get("subject"))
    s.check("one delivered is ticked there too", "✓" in items.get(f"extra:{cake}", {}).get("subject", ""))
    s.check("and its link opens its own card",
            xi.get("link", "").endswith(f"#ev-extra-{picnic}"), detail=xi.get("link"))

    s.section("What is typed is shown, not run")
    # The body is the part sent as HTML; an extra's name is in it.
    conn.execute("UPDATE booking_extras SET name = ? WHERE id = ?", (f"<b>{TAG}</b>", flowers))
    conn.commit()
    with m.app.test_request_context("/"):
        st = next(i for i in m.house_calendar_items(conn, today, today + timedelta(days=60))
                  if i["key"] == f"stay:{bid}")
        content = m.graph_event_body(st)["body"]["content"]
    s.check("a name with markup in it goes to Outlook escaped",
            "<b>" not in content and _html.escape(f"<b>{TAG}</b>") in content,
            detail=content[:200])

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
