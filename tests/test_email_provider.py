"""Connecting a provider, and finding out when it will not connect.

The house has held 487 messages waiting for an email provider. The day one is
attached is the day this code runs for the first time in earnest, and it had
two holes that only open on exactly that day.

WHY THE REASON MATTERS MORE THAN THE FAILURE.

  str() on an HTTPError is "HTTP Error 403: Forbidden" and nothing more. The
  reason is in the BODY, and Resend's first-day refusals are all precise and
  all fixable in two minutes: the domain is not verified yet, the from address
  is not on the domain, the key is restricted to one recipient. Every one of
  those was being collapsed into "provider rejected it" and written to a
  column nobody reads. Somebody would have spent an afternoon on a message
  the provider had already explained.

AND THE TUPLE, WHICH IS THE SAME BUG THIS REPO ALREADY SHIPPED ONCE.

  send_email_via_resend now answers (ok, why). A bare `if` on a two-item tuple
  is TRUE WHETHER OR NOT THE SEND WORKED, because a non-empty tuple is truthy
  -- so the one-line change from `if f(...)` to unpacking is the whole
  difference between reporting a refusal and reporting success on every failed
  message, silently, with the guest never written to. is_range_available cost
  this codebase exactly this bug already.

  So it is checked twice here: once by behaviour, and once by reading the
  source, because a future edit back to `if send_email_via_resend(...)` passes
  every behavioural test that mocks the transport to succeed.

AND A TEST SEND THAT IS NOT A GUEST.

  Until now the first proof a provider worked was a guest either getting or
  not getting their booking confirmation -- and a confirmation that does not
  arrive is invisible from this side. The house finds out when somebody rings
  about a booking they were never told had been accepted.

  It goes to the signed-in person and nowhere else. There is deliberately no
  box to type an address into: a form on an admin page that sends text to an
  arbitrary address from a freshly verified domain is how a domain gets
  listed, and "just to check" is answered by sending to yourself.
"""
from _harness import Suite, clients, db, flashes

import io
import json

import _harness

m = _harness.m


class _FakeHTTPError(m.HTTPError):
    """A real HTTPError carrying a body, which is where Resend puts the reason."""

    def __init__(self, code, payload):
        body = json.dumps(payload).encode("utf-8") if isinstance(payload, dict) \
            else payload.encode("utf-8")
        m.HTTPError.__init__(self, "https://api.resend.com/emails", code,
                             "Forbidden", {}, io.BytesIO(body))


def run():
    s = Suite("the email provider, connected")
    oc, ec, owner, _emp = clients()
    conn = db()

    s.section("What Resend said, not that it said something")

    # The one that will actually happen: the domain is added but the DNS
    # records have not propagated, or the from address is on a different one.
    why = m.resend_refusal(_FakeHTTPError(403, {
        "statusCode": 403, "name": "validation_error",
        "message": "The chateaugudanes.com domain is not verified. Please add "
                   "and verify your domain on https://resend.com/domains"}))
    s.check("an unverified domain says so, in Resend's own words",
            "not verified" in why and "resend.com/domains" in why,
            detail=why)
    s.check("and the status code is kept", "403" in why, detail=why)

    why401 = m.resend_refusal(_FakeHTTPError(401, {"message": "API key is invalid"}))
    s.check("a bad key says the key is bad", "API key is invalid" in why401,
            detail=why401)

    # A body that is not JSON must not lose the body.
    whyraw = m.resend_refusal(_FakeHTTPError(500, "upstream exploded"))
    s.check("a body that is not JSON is still passed through",
            "upstream exploded" in whyraw, detail=whyraw)

    whynet = m.resend_refusal(m.URLError("nodename nor servname provided"))
    s.check("and being unable to reach it is a different sentence",
            "reach" in whynet.lower() and "nodename" in whynet, detail=whynet)

    s.section("The reason reaches the outbox, not a constant")

    was_enabled, was_resend = m.resend_enabled, m.send_email_via_resend
    m.resend_enabled = lambda: True
    m.send_email_via_resend = (
        lambda to, subj, body, ics=None, name=None, html=None:
        (False, "Resend refused it (403): the domain is not verified"))
    try:
        with m.app.test_request_context("/"):
            went = m.send_email("zzprov@example.invalid", "ZZPROV a booking",
                                "the body")
        s.check("a refused message reports failure", went is False, detail=str(went))
        row = conn.execute(
            "SELECT * FROM email_outbox WHERE to_address = ? ORDER BY id DESC LIMIT 1",
            ("zzprov@example.invalid",)).fetchone()
        s.check("and is kept rather than lost", row is not None)
        if row:
            s.check("with the reason the provider gave, not a stock phrase",
                    "not verified" in (row["last_error"] or ""),
                    detail="last_error = %r" % (row["last_error"],))

        # THE BUG THIS GUARDS. If send_email goes back to truth-testing the
        # tuple, this exact call reports SUCCESS -- (False, "...") is truthy.
        s.check("a refusal is not read as a success",
                went is not True,
                detail="a bare `if` on (False, 'reason') is TRUE; that would "
                       "mark every failed message delivered and write nothing "
                       "to the outbox")
        delivered = conn.execute(
            "SELECT COUNT(*) c FROM guest_messages WHERE to_address = ? "
            "AND delivered = 1", ("zzprov@example.invalid",)).fetchone()
        s.check("and nothing is filed as delivered", delivered["c"] == 0,
                detail=str(delivered["c"]))
    finally:
        m.resend_enabled, m.send_email_via_resend = was_enabled, was_resend
        conn.execute("DELETE FROM email_outbox WHERE to_address = ?",
                     ("zzprov@example.invalid",))
        conn.execute("DELETE FROM guest_messages WHERE to_address = ?",
                     ("zzprov@example.invalid",))
        conn.commit()

    # Reading the source, because the behavioural check above only fires when
    # the transport is mocked to FAIL. Every other suite mocks it to succeed,
    # where truth-testing and unpacking agree.
    src = io.open("app.py", encoding="utf-8").read()
    s.check("send_email unpacks the transport rather than truth-testing it",
            "went, why = send_email_via_resend(" in src
            and "if send_email_via_resend(" not in src,
            detail="`if send_email_via_resend(...)` on a 2-tuple is always true")

    s.section("A test send, so a guest is not the experiment")

    s.check("an employee cannot send one",
            ec.post("/admin/email-outbox/test").status_code != 302
            or ec.post("/admin/email-outbox/test",
                       follow_redirects=True).request.path != "/admin/email-outbox")

    # With nothing configured, it must say so rather than appearing to work.
    r = oc.post("/admin/email-outbox/test", follow_redirects=True)
    s.check("with no provider it says there is no provider",
            any("no email provider" in f.lower() for f in flashes(r)),
            detail="; ".join(flashes(r)[:1]))

    was_enabled, was_resend = m.resend_enabled, m.send_email_via_resend
    m.resend_enabled = lambda: True
    m.RESEND_FROM = "bookings@chateaugudanes.com"
    sent = []
    m.send_email_via_resend = (
        lambda to, subj, body, ics=None, name=None, html=None:
        (sent.append((to, subj, body)), (True, None))[1])
    try:
        r = oc.post("/admin/email-outbox/test", follow_redirects=True)
        s.check("the owner can send one", len(sent) == 1, detail=str(len(sent)))
        owner_row = conn.execute("SELECT email FROM users WHERE id = ?",
                                 (owner["id"],)).fetchone()
        s.check("it goes to the signed-in person's own address",
                sent and sent[0][0] == owner_row["email"],
                detail="%s vs %s" % (sent[0][0] if sent else None,
                                     owner_row["email"]))
        s.check("and the page says where it went",
                any(owner_row["email"] in f for f in flashes(r)),
                detail="; ".join(flashes(r)[:1]))

        # No typed address, ever -- checked by SENDING one, not by reading the
        # form. Reading the form only proves nobody drew a box; the route is
        # what decides, and anybody can post a field the page never drew. An
        # earlier version of this check did read the form, and passed happily
        # with the route honouring `to`.
        del sent[:]
        r = oc.post("/admin/email-outbox/test",
                    data={"to": "somebody-else@example.invalid"},
                    follow_redirects=True)
        s.check("a typed address is ignored, not obeyed",
                sent and sent[0][0] == owner_row["email"],
                detail="went to %s — an admin form that mails an arbitrary "
                       "address from a freshly verified domain is how a domain "
                       "gets listed" % (sent[0][0] if sent else "nothing"))
        page = oc.get("/admin/email-outbox").get_data(as_text=True)
        form = page.split('action="/admin/email-outbox/test"')[1].split("</form>")[0] \
            if 'action="/admin/email-outbox/test"' in page else ""
        s.check("and no box is drawn to invite one",
                'type="email"' not in form and 'name="to"' not in form,
                detail=form[:120])

        # And it must not tidy up after itself into the outbox.
        before = conn.execute(
            "SELECT COUNT(*) c FROM email_outbox WHERE sent_at IS NULL").fetchone()["c"]
        m.send_email_via_resend = (
            lambda to, subj, body, ics=None, name=None, html=None:
            (False, "Resend refused it (403): the domain is not verified"))
        r = oc.post("/admin/email-outbox/test", follow_redirects=True)
        s.check("a failed test says exactly why",
                any("not verified" in f for f in flashes(r)),
                detail="; ".join(flashes(r)[:1]))
        after = conn.execute(
            "SELECT COUNT(*) c FROM email_outbox WHERE sent_at IS NULL").fetchone()["c"]
        s.check("and does not file itself in the outbox", after == before,
                detail="%d then %d — checking something works should not leave "
                       "tidying up" % (before, after))
    finally:
        m.resend_enabled, m.send_email_via_resend = was_enabled, was_resend

    s.section("It goes through the same door as a real letter")

    # A test that calls a transport directly proves the transport works and
    # nothing else. MAIL_REDIRECT_TO is applied in send_email, once, because
    # there are two transports -- so a test send that skipped send_email would
    # be the ONE letter that reached a real address on a test deployment, and
    # would report a healthy provider while every real message went elsewhere.
    was_r, was_resend, was_redirect = (m.resend_enabled, m.send_email_via_resend,
                                       m.MAIL_REDIRECT_TO)
    landed = []
    m.resend_enabled = lambda: True
    m.send_email_via_resend = (
        lambda to, subj, body, ics=None, name=None, html=None:
        (landed.append((to, subj)), (True, None))[1])
    m.MAIL_REDIRECT_TO = "zzredirect@example.invalid"
    try:
        oc.post("/admin/email-outbox/test", follow_redirects=True)
        s.check("with the redirect on, the test goes to the redirect address",
                landed and landed[0][0] == "zzredirect@example.invalid",
                detail="went to %s — this is the letter that would otherwise "
                       "be the only one reaching a real inbox on a test "
                       "deployment" % (landed[0][0] if landed else "nothing"))
        s.check("and still says who it was addressed to",
                landed and "[test" in landed[0][1], detail=str(landed[:1]))
        # test_stale_mail states this rule for the audit log and it holds here:
        # an address written into a permanent log to record something ABOUT
        # email keeps the thing the log is only meant to describe. Both halves
        # of the sender line are addresses -- RESEND_FROM is one, the redirect
        # is another -- so they belong in the flash, read once, not the log.
        noted = conn.execute(
            "SELECT details FROM audit_log WHERE action = 'email_provider_tested' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        s.check("and the audit line records the transport, not the addresses",
                noted and "@" not in (noted["details"] or ""),
                detail=str(noted["details"]) if noted else "nothing logged")
    finally:
        (m.resend_enabled, m.send_email_via_resend,
         m.MAIL_REDIRECT_TO) = was_r, was_resend, was_redirect

    # Read the route, because the two checks above pass just as happily if
    # somebody re-adds a direct transport call for the Resend case only: the
    # redirect suite would stay green, and so would this, until the day
    # somebody set MAIL_REDIRECT_TO on a deployment with SMTP configured.
    route = src.split("def test_email_provider")[1].split("\n@app.route")[0]
    s.check("the route calls send_email rather than a transport directly",
            "send_email_via_resend(" not in route and "send_email(" in route,
            detail="MAIL_REDIRECT_TO lives in send_email; going around it "
                   "sends the one letter that reaches a real person")
    s.check("and asks it for the reason rather than inventing one",
            "report=report" in route and "report.get" in route)

    s.section("The button is on the page, and only when it can be used")

    body = oc.get("/admin/email-outbox").get_data(as_text=True)
    s.check("the test button is offered once a provider is set",
            "/admin/email-outbox/test" not in body,
            detail="with nothing configured there is nothing to test")
    was_enabled = m.resend_enabled
    m.resend_enabled = lambda: True
    try:
        body = oc.get("/admin/email-outbox").get_data(as_text=True)
        s.check("and appears once one is", "/admin/email-outbox/test" in body)
        # It has to be reachable when the outbox is EMPTY, which is exactly
        # when somebody wants it -- so it cannot live inside `{% if waiting %}`.
        tpl = io.open("templates/admin_email_outbox.html", encoding="utf-8").read()
        head = tpl.split("test_email_provider")[0]
        s.check("and is not hidden inside the have-we-got-held-mail block",
                head.count("{% if waiting %}") == 0,
                detail="the moment you most want to test a provider is the "
                       "moment there is nothing waiting")
    finally:
        m.resend_enabled = was_enabled

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
