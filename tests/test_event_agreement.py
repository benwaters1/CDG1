"""What was agreed on an event, the date held while they decide, and the plan.



An enquiry carried a price and a note. The figure could be re-typed by anybody

at any time, nothing was ever signed, and a couple asking "what did we agree

about the marquee" had an email thread to go on — on the most expensive thing

the house sells. A date promised verbally for three weeks was, as far as the

app knew, free, so a room booking could take it and nobody found out until the

wedding was confirmed on top of it. And a €25,000 total had one due date on it,

when a wedding is paid in three or four goes over a year.



Four things carry this file.



  AN ACCEPTED QUOTE CANNOT BE EDITED INTO SAYING SOMETHING ELSE. Re-quoting

  supersedes rather than overwrites, and the terms are copied onto the quote

  when it is raised — so changing the house wording never changes what

  somebody already accepted, and the price agreed in March is still readable

  in September when they ask why it moved.



  ACCEPTING IS NOT CONFIRMING. Confirming blocks the date against every room

  booking in the diary, and that is the house's act rather than the guest's:

  somebody has to look at the calendar. The owner is told instead.



  A HOLD BLOCKS THE DATE AND THEN LETS GO. It stops rooms being sold on a date

  the owner has promised, and lapses on its own so a conversation nobody

  followed up cannot quietly close a season. The house is told when it does.



  THE PLAN IS NOT A LEDGER. Nothing in the instalment table records money.

  Whether a stage has been met is worked out from what the event has actually

  received, oldest first, so there is one record of what came in and a schedule

  that cannot disagree with it.

"""

from datetime import datetime, timedelta, timezone



from _harness import Suite, clients, db, flashes

import _harness



m = _harness.m

TAG = "ZZEA"





def _cleanup():

    conn = db()

    conn.execute("""DELETE FROM event_quotes WHERE event_id IN

                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""", (TAG + "%",))

    conn.execute("""DELETE FROM event_holds WHERE event_id IN

                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""", (TAG + "%",))

    conn.execute("""DELETE FROM event_instalments WHERE event_id IN

                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""", (TAG + "%",))

    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))

    conn.commit()

    conn.close()





def _event(ref, when, *, status="quoted", price=25000):

    conn = db()

    conn.execute(

        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,

           contact_name, contact_email, preferred_date, end_date, guest_count,

           status, quoted_price, amount_paid, created_at)

           VALUES (?, ?, 'wedding', ?, ?, ?, ?, 80, ?, ?, 0, ?)""",

        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",

         f"zzea.{ref}@example.invalid".lower(), when.isoformat(),

         (when + timedelta(days=1)).isoformat(), status, price,

         datetime.now(timezone.utc).isoformat()))

    conn.commit()

    row = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",

                       (f"{TAG}-{ref}",)).fetchone()

    conn.close()

    return row





def _row(event_id):

    conn = db()

    try:

        return conn.execute("SELECT * FROM event_inquiries WHERE id = ?",

                            (event_id,)).fetchone()

    finally:

        conn.close()





def _quotes(event_id):

    conn = db()

    try:

        return conn.execute(

            "SELECT * FROM event_quotes WHERE event_id = ? ORDER BY version",

            (event_id,)).fetchall()

    finally:

        conn.close()





def _free_window(days_out, span=1):

    """Dates the house would actually accept, from roughly here.



    The seeded ateliers hold the whole château for their runs, so a date

    reached by arithmetic alone is refused every so often — and a refused

    action looks exactly like a feature that does not work.

    """

    conn = db()

    try:

        room = conn.execute(

            "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()

        day = m.house_today() + timedelta(days=days_out)

        for _ in range(200):

            ok, _why = m.is_range_available(

                conn, room["id"], day, day + timedelta(days=span + 1))

            if ok:

                return day

            day += timedelta(days=3)

        raise AssertionError("no free window")

    finally:

        conn.close()





def run():

    s = Suite("An event agreement")

    _cleanup()

    oc, ec, _owner, _emp = clients()

    sent = []

    was_email = m.send_email

    m.send_email = lambda to, subj, body, **k: (sent.append((to, subj, body)), True)[1]



    try:

        when = _free_window(300)

        e = _event("W", when)



        s.section("A quote is a version, not a field")

        oc.post(f"/admin/events/{e['id']}/quote", data={

            "quoted_price": "25000", "deposit_amount": "5000",

            "balance_due_date": (when - timedelta(days=30)).isoformat(),

            "guest_count": "80", "includes": "The whole house and the terrace.",

        }, follow_redirects=True)

        qs = _quotes(e["id"])

        s.check("it is raised", len(qs) == 1, detail=f"{len(qs)}")

        s.check("with a version on it", qs and qs[0]["version"] == 1)

        s.check("and the house terms copied onto it",

                qs and (qs[0]["terms"] or "").strip(),

                detail="a couple agreed to the words in front of them that "

                       "day; changing the setting must not change them")

        s.check("nothing has gone out yet", not sent,

                detail="a quote for the largest thing the house sells should "

                       "be readable and correctable before anybody sees it")



        s.section("Sending it")

        oc.post(f"/admin/events/quote/{qs[0]['id']}/send", follow_redirects=True)

        s.check("it goes to the contact",

                sent and sent[0][0] == e["contact_email"], detail=f"{sent[:1]}")

        s.check("with their own link in it",

                sent and qs[0]["token"] in sent[0][2])

        s.check("and it is recorded as sent",

                _quotes(e["id"])[0]["status"] == "sent")



        s.section("What the couple sees")

        page = oc.get(f"/events/quote/{qs[0]['token']}").get_data(as_text=True)

        s.check("the total", "25000.00" in page.replace(",", ""))

        s.check("what it covers", "the terrace" in page)

        s.check("and the terms they are agreeing to",

                "listed monument" in page or "Music outdoors" in page)

        s.check("never indexed", "noindex" in page,

                detail="one couple's money and one couple's day")

        s.check("a made-up token is a 404",

                oc.get("/events/quote/nonsense").status_code == 404)



        s.section("Accepting it")

        sent.clear()

        oc.post(f"/events/quote/{qs[0]['token']}",

                data={"accepted_name": "A Couple"}, follow_redirects=True)

        accepted = _quotes(e["id"])[0]

        s.check("it is accepted", accepted["status"] == "accepted")

        s.check("with the name they typed", accepted["accepted_name"] == "A Couple",

                detail=f"{accepted['accepted_name']!r}")

        s.check("and the date they did it", bool(accepted["accepted_at"]))

        after = _row(e["id"])

        s.check("the agreed figures are on the enquiry",

                abs((after["quoted_price"] or 0) - 25000) < 0.01

                and abs((after["deposit_amount"] or 0) - 5000) < 0.01,

                detail=f"{after['quoted_price']} / {after['deposit_amount']} — "

                       "everything downstream reads the enquiry")

        # ACCEPTING IS NOT CONFIRMING.

        s.check("but the event is not confirmed by it",

                after["status"] != "confirmed",

                detail="confirming blocks the date against every room booking "

                       "in the diary, and somebody has to look at the calendar")

        s.check("the owner is told instead",

                any("accepted" in b.lower() for _to, _s, b in sent),

                detail=f"{[t for t, _s, _b in sent]}")

        s.check("and told the date is not blocked yet",

                any("not blocked" in b.lower() for _to, _s, b in sent),

                detail="the one thing they would otherwise assume")



        s.section("An accepted quote cannot be accepted again")

        oc.post(f"/events/quote/{qs[0]['token']}",

                data={"accepted_name": "Somebody Else"}, follow_redirects=True)

        s.check("the name stands", _quotes(e["id"])[0]["accepted_name"] == "A Couple",

                detail="a forged POST must not rewrite who agreed to it")



        s.section("Re-quoting supersedes, and keeps the old one readable")

        oc.post(f"/admin/events/{e['id']}/quote", data={

            "quoted_price": "27500", "deposit_amount": "5000",

        }, follow_redirects=True)

        qs2 = _quotes(e["id"])

        s.check("there are two", len(qs2) == 2, detail=f"{len(qs2)}")

        s.check("the old one is still there, at its old price",

                abs(qs2[0]["quoted_price"] - 25000) < 0.01,

                detail="the price agreed in March, readable in September when "

                       "they ask why it moved")

        s.check("and it is still marked accepted",

                qs2[0]["status"] == "accepted",

                detail="raising a new quote does not un-agree the old one")

        s.check("only the new one is open",

                m.live_event_quote(db(), e["id"])["version"] == 2)

        old_page = oc.get(f"/events/quote/{qs2[0]['token']}").get_data(as_text=True)

        s.check("and the old link still opens", "25000.00" in old_page.replace(",", ""))



        s.section("A quote replaced before anybody accepted it is kept too")
        # The version above was already accepted, so a break that DELETED
        # superseded quotes left it standing and the checks passed. An
        # unaccepted one is the case that would actually have gone: three
        # re-quotes over a fortnight and no record of the first two.
        spare = _event("SPARE", _free_window(600))
        for price in ("1000", "1200", "1500"):
            oc.post(f"/admin/events/{spare['id']}/quote",
                    data={"quoted_price": price}, follow_redirects=True)
        spares = _quotes(spare["id"])
        s.check("all three re-quotes are still there", len(spares) == 3,
                detail=f"{len(spares)} — the first two were never accepted, "
                       "which is exactly when a price history is worth having")
        s.check("and only the last is open",
                [q["status"] for q in spares] == ["superseded", "superseded", "draft"],
                detail=f"{[q['status'] for q in spares]}")

        s.section("The refusal to re-accept lives in the function, not only the route")
        # The route guards this too, so a break in the function alone changed
        # nothing observable. Called directly, which is how the next caller
        # will reach it.
        conn = db()
        settled = conn.execute(
            "SELECT * FROM event_quotes WHERE event_id = ? AND status = 'accepted'",
            (e["id"],)).fetchone()
        with m.app.test_request_context("/"):
            ok, problem = m.accept_event_quote(conn, settled)
        conn.close()
        s.check("a settled quote refuses", not ok and problem, detail=f"{problem}")

        s.section("Holding the date")

        held = _free_window(400, span=2)

        oc.post(f"/admin/events/{e['id']}/hold", data={

            "start_date": held.isoformat(),

            "end_date": (held + timedelta(days=1)).isoformat(),

            "days": "21",

        }, follow_redirects=True)

        conn = db()

        holds = m.event_holds(conn, e["id"])

        s.check("the hold exists", len(holds) == 1, detail=f"{len(holds)}")

        # THE POINT OF IT. Before this the app had only free or confirmed.

        room = conn.execute(

            "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()

        ok, why = m.is_range_available(conn, room["id"], held, held + timedelta(days=2))

        conn.close()

        s.check("and no room can be sold on those dates", not ok, detail=f"{why}")

        s.check("with a reason that names a person to ring, not a dead end",

                "provisional" in (why or "").lower(),

                detail=f"{why} — 'not available' sends whoever is at the desk "

                       "looking for a booking that does not exist")



        s.section("A hold that overlaps something sold is refused")

        conn = db()

        room = conn.execute(

            "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()

        with m.app.test_request_context("/"):

            hold, error = m.place_event_hold(

                conn, e["id"], held.isoformat(), (held + timedelta(days=1)).isoformat())

        conn.close()

        s.check("the second hold on the same dates is refused",

                hold is None and error, detail=f"{error}")



        s.section("And it lets go on its own")

        conn = db()

        conn.execute(

            "UPDATE event_holds SET expires_at = ? WHERE event_id = ?",

            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), e["id"]))

        conn.commit()

        sent.clear()

        with m.app.test_request_context("/"):

            out = m.lapse_event_holds(conn)

        s.check("it lapses", out["event holds lapsed"] == 1, detail=f"{out}")

        s.check("and says why on the record",

                conn.execute(

                    "SELECT released_reason FROM event_holds WHERE event_id = ?",

                    (e["id"],)).fetchone()["released_reason"] == "it ran out")

        ok_again, _why = m.is_range_available(conn, room["id"], held,

                                              held + timedelta(days=2))

        conn.close()

        s.check("the dates are on the market again", ok_again,

                detail="a promise nobody followed up must not close a season")

        s.check("and the house is told, because that is a weekend to sell",

                any("back on the market" in (subj or "") or "run out" in (b or "")

                    for _to, subj, b in sent),

                detail=f"{[t for t, _s, _b in sent]}")



        s.section("The plan, which is not a ledger")

        conn = db()

        with m.app.test_request_context("/"):

            row1, err1 = m.add_event_instalment(

                conn, e["id"], "Deposit", "5000",

                (m.house_today() + timedelta(days=2)).isoformat())

            row2, err2 = m.add_event_instalment(

                conn, e["id"], "Second", "10000",

                (m.house_today() + timedelta(days=100)).isoformat())

        conn.close()

        s.check("two stages go on", row1 and row2 and not err1 and not err2,

                detail=f"{err1} / {err2}")

        conn = db()

        with m.app.test_request_context("/"):

            state = m.event_schedule(conn, e["id"])

        conn.close()

        # AGAINST THE ACCEPTED QUOTE, not the one just raised. Raising a

        # quote does not change what was agreed -- only accepting one does --

        # so the plan is measured against the 25,000 the couple signed for

        # rather than the 27,500 now in front of them.

        s.check("and the rest is visibly unscheduled",

                abs(state["unplanned"] - (25000 - 15000)) < 0.01,

                detail=f"{state['unplanned']} — measured against the accepted "

                       "quote, since raising one does not change the agreement")

        conn = db()

        with m.app.test_request_context("/"):

            over, over_err = m.add_event_instalment(

                conn, e["id"], "Too much", "20000",

                (m.house_today() + timedelta(days=150)).isoformat())

        conn.close()

        s.check("a stage that would take the plan over the quote is refused",

                over is None and "unscheduled" in (over_err or ""),

                detail=f"{over_err} — a plan that does not add up to the "

                       "agreement is worse than no plan")



        s.section("What has been met is worked out from what came in")

        conn = db()

        with m.app.test_request_context("/"):

            m.record_event_payment(conn, e["id"], 5000, method="bank_transfer")

        conn.commit()

        with m.app.test_request_context("/"):

            state = m.event_schedule(conn, e["id"])

        conn.close()

        s.check("the first stage is met", state["instalments"][0]["met"],

                detail=f"{state['instalments'][0]}")

        s.check("the second is not", not state["instalments"][1]["met"])

        s.check("and it is the next one due",

                state["next_due"]["row"]["label"] == "Second",

                detail=f"{state['next_due']['row']['label']}")

        s.check("nothing in the plan recorded the money",

                state["bill"]["paid"] == 5000,

                detail="the plan is a plan; event_payments and amount_paid are "

                       "the one record of what arrived")



        s.section("Money lands on the earliest stage by DATE, not by typing order")
        # Found by accident: a schedule whose "second payment" was dated before
        # its deposit had the money applied to the second payment, and the
        # chase then asked for the rest of that. Which is right -- oldest by
        # date is what "the next one due" means -- but it is worth pinning,
        # because somebody adding a stage out of order will otherwise think
        # the figures have gone wrong.
        conn = db()
        with m.app.test_request_context("/"):
            early, early_err = m.add_event_instalment(
                conn, e["id"], "Added last, falls due first", "3000",
                (m.house_today() + timedelta(days=1)).isoformat())
            state = m.event_schedule(conn, e["id"])
        conn.close()
        s.check("it goes on the plan", early and not early_err, detail=f"{early_err}")
        s.check("and sorts to the front", 
                state["instalments"][0]["row"]["label"] == "Added last, falls due first",
                detail=f"{[x['row']['label'] for x in state['instalments']]}")
        s.check("so the money already in covers it first",
                state["instalments"][0]["met"],
                detail="oldest by date is what 'the next one due' means")
        oc.post(f"/admin/events/instalment/{early['id']}/delete", follow_redirects=True)
        conn = db()
        left = conn.execute(
            "SELECT COUNT(*) AS c FROM event_instalments WHERE id = ?",
            (early["id"],)).fetchone()["c"]
        with m.app.test_request_context("/"):
            after_delete = m.event_schedule(conn, e["id"])
        conn.close()
        s.check("taking a stage off the plan works", left == 0, detail=f"{left}")
        s.check("and nothing about what came in has changed",
                after_delete["bill"]["paid"] == 5000,
                detail=f"{after_delete['bill']['paid']} — the plan is a plan; "
                       "deleting a stage must not touch the money")

        s.section("The chase asks for the stage, not for everything left")

        conn = db()

        conn.execute("UPDATE event_inquiries SET status = 'confirmed' WHERE id = ?",

                     (e["id"],))

        conn.execute(

            "UPDATE event_instalments SET due_date = ? WHERE event_id = ? AND label = 'Second'",

            ((m.house_today() + timedelta(days=5)).isoformat(), e["id"]))

        conn.commit()

        sent.clear()

        with m.app.test_request_context("/"):

            said = m.run_event_balance_reminder_job(conn, 7)

        conn.close()

        body = " ".join(b for _to, _s, b in sent)


        s.check("they are written to", any(e["contact_email"] == t for t, _s, _b in sent),

                detail=f"{said} / {[t for t, _s, _b in sent]}")

        s.check("for the stage, not the whole remainder",

                "10000" in body.replace(",", "").replace(" ", ""),

                detail="a couple who have paid the deposit are asked for the "

                       "second payment, not for everything")

        s.check("and the stage is stamped, not the event",

                _row(e["id"])["balance_reminder_sent_at"] is None,

                detail="stamping the event would mean one reminder per "

                       "wedding rather than one per instalment")

        conn = db()

        stamped = conn.execute(

            """SELECT reminder_sent_at FROM event_instalments

                WHERE event_id = ? AND label = 'Second'""", (e["id"],)).fetchone()

        conn.close()

        s.check("the stage itself is", bool(stamped["reminder_sent_at"]))

        sent.clear()

        conn = db()

        with m.app.test_request_context("/"):

            m.run_event_balance_reminder_job(conn, 7)

        conn.close()

        s.check("and a second run writes nothing", not sent, detail=f"{sent}")



        s.section("An event with no plan is chased exactly as before")

        plain = _event("PLAIN", _free_window(500), status="confirmed", price=8000)

        conn = db()

        conn.execute(

            "UPDATE event_inquiries SET balance_due_date = ? WHERE id = ?",

            ((m.house_today() + timedelta(days=3)).isoformat(), plain["id"]))

        conn.commit()

        sent.clear()

        with m.app.test_request_context("/"):

            m.run_event_balance_reminder_job(conn, 7)

        conn.close()

        s.check("the single dated balance still chases",

                any(plain["contact_email"] == t for t, _s, _b in sent),

                detail=f"{[t for t, _s, _b in sent]}")

        s.check("and stamps the event, since there is no stage to stamp",

                bool(_row(plain["id"])["balance_reminder_sent_at"]))



        s.section("The page")

        page = oc.get(f"/admin/events/{e['id']}/agreement").get_data(as_text=True)

        s.check("it opens", e["reference_code"] in page)

        s.check("both quotes are on it", "25000.00" in page.replace(",", "")

                and "27500.00" in page.replace(",", ""))

        s.check("and the plan", "Second" in page)

        s.check("it is reachable from the enquiries list",

                f"/admin/events/{e['id']}/agreement"

                in oc.get("/admin/events").get_data(as_text=True),

                detail="a page nobody can get to is a page nobody uses")



        s.section("The wording, edited where the enquiries are")
        conn = db()
        was_terms = m.event_terms(conn)
        conn.close()
        oc.post("/admin/events/terms",
                data={"terms": "No fireworks. The valley is a nature reserve."},
                follow_redirects=True)
        conn = db()
        now_terms = m.event_terms(conn)
        conn.close()
        s.check("it saves", "nature reserve" in now_terms, detail=f"{now_terms[:60]!r}")
        # AND IT DOES NOT REACH BACK. A couple agreed to the words in front of
        # them; editing the house wording afterwards must not change them.
        s.check("the quote somebody already accepted is untouched",
                "nature reserve" not in (_quotes(e["id"])[0]["terms"] or ""),
                detail="the terms are copied onto a quote when it is raised, "
                       "not looked up when it is read")
        oc.post("/admin/events/terms", data={"terms": ""}, follow_redirects=True)
        conn = db()
        s.check("clearing it falls back to the house wording",
                "listed monument" in m.event_terms(conn),
                detail=f"{m.event_terms(conn)[:60]!r}")
        conn.close()

        s.section("Guards")

        s.check("an unknown event is a 404",

                oc.get("/admin/events/999999/agreement").status_code == 404)

        s.check("an employee cannot read an agreement",

                ec.get(f"/admin/events/{e['id']}/agreement").status_code in (302, 403))

        conn = db()

        before_terms = m.event_terms(conn)

        conn.close()

        ec.post("/admin/events/terms", data={"terms": "employee wrote this"})

        conn = db()

        s.check("nor change the wording every quote carries",

                m.event_terms(conn) == before_terms,

                detail="read back, because a refusal and a save are both a 302")

        conn.close()

    finally:

        m.send_email = was_email

        _cleanup()

    return s

