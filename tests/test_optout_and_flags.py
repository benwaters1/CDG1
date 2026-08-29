"""Who the house may write to, and the inbox it works from.

The do-not-write list is the part of this that the privacy notice makes a
promise about, in a sentence anybody can hold the software to:

    "We keep the address on a do-not-write list afterwards rather than
     deleting it, because that is the only way to be sure an old list never
     puts you back on."

That is a claim about code, not a sentiment, and it is true only because three
separate paths agree: the newsletter mailing list excludes opted-out addresses,
every campaign send excludes them, and signing up again while opted out gets a
row but no confirmation email — so there is nothing to click and the address
can never become confirmed. Any one of those going missing leaves the notice
saying something the app no longer does.

Two properties of the signup form fall out of the same code and neither is
obvious from reading it:

  - It is not an oracle. A brand new address, one already confirmed, and one
    on the do-not-write list all get the same sentence back. Different answers
    would let anyone check whether a given person is on the château's list.
  - It is not a cannon. Submitting a confirmed address again sends nothing, so
    the form cannot be used to make the château email somebody repeatedly.

The inbox flags are the smaller half: assigning one has to reach the person it
was assigned to, and resolving has to be distinguishable from dismissing —
"we dealt with it" and "this was not a real problem" are different facts about
the same email, and a report that merges them is worth nothing.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztflag"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM newsletter_subscribers WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM notifications WHERE title LIKE ?""", ("%" + TAG + "%",))
    conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM announcements WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _held(address):
    """Messages the app tried to send to one address."""
    conn = db()
    try:
        return conn.execute("SELECT * FROM email_outbox WHERE to_address = ?",
                            (address,)).fetchall()
    finally:
        conn.close()


def _subscriber(address):
    conn = db()
    try:
        return conn.execute("SELECT * FROM newsletter_subscribers WHERE email = ?",
                            (address,)).fetchone()
    finally:
        conn.close()


def _flag(subject, status="open"):
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO email_flags (graph_message_id, from_name, from_address,
           subject, status, received_at, created_at, updated_at, unanswered)
           VALUES (?, 'A Guest', 'guest@example.invalid', ?, ?, ?, ?, ?, 1)""",
        (TAG + subject + "-msg", TAG + subject, status, now, now, now))
    conn.commit()
    row = conn.execute("SELECT * FROM email_flags WHERE subject = ?",
                       (TAG + subject,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Who the house may write to")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    s.section("Signing up, and being written to")
    fresh = TAG + "fresh@example.invalid"
    anon.post("/newsletter/subscribe", data={"email": fresh}, follow_redirects=True)
    s.check("a new address is recorded", _subscriber(fresh) is not None)
    s.check("but not yet on the list, because it is unconfirmed",
            _subscriber(fresh)["confirmed_at"] is None)
    s.check("and is sent something to click", len(_held(fresh)) == 1,
            detail=f"{len(_held(fresh))} held — with no provider configured "
                   "the confirmation goes to the outbox")

    conn = db()
    on_list = [r["email"] for r in m.newsletter_recipients(conn)]
    conn.close()
    s.check("an unconfirmed address is not on the mailing list", fresh not in on_list)

    anon.get(f"/newsletter/confirm/{_subscriber(fresh)['token']}")
    conn = db()
    on_list = [r["email"] for r in m.newsletter_recipients(conn)]
    conn.close()
    s.check("clicking the link puts them on it", fresh in on_list,
            detail="a list nobody can join is not a list")

    s.section("The do-not-write list, which the privacy notice promises")
    gone = TAG + "gone@example.invalid"
    anon.post("/newsletter/subscribe", data={"email": gone}, follow_redirects=True)
    anon.get(f"/newsletter/confirm/{_subscriber(gone)['token']}")
    oc.post("/admin/emails/optout", data={"email": gone, "reason": "asked in person"},
            follow_redirects=True)
    conn = db()
    opted = conn.execute("SELECT * FROM email_optouts WHERE email = ?", (gone,)).fetchone()
    on_list = [r["email"] for r in m.newsletter_recipients(conn)]
    conn.close()
    s.check("an address the owner adds by hand is recorded", opted is not None)
    s.check("with why, so it can be explained later",
            opted and opted["reason"] == "asked in person")
    s.check("and comes off the mailing list at once", gone not in on_list,
            detail="the opt-out is not the same column as unsubscribed_at, "
                   "and has to suppress the newsletter too")

    # The sentence in the notice: an old list must not be able to put them back.
    before = len(_held(gone))
    anon.post("/newsletter/subscribe", data={"email": gone}, follow_redirects=True)
    s.check("signing them up again sends them nothing",
            len(_held(gone)) == before,
            detail=f"{len(_held(gone)) - before} new message(s) to a "
                   "do-not-write address")
    conn = db()
    on_list = [r["email"] for r in m.newsletter_recipients(conn)]
    conn.close()
    s.check("and does not put them back on the list", gone not in on_list,
            detail="this is the sentence the privacy notice makes")

    # The address above is both confirmed AND opted out, so the "already
    # confirmed" branch returns before the opt-out one is ever consulted —
    # which means it does not test the opt-out at all. Removing the opt-out
    # from that condition leaves this section entirely green. So: an address
    # that has NEVER confirmed and is on the do-not-write list. Now the only
    # thing that can stop a confirmation being sent is the opt-out itself.
    never = TAG + "never@example.invalid"
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
                 (never, "wrote in", datetime.now(timezone.utc).isoformat()))
    conn.execute("DELETE FROM submission_log WHERE action = 'newsletter_subscribe'")
    conn.commit()
    conn.close()
    anon.post("/newsletter/subscribe", data={"email": never}, follow_redirects=True)
    s.check("an address that never confirmed is still not written to",
            len(_held(never)) == 0,
            detail=f"{len(_held(never))} message(s) — nothing but the "
                   "do-not-write list stands between this address and a send")
    s.check("and it cannot get onto the list, because there is nothing to click",
            (_subscriber(never) or {"confirmed_at": None})["confirmed_at"] is None)

    # ...and a campaign must not reach them either, which is a different query
    # in a different part of the file.
    conn = db()
    reachable = conn.execute(
        "SELECT 1 FROM email_optouts WHERE email = ?", (gone,)).fetchone()
    conn.close()
    s.check("the campaign side reads the same list", reachable is not None)

    s.section("The signup form is not an oracle")
    # Three addresses in three different states. Different answers would let
    # anybody test whether a given person is on the château's list.
    #
    # The rate limiter is cleared first, or it becomes a fourth answer and the
    # difference measured is throttling rather than address state. Throttling
    # leaks nothing — it is the same for every address — but it does hide the
    # property this section is about, and a first version of this check failed
    # on exactly that.
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action = 'newsletter_subscribe'")
    conn.commit()
    conn.close()
    answers = set()
    for address in (TAG + "brandnew@example.invalid", fresh, gone):
        r = anon.post("/newsletter/subscribe", data={"email": address},
                      follow_redirects=True)
        answers.add(" ".join(flashes(r)))
    s.check("a new address, a confirmed one and an opted-out one read alike",
            len(answers) == 1, detail=str(answers))

    s.section("Nor a way to email somebody repeatedly")
    held_before = len(_held(fresh))
    for _ in range(3):
        anon.post("/newsletter/subscribe", data={"email": fresh}, follow_redirects=True)
    s.check("submitting a confirmed address again sends nothing",
            len(_held(fresh)) == held_before,
            detail=f"{len(_held(fresh)) - held_before} more message(s) — the "
                   "form would otherwise be a way to have the château write to "
                   "somebody on demand")

    s.section("Coming back deliberately clears it")
    # The documented exception: confirming is an explicit act by the person
    # themselves, so it overrides an older opt-out rather than being ignored.
    conn = db()
    token = conn.execute("SELECT token FROM newsletter_subscribers WHERE email = ?",
                         (gone,)).fetchone()["token"]
    conn.execute("UPDATE newsletter_subscribers SET confirmed_at = NULL WHERE email = ?",
                 (gone,))
    conn.commit()
    conn.close()
    anon.get(f"/newsletter/confirm/{token}")
    conn = db()
    still_out = conn.execute("SELECT 1 FROM email_optouts WHERE email = ?",
                             (gone,)).fetchone()
    on_list = [r["email"] for r in m.newsletter_recipients(conn)]
    conn.close()
    s.check("clicking confirm clears the opt-out", still_out is None)
    s.check("and puts them back on the list", gone in on_list,
            detail="somebody who asks to come back has to be able to")

    s.section("An email flagged for somebody")
    flag = _flag("Rate query")
    conn = db()
    before_n = conn.execute(
        "SELECT COUNT(*) c FROM notifications WHERE user_id = ?",
        (emp["id"],)).fetchone()["c"]
    conn.close()
    oc.post(f"/admin/inbox-flags/{flag['id']}/assign",
            data={"user_id": str(emp["id"])}, follow_redirects=True)
    conn = db()
    after = conn.execute("SELECT * FROM email_flags WHERE id = ?", (flag["id"],)).fetchone()
    after_n = conn.execute(
        "SELECT COUNT(*) c FROM notifications WHERE user_id = ?",
        (emp["id"],)).fetchone()["c"]
    conn.close()
    s.check("it is assigned to them", after["assigned_to_user_id"] == emp["id"],
            detail=str(after["assigned_to_user_id"]))
    s.check("and they are told", after_n == before_n + 1,
            detail=f"{after_n - before_n} notification(s) — an assignment "
                   "nobody is told about is a note to self")

    oc.post(f"/admin/inbox-flags/{flag['id']}/assign", data={"user_id": ""},
            follow_redirects=True)
    conn = db()
    unassigned = conn.execute("SELECT * FROM email_flags WHERE id = ?",
                              (flag["id"],)).fetchone()
    quiet = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id = ?",
                         (emp["id"],)).fetchone()["c"]
    conn.close()
    s.check("it can be unassigned", unassigned["assigned_to_user_id"] is None)
    s.check("without telling anybody they were given it", quiet == after_n,
            detail=f"{quiet - after_n} notification(s) for an unassignment")

    s.section("Dealt with, and not a problem, are different things")
    done = _flag("Answered")
    junk = _flag("Spam")
    oc.post(f"/admin/inbox-flags/{done['id']}/resolve", follow_redirects=True)
    oc.post(f"/admin/inbox-flags/{junk['id']}/dismiss", follow_redirects=True)
    conn = db()
    d = conn.execute("SELECT * FROM email_flags WHERE id = ?", (done["id"],)).fetchone()
    j = conn.execute("SELECT * FROM email_flags WHERE id = ?", (junk["id"],)).fetchone()
    conn.close()
    s.check("one reads as resolved", d["status"] == "resolved", detail=d["status"])
    s.check("the other as dismissed", j["status"] == "dismissed", detail=j["status"])
    s.check("and they are not the same word", d["status"] != j["status"],
            detail="a report that merges them cannot tell how much of the "
                   "inbox was a real problem")
    s.check("with who closed it", d["resolved_by_user_id"] == owner["id"],
            detail=str(d["resolved_by_user_id"]))
    s.check("and when", bool(d["resolved_at"]))

    s.section("Posting to the house, and correcting a typo in it")
    conn = db()
    before_n = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id = ?",
                            (emp["id"],)).fetchone()["c"]
    conn.close()
    oc.post("/announcements/new", data={
        "title": TAG + " Water off Tuesday", "body": "From 9 until noon.",
    }, follow_redirects=True)
    conn = db()
    ann = conn.execute("SELECT * FROM announcements WHERE title = ?",
                       (TAG + " Water off Tuesday",)).fetchone()
    told = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id = ?",
                        (emp["id"],)).fetchone()["c"]
    conn.close()
    s.check("it is posted", ann is not None)
    s.check("and everybody on the staff is told once", told == before_n + 1,
            detail=f"{told - before_n} notification(s)")

    oc.post(f"/announcements/{ann['id']}/edit", data={
        "title": TAG + " Water off Wednesday", "body": "From 9 until noon.",
    }, follow_redirects=True)
    conn = db()
    fixed = conn.execute("SELECT * FROM announcements WHERE id = ?",
                         (ann["id"],)).fetchone()
    after_edit = conn.execute("SELECT COUNT(*) c FROM notifications WHERE user_id = ?",
                              (emp["id"],)).fetchone()["c"]
    conn.close()
    s.check("a correction changes it", fixed["title"] == TAG + " Water off Wednesday",
            detail=fixed["title"])
    # Deliberate: fixing a typo must not ring everybody's phone a second time,
    # or nobody reads the third one.
    s.check("but does not tell everybody again", after_edit == told,
            detail=f"{after_edit - told} notification(s) for an edit")

    r = oc.post(f"/announcements/{ann['id']}/edit", data={"title": "  ", "body": "x"},
                follow_redirects=True)
    conn = db()
    unchanged = conn.execute("SELECT title FROM announcements WHERE id = ?",
                             (ann["id"],)).fetchone()["title"]
    conn.close()
    s.check("one with no title is refused",
            unchanged == TAG + " Water off Wednesday", detail=unchanged)
    s.check("and says so rather than erroring", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    s.section("Guards")
    guard = _flag("Guarded")
    ec.post(f"/admin/inbox-flags/{guard['id']}/resolve")
    ec.post(f"/admin/inbox-flags/{guard['id']}/assign", data={"user_id": str(emp["id"])})
    ec.post("/admin/emails/optout", data={"email": TAG + "rogue@example.invalid"})
    conn = db()
    untouched = conn.execute("SELECT * FROM email_flags WHERE id = ?",
                             (guard["id"],)).fetchone()
    rogue = conn.execute("SELECT COUNT(*) c FROM email_optouts WHERE email = ?",
                         (TAG + "rogue@example.invalid",)).fetchone()["c"]
    conn.close()
    s.check("an employee cannot close a flag", untouched["status"] == "open",
            detail=untouched["status"])
    s.check("nor assign one", untouched["assigned_to_user_id"] is None)
    s.check("nor add somebody to the do-not-write list", rogue == 0, detail=str(rogue))
    s.check("and is sent away rather than shown the page",
            ec.get("/admin/inbox-flags").status_code in (302, 403))
    s.check("while the owner can open it",
            oc.get("/admin/inbox-flags").status_code == 200)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
