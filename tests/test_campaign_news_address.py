"""Campaigns go out from a news address of their own, never the booking one.

The owner, 11 November 2026, before a send to a mailing list of some 24,000
people built up over years: mail providers judge a sender by its domain, so a
big send to an old list that bounces or draws complaints costs that domain its
standing -- and booking confirmations come from the same one. So campaigns get
their own sending address (RESEND_CAMPAIGN_FROM, on news.chateaugudanes.com),
and booking mail keeps its own.

Held here:
  - the letter Resend is handed says who it is from: the news address for a
    campaign, the usual one for everything else;
  - a campaign held (guest mail off, the provider down) keeps its sender, and
    goes from the news address when it is sent later;
  - the campaign page says which address a send will go from, and says plainly
    when there is no news address and it would be the booking one.

Nothing reaches Resend: the request is caught before the network.
"""
import ast
import json

from _harness import Suite, clients, db, visible_text
import _harness

m = _harness.m
TAG = "ZZNEWS"
NEWS = "Château de Gudanes <news@news.example.invalid>"
BOOKING = "Château de Gudanes <bookings@mail.example.invalid>"
TO = "zznews.guest@example.invalid"


def _real_resend(namespace):
    """The real send_email_via_resend, defined in a copy of the app's globals
    whose network call is a stand-in -- the harness keeps the module's own
    replaced, and this must not reach Resend either."""
    src = open(m.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "send_email_via_resend")
    code = compile(ast.Module(body=[node], type_ignores=[]), m.__file__, "exec")
    exec(code, namespace)
    return namespace["send_email_via_resend"]


class Caught:
    def __init__(self):
        self.payloads = []

    def __call__(self, req, timeout=None):
        self.payloads.append(json.loads(req.data.decode("utf-8")))
        outer = self

        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"id": "zznews-test"}'
        return Answer()


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append({"to": to_address, "subject": subject, "sender": kw.get("sender")})
        return True, None


def run():
    s = Suite("Campaigns from a news address of their own")
    oc, _ec, _owner, _emp = clients()
    saved = (m.RESEND_API_KEY, m.RESEND_FROM, m.RESEND_CAMPAIGN_FROM, m.send_email_via_resend,
             m.guest_mail_live)
    try:
        _suite(s, oc)
    finally:
        (m.RESEND_API_KEY, m.RESEND_FROM, m.RESEND_CAMPAIGN_FROM, m.send_email_via_resend,
         m.guest_mail_live) = saved
        conn = db()
        conn.execute("DELETE FROM email_outbox WHERE to_address = ?", (TO,))
        conn.execute("DELETE FROM campaign_sends WHERE recipient_email = ?", (TO,))
        conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()
    return s


def _suite(s, oc):
    s.section("The letter Resend is handed")
    caught = Caught()
    space = dict(m.__dict__)
    space.update(urlopen=caught, RESEND_FROM=BOOKING, RESEND_API_KEY="re_test_stand_in")
    real = _real_resend(space)
    real(TO, "Hello", "Body", sender=NEWS)
    real(TO, "Your booking", "Body")
    s.check("a campaign says it is from the news address",
            caught.payloads and caught.payloads[0]["from"] == NEWS, detail=str(caught.payloads[:1]))
    s.check("anything else is from the usual one",
            len(caught.payloads) > 1 and caught.payloads[1]["from"] == BOOKING,
            detail=str(caught.payloads[1:2]))

    s.section("Through the house's own sending")
    rec = Recorder()
    m.RESEND_API_KEY, m.RESEND_FROM, m.RESEND_CAMPAIGN_FROM = "re_test_stand_in", BOOKING, NEWS
    m.send_email_via_resend = rec
    m.send_email(TO, "Your booking", "Body", despite_switch="a test of the sending address")
    m.send_email(TO, "News", "Body", sender=NEWS, despite_switch="a test of the sending address")
    s.check("booking mail is handed no sender, so it keeps the usual address",
            rec.calls and rec.calls[0]["sender"] is None, detail=str(rec.calls))
    s.check("a letter given the news address is sent from it",
            len(rec.calls) > 1 and rec.calls[1]["sender"] == NEWS, detail=str(rec.calls))

    conn = db()
    now = _harness.datetime_now()
    conn.execute("""INSERT INTO campaign_templates (name, area, subject, body, created_at)
                    VALUES (?, 'marketing', 'News', 'Dear {{first_name}}, news.', ?)""",
                 (f"{TAG} letter", now))
    template = conn.execute("SELECT * FROM campaign_templates WHERE name = ?",
                            (f"{TAG} letter",)).fetchone()
    conn.commit()

    s.section("A campaign held, and sent later")
    rec.calls.clear()
    m.RESEND_API_KEY = None          # no provider: every letter is held
    with m.app.test_request_context():
        m.send_campaign(conn, template, {TO: "Zz News"}, None)
    row = conn.execute("SELECT * FROM email_outbox WHERE to_address = ? AND sent_at IS NULL "
                       "ORDER BY id DESC", (TO,)).fetchone()
    s.check("held, it keeps the news address with it",
            not rec.calls and row is not None and row["sender"] == NEWS,
            detail=f"{rec.calls} {dict(row) if row else None}")
    m.RESEND_API_KEY = "re_test_stand_in"
    if row is not None:
        m.send_held_letter(conn, row)
    s.check("and goes from the news address when it is sent",
            rec.calls and rec.calls[-1]["sender"] == NEWS, detail=str(rec.calls))
    conn.close()

    s.section("The campaign page says which address")
    page = visible_text(oc.get(f"/admin/emails/{template['id']}").get_data(as_text=True))
    s.check("with a news address: that it goes from it", "Goes out from " + NEWS in page,
            detail=page[page.find("Send"):][:300])
    m.RESEND_CAMPAIGN_FROM = None
    page = visible_text(oc.get(f"/admin/emails/{template['id']}").get_data(as_text=True))
    s.check("without one: plainly, that it would go from the booking address",
            "No separate news address is set" in page and BOOKING in page,
            detail=page[page.find("Send"):][:400])


if __name__ == "__main__":
    print(run().report())
