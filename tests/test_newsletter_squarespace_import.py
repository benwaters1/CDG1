"""The old Squarespace mailing list, brought across onto the newsletter.

The old site's newsletter had some twenty-five thousand people on it, and this
list started empty on 19 October 2026. The import reads Squarespace's export,
shows what it would do, and on a second press puts them on the list.

Checked here, each with the failure it guards:

  - the export is read whatever it calls its columns, with a byte-order mark,
    in either date order, and says which column each fact came from -- a file
    read wrongly is a list written wrongly, and nobody would see it;
  - an address twice is one person, and a stop said on either row is a stop;
  - the check writes nothing to the list;
  - the run brings across only what the sign-up box would let on: never anybody
    unsubscribed in Squarespace, on the do-not-write list, who asked us to stop
    using their details, who left this list, or who looks like a script, and
    one spelling per Gmail inbox;
  - each is on the list from the day Squarespace says, with the yes written down
    at that moment and how, so the backfill has nothing to add;
  - nobody is written to, and the report names who did not come across and why;
  - a run is of the file checked, once: the same check cannot be run twice;
  - twenty-five thousand addresses go through in one press, in reasonable time.
"""
import io
import time
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "zzsqs"

NEW_A = TAG + "anne@example.com"
NEW_B = TAG + "bernard@example.fr"
UNDATED = TAG + "undated@example.com"
TWICE = TAG + "twice@example.com"               # twice, one row unsubscribed
UNSUBSCRIBED = TAG + "gone@example.com"
EX_ON = TAG + "already@example.com"             # on the list here already
EX_WAIT = TAG + "waiting@example.com"           # confirm-first days, never pressed
EX_LEFT = TAG + "left@example.com"              # left this list by the link
OPTED_OUT = TAG + "optout@example.com"          # on the do-not-write list
RESTRICTED = TAG + "restricted@example.com"     # asked us to stop using their details
KEEP_GMAIL = TAG + "joan@gmail.com"             # on the list here
JOAN_AGAIN = TAG + "jo.an@gmail.com"            # another spelling of her, in the file
PETE_FIRST = TAG + "pe.te@gmail.com"            # joined Squarespace first
PETE_LATER = TAG + "pete@gmail.com"             # the same inbox, later
SCATTERED = TAG + "x.y.z.w@gmail.com"           # a dot between most of the letters
BULK = TAG + "bulk{:05d}@example.org"
BULK_COUNT = 25000


def _cleanup():
    conn = db()
    for table in ("newsletter_subscribers", "email_optouts", "consent_events", "guests"):
        conn.execute(f"DELETE FROM {table} WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM import_drafts WHERE kind = ?", (m.SQUARESPACE_IMPORT_KIND,))
    conn.commit()
    conn.close()


def _row(email):
    conn = db()
    r = conn.execute("SELECT * FROM newsletter_subscribers WHERE email = ?", (email,)).fetchone()
    conn.close()
    return r


class _Letters:
    def __init__(self):
        self.sent = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.sent.append((to_address, subject))
        return True


def _csv(lines):
    return "﻿" + "\r\n".join(lines) + "\r\n"


def _upload(client, text, name="squarespace-export.csv"):
    return client.post("/admin/newsletter/import",
                       data={"action": "check", "list": (io.BytesIO(text.encode("utf-8")), name)},
                       content_type="multipart/form-data", follow_redirects=True)


def _token(page):
    import re
    found = re.search(r'name="token" value="([^"]+)"', page)
    return found.group(1) if found else None


def run():
    s = Suite("Newsletter: the Squarespace list brought across")
    letters = _Letters()
    real_send = m.send_email
    m.send_email = letters
    _cleanup()
    try:
        _run(s, letters)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, letters):
    s.section("Reading the export")
    text = _csv([
        "Email Address,First Name,Last Name,Date Subscribed,Status",
        f"{NEW_A.upper()},Anne,Martin,03/04/2021,Subscribed",
        f"{NEW_B},Bernard,,25/12/2020 18:30,Subscribed",
        f"{UNDATED},,,,Subscribed",
        f"{TWICE},,,01/02/2022,Subscribed",
        f"{TWICE.upper()},,,01/01/2022,Unsubscribed",
        f"{UNSUBSCRIBED},,,01/02/2022,Unsubscribed",
        "not-an-address,,,01/02/2022,Subscribed",
        f"{EX_ON},,,01/02/2019,Subscribed",
        f"{EX_WAIT},,,01/02/2019,Subscribed",
        f"{EX_LEFT},,,01/02/2019,Subscribed",
        f"{OPTED_OUT},,,01/02/2019,Subscribed",
        f"{RESTRICTED},,,01/02/2019,Subscribed",
        f"{JOAN_AGAIN},,,01/02/2019,Subscribed",
        f"{PETE_LATER},,,01/06/2019,Subscribed",
        f"{PETE_FIRST},,,01/02/2018,Subscribed",
        f"{SCATTERED},,,01/02/2019,Subscribed",
        f"{TAG}future@example.com,,,01/01/2099,Subscribed",
    ])
    people, columns, problems = m.parse_squarespace_list(text)
    by = {p["email"]: p for p in people}
    s.check("the email column is found under a byte-order mark and its own name",
            columns.get("email") == "email address", detail=str(columns))
    s.check("so are the date and the status columns",
            columns.get("date") == "date subscribed" and columns.get("status") == "status",
            detail=str(columns))
    s.check("the name columns are seen, to be left behind",
            set(columns.get("names") or []) == {"first name", "last name"}, detail=str(columns))
    s.check("addresses are kept in lower case", NEW_A in by and NEW_A.upper() not in by)
    s.check("an address twice is one person", sum(1 for p in people if p["email"] == TWICE) == 1)
    s.check("and a stop said on either row is a stop", by[TWICE]["off"] is True)
    s.check("joined at the earlier of its two dates",
            (by[TWICE]["at"] or "").startswith("2022-01-01"), detail=str(by[TWICE]["at"]))
    s.check("a file with 25/12 in it is read day first, so 03/04/2021 is 3 April",
            (by[NEW_A]["at"] or "").startswith("2021-04-03"), detail=str(by[NEW_A]["at"]))
    s.check("a date alone is put at noon UTC, the same day in the Ariège",
            by[NEW_A]["at"] == "2021-04-03T12:00:00+00:00", detail=str(by[NEW_A]["at"]))
    s.check("a time with no zone is the house's own",
            by[NEW_B]["at"] == "2020-12-25T17:30:00+00:00", detail=str(by[NEW_B]["at"]))
    s.check("a date in the future is no date", by[TAG + "future@example.com"]["at"] is None)
    s.check("an empty date is no date, not a crash", by[UNDATED]["at"] is None)
    s.check("a row with no address is named as a problem",
            problems == [("not-an-address", m.SQS_SKIP_NOT_EMAIL)], detail=str(problems))

    p2, c2, _ = m.parse_squarespace_list(_csv(["Email,Created On", f"{NEW_A},03/04/2021"]))
    s.check("with no 25/12 anywhere, a slash date is read month first, as Squarespace writes it",
            p2 and (p2[0]["at"] or "").startswith("2021-03-04"), detail=str(p2))
    s.check("and with no status column, everybody is taken as subscribed",
            c2.get("status") is None and p2 and p2[0]["off"] is False, detail=str(c2))
    p3, _, _ = m.parse_squarespace_list(_csv(["Email,Unsubscribed",
                                              f"{NEW_A},2022-03-01", f"{NEW_B},"]))
    s.check("a column of unsubscribe dates means a date is a stop and a blank is not",
            [p["off"] for p in p3] == [True, False], detail=str(p3))
    p4, c4, _ = m.parse_squarespace_list(f"{NEW_A}\n{NEW_B}\n")
    s.check("a bare column of addresses with no header loses nobody",
            [p["email"] for p in p4] == [NEW_A, NEW_B], detail=str(p4))
    _, _, p5 = m.parse_squarespace_list(_csv(["Name,Phone", "Anne,0102"]))
    s.check("a file with no addresses in it says so",
            p5 and "no column of email addresses" in p5[0][1], detail=str(p5))

    s.section("Checking the file writes nothing")
    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    for email, confirmed, left in ((EX_ON, "2026-10-20T10:00:00+00:00", None),
                                   (EX_WAIT, None, None),
                                   (EX_LEFT, "2026-10-20T10:00:00+00:00", "2026-10-21T10:00:00+00:00"),
                                   (KEEP_GMAIL, "2026-10-20T10:00:00+00:00", None)):
        conn.execute("""INSERT INTO newsletter_subscribers (email, token, source, created_at,
                          confirmed_at, unsubscribed_at) VALUES (?, ?, 'site', ?, ?, ?)""",
                     (email, "tok-" + email, "2026-10-20T09:00:00+00:00", confirmed, left))
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'asked', ?)",
                 (OPTED_OUT, now))
    conn.execute("INSERT INTO guests (name, email, created_at, restricted_at) VALUES (?, ?, ?, ?)",
                 ("Restricted Guest", RESTRICTED, now, now))
    conn.commit()
    conn.close()

    oc, ec, owner, emp = clients()
    r = oc.get("/admin/newsletter")
    s.check("the newsletter page links to the import",
            "/admin/newsletter/import" in r.get_data(as_text=True), r)
    r = oc.get("/admin/newsletter/import")
    s.check("the import page opens", r.status_code == 200, r, detail=str(r.status_code))

    r = _upload(oc, text)
    page = r.get_data(as_text=True)
    s.check("checking the file is answered with what it would do",
            r.status_code == 200 and "Nothing has been saved yet" in page, r,
            detail=str(r.status_code))
    s.check("it says which column each fact came from",
            "<strong>email address</strong>" in page and "<strong>date subscribed</strong>" in page)
    s.check("and how many would go on", "Put 6 on the list" in page,
            detail="expected 6: Anne, Bernard, the two undated, Pete's first spelling, "
                   "and the one waiting")
    s.check("and why the others would not", m.SQS_SKIP_UNSUBSCRIBED.capitalize() in page
            and m.SQS_SKIP_OPTOUT.capitalize() in page)
    s.check("nothing is on the list from the check",
            _row(NEW_A) is None and _row(EX_WAIT)["confirmed_at"] is None)
    token = _token(page)
    s.check("the run carries the check's token", bool(token))

    if emp:
        r = ec.post("/admin/newsletter/import", data={"action": "run", "token": token or ""})
        s.check("staff without the owner's keys cannot run it",
                r.status_code in (302, 403), detail=str(r.status_code))
        s.check("and nothing went on when they tried", _row(NEW_A) is None)

    s.section("Bringing them across")
    before = len(letters.sent)
    r = oc.post("/admin/newsletter/import", data={"action": "run", "token": token or ""},
                follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("the run is answered", r.status_code == 200, r, detail=str(r.status_code))
    s.check("it says how many came across of how many",
            "Brought across 6 of 15 subscribers" in said, detail=said)
    s.check("and that one of them was waiting here, and one was on the list already",
            "1 of them were waiting to confirm here" in said
            and "1 already on the list" in said, detail=said)
    s.check("and that nobody has been written to", "nobody has been written to" in said,
            detail=said)
    for who, why in ((UNSUBSCRIBED, m.SQS_SKIP_UNSUBSCRIBED), (TWICE, m.SQS_SKIP_UNSUBSCRIBED),
                     (OPTED_OUT, m.SQS_SKIP_OPTOUT), (RESTRICTED, m.SQS_SKIP_RESTRICTED),
                     (EX_LEFT, m.SQS_SKIP_LEFT), (SCATTERED, m.SCRIPT_SIGNUP_DOTS),
                     ("not-an-address", m.SQS_SKIP_NOT_EMAIL)):
        s.check(f"it names {who.replace(TAG, '')} as not brought across: {why}",
                who in said and why in said, detail=said)
    s.check("and the later spelling of a Gmail inbox on the list here",
            JOAN_AGAIN in said and f"another spelling of {KEEP_GMAIL}" in said, detail=said)
    s.check("and of one earlier in the file",
            PETE_LATER in said and f"another spelling of {PETE_FIRST}" in said, detail=said)
    s.check("an error, because some were not brought across", "flash-error" in r.get_data(as_text=True))
    s.check("the run lands on the list showing where they came from",
            "Squarespace" in r.get_data(as_text=True))

    a = _row(NEW_A)
    s.check("a new one is on the list", a is not None and a["confirmed_at"]
            and a["unsubscribed_at"] is None)
    s.check("marked as from Squarespace", a and a["source"] == m.SQUARESPACE_SOURCE)
    s.check("on the list from the day Squarespace says",
            a and a["created_at"] == a["confirmed_at"] == "2021-04-03T12:00:00+00:00",
            detail=str(a and dict(a)))
    s.check("with no letter recorded as sent, because none was",
            a and a["confirm_sent_at"] is None)
    s.check("the undated one is dated the day of the import",
            (_row(UNDATED)["confirmed_at"] or "")[:4] == now[:4])
    s.check("the earlier spelling of a Gmail inbox is the one kept",
            _row(PETE_FIRST) is not None and _row(PETE_LATER) is None)
    s.check("the one waiting here is now on the list",
            _row(EX_WAIT)["confirmed_at"] and _row(EX_WAIT)["unsubscribed_at"] is None)
    s.check("the one already on the list is untouched",
            _row(EX_ON)["confirmed_at"] == "2026-10-20T10:00:00+00:00"
            and _row(EX_ON)["source"] == "site")
    s.check("the one who left here is not put back", _row(EX_LEFT)["unsubscribed_at"])
    s.check("nobody refused is on the list",
            all(_row(e) is None for e in (UNSUBSCRIBED, TWICE, OPTED_OUT, RESTRICTED,
                                          JOAN_AGAIN, SCATTERED)))
    s.check("nobody was written to", len(letters.sent) == before,
            detail=str(letters.sent[before:]))

    conn = db()
    consent = {c["email"]: c for c in conn.execute(
        "SELECT * FROM consent_events WHERE email LIKE ? AND channel = 'newsletter' "
        "AND granted = 1", (TAG + "%",)).fetchall()}
    recipients = {x["email"] for x in m.newsletter_recipients(conn)}
    draft = conn.execute("SELECT 1 FROM import_drafts WHERE token = ?", (token,)).fetchone()
    conn.close()
    s.check("each yes is in the consent history with how",
            NEW_A in consent and "Squarespace" in consent[NEW_A]["how"]
            and EX_WAIT in consent, detail=str(consent.get(NEW_A) and dict(consent[NEW_A])))
    s.check("at the list's own moment", consent.get(NEW_A)
            and consent[NEW_A]["created_at"] == a["confirmed_at"])
    s.check("and put down to the owner who ran it",
            consent.get(NEW_A) and consent[NEW_A]["recorded_by_user_id"] == owner["id"],
            detail=str(consent.get(NEW_A) and consent[NEW_A]["recorded_by_user_id"]))
    s.check("the newsletter now writes to them",
            {NEW_A, NEW_B, UNDATED, PETE_FIRST, EX_WAIT} <= recipients)
    s.check("the file kept for the check is gone", draft is None)

    conn = db()
    m.backfill_consent_history(conn)
    lines = conn.execute(
        "SELECT email, COUNT(*) AS n FROM consent_events WHERE email LIKE ? AND channel = "
        "'newsletter' AND granted = 1 GROUP BY email", (TAG + "%",)).fetchall()
    conn.commit()
    conn.close()
    s.check("the startup backfill finds nothing more to say about them",
            all(l["n"] == 1 for l in lines), detail=str([(l["email"], l["n"]) for l in lines]))

    r = oc.get("/admin/newsletter?source=Squarespace")
    chip = r.get_data(as_text=True)
    s.check("the list's Came from chip shows the ones from Squarespace",
            NEW_A in chip and EX_ON not in chip, r)

    s.section("Run once, of the file checked")
    r = oc.post("/admin/newsletter/import", data={"action": "run", "token": token or ""},
                follow_redirects=True)
    s.check("the same check cannot be run twice",
            "already been run" in " ".join(flashes(r)), detail=str(flashes(r)))
    r = _upload(oc, text)
    page = r.get_data(as_text=True)
    s.check("checking the same file again finds nobody to add",
            "There is nobody in this file to add" in page and "Put " not in page)
    r = oc.post("/admin/newsletter/import", data={"action": "check"}, follow_redirects=True)
    s.check("a check with no file asks for one", "Choose the exported file first" in
            " ".join(flashes(r)), detail=str(flashes(r)))
    r = _upload(oc, _csv(["Name,Phone", "Anne,0102"]))
    s.check("a file with no addresses says so, and saves nothing",
            "no column of email addresses" in " ".join(flashes(r)), detail=str(flashes(r)))

    s.section("Twenty-five thousand at once")
    big = _csv(["Email,Subscribed On,Status"]
               + [f"{BULK.format(i)},2020-01-01,Subscribed" for i in range(BULK_COUNT)])
    started = time.monotonic()
    r = _upload(oc, big)
    page = r.get_data(as_text=True)
    checked = time.monotonic() - started
    s.check("a list the size of the old one checks", f"Put {BULK_COUNT} on the list" in page,
            r, detail=f"{checked:.1f}s")
    started = time.monotonic()
    r = oc.post("/admin/newsletter/import", data={"action": "run", "token": _token(page) or ""},
                follow_redirects=True)
    ran = time.monotonic() - started
    conn = db()
    n = conn.execute("SELECT COUNT(*) AS c FROM newsletter_subscribers WHERE email LIKE ?",
                     (TAG + "bulk%",)).fetchone()["c"]
    conn.close()
    s.check("and comes across whole", n == BULK_COUNT and
            f"Brought across {BULK_COUNT} subscribers" in " ".join(flashes(r)),
            detail=f"{n} on the list; {flashes(r)}")
    s.check("in well under a request's time limit", checked + ran < 60,
            detail=f"check {checked:.1f}s, run {ran:.1f}s")
    s.check("still writing to nobody", len(letters.sent) == before)


if __name__ == "__main__":
    print(run().report())
