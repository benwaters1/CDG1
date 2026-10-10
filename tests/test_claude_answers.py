# -*- coding: utf-8 -*-
"""Every feature that asks Claude for JSON reads the answer where it arrives.

10 October: the owner topped up the Anthropic account and the translation
backlog did not move. The job said "Worked -- translated 0, 330 still
waiting". Seven features asked for structured answers with messages.parse()
and a raw JSON schema, and read `parsed_output` -- which the SDK fills only
for a pydantic TYPE and leaves None for a raw schema. Every call was made,
billed, answered with valid JSON in its text, and read as nothing: the receipt
scanner, the supplier-invoice reader, the menu-card reader, photo captions,
the work-in-progress photo check, meeting minutes and translation.

Each suite that touched one of them stood the function out entirely, or faked
a reply carrying parsed_output, so none could see it. This drives all six
others through a stand-in for the client that answers EXACTLY as the real one
does: create() returns content blocks and a stop reason with the JSON as
text, and parse() -- present, as on the real client -- returns parsed_output
None. A feature that goes back to reading parsed_output gets nothing and
fails here.

Nothing reaches the network: the harness refuses anthropic.Anthropic at
import, and this replaces it only inside each check.
"""
import json

from _harness import Suite
import _harness

m = _harness.m


def _answer(text, stop="end_turn"):
    block = type("Block", (), {"type": "text", "text": text})()
    return type("Reply", (), {"content": [block], "stop_reason": stop})()


def _client_answering(payload, stop="end_turn", seen=None):
    text = json.dumps(payload) if not isinstance(payload, str) else payload

    class _Client:
        def __init__(self, **kw):
            self.messages = self

        def create(self, **kw):
            if seen is not None:
                seen.append(kw)
            return _answer(text, stop)

        def parse(self, **kw):
            reply = _answer(text, stop)
            reply.parsed_output = None   # what the SDK sets for a raw schema
            return reply
    return _Client


MEETING = {"title": "Week ahead", "meeting_date": "2026-10-10", "agenda": "Rooms"}

# (what it is, how to call it, a reply it accepts, what proves it was read)
FEATURES = (
    # These two are stood down by the harness for every other suite (a scanner
    # feeds receipts in bulk, a camera card holds hundreds of frames, each a
    # paid request), so the real functions are called from where it keeps
    # them -- with the stand-in client, so still nothing leaves the machine.
    ("the receipt scanner",
     lambda: _harness.REAL_READ_RECEIPT(b"not-really-an-image"),
     {"vendor": "Leclerc", "total": 42.5, "unreadable": False},
     lambda r: r and r.get("vendor") == "Leclerc"),
    ("photo captions",
     lambda: m.suggest_photo_caption(b"not-really-an-image"),
     {"captions": ["The salon at dusk"], "alt": "A gilded salon"},
     lambda r: r and r.get("captions") == ["The salon at dusk"]),
    ("the work-in-progress photo check",
     lambda: _harness.REAL_ASSESS_MEDIA(b"not-really-an-image"),
     {"verdict": "keep", "reason": "the scaffolding in the window"},
     lambda r: r and r.get("verdict") == "keep"),
    ("meeting minutes",
     lambda: m.meeting_minutes_with_claude(MEETING, "We agreed the rota.", ["Karina"]),
     {"summary": "The rota was agreed.", "decisions": [], "actions": []},
     lambda r: r and r.get("summary") == "The rota was agreed."),
    ("the supplier-invoice reader",
     lambda: m.read_invoice_with_claude(b"%PDF-1.4", "delivery.pdf", []),
     {"lines": [{"description": "Flour", "quantity": 2}]},
     lambda r: r and r.get("lines") and r["lines"][0]["description"] == "Flour"),
    ("the menu-card reader",
     lambda: m.read_menu_card(text="Soupe a l'oignon"),
     {"dishes": [{"name": "Soupe a l'oignon", "course": "starter"}]},
     lambda r: r and r.get("dishes") and r["dishes"][0]["name"] == "Soupe a l'oignon"),
)


def run():
    s = Suite("Claude's answers are read where they arrive")
    was_anth, was_conf = m.anthropic.Anthropic, m.claude_configured
    m.claude_configured = lambda: True
    try:
        s.section("Each feature reads the JSON from the reply's text")
        for name, call, payload, proved in FEATURES:
            seen = []
            m.anthropic.Anthropic = _client_answering(payload, seen=seen)
            got = call()
            s.check(f"{name} reads what came back", proved(got), detail=repr(got))
            s.check(f"and asks with the schema in output_config",
                    seen and seen[0].get("output_config", {}).get("format", {})
                    .get("type") == "json_schema",
                    detail="without it nothing guarantees the text is JSON")

        s.section("A cut-off or declined answer is no answer, and not a crash")
        for stop in ("max_tokens", "refusal"):
            for name, call, payload, _proved in FEATURES:
                m.anthropic.Anthropic = _client_answering(
                    json.dumps(payload)[:12], stop=stop)
                got = call()
                s.check(f"{name}, stopped on {stop}, gives nothing back",
                        got is None, detail=repr(got))

        s.section("Nothing goes back to reading parsed_output")
        src = open(m.__file__, encoding="utf-8").read()
        s.check("no messages.parse() call is left in the app",
                ".messages.parse(" not in src,
                detail="parse() leaves parsed_output None for a raw schema; "
                       "use claude_structured, which reads the text")
        s.check("and a structured request is made in one place only",
                src.count('output_config={"format": {"type": "json_schema"') == 1,
                detail="the rest go through claude_structured")
    finally:
        m.anthropic.Anthropic = was_anth
        m.claude_configured = was_conf
    return s


if __name__ == "__main__":
    print(run().report())
