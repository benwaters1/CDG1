"""Draft with AI, and what it is given.

The drafter is only as good as what it is handed and only as safe as what it
is not. Checked here, with Microsoft and Anthropic both stood in:

  WHAT IT IS GIVEN. The one dossier; the conversation being answered in full
  and the ones before it; prices and availability for the dates in the
  message from the SAME functions the send check runs, so a draft cannot
  quote a figure the check on Send would then flag; the house's rooms,
  terms, letters and the owner's own notes; how this inbox actually writes;
  and who signs it.

  WHAT IT IS NOT. Anything about their health. Their telephone. Money, unless
  the owner is asking. And for somebody who asked us to stop using their
  details, nothing at all beyond the email being answered -- not their
  bookings, not their history, not a note.

  HOW IT ASKS. claude-opus-5-5, the server-side refusal fallback on,
  structured output so the draft and the facts it relied on come back
  separable, and the house rules (non-refundable as standard, never promise a
  refund) in every request.
"""
import json
from datetime import timedelta

from _graph_double import FakeGraph, message
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZRD"
GUEST = "zzrd.guest@example.invalid"
RES = "zzrd.reservations@example.invalid"
EXP = "experience@chateaugudanes.com"


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Response:
    def __init__(self, text, stop="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop


class FakeAnthropic:
    """Records each request and answers with whatever the test set."""
    answer = json.dumps({"draft": "Bonjour Ana,\n\nLa chambre de la tour est à vous.\n\nThe Château Team",
                         "language": "French",
                         "facts_used": [{"fact": "The tower room is free 14–17 June",
                                         "source": "live availability"}],
                         "unsure": ["Whether the dog may come"]})
    stop = "end_turn"
    requests = []
    raise_with = None

    def __init__(self, **kw):
        self.kw = kw
        outer = self

        class _Messages:
            def create(self, **request):
                FakeAnthropic.requests.append(request)
                if FakeAnthropic.raise_with:
                    raise FakeAnthropic.raise_with
                return _Response(FakeAnthropic.answer, FakeAnthropic.stop)

        class _Beta:
            messages = _Messages()

        self.messages = _Messages()
        self.beta = _Beta()
        self._outer = outer


class _AnthropicOn:
    def __enter__(self):
        self.saved = (m.anthropic.Anthropic, m.ANTHROPIC_API_KEY)
        m.anthropic.Anthropic = FakeAnthropic
        m.ANTHROPIC_API_KEY = "test-key-not-real"
        FakeAnthropic.requests = []
        FakeAnthropic.stop = "end_turn"
        FakeAnthropic.raise_with = None
        return self

    def __exit__(self, *exc):
        m.anthropic.Anthropic, m.ANTHROPIC_API_KEY = self.saved
        return False


def _cleanup(conn):
    gids = [r[0] for r in conn.execute("SELECT id FROM guests WHERE name LIKE ?", (TAG + "%",))]
    marks = ",".join("?" * len(gids)) or "NULL"
    conn.execute(f"DELETE FROM guest_notes WHERE guest_id IN ({marks})", gids)
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox IN (?, ?)", (RES, EXP))
    conn.execute("DELETE FROM addin_sessions WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'addin_%' OR action LIKE 'draft_reply%' "
                 "OR action LIKE 'check_send%'")
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.ADDIN_HOUSE_NOTES_SETTING,))
    conn.commit()


def _ago(n):
    return (m.datetime.now(m.timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run():
    s = Suite("drafting a reply")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = m.house_today()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 AND price_per_night > 0 "
                        "ORDER BY id LIMIT 1").fetchone()
    gid = conn.execute(
        """INSERT INTO guests (name, email, phone, dietary_notes, access_needs, preferences, language,
             vip, created_at)
           VALUES (?, ?, '+33 6 55 44 33 22', 'ZZRD nut allergy', 'ZZRD no stairs', 'the tower room',
                   'fr', 1, ?)""", (f"{TAG} Ana", GUEST, now)).lastrowid
    m.add_guest_note(conn, gid, "ZZRD-INTERNAL prefers to be called Madame")
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 640, ?)""",
        (room["id"], f"{TAG}S1", f"{TAG}t1", f"{TAG} Ana", GUEST,
         (today + timedelta(days=30)).isoformat(), (today + timedelta(days=33)).isoformat(), now))
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                 (m.ADDIN_HOUSE_NOTES_SETTING, "ZZRD-HOUSE-NOTE dogs are welcome in the tower room"))
    conn.commit()

    arrive = today + timedelta(days=60)
    leave = arrive + timedelta(days=3)
    asks = (f"Bonjour, avez-vous une chambre du {arrive.strftime('%d/%m/%Y')} au "
            f"{leave.strftime('%d/%m/%Y')} ? Merci, Ana")
    fake = FakeGraph(mailboxes=[RES, EXP])
    fake.messages[RES] = [
        message("q1", frm=GUEST, to=[RES], subject="Une chambre en été", when=_ago(1),
                preview=asks, conversation="c-now"),
        message("q0", frm=GUEST, to=[RES], subject="Last year", when=_ago(300),
                preview="ZZRD-EARLIER thank you for everything", conversation="c-old"),
    ]
    fake.bodies["q1"] = asks
    fake.bodies["q0"] = "ZZRD-EARLIER thank you for everything"
    fake.sent[EXP] = [
        {"subject": "Re: ateliers", "toRecipients": [{"emailAddress": {"address": "a@example.invalid"}}],
         "uniqueBody": {"content": "Dear Paul,\n\nZZRD-VOICE thank you so much for writing. " * 6}},
        {"subject": "internal", "toRecipients": [{"emailAddress": {"address": RES}}],
         "uniqueBody": {"content": "ZZRD-INTERNAL-MAIL to a colleague " * 10}},
        {"subject": "tiny", "toRecipients": [{"emailAddress": {"address": "b@example.invalid"}}],
         "uniqueBody": {"content": "ok"}},
    ]
    owner_caller = {"user_id": owner["id"], "name": owner["name"], "owner": True, "how": "signed in"}
    staff_caller = {"user_id": emp["id"], "name": emp["name"], "owner": False, "how": "signed in"}

    with fake, m.app.test_request_context("/"):
        ctx = m.gather_reply_context(conn, GUEST, caller=owner_caller, compose_text="",
                                     subject="Re: Une chambre en été", conversation_id="c-now",
                                     mailbox=EXP)
        blob = json.dumps(ctx, default=str, ensure_ascii=False)

        s.section("What the drafter is given")
        s.check("the guest, from the one dossier", ctx["guest"]["profile"]["name"] == f"{TAG} Ana")
        s.check("their booking", any(b["ref"] == f"{TAG}S1" for b in ctx["guest"]["bookings"]))
        s.check("the conversation being answered, in full",
                ctx["correspondence"]["current_thread"]
                and ctx["correspondence"]["current_thread"][-1]["text"] == asks,
                detail=str(ctx["correspondence"]["current_thread"])[:160])
        s.check("and the ones before it", "ZZRD-EARLIER" in json.dumps(ctx["correspondence"]["earlier"]))
        s.check("the language on file", ctx["guest_language_on_file"] == "Français")
        s.check("that the kitchen has their dietary notes, without what they say",
                ctx["guest"]["profile"]["kitchen_has_dietary_notes"] is True)
        s.check("the owner's notes for the drafter", "ZZRD-HOUSE-NOTE" in blob)
        s.check("the house's terms and letters",
                "terms_and_conditions" in ctx["house"] and "letter_templates" in ctx["house"])
        s.check("who signs it: experience@ is Jasmine", ctx["request"]["sign_off_as"] == "Jasmine")
        s.check("how that inbox writes", any("ZZRD-VOICE" in v for v in ctx["house_voice"]))
        s.check("but not a note to a colleague, or a two-word reply",
                not any("ZZRD-INTERNAL-MAIL" in v for v in ctx["house_voice"])
                and "ok" not in ctx["house_voice"])
        s.check("money, because the owner is asking", ctx["guest"]["account"] is not None)

        s.section("Prices from the send check's own engine")
        stay = ctx["live_facts"]["stay"]
        s.check("the dates in the message are read", stay and stay["arrival"] == arrive.isoformat()
                and stay["departure"] == leave.isoformat(), detail=str(stay)[:160])
        priced = [r for r in (stay or {}).get("rooms", []) if r["available"]]
        engine = {r["name"]: round(float(m.compute_room_total(conn, r, arrive, leave)), 2)
                  for r in conn.execute("SELECT * FROM rooms WHERE active = 1").fetchall()}
        s.check("every free room is priced exactly as the booking system prices it",
                priced and all(engine[r["room"]] == r["price_for_the_stay"] for r in priced),
                detail=str(priced)[:200])
        wrong = (f"The {room['name']} is €1 from {arrive.strftime('%d/%m/%Y')} to "
                 f"{leave.strftime('%d/%m/%Y')}.")
        facts = m.live_facts_for_text(conn, wrong, guest_email=GUEST)
        conflict = m.guess_email_conflict(conn, GUEST, "", wrong, m.datetime.now(m.timezone.utc).isoformat())
        s.check("and a wrong figure is what the check on Send would say it is",
                facts["send_check_would_say"] and conflict
                and facts["send_check_would_say"] == conflict["note"],
                detail=f"{facts['send_check_would_say']!r} vs {conflict and conflict['note']!r}")

        s.section("What the drafter is not given")
        for leak, what in (("ZZRD nut allergy", "a dietary note"), ("ZZRD no stairs", "an access need"),
                           ("55 44 33 22", "their telephone"), ("portal_token", "the key to their account")):
            s.check(f"not {what}", leak not in blob, detail=f"{leak!r} is in the context")

        staff_ctx = m.gather_reply_context(conn, GUEST, caller=staff_caller, mailbox=EXP)
        s.check("no money when a colleague is asking", staff_ctx["guest"]["account"] is None
                and all(b["amount"] is None for b in staff_ctx["guest"]["bookings"]))

        conn.execute("UPDATE guests SET restricted_at = ? WHERE id = ?", (now, gid))
        conn.commit()
        m.email_cache_forget()
        fake.calls.clear()
        r_ctx = m.gather_reply_context(conn, GUEST, caller=owner_caller, compose_text=asks,
                                       conversation_id="c-now", mailbox=EXP)
        r_blob = json.dumps(r_ctx, default=str, ensure_ascii=False)
        s.check("for somebody who asked us to stop: no profile, no bookings",
                r_ctx["guest"] is None and f"{TAG}S1" not in r_blob)
        s.check("no earlier emails — the mailboxes are not even searched",
                not any(c[1].endswith("/messages") and "$search" in c[2] for c in fake.calls)
                and "ZZRD-EARLIER" not in r_blob)
        s.check("no notes", "ZZRD-INTERNAL" not in r_blob)
        s.check("but the email being answered and today's prices",
                asks in r_ctx["compose_window"] and r_ctx["live_facts"]["stay"])
        s.check("and the person is told why",
                any("stop using their details" in n for n in r_ctx["notes"]))
        conn.execute("UPDATE guests SET restricted_at = NULL WHERE id = ?", (gid,))
        conn.commit()
        m.email_cache_forget()

        s.section("The modes")
        short = m.gather_reply_context(conn, GUEST, caller=owner_caller, mode="shorter",
                                       previous_draft="A long draft.", mailbox=EXP)
        s.check("shorter rewrites the previous draft", short["request"]["previous_draft"] == "A long draft."
                and "shorter" in short["request"]["instruction"])
        tr = m.gather_reply_context(conn, GUEST, caller=owner_caller, mode="translate",
                                    language="Spanish", previous_draft="Hello.", mailbox=EXP)
        s.check("translate names the language", "Spanish" in tr["request"]["instruction"])
        odd = m.gather_reply_context(conn, GUEST, caller=owner_caller, mode="something-else")
        s.check("an unknown mode is a plain reply", odd["request"]["mode"] == "reply")

        s.section("How it asks Claude")
        with _AnthropicOn():
            got = m.draft_reply_with_claude(ctx, asks)
            req = FakeAnthropic.requests[-1] if FakeAnthropic.requests else {}
            s.check("a draft comes back with the facts it relied on",
                    got and got["draft"].startswith("Bonjour") and got["facts_used"]
                    and got["unsure"] == ["Whether the dog may come"], detail=str(got)[:160])
            s.check("from Claude Opus 5.5", req.get("model") == "claude-opus-5-5")
            s.check("with the refusal fallback on",
                    req.get("fallbacks") == "default"
                    and "server-side-fallback-2026-07-01" in (req.get("betas") or []))
            s.check("as structured output",
                    (req.get("output_config") or {}).get("format", {}).get("schema") == m.DRAFT_SCHEMA)
            system_text = " ".join(b["text"] for b in req.get("system") or [])
            s.check("with the house rules in every request",
                    "non-refundable as standard" in system_text
                    and "Never promise, offer or imply a refund" in system_text)
            s.check("the house's facts cached between drafts",
                    (req.get("system") or [{}])[-1].get("cache_control") == {"type": "ephemeral"})
            whole = json.dumps(req, default=str, ensure_ascii=False)
            s.check("and nothing about their health anywhere in what was sent",
                    "nut allergy" not in whole and "no stairs" not in whole)
            FakeAnthropic.stop = "refusal"
            s.check("a refusal is no draft, not an empty one", m.draft_reply_with_claude(ctx, asks) is None)
            FakeAnthropic.stop = "max_tokens"
            s.check("nor is a draft cut off", m.draft_reply_with_claude(ctx, asks) is None)
            FakeAnthropic.stop = "end_turn"
            FakeAnthropic.answer, saved = "not json", FakeAnthropic.answer
            s.check("nor an answer that is not the shape asked for",
                    m.draft_reply_with_claude(ctx, asks) is None)
            FakeAnthropic.answer = saved
            FakeAnthropic.raise_with = RuntimeError("network down")
            s.check("nor a request that failed", m.draft_reply_with_claude(ctx, asks) is None)
            FakeAnthropic.raise_with = None

        s.section("Through the add-in")
        owner_key, _ = m.addin_issue_session(conn, owner["id"], "test", label=f"{TAG} owner")
        conn.commit()
        anon = m.app.test_client()
        r = anon.post("/api/draft-reply", data={"recipient_email": GUEST, "body": asks},
                      headers={"Authorization": f"Bearer {owner_key}"})
        s.check("not configured, it says so", r.status_code == 503)
        with _AnthropicOn():
            r = anon.post("/api/draft-reply", data={
                "recipient_email": GUEST, "body": asks, "subject": "Re: Une chambre en été",
                "conversation_id": "c-now", "mailbox": EXP, "mode": "reply"},
                headers={"Authorization": f"Bearer {owner_key}"})
            got = r.get_json() or {}
            s.check("the panel gets the draft", r.status_code == 200 and got.get("draft"),
                    detail=f"{r.status_code} {str(got)[:160]}")
            s.check("the facts and the doubts", got.get("facts_used") and got.get("unsure"))
            s.check("and who it is signed as", got.get("signed_as") == "Jasmine")
            audit = conn.execute("SELECT details FROM audit_log WHERE action = 'addin_draft' "
                                 "ORDER BY id DESC LIMIT 1").fetchone()
            s.check("the audit trail says who asked, never what was written",
                    audit and owner["name"] in audit["details"] and "chambre" not in audit["details"],
                    detail=audit and audit["details"])

            FakeAnthropic.answer = json.dumps({"summary": "Ana asked about a summer room.",
                                               "open_points": ["Dates in June"]})
            r = anon.post("/api/addin/summarise", data={"email": GUEST},
                          headers={"Authorization": f"Bearer {owner_key}"})
            got = r.get_json() or {}
            s.check("a long history is summarised on request",
                    r.status_code == 200 and got.get("summary") == "Ana asked about a summer room."
                    and got.get("open_points") == ["Dates in June"], detail=f"{r.status_code} {got}")
            conn.execute("UPDATE guests SET restricted_at = ? WHERE id = ?", (now, gid))
            conn.commit()
            r = anon.post("/api/addin/summarise", data={"email": GUEST},
                          headers={"Authorization": f"Bearer {owner_key}"})
            s.check("but not for somebody who asked us to stop", r.status_code == 409)
            conn.execute("UPDATE guests SET restricted_at = NULL WHERE id = ?", (gid,))
            conn.commit()
        r = anon.post("/api/addin/summarise", data={"email": GUEST},
                      headers={"Authorization": f"Bearer {owner_key}"})
        s.check("and without the key it says so", r.status_code == 503)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
