"""A performance review from draft to acknowledged.

A review is written frankly and only becomes the employee's business when the
manager decides it does. Three things follow from that, and none were tested:

  - a draft is invisible to the person it is about, including its text
  - sharing is what makes it visible, and it happens once
  - the acknowledgement is the employee's, and cannot be given by anyone else

The second one had a bug. The UPDATE is guarded with `AND status = 'draft'`, so
pressing Share again correctly changes nothing — but the notification was sent
regardless. An employee who had already read and acknowledged a review got
"Your performance review is ready" again, went looking for a new one, and found
the old one. The owner saw "Review shared with the employee." either way, so
there was nothing on either side to say the second press had done nothing.

Sharing is also the moment a review becomes usable in a promotion or a
dismissal, so it is now audited. Writing one already was.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZREV"
FRANK = "needs to stop arriving at ten past nine"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM performance_reviews WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM notifications WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _employee(name):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, role, status, job_role, password_hash, created_at)
           VALUES (?, ?, 'employee', 'active', 'Housekeeping', 'x', ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _as(user_id):
    """A client logged in as one particular person, the way the harness does it."""
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


def _review_of(user_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM performance_reviews WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,)).fetchone()
    finally:
        conn.close()


def _notifications(user_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? "
            "AND kind = 'performance_review'", (user_id,)).fetchone()["c"]
    finally:
        conn.close()


def run():
    s = Suite("Performance reviews")
    _cleanup()
    oc, ec, owner, emp = clients()

    person = _employee("Camille")
    theirs = _as(person["id"])
    today = m.house_today()

    s.section("Writing one")
    r = oc.post("/admin/hr/reviews/new", data={
        "user_id": str(person["id"]), "review_date": today.isoformat(),
        "period_start": (today - timedelta(days=180)).isoformat(),
        "period_end": today.isoformat(), "overall_rating": "4",
        "strengths": "unflappable in a full house",
        "improvements": FRANK, "goals": "run the turnover rota unaided",
    }, follow_redirects=True)
    rev = _review_of(person["id"])
    s.check("the review is saved", rev is not None, r)
    if rev is None:
        _cleanup()
        return s
    s.check("as a draft, not as something already sent",
            rev["status"] == "draft", detail=f"status {rev['status']!r}")
    s.check("with its rating", rev["overall_rating"] == 4)

    s.section("A rating outside 1-5 is dropped, not stored")
    # The column carries a CHECK constraint, so a bad value would 500 the form.
    oc.post("/admin/hr/reviews/new", data={
        "user_id": str(person["id"]), "review_date": today.isoformat(),
        "overall_rating": "9",
    }, follow_redirects=True)
    stray = _review_of(person["id"])
    s.check("the review is still created", stray["id"] != rev["id"])
    s.check("with no rating rather than an impossible one",
            stray["overall_rating"] is None, detail=f"got {stray['overall_rating']}")
    conn = db()
    conn.execute("DELETE FROM performance_reviews WHERE id = ?", (stray["id"],))
    conn.commit()
    conn.close()

    s.section("A review with no employee, or no date, is refused")
    before = _review_of(person["id"])["id"]
    oc.post("/admin/hr/reviews/new",
            data={"user_id": "", "review_date": today.isoformat()}, follow_redirects=True)
    oc.post("/admin/hr/reviews/new",
            data={"user_id": str(person["id"]), "review_date": ""}, follow_redirects=True)
    s.check("nothing is written either time",
            _review_of(person["id"])["id"] == before)

    s.section("A draft is not the employee's to read yet")
    page = theirs.get("/my-reviews")
    html = page.get_data(as_text=True)
    s.check("their reviews page loads", page.status_code == 200, page)
    s.check("the frank line from the unshared draft is nowhere in it", FRANK not in html,
            detail="an unfinished review leaked to the person it is about")

    s.section("Sharing is what makes it visible")
    n_before = _notifications(person["id"])
    oc.post(f"/admin/hr/reviews/{rev['id']}/share", follow_redirects=True)
    shared = _review_of(person["id"])
    s.check("the status moves to shared", shared["status"] == "shared",
            detail=f"got {shared['status']!r}")
    s.check("and the time it happened is recorded", shared["shared_at"] is not None)
    s.check("the employee is told, once", _notifications(person["id"]) == n_before + 1,
            detail=f"{n_before} -> {_notifications(person['id'])}")
    s.check("now they can read it", FRANK in theirs.get("/my-reviews").get_data(as_text=True),
            detail="a shared review still is not showing on their page")

    s.section("Sharing is audited — this is when it becomes usable")
    conn = db()
    audited = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'performance_review_shared'"
    ).fetchone()["c"]
    conn.close()
    s.check("there is a record of who released it", audited >= 1,
            detail="writing a review was audited, but handing it to the employee was not")

    s.section("The acknowledgement is the employee's, and nobody else's")
    other = _employee("Bruno")
    _as(other["id"]).post(f"/my-reviews/{rev['id']}/acknowledge",
                          data={"employee_comments": "not mine to sign"},
                          follow_redirects=True)
    s.check("another employee cannot acknowledge it",
            _review_of(person["id"])["status"] == "shared",
            detail="someone signed off a review that was not theirs")

    theirs.post(f"/my-reviews/{rev['id']}/acknowledge",
                data={"employee_comments": "fair enough, I will be on time"},
                follow_redirects=True)
    ack = _review_of(person["id"])
    s.check("the right person can", ack["status"] == "acknowledged",
            detail=f"got {ack['status']!r}")
    s.check("their comment is kept with it",
            (ack["employee_comments"] or "").startswith("fair enough"))
    s.check("and the time of it", ack["acknowledged_at"] is not None)

    s.section("Pressing Share again does not ping them a second time")
    # The bug. The UPDATE is guarded, so the row is untouched — but the
    # notification went out anyway, sending somebody back to re-read a review
    # they had already signed, while the owner was told it had been shared.
    settled = _review_of(person["id"])
    n2 = _notifications(person["id"])
    again = oc.post(f"/admin/hr/reviews/{rev['id']}/share", follow_redirects=True)
    said = " ".join(flashes(again)).lower()
    s.check("the acknowledgement is not undone",
            _review_of(person["id"])["status"] == "acknowledged")
    s.check("the shared time is unchanged",
            _review_of(person["id"])["shared_at"] == settled["shared_at"])
    s.check("no second notification is sent", _notifications(person["id"]) == n2,
            detail=f"{n2} -> {_notifications(person['id'])}; the employee is told a "
                   "review is ready when it is the one they already read")
    # Read the flash rather than the whole page: "already" turns up in
    # unrelated copy, so a substring search over the body passes either way.
    s.check("and the owner is told it did nothing, rather than that it worked",
            "already" in said and "shared with the employee" not in said,
            detail=f"flash was {said!r}")

    s.section("Guards")
    s.check("an employee cannot write a review",
            ec.post("/admin/hr/reviews/new", data={
                "user_id": str(person["id"]), "review_date": today.isoformat(),
            }).status_code in (302, 403))
    s.check("an employee cannot share one",
            ec.post(f"/admin/hr/reviews/{rev['id']}/share").status_code in (302, 403))
    s.check("sharing a review that does not exist is a 404",
            oc.post("/admin/hr/reviews/999999/share").status_code == 404)

    _cleanup()
    return s
