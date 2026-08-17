"""The wording of automated guest email, and the way back from a bad edit.

Editing these used to be a one-way door: the original wording is only written
when a row is absent, so a template rewritten badly stayed that way and went
out to guests. A reservation confirmation in this very database sat reading
"TEST SUBJECT {guest_name}" — left behind while testing the editor — until a
workflow run happened to print the subject.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
KEY = "restaurant_confirmed"


def _row(key=KEY):
    conn = db()
    r = conn.execute("SELECT * FROM email_templates WHERE template_key = ?", (key,)).fetchone()
    conn.close()
    return r


def run():
    s = Suite("Email templates")
    oc, ec, _owner, _emp = clients()

    default = next((d for d in m.DEFAULT_EMAIL_TEMPLATES if d[0] == KEY), None)
    s.check("the shipped wording is reachable from a route", default is not None)
    if not default:
        return s
    _key, _label, orig_subject, orig_body = default

    s.section("No template ships with placeholder text")
    conn = db()
    bad = conn.execute(
        "SELECT template_key FROM email_templates "
        "WHERE subject LIKE '%TEST %' OR body LIKE '%TEST %' "
        "   OR subject LIKE '%TODO%' OR body LIKE '%TODO%'").fetchall()
    conn.close()
    s.check("nothing holds test or TODO text", not bad,
            detail=", ".join(r["template_key"] for r in bad))

    s.section("Every template still has its placeholders")
    # A template that lost {guest_name} sends "Hi ," to a paying guest.
    conn = db()
    rows = conn.execute("SELECT template_key, subject, body FROM email_templates").fetchall()
    conn.close()
    defaults = {k: (subj, body) for k, _l, subj, body in m.DEFAULT_EMAIL_TEMPLATES}
    stripped = []
    for r in rows:
        if r["template_key"] not in defaults:
            continue
        want_subject, want_body = defaults[r["template_key"]]
        import re
        expected = set(re.findall(r"\{(\w+)\}", want_subject + want_body))
        have = set(re.findall(r"\{(\w+)\}", (r["subject"] or "") + (r["body"] or "")))
        missing = expected - have
        if missing:
            stripped.append(f"{r['template_key']} lost {','.join(sorted(missing))}")
    s.check("no template has lost a placeholder", not stripped, detail=" | ".join(stripped[:3]))

    s.section("Placeholder wording cannot reach a guest")
    # Catching this on a page only works if somebody opens the page. Three
    # times now the restaurant confirmation has sat reading "TEST SUBJECT"
    # between one look and the next, so the send itself has to refuse.
    conn = db()
    conn.execute("UPDATE email_templates SET subject = ?, body = ? WHERE template_key = ?",
                 ("TEST SUBJECT {guest_name}", "TEST BODY {reference_code}", KEY))
    conn.commit()
    subject, body = m.render_email_template(conn, KEY, {
        "guest_name": "Marie", "dinner_date": "2026-09-01", "party_size": 2,
        "reference_code": "ABC123", "manage_url": "https://example.com/x",
        "price_block": "", "balance_block": "", "arrival_time": "19:30",
    })
    s.check("a template holding TEST text is not sent as written",
            "TEST SUBJECT" not in (subject or "") and "TEST BODY" not in (body or ""),
            detail=f"got {subject!r}")
    s.check("the guest gets the shipped wording instead, filled in",
            subject and "Marie" not in (subject or "") and "ABC123" in (body or ""),
            detail=f"subject={subject!r}")
    # and it is not a silent swap
    conn.execute("UPDATE email_templates SET subject = ?, body = ?, updated_at = NULL "
                 "WHERE template_key = ?", (orig_subject, orig_body, KEY))
    conn.commit()
    conn.close()

    s.section("A bad edit can be undone")
    r = oc.post(f"/management/email-templates/{KEY}/edit",
                data={"subject": "ZZTPL wrecked", "body": "ZZTPL nothing useful"},
                follow_redirects=True)
    s.check("the edit saves", (_row()["subject"] or "") == "ZZTPL wrecked", r)

    page = oc.get("/management/email-templates").get_data(as_text=True)
    s.check("the editor now offers a way back", "Restore original wording" in page)

    r = oc.post(f"/management/email-templates/{KEY}/restore", follow_redirects=True)
    row = _row()
    s.check("restoring brings back the original subject", row["subject"] == orig_subject, r,
            detail=f"got {row['subject']!r}")
    s.check("restoring brings back the original body", row["body"] == orig_body)
    s.check("it no longer counts as edited", row["updated_at"] is None,
            detail=f"updated_at={row['updated_at']!r}")

    s.section("Guards")
    r = oc.post("/management/email-templates/not_a_real_template/restore")
    s.check("restoring an unknown template is a 404, not a crash", r.status_code == 404,
            detail=f"HTTP {r.status_code}")
    r = ec.post(f"/management/email-templates/{KEY}/restore")
    s.check("an employee cannot restore a template", r.status_code in (302, 403),
            detail=f"HTTP {r.status_code}")
    r = oc.post(f"/management/email-templates/{KEY}/edit", data={"subject": "", "body": ""},
                follow_redirects=True)
    s.check("an empty edit is refused", _row()["subject"] == orig_subject, r)

    return s
