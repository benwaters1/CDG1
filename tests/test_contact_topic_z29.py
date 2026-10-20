"""The Contact page's "Write to Us" form, as z29 made it (20 October 2026).

The form stopped being "Not sure of the dates / tell us roughly" for nightly
stays: rooms and retreats book online. It asks what the message is about
first (a private event, a wedding, a photoshoot or filming, the whole
château for a group, something else), makes the message required, drops
nights and flexibility, and allows up to 200 guests for an event.

Held here:
  - the topic is said first, in words, in the enquiry's notes and its task;
  - an event goes to the events inbox (experience@), anything else to
    reservations@ as before;
  - a post without a message is refused and nothing is kept;
  - a blank guest count says nothing, and an event's 150 is kept as 150;
  - a topic that is not one of the five, or none (a page cached before z29),
    is "something else", never an error and never stored as typed.
"""
from _harness import Suite, db, flashes
import _harness

m = _harness.m
TAG = "zzctz"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'contact_enquiry'")
    conn.commit()
    conn.close()


def _post(who, **fields):
    sent = []
    real = m.send_email
    m.send_email = lambda to, subject, body, *a, **kw: sent.append((to, subject, body)) or True
    data = {"action": "flexible", "email": f"{TAG}.{who}@example.invalid",
            "name": f"{TAG} {who}", "rough_month": "", "rough_guests": "",
            "message": "We would love to hear what is possible."}
    data.update(_harness.contact_form_fields())
    data.update(fields)
    try:
        r = m.app.test_client().post("/contact", data=data, follow_redirects=True)
    finally:
        m.send_email = real
    conn = db()
    entry = conn.execute("SELECT * FROM waitlist_entries WHERE email = ?",
                         (data["email"],)).fetchone()
    task = conn.execute("SELECT * FROM tasks WHERE waitlist_entry_id = ?",
                        (entry["id"],)).fetchone() if entry else None
    conn.execute("DELETE FROM submission_log WHERE action = 'contact_enquiry'")
    conn.commit()
    conn.close()
    return r, entry, task, [x for x in sent if x[1].startswith("Someone wrote to us")]


def run():
    s = Suite("Write to Us (z29)")
    _cleanup()
    try:
        s.section("A wedding")
        r, entry, task, letters = _post("wedding", topic="wedding", rough_guests="150",
                                        message="Sixty to a hundred and fifty, next June.")
        s.check("is kept", entry is not None, detail="; ".join(flashes(r)))
        notes = (entry["notes"] or "") if entry else ""
        s.check("saying first that it is a wedding", notes.startswith("Wedding."), detail=notes)
        s.check("with the 150 guests it gave, not clamped to 40", "150 guests" in notes, detail=notes)
        s.check("and nothing about nights", "night" not in notes, detail=notes)
        s.check("its task says what it is about", task is not None and "wedding" in task["title"],
                detail=str(task and task["title"]))
        s.check("and it goes to the events inbox",
                [to for to, _s, _b in letters] == [m.house_address_for(db(), "events")],
                detail=str(letters))
        s.check("and the guest is thanked in the new words",
                flashes(r) == ["Thank you. Your message has reached the château, and we will write back."],
                detail=str(flashes(r)))

        s.section("Something else")
        r, entry, task, letters = _post("other", topic="other")
        notes = (entry["notes"] or "") if entry else ""
        s.check("is said as something else", notes.startswith("Something else."), detail=notes)
        s.check("with no guests nobody gave", "guest" not in notes, detail=notes)
        s.check("and goes to reservations@, as before",
                [to for to, _s, _b in letters] == [m.house_address_for(db(), "rooms")],
                detail=str(letters))

        s.section("A topic that is not one of the five")
        r, entry, _t, _l = _post("odd", topic="<script>")
        s.check("is something else, never stored as typed",
                entry is not None and (entry["notes"] or "").startswith("Something else.")
                and "<script>" not in (entry["notes"] or ""), detail=str(entry and entry["notes"]))
        r, entry, _t, _l = _post("cached")
        s.check("and a page from before z29, with no topic, still arrives",
                entry is not None and (entry["notes"] or "").startswith("Something else."))

        s.section("No message")
        r, entry, task, letters = _post("blank", topic="private_event", message="  ")
        s.check("is refused, and nothing is kept", entry is None and task is None and not letters)
        s.check("and the visitor is told what is missing",
                any("what you have in mind" in f for f in flashes(r)), detail=str(flashes(r)))
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
