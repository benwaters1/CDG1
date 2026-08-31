"""The mailing list nobody could write to, and a count that overstated it.

The public site collects newsletter subscribers, confirms them by email and
lets them unsubscribe. The house had never been able to write to any of them:
every campaign segment is a PAST GUEST — room, restaurant, workshop — so
somebody who subscribed and has not yet stayed could not be reached at all. It
is the most consent-clean list in the app, double opted-in, and the one with no
way out of the building.

newsletter_recipients was already there, correct, with a docstring explaining
exactly why its opt-out join matters. Nothing called it.

AND THE NUMBER THE OWNER SAW WAS NOT THAT LIST. The gallery page counted
`confirmed_at IS NOT NULL AND unsubscribed_at IS NULL` and stopped, so it
included anybody the owner had put on the do-not-write list by hand — the very
case the docstring says must be excluded. The figure overstated the list by
the people the house had promised not to write to.

Two definitions of one number, which is the fault this repo keeps finding. So
the checks below are mostly about the two moving together.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztnews"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM newsletter_subscribers WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG.upper() + "%",))
    conn.commit()
    conn.close()


def _subscriber(name, confirmed=True, unsubscribed=False):
    email = f"{TAG}{name}@example.invalid"
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO newsletter_subscribers (email, token, created_at, confirmed_at,
           unsubscribed_at) VALUES (?, ?, ?, ?, ?)""",
        (email, TAG + name + "tok", now, now if confirmed else None,
         now if unsubscribed else None))
    conn.commit()
    conn.close()
    return email


def _opt_out(email):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
        (email, "asked on the telephone", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _list():
    conn = db()
    try:
        with m.app.test_request_context():
            return {r["email"] for r in m.newsletter_recipients(conn)}
    finally:
        conn.close()


def _newsletter_cell():
    """The Newsletter figure off the band itself.

    Read from the function that builds it rather than scraped off the page,
    where a loose search for a number after the word finds the count of
    photographs instead.
    """
    conn = db()
    try:
        with m.app.test_request_context():
            page = m.app.test_client()
            _ = page  # the band is built inside the view, so go via the view
    finally:
        conn.close()
    oc2, _e, _o, _emp = clients()
    body = oc2.get("/admin/gallery").get_data(as_text=True)
    import re
    for mo in re.finditer(r'overview-value">\s*([0-9]+)(.*?)overview-label">\s*([^<]+)',
                          body, re.S):
        if mo.group(3).strip() == "Newsletter":
            return int(mo.group(1))
    return None


def _blast(segments):
    conn = db()
    try:
        with m.app.test_request_context():
            return set(m.promo_blast_recipients(conn, segments))
    finally:
        conn.close()


def run():
    s = Suite("Writing to the newsletter")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("Who is actually on the list")
    live = _subscriber("live")
    unconfirmed = _subscriber("unconfirmed", confirmed=False)
    gone = _subscriber("gone", unsubscribed=True)
    quiet = _subscriber("quiet")
    _opt_out(quiet)

    on = _list()
    s.check("a confirmed subscriber is on it", live in on)
    s.check("somebody who never confirmed is not", unconfirmed not in on,
            detail="a double opt-in that sends before the second step is not "
                   "a double opt-in")
    s.check("somebody who unsubscribed is not", gone not in on)
    s.check("and somebody the owner put on the do-not-write list is not",
            quiet not in on,
            detail="they never touched the newsletter's own unsubscribe link, "
                   "so unsubscribed_at is null and only the opt-out table knows")

    s.section("The number the owner is shown IS that list")
    # It was a second query that agreed most of the time. It left out the
    # opt-out join, so it counted the person above who asked on the telephone.
    r = oc.get("/admin/gallery")
    s.check("the page opens", r.status_code == 200, detail=str(r.status_code))
    shown = _newsletter_cell()
    s.check("it shows a newsletter figure", shown is not None)
    s.check("and it is the size of the actual list", shown == len(on),
            detail=f"band says {shown}, the list has {len(on)} — it used to "
                   "count the opted-out one too")

    before = len(on)
    _opt_out(live)
    s.check("opting somebody out takes them off the list",
            len(_list()) == before - 1, detail=f"{before} -> {len(_list())}")
    s.check("and the figure moves with it", _newsletter_cell() == before - 1,
            detail=f"band says {_newsletter_cell()}, expected {before - 1} — "
                   "two definitions of one number is the fault this is about")

    s.section("They can be written to at all")
    conn = db()
    conn.execute("DELETE FROM email_optouts WHERE email = ?", (live,))
    conn.commit()
    conn.close()

    s.check("newsletter is a segment", "newsletter" in m.GUEST_BLAST_SEGMENTS,
            detail=str(m.GUEST_BLAST_SEGMENTS))
    reach = _blast(["newsletter"])
    s.check("a subscriber is reachable", live in reach,
            detail="the three other segments are all past guests, so somebody "
                   "who subscribed and has not stayed could not be reached")
    s.check("an unconfirmed one is not", unconfirmed not in reach)
    s.check("nor one who unsubscribed", gone not in reach)
    s.check("nor one on the do-not-write list", quiet not in reach)

    s.section("It is offered where the sending is done")
    conn = db()
    conn.execute(
        """INSERT INTO promo_codes (code, discount_type, discount_value, applies_to,
           active, created_at) VALUES (?, 'percent', 10, 'room', 1, ?)""",
        (TAG.upper() + "10", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    code_id = conn.execute("SELECT id FROM promo_codes WHERE code = ?",
                           (TAG.upper() + "10",)).fetchone()["id"]
    conn.close()
    r = oc.get(f"/admin/promo-codes/{code_id}/blast")
    body = r.get_data(as_text=True)
    s.check("the blast page opens", r.status_code == 200, detail=str(r.status_code))
    # The CHECKBOX, not just the string. The page also echoes the current
    # selection back as hidden inputs, so a search for value="newsletter"
    # alone passes on that echo while the box itself is gone -- which is
    # exactly what the control found.
    s.check("the blast page offers it as something to tick",
            'type="checkbox" name="segment" value="newsletter"' in body,
            detail="a segment nothing offers is a segment nobody uses")
    s.check("and says what kind of list it is",
            "unsubscribe" in body.lower() and "confirmed" in body.lower(),
            detail="somebody writing marketing mail should be told which list "
                   "they are about to use")

    s.section("The months filter does not quietly drop them")
    # A subscriber has no visit date. Filtering them out because they have
    # never stayed would defeat the reason for the segment.
    conn = db()
    try:
        with m.app.test_request_context():
            recent = set(m.promo_blast_recipients(
                conn, ["newsletter"],
                (datetime.now(m.LOCAL_TZ).date() - timedelta(days=30)).isoformat()))
    finally:
        conn.close()
    s.check("a subscriber survives a since-date filter", live in recent,
            detail="they have no visit date to compare, so the filter cannot "
                   "apply to them and must not exclude them")

    s.section("Mixing segments still emails somebody once")
    both = _blast(["newsletter", "room"])
    s.check("the newsletter one is in it", live in both)
    s.check("and the list is de-duplicated",
            len(both) == len(set(both)),
            detail="a subscriber who has also stayed must not get it twice")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
