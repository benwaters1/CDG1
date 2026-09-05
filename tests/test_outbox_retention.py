# -*- coding: utf-8 -*-
"""The second copy of every letter, which nothing ever deleted.

The privacy notice says of what the house writes to a guest: "Kept for two
years after the stay, then deleted." That was true of guest_messages, which
purge_guest_messages clears on exactly that clock, and it was not true of the
copy in email_outbox.

email_outbox is the failure queue. A message that could not be sent is filed
here with its whole body; sending it later sets sent_at and leaves the row
where it is. Nothing has ever removed one except the owner pressing a button.
So the same words the notice promises to delete after two years sat in a
second table indefinitely — 484 of them in a database two seasons old.

WHAT THIS HAS TO GET RIGHT:

  THE SAME CLOCK, NOT A SECOND ONE. Keyed on GUEST_MESSAGE_KEEP_MONTHS, the
  constant the notice states, rather than a number of its own. Two retention
  periods for the same words are two numbers that drift apart, and the notice
  states one.

  SENT OR NOT. After two years a message the house never managed to send is
  not going to be sent, and holding its text is the thing the notice says the
  house does not do. Held mail is refused for sending long before then —
  EMAIL_OUTBOX_STALE_DAYS — so nothing useful is lost.

  AND IT MUST NOT TAKE THIS WEEK'S FAILURES WITH IT. A queue the owner has
  not read yet is the whole point of the page; a purge that clears it is
  worse than no purge, because the failure disappears without anybody seeing
  it.

  IT HAS TO ACTUALLY RUN. A purge nobody calls is a function, not a policy,
  so the daily pass is checked for calling it.
"""
from datetime import timedelta

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "outboxret-"


def _cleanup(conn):
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?",
                 (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The outbox obeys the two years the notice promises")
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    keep = m.GUEST_MESSAGE_KEEP_MONTHS

    def put(name, *, created, sent=None):
        conn.execute(
            """INSERT INTO email_outbox (to_address, subject, body, reason,
                                         created_at, sent_at)
               VALUES (?, ?, ?, 'test', ?, ?)""",
            (TAG + name, "Your stay", "What we told them",
             created.isoformat(), sent.isoformat() if sent else None))
        conn.commit()

    def alive(name):
        return conn.execute(
            "SELECT COUNT(*) AS c FROM email_outbox WHERE to_address = ?",
            (TAG + name,)).fetchone()["c"]

    old = now - timedelta(days=int(keep * 30.44) + 30)
    recent = now - timedelta(days=3)

    put("sent-long-ago", created=old, sent=old + timedelta(days=1))
    put("never-sent-long-ago", created=old)
    put("sent-this-week", created=recent, sent=now)
    put("still-waiting", created=recent)
    # Sent yesterday, but written before the cutoff — the row is kept on the
    # LATER of the two, because it was still live business until it went.
    put("old-but-sent-recently", created=old, sent=now - timedelta(days=1))

    s.check("all five are in the outbox to begin with",
            sum(alive(n) for n in ("sent-long-ago", "never-sent-long-ago",
                                   "sent-this-week", "still-waiting",
                                   "old-but-sent-recently")) == 5)

    result = m.purge_sent_outbox(conn, now=now)
    conn.commit()

    s.check("a letter sent more than two years ago is gone",
            alive("sent-long-ago") == 0)
    s.check("and so is one that was never sent at all",
            alive("never-sent-long-ago") == 0,
            detail="after two years it is not going to be sent, and its text "
                   "is what the notice says the house does not keep")
    s.check("this week's is kept",
            alive("sent-this-week") == 1)
    s.check("and so is a failure the owner has not read yet",
            alive("still-waiting") == 1,
            detail="a purge that clears the queue is worse than no purge — "
                   "the failure disappears without anybody seeing it")
    s.check("a message written long ago but sent yesterday is kept",
            alive("old-but-sent-recently") == 1,
            detail="it was live business until it went")

    s.check("and it says how many it cleared",
            result.get("outbox_messages") == 2, detail=str(result))

    # THE SAME CLOCK. A second retention number for the same words is a
    # second number to forget when the notice changes.
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?",
                 (TAG + "%",))
    edge = now - timedelta(days=int(keep * 30.44) - 5)
    put("just-inside", created=edge, sent=edge)
    m.purge_sent_outbox(conn, now=now)
    conn.commit()
    s.check("a letter five days short of the two years is kept",
            alive("just-inside") == 1,
            detail="the cutoff is GUEST_MESSAGE_KEEP_MONTHS, the number the "
                   "privacy notice states")

    # IT HAS TO RUN. A purge nobody calls is a function, not a policy.
    import inspect
    daily = inspect.getsource(m.run_health_notes_purge_job)
    s.check("the daily pass actually calls it",
            "purge_sent_outbox" in daily,
            detail="a purge nothing calls is a function, not a policy")

    _cleanup(conn)
    conn.close()
    return s
