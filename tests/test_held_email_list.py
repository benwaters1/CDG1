"""Held Email is a list like the others, and its bulk actions are the single ones.

WHAT WENT WRONG. With Write to guests off, Held Email is the page that fills
up, and it was the one long list in the house with none of the controls the
others have: the two hundred most recent drawn at once (of 488 in the copy
read on 9 October 2026), the same reason printed on every row, two buttons per
row and no way to see only the letters to guests -- the question the switch
makes worth asking. Its button read "Try sending all 1 again".

WHAT THIS PINS.
  - The chips answer who a letter is for, why it is waiting and how old it is,
    and clicking one gives what it counts.
  - It pages rather than stopping at an arbitrary number.
  - Ticked letters are sent, or discarded, by the same helper the row's own
    button uses, so the guest's record and the mail log say the same thing
    whichever was pressed -- and a ticked letter that has gone since the page
    was drawn is named, not counted as done.
"""
import re

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "zzheld"
GUEST = f"{TAG}.guest@example.invalid"
HOUSE = "accounts@chateaugudanes.com"


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM email_outbox WHERE subject LIKE ?", (TAG.upper() + "%",))]
    conn.executemany("DELETE FROM mail_log WHERE outbox_id = ?", [(i,) for i in ids])
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE ?", (TAG.upper() + "%",))
    conn.execute("DELETE FROM guest_messages WHERE subject LIKE ?", (TAG.upper() + "%",))
    conn.execute("DELETE FROM mail_log WHERE subject LIKE ?", (TAG.upper() + "%",))
    conn.execute("DELETE FROM guests WHERE email = ?", (GUEST,))
    conn.commit()


def _switch(conn, live):
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('guest_mail_live', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 ("1" if live else "0",))
    conn.commit()


def _held(conn, subject):
    return conn.execute("SELECT * FROM email_outbox WHERE subject = ?", (subject,)).fetchone()


def _chip(page, facet_label, chip):
    """The count printed on one chip of one facet row, or None."""
    row = re.search(r'<span class="facet-label">%s</span>(.*?)</div>' % re.escape(facet_label),
                    page, re.S)
    if not row:
        return None
    hit = re.search(r'>%s <span class="chip-n">(\d+)</span>' % re.escape(chip), row.group(1))
    return int(hit.group(1)) if hit else None


def run():
    s = Suite("Held email as a list")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    was = conn.execute("SELECT value FROM app_settings WHERE key = 'guest_mail_live'").fetchone()
    sent = []
    real = (m.resend_enabled, m.send_email_via_resend)
    try:
        _switch(conn, False)
        # A guest the house holds a profile for, which is what makes a letter
        # one "to a guest" rather than to somebody else.
        conn.execute("INSERT INTO guests (name, email, vip, created_at) VALUES (?, ?, 0, ?)",
                     (TAG + " Guest", GUEST, _harness.datetime_now()))
        conn.commit()
        with m.app.test_request_context("/"):
            m.send_email(GUEST, TAG.upper() + " to a guest", "Your stay.")
            m.send_email(GUEST, TAG.upper() + " second to a guest", "Your stay again.")
            m.send_email(GUEST, TAG.upper() + " third to a guest", "And again.")
        m.queue_undelivered(HOUSE, TAG.upper() + " to the house", "Digest.", None, None,
                            "no email provider configured")
        old = _held(conn, TAG.upper() + " third to a guest")
        conn.execute("UPDATE email_outbox SET created_at = ? WHERE id = ?",
                     ("2026-01-05T10:00:00+00:00", old["id"]))
        conn.commit()

        s.section("The chips answer the questions")
        page = oc.get(f"/admin/email-outbox?q={TAG}").get_data(as_text=True)
        s.check("who each letter is for", _chip(page, "To", "A guest") == 3
                and _chip(page, "To", "The house") == 1,
                detail=f"guest {_chip(page, 'To', 'A guest')}, house {_chip(page, 'To', 'The house')}")
        s.check("why it is waiting, in words", _chip(page, "Waiting because",
                                                     "Writing to guests is off") == 3
                and _chip(page, "Waiting because", "Email is not connected") == 1)
        s.check("and how old", _chip(page, "Age", "Too old to send by itself") == 1
                and _chip(page, "Age", "This week") == 3)
        only = oc.get(f"/admin/email-outbox?q={TAG}&to=The+house").get_data(as_text=True)
        s.check("clicking one gives what it counts",
                TAG.upper() + " to the house" in only and TAG.upper() + " to a guest" not in only)
        s.check("and the switch says it is the reason",
                "Write to guests is switched off" in page)

        s.section("It pages rather than stopping")
        total = conn.execute("SELECT COUNT(*) AS c FROM email_outbox "
                             "WHERE sent_at IS NULL").fetchone()["c"]
        if total > m.LIST_PAGE_SIZE:
            first = oc.get("/admin/email-outbox").get_data(as_text=True)
            second = oc.get("/admin/email-outbox?page=2").get_data(as_text=True)
            s.check("a long list says where you are in it",
                    f"of {total}" in first and "Page 1 of" in first, detail=f"{total} waiting")
            s.check("and page two is the next ones, not the first again",
                    "Page 2 of" in second
                    and re.findall(r'name="ids" value="(\d+)"', first)[:3]
                    != re.findall(r'name="ids" value="(\d+)"', second)[:3])
        else:
            s.check("there are few enough here that one page holds them",
                    "list-pager" not in oc.get("/admin/email-outbox").get_data(as_text=True))

        s.section("Ticked letters go the way a single one does")
        m.resend_enabled = lambda: True
        m.send_email_via_resend = (
            lambda to, subj, body, ics=None, name=None, html=None, reply_to=None:
            (sent.append(subj), (True, None))[1])
        a = _held(conn, TAG.upper() + " to a guest")
        gone = 999999999
        r = oc.post("/admin/email-outbox/ticked",
                    data={"action": "send", "ids": [str(a["id"]), str(old["id"]), str(gone)]},
                    follow_redirects=True)
        said = " ".join(flashes(r))
        s.check("the ticked ones are sent, the old one too because it was chosen",
                sorted(sent) == sorted([a["subject"], old["subject"]]), r, detail=str(sent))
        s.check("even with Write to guests off, because somebody chose them",
                _held(conn, a["subject"])["sent_at"] is not None)
        line = conn.execute("SELECT status FROM mail_log WHERE outbox_id = ?",
                            (a["id"],)).fetchone()
        s.check("and the mail log says sent, as the row's own button would have made it",
                line is not None and line["status"] == "sent",
                detail=str(dict(line)) if line else "no line")
        s.check("a ticked letter that had already gone is named, not counted",
                f"letter #{gone}" in said and "no longer waiting" in said, detail=said)

        b = _held(conn, TAG.upper() + " second to a guest")
        h = _held(conn, TAG.upper() + " to the house")
        r = oc.post("/admin/email-outbox/ticked",
                    data={"action": "discard", "ids": [str(b["id"]), str(h["id"])]},
                    follow_redirects=True)
        s.check("discarding the ticked ones takes them out of the queue",
                _held(conn, b["subject"]) is None and _held(conn, h["subject"]) is None, r)
        s.check("and says how many", "Discarded 2 letters" in " ".join(flashes(r)),
                detail=" ".join(flashes(r)))
        line = conn.execute("SELECT status FROM mail_log WHERE outbox_id = ?",
                            (b["id"],)).fetchone()
        s.check("with the mail log marking it thrown away, as a single discard does",
                line is not None and line["status"] == "discarded",
                detail=str(dict(line)) if line else "no line")
        r = oc.post("/admin/email-outbox/ticked", data={"action": "send"},
                    follow_redirects=True)
        s.check("nothing ticked says so", "Nothing was selected" in " ".join(flashes(r)))
    finally:
        m.resend_enabled, m.send_email_via_resend = real
        if was is None:
            conn.execute("DELETE FROM app_settings WHERE key = 'guest_mail_live'")
        else:
            _switch(conn, was["value"] != "0")
        _cleanup(conn)
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
