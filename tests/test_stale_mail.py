"""Held mail that has gone past sending.

A message the app could not send is kept rather than lost. Nothing looked at
how old it had got, so "try sending all" meant all of it — and the day a
provider is connected, the château would deliver whatever had accumulated.
The outbox in this database is holding four hundred and eighty-three messages
from two days in August, a hundred and eighty-three of them to a real address.

The principle was already in the code and applied to one message only:
send_email takes keep=False for the waitlist notice, with the reason written
out — a stale one is worse than none, because the room that came free three
weeks ago has gone. A booking acknowledgement is no different. Those are kept
instead, and were kept for ever.

WHAT IS HELD HERE

  A CURRENT MESSAGE GOES AND AN OLD ONE DOES NOT, on the same press. Not one
  or the other: the batch has to be able to do both, or the feature is just a
  switch that turns sending off.

  IT SAYS WHAT IT LEFT. Through bulk_message, which names items rather than
  counting them and is an error the moment anything is skipped — because a
  bulk action that half worked is exactly the thing that must not look clean.

  ONE CAN STILL BE SENT BY HAND, however old, because that is somebody
  looking at a particular message and deciding. The message back says it was
  stale, so the decision is informed rather than accidental.

  A MESSAGE WITH NO READABLE DATE IS NOT CALLED STALE. Guessing that way
  throws mail away on the strength of a bad timestamp.

  AND THE OWNER IS TOLD, but only while there are old ones. "Four hundred are
  held because no provider is configured" is true every morning until one is
  configured, and a line that cannot go away is furniture.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZSTALE"


class _MailWorks:
    """A provider that accepts, and still sends nothing.

    The outbox refuses to do anything at all without one configured, so
    without this every check below would be testing the "no provider" branch.
    Strictly less capable than the harness's own stub: it reports success and
    reaches nobody.
    """

    def __enter__(self):
        self.real_send = m.send_email
        self.real_enabled = m.resend_enabled
        self.sent = []

        def _accept(to, subject, body, *a, **kw):
            self.sent.append((to, subject))
            return True

        m.send_email = _accept
        m.resend_enabled = lambda: True
        return self

    def __exit__(self, *_exc):
        m.send_email = self.real_send
        m.resend_enabled = self.real_enabled
        return False


def _cleanup(conn):
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE details LIKE ?", ("%" + TAG + "%",))
    conn.commit()


def run():
    s = Suite("held mail that has gone past sending")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    def hold(subject, days_ago, address=None):
        when = (m.datetime.now(m.timezone.utc)
                - timedelta(days=days_ago)).isoformat()
        conn.execute(
            """INSERT INTO email_outbox (to_address, subject, body, reason,
                       attempts, created_at)
               VALUES (?, ?, 'body', 'no email provider configured', 0, ?)""",
            (address or f"{TAG}.{days_ago}@example.invalid".lower(),
             TAG + " " + subject, when))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    # Everything already in this database is months old, so it would drown the
    # counts below. The checks look at the rows this suite made.
    fresh = hold("fresh confirmation", 1)
    borderline = hold("just inside", m.EMAIL_OUTBOX_STALE_DAYS - 1)
    old = hold("august confirmation", 40)
    older = hold("older still", 90)
    # created_at is NOT NULL, so the unreadable case is a MALFORMED stamp
    # rather than a missing one -- a bad write or something a migration left.
    # Worth having: the page counts stale rows with a string comparison in
    # SQL while held_mail_stale parses, and the two have to agree about a
    # value neither can read.
    conn.execute(
        """INSERT INTO email_outbox (to_address, subject, body, reason,
                   attempts, created_at)
           VALUES (?, ?, 'body', 'no email provider configured', 0,
                   'not a timestamp')""",
        (f"{TAG}.none@example.invalid".lower(), TAG + " no date at all"))
    undated = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    def row(rid):
        return conn.execute("SELECT * FROM email_outbox WHERE id = ?",
                            (rid,)).fetchone()

    def gone(rid):
        return row(rid) is None

    def sent_at(rid):
        r = row(rid)
        return r["sent_at"] if r else "(deleted)"

    s.section("What counts as too old")
    s.check("a message held yesterday is current",
            not m.held_mail_stale(row(fresh)))
    s.check(f"one held {m.EMAIL_OUTBOX_STALE_DAYS - 1} days is still current",
            not m.held_mail_stale(row(borderline)),
            detail=f"the line is {m.EMAIL_OUTBOX_STALE_DAYS} days")
    s.check("one held forty days is not", m.held_mail_stale(row(old)))
    s.check("and one whose date cannot be read is NOT called stale",
            not m.held_mail_stale(row(undated)),
            detail="throwing a message away on the strength of a bad "
                   "timestamp is the wrong direction to guess in")
    s.check("its age is unknown rather than nought",
            m.held_mail_age_days(row(undated)) is None,
            detail=str(m.held_mail_age_days(row(undated))))

    s.section("Sending them all sends the current ones and leaves the rest")
    with _MailWorks() as mail:
        r = oc.post("/admin/email-outbox/send", follow_redirects=True)
    said = " ".join(flashes(r))
    subjects = [sub for _to, sub in mail.sent]
    s.check("the fresh one went",
            any("fresh confirmation" in x for x in subjects),
            detail=str(subjects[:4]))
    s.check("and the one just inside the line went too",
            any("just inside" in x for x in subjects),
            detail="a rule that stops the batch sending anything is a rule "
                   "that turned sending off")
    s.check("the forty-day-old one did not",
            not any("august confirmation" in x for x in subjects))
    s.check("nor the ninety-day-old one",
            not any("older still" in x for x in subjects))
    s.check("the fresh one is marked as sent", sent_at(fresh))
    s.check("and the old one is still waiting, not lost",
            row(old) is not None and not sent_at(old))

    s.section("And it says what it left behind")
    s.check("the message names one of them",
            "august confirmation" in said or "older still" in said,
            detail=said[:200] or "nothing was said")
    s.check("with how old it was", "days" in said, detail=said[:200])
    # Asked of the flash's category, not of words in it. The first version
    # had an `or` clause that was true whatever the category was, so it would
    # have passed on a cheerful green banner.
    page = r.get_data(as_text=True)
    at = page.find("Sent ")
    s.check("and it is flashed as an error, not a success",
            "flash-error" in page[max(0, at - 200):at + 40] if at >= 0
            else False,
            detail="a bulk action that half worked must not look clean")

    s.section("One can still be sent by hand, however old")
    with _MailWorks() as mail:
        r = oc.post("/admin/email-outbox/send", data={"id": str(old)},
                    follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("it goes", any("august confirmation" in sub for _to, sub in mail.sent),
            detail=str(mail.sent))
    s.check("and the page says it had been waiting",
            "waiting" in said and "days" in said,
            detail=said[:160] or "nothing was said")
    s.check("so the choice is an informed one",
            "chose" in said or "rather than the batch" in said,
            detail=said[:160])

    s.section("Discarding the old ones")
    # A current message that is still WAITING at this point. Everything else
    # current has just been sent, so without this the check that the discard
    # spares them passes for want of anything to spare -- which is exactly
    # what its control found.
    still_waiting = hold("held after the batch", 2)
    conn.commit()
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE sent_at IS NULL "
        "AND subject LIKE ?", (TAG + "%",)).fetchone()["c"]
    r = oc.post("/admin/email-outbox/discard-stale", follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("the ninety-day-old one is gone", gone(older))
    s.check("the undated one is NOT", not gone(undated),
            detail="it was never called stale, so it is not swept up with them")
    s.check("nor is the one just inside the line", not gone(borderline))
    s.check("and a current message still waiting is untouched",
            not gone(still_waiting) and not sent_at(still_waiting),
            detail="discarding the old ones must not take the ones that are "
                   "still worth sending with them")
    s.check("the message says how many and how old",
            "Discarded" in said and "days" in said, detail=said[:160])
    s.check("and it is in the audit log",
            conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log "
                "WHERE action = 'email_outbox_discarded_stale'").fetchone()["c"] >= 1)
    s.check("without writing the recipients into it",
            conn.execute(
                "SELECT COUNT(*) AS c FROM audit_log WHERE details LIKE ?",
                ("%@example.invalid%",)).fetchone()["c"] == 0,
            detail="recording that mail was thrown away by listing who it was "
                   "for keeps the thing being disposed of")
    s.check("something was actually discarded", before > 0)

    s.section("An employee cannot")
    fresh2 = hold("employee attempt", 40)
    conn.commit()
    ec.post("/admin/email-outbox/discard-stale", follow_redirects=True)
    s.check("the message is still there", not gone(fresh2),
            detail="refusing and succeeding both redirect, so what settles it "
                   "is whether the row went")

    s.section("The owner is told, and only while there is something to do")
    with m.app.test_request_context("/"):
        warns = m.owner_home_warnings(conn, house_today())
    titles = [w["title"] for w in warns]
    s.check("the panel says held mail has gone past sending",
            any("too old to send" in t for t in titles), detail=str(titles))
    hit = next((w for w in warns if "too old to send" in w["title"]), None)
    s.check("and links to the page that can act on it",
            hit and "email-outbox" in hit["href"], detail=str(hit))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
