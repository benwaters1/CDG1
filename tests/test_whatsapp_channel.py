"""Reaching a guest on WhatsApp, and being honest when it cannot.

The house's guests are mostly not French and mostly not on a French number. An
SMS abroad is billed per message and lands somewhere nobody looks; WhatsApp is
where those guests already are. Twilio sends it through the same endpoint this
app already posts to, so the code is small.

WHAT IS NOT SMALL IS THE RULE UNDERNEATH IT. WhatsApp does not let a business
open a conversation with free text. Outside a 24-hour window that the GUEST
opens by writing first, a business-initiated message must be a template Meta
approved in advance, sent by its id with its variables filled. Every message
this app sends is business-initiated, so without an approved template it does
not arrive late or look wrong -- Meta refuses it.

That makes the middle state the one worth testing. WhatsApp configured with no
approved template for THIS message is neither an error nor WhatsApp, and the
tempting implementations are both bad: sending it anyway is a clean run and a
guest with nothing, and quietly using SMS is right but unreadable six weeks
later when somebody asks why the WhatsApp they set up is not being used. It
goes by text AND the outbox says why in words.

The prefix is checked on BOTH numbers because prefixing only the recipient is
accepted by the API and delivers nothing -- a wrong answer that reports
success, which is the category this file exists for.
"""
from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ZZWA"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM sms_outbox WHERE phone = '+33611223344'")
    conn.execute("DELETE FROM app_settings WHERE key = 'whatsapp_template_checkin'")
    conn.commit()


def run():
    s = Suite("WhatsApp")
    conn = db()
    _cleanup(conn)

    s.section("Off by default, and it says so rather than failing")
    s.check("the harness leaves no WhatsApp sender",
            not m.whatsapp_enabled(),
            detail="the shipping state, and the state every other suite runs in")
    # Through send_sms rather than the provider call, which the harness
    # blocks outright and rightly -- every message costs money. What is being
    # checked is the gate, which used to ask sms_enabled() whatever channel
    # the message was on and so held every message in a WhatsApp-only house.
    ok, why = m.send_sms(conn, "+33611223344", "hello",
                         channel="whatsapp", hold=False)
    s.check("a WhatsApp message with no sender is not sent",
            ok is False, detail=repr(why))
    s.check("and nothing is queued to try later",
            conn.execute("SELECT COUNT(*) AS c FROM sms_outbox "
                         "WHERE phone = '+33611223344'").fetchone()["c"] == 0,
            detail="hold=False, because a message only true today is worse "
                   "than nothing when it arrives in March")

    before = m.WHATSAPP_FROM_NUMBER
    m.WHATSAPP_FROM_NUMBER = "+15550001111"
    try:
        s.section("What Twilio is actually posted")
        sms = m.twilio_message_fields("+33611223344", "hello")
        s.check("an ordinary text carries the number as it is",
                sms["To"] == "+33611223344"
                and "whatsapp:" not in (sms["From"] or ""),
                detail=repr(sms))
        s.check("and its words in the body", sms.get("Body") == "hello")

        wa = m.twilio_message_fields("+33611223344", "hello", channel="whatsapp")
        # Both. Prefixing only the recipient is accepted and delivers nothing.
        s.check("WhatsApp prefixes the recipient",
                wa["To"] == "whatsapp:+33611223344", detail=repr(wa))
        s.check("and the sender as well",
                wa["From"] == "whatsapp:+15550001111",
                detail="prefixing only one is accepted by the API and "
                       "delivers nothing")

        s.section("An approved template replaces the body")
        tpl = m.twilio_message_fields(
            "+33611223344", "hello", channel="whatsapp",
            template_id="HX123", variables=["Amelie", "https://x.invalid/b"])
        s.check("the template goes by id", tpl.get("ContentSid") == "HX123")
        s.check("its variables are numbered from one",
                tpl.get("ContentVariables")
                == '{"1": "Amelie", "2": "https://x.invalid/b"}',
                detail=repr(tpl.get("ContentVariables")))
        s.check("and no body is sent alongside it",
                "Body" not in tpl,
                detail="Meta ignores it, so sending it only invites somebody "
                       "to edit the wrong one of the two")

        s.section("Which channel the arrival note actually takes")
        # Recorded rather than sent: the harness stands the provider down, and
        # what is being tested is the DECISION, not the network call.
        calls = []
        real = m.sms_provider_send
        m.sms_provider_send = lambda n, b, c="sms", t="", v=None: (
            calls.append({"to": n, "channel": c, "template": t, "vars": v}),
            (True, "SM-test"))[1]
        m.SMS_PROVIDER_SID = m.SMS_PROVIDER_TOKEN = "test"
        m.SMS_FROM_NUMBER = "+15550002222"
        try:
            room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
            today = m.house_today()
            conn.execute(
                """INSERT INTO bookings (room_id, reference_code, manage_token,
                   guest_name, guest_email, guest_phone, arrival_date,
                   departure_date, party_size, status, total_price, created_at)
                   VALUES (?, ?, ?, 'Amelie Fontaine', 'a@example.invalid',
                   '+33611223344', ?, ?, 2, 'confirmed', 400, ?)""",
                (room, TAG + "1", TAG.lower() + "tok1", today.isoformat(),
                 (today + m.timedelta(days=2)).isoformat(),
                 m.datetime.now(m.timezone.utc).isoformat()))
            conn.commit()

            # NO TEMPLATE YET. This is the state a house is in for the weeks
            # Meta takes to approve one, and it must not be a silent nothing.
            m.run_guest_text_job(conn, "checkin")
            s.check("with no approved template it goes by text",
                    calls and calls[-1]["channel"] == "sms",
                    detail="Meta refuses free text for a conversation the "
                           "business opens, so a WhatsApp here sends nothing")
            row = conn.execute(
                """SELECT * FROM sms_outbox WHERE phone = '+33611223344'
                   ORDER BY id DESC LIMIT 1""").fetchone()
            s.check("and the outbox says why in words",
                    row and "no approved template" in (row["reason"] or ""),
                    detail=repr(row["reason"] if row else None))
            s.check("and records which way it went",
                    row and row["channel"] == "sms")

            # Now approved.
            conn.execute(
                """INSERT INTO app_settings (key, value)
                   VALUES ('whatsapp_template_checkin', 'HX999')""")
            conn.execute("UPDATE bookings SET checkin_text_sent_at = NULL "
                         "WHERE reference_code = ?", (TAG + "1",))
            conn.commit()
            calls.clear()
            m.run_guest_text_job(conn, "checkin")
            s.check("with one approved it goes by WhatsApp",
                    calls and calls[-1]["channel"] == "whatsapp",
                    detail=repr(calls[-1] if calls else None))
            s.check("carrying the template id",
                    calls and calls[-1]["template"] == "HX999")
            s.check("and the guest's own first name as variable one",
                    calls and calls[-1]["vars"]
                    and calls[-1]["vars"][0] == "Amelie",
                    detail="positional, because that is what Meta approved: "
                           + repr(calls[-1]["vars"] if calls else None))
            row = conn.execute(
                """SELECT * FROM sms_outbox WHERE phone = '+33611223344'
                   ORDER BY id DESC LIMIT 1""").fetchone()
            s.check("and the outbox records it as WhatsApp",
                    row and row["channel"] == "whatsapp",
                    detail="the first question when a guest says they had "
                           "nothing")
        finally:
            m.sms_provider_send = real
            m.SMS_PROVIDER_SID = m.SMS_PROVIDER_TOKEN = m.SMS_FROM_NUMBER = None
    finally:
        m.WHATSAPP_FROM_NUMBER = before

    s.section("And it is all off again afterwards")
    # The harness asserts this at import; a suite that left a sender set would
    # hand every later suite a WhatsApp path it never asked for.
    s.check("no WhatsApp sender is left configured", not m.whatsapp_enabled())
    s.check("and no texting provider either", not m.sms_enabled())

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
