"""The two guest texts are the owner's words, and off until the owner says.

Both shipped switched on with a sentence the app had made up for each: "we
look forward to seeing you today" on the morning of arrival, and "Keys in the
bowl by the door" the evening before departure -- there is no bowl rule at the
house anybody had agreed to. So every guest with a mobile would have been told
something nobody at the house wrote, on the day the provider went live.

What this holds:

  - BOTH SWITCHES DEFAULT OFF, and a house that already had them on is turned
    off ONCE, never again, so an owner who switches one back on is not
    overruled on the next deploy.
  - THERE IS NO DEFAULT WORDING. A text with nothing written is not sent by
    the job, by Send these now, or switched on, and it stamps nobody, so
    writing the words later still reaches the guests already in the window.
  - EVERY PAGE SAYS SO. Automation says "No wording yet -- write it here"
    beside the switch; the booking page lists no text that is off or
    unwritten; the guest's own page promises no text that will not come.
  - THE LABELS SAY WHAT THE CODE DOES: arrival the morning of arrival,
    departure the evening before departure.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, keep_rows
import _harness

m = _harness.m
TAG = "ZTGTW"
PHONE = "+33677777771"
INVENTED = ("look forward to seeing you today", "Keys in the bowl")
SETTINGS = ("automation_checkin_text_enabled", "automation_checkout_text_enabled",
            "sms_checkin_template", "sms_checkout_template", "guest_texts_off_done")


def _set(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                     (key, value))
    conn.commit()


def _get(conn, key):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _cleanup(conn):
    conn.execute("DELETE FROM sms_outbox WHERE phone = ?", (PHONE,))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _stay(conn, ref, arrive_in=0, nights=2):
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    arrive = m.house_today() + timedelta(days=arrive_in)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, created_at)
           VALUES (?, ?, ?, 'Odile Marchand', 'o@example.invalid', ?, ?, ?, 2,
                   'confirmed', 400, ?)""",
        (room, TAG + ref, (TAG + "tok" + ref).lower(), PHONE, arrive.isoformat(),
         (arrive + timedelta(days=nights)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                        (TAG + ref,)).fetchone()


def _switch_row(html, key):
    """The Automation page's row for one switch, and nothing past it."""
    start = html.find(f'id="{key}"')
    if start < 0:
        return ""
    end = html.find('class="auto-row', start + 1)
    return html[start:end if end > 0 else len(html)]


def _texts_block(html):
    start = html.find("Texts the house sends by itself")
    if start < 0:
        return ""
    return html[start:html.find("</ul>", start)]


def run():
    s = Suite("Guest texts: the owner's words, off until switched on")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = keep_rows(conn, "app_settings",
                         "key IN (%s)" % ",".join("?" * len(SETTINGS)), SETTINGS)
    _cleanup(conn)
    try:
        _suite(s, oc, conn)
    finally:
        _cleanup(conn)
        put_back()
        conn.close()
    return s


def _suite(s, oc, conn):
    s.section("Off by default")
    for key in ("automation_checkin_text_enabled", "automation_checkout_text_enabled"):
        s.check(f"{key} ships off", m.AUTOMATION_SETTING_DEFAULTS.get(key) == "0",
                detail=repr(m.AUTOMATION_SETTING_DEFAULTS.get(key)))
    for key in SETTINGS[:4]:
        _set(conn, key, None)
    s.check("a house that never touched them reads them as off",
            not m.guest_text_on(conn, "checkin") and not m.guest_text_on(conn, "checkout"))

    s.section("There is no default wording")
    for kind, spec in m.GUEST_TEXTS.items():
        s.check(f"the {kind} text carries no wording of its own",
                "default" not in spec
                and not any(w in str(v) for v in spec.values() for w in INVENTED),
                detail=sorted(spec))
    s.check("the old name for the arrival default is gone",
            not hasattr(m, "CHECKIN_TEXT_DEFAULT"))
    booking = _stay(conn, "TODAY")
    with m.app.test_request_context():
        bodies = {k: m.guest_text_body(conn, booking, k) for k in m.GUEST_TEXTS}
    s.check("with nothing written, each text's body is empty",
            bodies == {"checkin": "", "checkout": ""}, detail=repr(bodies))

    s.section("Switched on with nothing written, nothing goes")
    _set(conn, "automation_checkin_text_enabled", "1")
    with m.app.test_request_context():
        said = m.run_guest_text_job(conn, "checkin")
    conn.commit()
    s.check("the run says it has no wording", "no wording" in said, detail=said)
    s.check("nothing is written to the outbox",
            conn.execute("SELECT COUNT(*) AS c FROM sms_outbox WHERE phone = ?",
                         (PHONE,)).fetchone()["c"] == 0)
    s.check("and the stay is not stamped as told",
            conn.execute("SELECT checkin_text_sent_at FROM bookings WHERE id = ?",
                         (booking["id"],)).fetchone()["checkin_text_sent_at"] is None,
            detail="a stamp now would mean the guest never gets it once the "
                   "owner does write something")
    try:
        m.run_checkin_text_job(conn)
        raised = None
    except m.JobNotSetUp as e:
        raised = str(e)
    s.check("on the schedule it is 'Couldn't run', not 'Worked'",
            raised is not None and "no wording" in raised, detail=repr(raised))
    r = oc.post("/management/texting/run-checkin", data={"kind": "checkin"},
                follow_redirects=True)
    s.check("and Send these now says so too",
            any("no wording" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("The Automation page")
    _set(conn, "automation_checkin_text_enabled", None)
    html = oc.get("/admin/automation").get_data(as_text=True)
    arrive_row = _switch_row(html, "automation_checkin_text_enabled")
    leave_row = _switch_row(html, "automation_checkout_text_enabled")
    s.check("the arrival switch says it has no wording yet",
            "No wording yet" in arrive_row and "write it here" in arrive_row,
            detail=arrive_row[:300])
    s.check("and links to where it is written",
            "/management/texting#text-checkin" in arrive_row, detail=arrive_row[:400])
    s.check("so does the departure switch",
            "No wording yet" in leave_row
            and "/management/texting#text-checkout" in leave_row)
    s.check("the arrival text is called the morning they arrive",
            "Text a guest the morning they arrive" in arrive_row,
            detail=arrive_row[:200])
    s.check("the departure text the evening before they leave",
            "Text a guest the evening before they leave" in leave_row,
            detail=leave_row[:200])
    s.check("and neither keeps its old wrong label",
            "the day before they arrive" not in html
            and "the day they leave" not in html)
    s.check("both switches are drawn unticked",
            "checked" not in arrive_row.split("</label>")[0]
            and "checked" not in leave_row.split("</label>")[0])

    _set(conn, "sms_checkin_template", "Bonjour {guest_name}. {manage_url}")
    html = oc.get("/admin/automation").get_data(as_text=True)
    s.check("once written, the arrival switch stops saying so",
            "No wording yet" not in _switch_row(html, "automation_checkin_text_enabled"))
    s.check("while the unwritten one still does",
            "No wording yet" in _switch_row(html, "automation_checkout_text_enabled"))
    _set(conn, "sms_checkin_template", None)

    s.section("The texting page")
    html = oc.get("/management/texting").get_data(as_text=True)
    s.check("an unwritten text says so where it is written",
            html.count("No wording yet — write it here") >= 2, detail="")
    s.check("its box is empty rather than filled with something made up",
            not any(w in html for w in INVENTED))
    s.check("the arrival text is labelled the morning of arrival",
            "The arrival text — sent the morning of arrival" in html)
    s.check("the departure text the evening before departure",
            "The departure text — sent the evening before departure" in html)
    s.check("and the button sends today's arrivals, which is what it does",
            "Send today's arrival texts now" in html
            and "tomorrow's arrival" not in html)

    s.section("The booking page lists only texts that will go")
    page = oc.get(f"/admin/bookings/{booking['id']}")
    html = page.get_data(as_text=True)
    s.check("the booking page opens", page.status_code == 200, detail=str(page.status_code))
    s.check("off and unwritten: no texts block at all",
            "Texts the house sends by itself" not in html, detail=_texts_block(html)[:200])

    _set(conn, "automation_checkin_text_enabled", "1")
    _set(conn, "automation_checkout_text_enabled", "1")
    html = oc.get(f"/admin/bookings/{booking['id']}").get_data(as_text=True)
    s.check("on but unwritten: still nothing",
            "Texts the house sends by itself" not in html, detail=_texts_block(html)[:200])

    for key in ("automation_checkin_text_enabled", "automation_checkout_text_enabled"):
        _set(conn, key, None)
    _set(conn, "sms_checkin_template", "Bonjour {guest_name}. {manage_url}")
    html = oc.get(f"/admin/bookings/{booking['id']}").get_data(as_text=True)
    s.check("written but off: still nothing",
            "Texts the house sends by itself" not in html, detail=_texts_block(html)[:200])

    later = _stay(conn, "LATER", arrive_in=5, nights=3)
    _set(conn, "automation_checkin_text_enabled", "1")
    block = _texts_block(oc.get(f"/admin/bookings/{later['id']}").get_data(as_text=True))
    s.check("on and written: the arrival text is listed",
            "The arrival text" in block, detail=block[:300])
    s.check("going the morning of arrival",
            "goes the morning of arrival" in block, detail=block[:300])
    s.check("while the departure text, unwritten, is not",
            "The departure text" not in block, detail=block[:300])

    _set(conn, "sms_checkout_template", "Bonjour {guest_name}. {statement_url}")
    _set(conn, "automation_checkout_text_enabled", "1")
    block = _texts_block(oc.get(f"/admin/bookings/{later['id']}").get_data(as_text=True))
    s.check("the departure text, once on and written, goes the evening before departure",
            "The departure text" in block and "goes the evening before departure" in block,
            detail=block[:400])
    s.check("and no block on the page quotes made-up words",
            not any(w in block for w in INVENTED))

    conn.execute("UPDATE bookings SET checkin_text_sent_at = ? WHERE id = ?",
                 (datetime.now(timezone.utc).isoformat(), later["id"]))
    conn.commit()
    for key in ("automation_checkin_text_enabled", "automation_checkout_text_enabled",
                "sms_checkin_template", "sms_checkout_template"):
        _set(conn, key, None)
    block = _texts_block(oc.get(f"/admin/bookings/{later['id']}").get_data(as_text=True))
    s.check("a text already sent is still recorded after it is switched off",
            "The arrival text" in block and "sent" in block, detail=block[:300])

    s.section("The guest's own page promises only a text that will come")
    html = oc.get(f"/book/manage/{later['manage_token']}").get_data(as_text=True)
    s.check("off: no promise of a text",
            "will text you" not in html, detail="")
    _set(conn, "sms_checkin_template", "Bonjour {guest_name}. {manage_url}")
    _set(conn, "automation_checkin_text_enabled", "1")
    html = oc.get(f"/book/manage/{later['manage_token']}").get_data(as_text=True)
    s.check("on and written: it says the morning they arrive",
            "text you on the morning you arrive" in html)
    s.check("and never the day before", "text you the day before" not in html)

    s.section("A house that already had them on is turned off, once")
    _set(conn, "guest_texts_off_done", None)
    _set(conn, "automation_checkin_text_enabled", "1")
    _set(conn, "automation_checkout_text_enabled", "1")
    _set(conn, "sms_checkin_template",
         "Bonjour {guest_name}, we look forward to seeing you today. "
         "How to find us: {manage_url}")
    _set(conn, "sms_checkout_template",
         "Bonjour {guest_name}, checkout is 11am. Keys in the bowl by the door. "
         "Bill: {statement_url}")
    did = m.switch_guest_texts_off_once(conn)
    s.check("the first run does it", did is True)
    s.check("both switches are off",
            _get(conn, "automation_checkin_text_enabled") == "0"
            and _get(conn, "automation_checkout_text_enabled") == "0")
    s.check("a saved copy of the old made-up arrival wording is removed",
            _get(conn, "sms_checkin_template") is None,
            detail=repr(_get(conn, "sms_checkin_template")))
    s.check("and the departure one, whatever checkout time it carried",
            _get(conn, "sms_checkout_template") is None,
            detail=repr(_get(conn, "sms_checkout_template")))
    s.check("and it records that it has been done",
            _get(conn, "guest_texts_off_done") is not None)

    _set(conn, "automation_checkin_text_enabled", "1")
    _set(conn, "sms_checkin_template", "Our own words. {manage_url}")
    again = m.switch_guest_texts_off_once(conn)
    s.check("a second run does nothing", again is False)
    s.check("so the owner switching a text back on is not overruled",
            _get(conn, "automation_checkin_text_enabled") == "1")
    s.check("nor their wording touched",
            _get(conn, "sms_checkin_template") == "Our own words. {manage_url}")

    _set(conn, "guest_texts_off_done", None)
    m.switch_guest_texts_off_once(conn)
    s.check("and the owner's own wording survives even the first run",
            _get(conn, "sms_checkin_template") == "Our own words. {manage_url}",
            detail="only the shape of the old made-up sentence is removed")


if __name__ == "__main__":
    print(run().report())
