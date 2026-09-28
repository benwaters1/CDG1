"""Two controls that were on the page and doing nothing.

Both are the same shape of fault: something a guest can see and use, which
looks exactly as it looks when it works, and which has no effect at all. Not
one line of either would have shown up in a log.

THE CURRENCY PICKER. Every public page offered euros, dollars, pounds and
Australian dollars, and the conversion was fetched from the GUEST'S BROWSER --
which the browser refuses, for want of a CORS header on the reply. The catch
handler passes null, paint() returns early, and the figure on the page never
changes. A guest picks USD and the price stays in euros. Seen with a real
browser against a real page: the fetch is blocked, and nothing anywhere says
so. Now the house fetches it, daily, and the rates are rendered into the HTML
-- so it also works with javascript off, and cannot be broken by a third party
declining to send a header.

THE REPLY-TO. The house runs five inboxes and routes incoming mail between
them. Everything it SENT went from one address with no Reply-To, so a guest
hitting Reply on a dinner confirmation wrote to whichever inbox RESEND_FROM
happened to name. Nobody could tell, because a reply that lands in the wrong
inbox still lands -- it is just answered by the wrong person, or late, or not
at all.

The rule for both: WHEN IT CANNOT DO THE JOB IT DOES NOT DRAW THE CONTROL.
No rates means no currency selector, rather than one that changes nothing. No
matching mailbox means no Reply-To header, rather than a guessed address that
sends a guest's reply somewhere the house does not read.
"""
from _harness import Suite, clients, db

import json

import _harness

m = _harness.m


def _set_rates(conn, payload):
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (m.FX_SETTING, json.dumps(payload)))
    conn.commit()


def run():
    s = Suite("rates the house fetches, replies that reach the right inbox")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    anon = m.app.test_client()
    was = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                       (m.FX_SETTING,)).fetchone()

    s.section("The rates the house holds")

    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.FX_SETTING,))
    conn.commit()
    fx = m.exchange_rates(conn)
    s.check("with nothing fetched there are no rates", fx["rates"] == {},
            detail=str(fx["rates"]))
    s.check("and it says so rather than pretending", fx["stale"] is True)

    now = m.datetime.now(m.timezone.utc)
    _set_rates(conn, {"rates": {"USD": 1.16, "GBP": 0.86, "AUD": 1.61},
                      "at": now.isoformat()})
    fx = m.exchange_rates(conn)
    s.check("a fresh fetch is used", fx["rates"].get("USD") == 1.16, detail=str(fx))
    s.check("and is not called stale", fx["stale"] is False)

    # Old enough to be wrong, and the honest answer is to say nothing.
    old = (now - m.timedelta(days=m.FX_STALE_DAYS + 1)).isoformat()
    _set_rates(conn, {"rates": {"USD": 9.99}, "at": old})
    fx = m.exchange_rates(conn)
    s.check("rates older than the limit are not offered", fx["rates"] == {},
            detail="a rate from last month under a dollar sign is worse than "
                   "no dollar sign: %s" % fx["rates"])
    s.check("though the page can still see it was held",
            fx["held"].get("USD") == 9.99,
            detail="so the owner's side can say 'we have rates and they are "
                   "too old' rather than the same nothing a fresh install shows")

    s.section("What the guest is actually shown")

    _set_rates(conn, {"rates": {"USD": 1.16, "GBP": 0.86}, "at": now.isoformat()})
    body = anon.get("/").get_data(as_text=True)
    s.check("the rates are in the page itself",
            "1.16" in body, detail="rendered by the house, so the conversion "
                                   "needs no third party and no javascript")
    s.check("and the selector is offered", 'id="g-currency"' in body)
    # The fault this replaced: a control that cannot do its job, still drawn.
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.FX_SETTING,))
    conn.commit()
    body = anon.get("/").get_data(as_text=True)
    s.check("with no rates the selector is not drawn at all",
            'id="g-currency"' not in body,
            detail="an inert control is worse than none — it was on every "
                   "page for months changing nothing when it was used")
    s.check("and the page still works", anon.get("/").status_code == 200)

    # And nothing reaches the network to get them.
    s.check("the fetch itself is blocked under test",
            m.fetch_exchange_rates.__name__ == "_blocked",
            detail="the whole point of moving this off the guest's browser is "
                   "that the house makes the call; a suite that made it would "
                   "make it hundreds of times a run")

    s.section("Where a reply goes")

    was_boxes = m.MS_GRAPH_MAILBOXES
    m.MS_GRAPH_MAILBOXES = ["bookings@chateaugudanes.com",
                            "restaurant@chateaugudanes.com",
                            "experience@chateaugudanes.com"]
    try:
        s.check("a dinner reply goes to the restaurant",
                m.reply_to_for("restaurant") == "restaurant@chateaugudanes.com")
        s.check("a stay reply goes to bookings",
                m.reply_to_for("rooms") == "bookings@chateaugudanes.com")
        s.check("an atelier reply goes to experience",
                m.reply_to_for("workshops") == "experience@chateaugudanes.com",
                detail="the inbox is named experience@, not workshops@, and "
                       "the map knows both names for the same area")
        # The refusal to guess. events@ is not configured here.
        s.check("an area with no inbox gets no Reply-To",
                m.reply_to_for("events") is None,
                detail="a guessed address sends a guest's reply somewhere "
                       "nobody reads, which is worse than the single inbox "
                       "this replaced")
        s.check("and neither does a letter with no area at all",
                m.reply_to_for(None) is None)

        s.section("And it reaches the letter")

        sent = []
        was_enabled, was_resend = m.resend_enabled, m.send_email_via_resend
        m.resend_enabled = lambda: True
        m.send_email_via_resend = (
            lambda to, subj, body, ics=None, name=None, html=None, reply_to=None:
            (sent.append({"to": to, "reply_to": reply_to}), (True, None))[1])
        try:
            with m.app.test_request_context("/"):
                m.send_email("zzfx@example.invalid", "Table", "b",
                             keep=False, area="restaurant")
            s.check("the transport is given it",
                    sent and sent[0]["reply_to"] == "restaurant@chateaugudanes.com",
                    detail=str(sent[:1]))
            del sent[:]
            with m.app.test_request_context("/"):
                m.send_email("zzfx@example.invalid", "Anything", "b", keep=False)
            s.check("and a letter with no area carries none",
                    sent and sent[0]["reply_to"] is None, detail=str(sent[:1]))
        finally:
            m.resend_enabled, m.send_email_via_resend = was_enabled, was_resend

        # Read rather than run: every check above stubs send_email_via_resend,
        # so deleting the two lines that put reply_to INTO the payload passes
        # all of them. test_html_email reads the same function for the same
        # reason. Both transports or neither, too — a rule inside one of them
        # stops applying the day somebody configures the other.
        src = open("app.py", encoding="utf-8").read()
        real = src.split("def send_email_via_resend")[1].split(chr(10) + "def ")[0]
        s.check("the Resend payload actually carries it",
                'payload["reply_to"] = reply_to' in real and "if reply_to:" in real,
                detail="send_email can hand it down perfectly and the "
                       "transport can still drop it on the floor")
        s.check("the SMTP branch sets the header too",
                'msg["Reply-To"] = replies_to' in src,
                detail="there are two transports and which one is configured "
                       "is an environment variable somebody set months ago")
        s.check("and the real senders say which area they are",
                src.count('area="restaurant"') >= 1
                and src.count('area="workshops"') >= 1
                and src.count('area="rooms"') >= 1,
                detail="wired at the one helper each rather than at sixty-six "
                       "call sites")
    finally:
        m.MS_GRAPH_MAILBOXES = was_boxes

    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.FX_SETTING,))
    if was:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                     (m.FX_SETTING, was["value"]))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
