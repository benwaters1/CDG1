"""Every email between the house and a guest, from every inbox, beside their
email in Outlook and on their page in the dashboard.

Read from Microsoft 365 through a stand-in (tests/_graph_double.py). What it
must do:

  - search every inbox the reader may see, for every address the guest is
    known by -- a merged profile's included;
  - count a message once when it sits in two inboxes (a reply cc'd to a
    colleague), and say which inboxes;
  - keep accounts@ -- money -- to the owner;
  - name an inbox Microsoft would not open, in Microsoft's words, rather than
    show a shorter history as if it were the whole;
  - group it into conversations, newest first, and say whose turn it is;
  - and NOT COPY IT INTO THIS SYSTEM. The privacy notice says the history is
    read when somebody opens a guest and held in memory for a few minutes.
    So after reading, the whole database is searched for the words of the
    emails, and they must not be anywhere in it.
"""
from datetime import timedelta

from _graph_double import FakeGraph, message
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZEH"
GUEST = "zzeh.guest@example.invalid"
OLD = "zzeh.old@example.invalid"
RES, EXP, ACC, BCOM = ("zzeh.reservations@example.invalid", "zzeh.experience@example.invalid",
                       "accounts@zzeh-house.invalid", "zzeh.bookingcom@example.invalid")
SECRET_SUBJECT = "ZZEH the rose garden question"


def _cleanup(conn):
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox IN (?, ?, ?, ?)", (RES, EXP, ACC, BCOM))
    conn.execute("DELETE FROM addin_sessions WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'addin_%'")
    conn.commit()


def _days_ago(n):
    return (m.datetime.now(m.timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _found_anywhere(conn, needle):
    """Every table and text column in the database holding `needle`."""
    hits = []
    for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")
                if (r[2] or "").upper() in ("TEXT", "")]
        for col in cols:
            if conn.execute(f'SELECT 1 FROM "{table}" WHERE "{col}" LIKE ? LIMIT 1',
                            (f"%{needle}%",)).fetchone():
                hits.append(f"{table}.{col}")
    return hits


def run():
    s = Suite("the email history")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    gid = conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                       (f"{TAG} Guest", GUEST, now)).lastrowid
    conn.execute("INSERT INTO guests (name, email, merged_into_id, created_at) VALUES (?, ?, ?, ?)",
                 (f"{TAG} Guest (old)", OLD, gid, now))
    conn.commit()

    fake = FakeGraph(mailboxes=[RES, EXP, ACC, BCOM])
    reply = message("m2", frm=RES, to=[GUEST], subject="Re: Your stay in June", when=_days_ago(19),
                    preview="Of course — the tower room is yours.", internet_id="<shared-2@x>",
                    conversation="c1")
    fake.messages[RES] = [
        message("m1", frm=GUEST, to=[RES], subject="Your stay in June", when=_days_ago(20),
                preview="Could we have the tower room?", conversation="c1"),
        reply,
        message("draft", frm=RES, to=[GUEST], subject="unsent", when=_days_ago(1), draft=True),
        message("other", frm="someone.else@example.invalid", to=[RES], subject="Not them",
                when=_days_ago(2)),
    ]
    fake.messages[EXP] = [
        dict(reply, id="m2-copy"),
        message("m3", frm=OLD, to=[EXP], subject=SECRET_SUBJECT, when=_days_ago(5),
                preview="ZZEH-PREVIEW-WORDS may we see the roses", conversation="c2"),
    ]
    fake.messages[ACC] = [
        message("m4", frm=ACC, to=[GUEST], subject="Your statement", when=_days_ago(10),
                preview="Balance 120", conversation="c3"),
    ]
    fake.bodies["m3"] = "Dear house,\nZZEH-BODY-WORDS may we see the roses?\nAna"
    fake.refuse_mailboxes[BCOM] = 403

    with fake, m.app.test_request_context("/"):
        conn.execute("INSERT INTO mailbox_routing (mailbox, label, active, signature_name, created_at) "
                     "VALUES (?, 'Reservations', 1, 'The Château Team', ?)", (RES, now))
        conn.commit()
        addresses = m.guest_all_addresses(conn, gid)
        h_owner = m.guest_email_history(conn, addresses, owner=True)

        s.section("Every inbox, every address they are known by")
        searches = [c for c in fake.calls if c[0] == "GET" and c[1].endswith("/messages")]
        s.check("every inbox the owner may see was searched",
                {c[1].split("/")[2] for c in searches} == {RES, EXP, ACC, BCOM},
                detail=str(sorted({c[1].split('/')[2] for c in searches})))
        s.check("for the profile's address and the merged one's",
                all(GUEST in c[2]["$search"] and OLD in c[2]["$search"] for c in searches),
                detail=searches and searches[0][2]["$search"])
        subjects = [x["subject"] for x in h_owner["messages"]]
        s.check("what they wrote and what we wrote, from every inbox",
                {"Your stay in June", "Re: Your stay in June", SECRET_SUBJECT,
                 "Your statement"} == set(subjects), detail=str(subjects))
        s.check("a draft nobody sent is not history", "unsent" not in subjects)
        s.check("nor is somebody else's email", "Not them" not in subjects)
        twice = next((x for x in h_owner["messages"] if x["subject"] == "Re: Your stay in June"), None)
        s.check("a reply in two inboxes is counted once",
                subjects.count("Re: Your stay in June") == 1)
        s.check("and says both inboxes", twice and sorted(twice["mailboxes"]) == sorted([RES, EXP]),
                detail=twice and twice["mailboxes"])
        s.check("our side says who signs that inbox",
                twice and twice["direction"] == "out" and twice["signed_as"] == "The Château Team",
                detail=twice and (twice["direction"], twice["signed_as"]))
        s.check("newest first", h_owner["messages"][0]["subject"] == SECRET_SUBJECT)

        s.section("An inbox Microsoft would not open is named, not skipped")
        failed = h_owner["failed"]
        s.check("it is listed", failed and failed[0]["mailbox"] == BCOM, detail=str(failed))
        s.check("in Microsoft's own words", failed and "RAOP" in failed[0]["why"])

        s.section("Conversations, and whose turn it is")
        s.check("grouped into conversations", len(h_owner["threads"]) == 3,
                detail=str([t["subject"] for t in h_owner["threads"]]))
        latest = h_owner["threads"][0]
        s.check("the latest first, and theirs is waiting for us",
                latest["subject"] == SECRET_SUBJECT and latest["waiting"])
        s.check("the June conversation has both sides", next(
            t for t in h_owner["threads"] if t["subject"] == "Your stay in June")["count"] == 2)
        words = h_owner["summary"]["words"]
        s.check("summed up in a line", "4 emails in 3 conversations" in words
                and "The last word is theirs" in words, detail=words)

        s.section("accounts@ is the owner's")
        # Asked afresh: from the owner's copy in memory, a colleague's history
        # would open no inbox at all and prove nothing about which.
        m.email_cache_forget()
        fake.calls.clear()
        h_staff = m.guest_email_history(conn, addresses, owner=False)
        read = {c[1].split("/")[2] for c in fake.calls if c[1].endswith("/messages")}
        s.check("a colleague's history does not open it", ACC not in read, detail=str(read))
        s.check("so the statement is not in it",
                "Your statement" not in [x["subject"] for x in h_staff["messages"]])

        s.section("Held in memory for minutes, never written down")
        m.guest_email_history(conn, addresses, owner=True)      # read once...
        fake.calls.clear()
        m.guest_email_history(conn, addresses, owner=True)      # ...and again
        s.check("asked again within minutes, Microsoft is not asked again", not fake.calls)
        s.check("'a few minutes' is a few minutes", m.EMAIL_HISTORY_TTL <= 600 and m.EMAIL_BODY_TTL <= 600)
        bodies = m.email_bodies([x for x in h_owner["messages"] if x["subject"] == SECRET_SUBJECT])
        s.check("the text of a message is read for the drafter",
                any("ZZEH-BODY-WORDS" in t for t in bodies.values()), detail=str(bodies)[:120])
        asked = [c for c in fake.calls if c[1].endswith("/messages/m3")]
        s.check("only what that message added, as plain text",
                asked and "uniqueBody" in asked[0][2]["$select"]
                and "text" in asked[0][3].get("Prefer", ""), detail=str(asked)[:200])
        m.email_cache_forget()
        fake.calls.clear()
        m.guest_email_history(conn, addresses, owner=True)
        s.check("and forgotten on request", bool(fake.calls))

        s.section("Through the add-in")
        owner_key, _ = m.addin_issue_session(conn, owner["id"], "test", label=f"{TAG} owner")
        staff_key, _ = m.addin_issue_session(conn, emp["id"], "test", label=f"{TAG} staff")
        conn.commit()
        anon = m.app.test_client()
        r = anon.post("/api/addin/history", data={"email": OLD},
                      headers={"Authorization": f"Bearer {owner_key}"})
        got = r.get_json() or {}
        s.check("the panel gets the history from an old address too",
                r.status_code == 200 and got.get("available")
                and got["summary"]["messages"] == 4, detail=f"{r.status_code} {str(got)[:160]}")
        s.check("as conversations, with where each email is",
                got.get("threads") and got["threads"][0]["messages"][0]["mailboxes"] == [EXP])
        r = anon.post("/api/addin/history", data={"email": GUEST},
                      headers={"Authorization": f"Bearer {staff_key}"})
        got = r.get_json() or {}
        s.check("a colleague's panel has no statement in it",
                "Your statement" not in str(got) and got.get("available"))
        r = anon.post("/api/addin/history", data={"email": "not an address"},
                      headers={"Authorization": f"Bearer {owner_key}"})
        s.check("a bad address is answered, not raised", r.status_code == 400)

        s.section("On the guest page")
        r = oc.get(f"/guests/{gid}")
        s.check("the page asks for the emails after it has drawn",
                f"/guests/{gid}/emails" in r.get_data(as_text=True))
        part = oc.get(f"/guests/{gid}/emails").get_data(as_text=True)
        s.check("and they come back as conversations",
                SECRET_SUBJECT in part and "waiting for our reply" in part)
        s.check("with the inbox Microsoft would not open named", BCOM in part and "RAOP" in part)
        s.check("the emails are the owner's on the dashboard",
                ec.get(f"/guests/{gid}/emails").status_code in (302, 403))

    s.section("Nothing of it is in the database")
    for needle in ("ZZEH-PREVIEW-WORDS", "ZZEH-BODY-WORDS", "the rose garden question"):
        where = _found_anywhere(conn, needle)
        s.check(f"{needle!r} is nowhere", not where, detail=f"found in {where}")

    s.section("Not set up, it says so")
    m.email_cache_forget()
    h = m.guest_email_history(conn, [GUEST], owner=True)
    s.check("no history, and why", not h["available"] and "not set up" in h["why"], detail=h["why"])

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
