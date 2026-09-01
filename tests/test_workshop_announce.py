"""The public page promises dates go to the newsletter. Nothing could send them.

templates/workshops_public.html says, in two places:

    "Dates for the coming season are being finalised. They are released
     here first, and to the newsletter before that."

That is a claim about this code, in the same way the privacy notice is —
and this suite checks the claim, not that the page renders. It was not
true. The newsletter list exists and people subscribe to it, and
CAMPAIGN_SEGMENTS — the audience picker on the campaign sender — offered
room guests, dinner guests, workshop guests and all profiles. Not the
newsletter. The one list that asked to hear about dates was the one list a
campaign could not reach.

Not a deliberate exclusion either: the promo-code blast has always had a
newsletter box, separated with a comment about it being a different KIND of
list. The campaign sender simply never got one, and the promise was written
as though it had.

The second half is the per-workshop alumni. A new date is announced to the
people who did the last one; "everybody who ever attended anything" is the
wrong list for it, and it was the only workshop audience there was.

If the wording on the public page ever changes, this suite should change
with it — a page that promises something the software does not do is worse
than a page that promises nothing.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZANN"


def _cleanup(conn):
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM newsletter_subscribers WHERE email LIKE ?",
                 ("%" + TAG.lower() + "%",))
    conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Announcing new dates")
    today = house_today()
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    s.section("What the public page actually promises")
    # Read from the page rather than assumed, so that rewording it makes
    # this suite say so instead of quietly passing on a claim nobody makes
    # any more.
    with open(_harness.ROOT + "/templates/workshops_public.html",
              encoding="utf-8") as fh:
        public = fh.read()
    promises_newsletter = "to the newsletter before that" in public
    s.check("the page tells guests dates reach the newsletter first",
            promises_newsletter,
            detail="if this has been reworded, the checks below are about a "
                   "promise nobody is making and should be reconsidered "
                   "rather than kept green")

    s.section("So a campaign has to be able to reach the newsletter")
    s.check("the newsletter is one of the audiences on offer",
            "newsletter" in m.CAMPAIGN_SEGMENTS,
            detail=f"{sorted(m.CAMPAIGN_SEGMENTS)} — the promo-code blast "
                   "has always had a newsletter box; the campaign sender, "
                   "which is what an announcement is, never did")

    # And it must resolve to real people, not merely be a key in a dict.
    conn.execute(
        """INSERT INTO newsletter_subscribers (email, token, source,
                                               confirmed_at, created_at)
           VALUES (?, ?, 'test', ?, ?)""",
        (TAG.lower() + "reader@example.invalid", "tok-" + TAG.lower(), now, now))
    conn.commit()
    reached = m.campaign_audience(conn, ["newsletter"])
    s.check("and a confirmed subscriber is in that audience",
            TAG.lower() + "reader@example.invalid" in reached,
            detail=f"{len(reached)} in the newsletter audience")

    s.section("A new date is announced to the people who did the last one")
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, created_at)
           VALUES (?, '', 300, 12, 1, ?)""", (TAG + " Cooking", now))
    wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                          capacity, created_at)
           VALUES (?, ?, ?, 12, ?)""",
        (wid, (today - timedelta(days=90)).isoformat(),
         (today - timedelta(days=87)).isoformat(), now))
    past = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                                          party_size, status, reference_code,
                                          manage_token, created_at)
           VALUES (?, 'Aline', ?, 1, 'confirmed', ?, ?, ?)""",
        (past, TAG.lower() + "aline@example.invalid", TAG + "ALINE",
         "tok-" + TAG.lower() + "-aline", now))
    conn.commit()

    # Its own template. The harness runs against a copy of the database, so
    # this never reaches a guest, and borrowing a seeded row would make the
    # section pass or fail on somebody else's data.
    conn.execute(
        """INSERT INTO campaign_templates (name, area, category, subject,
                                           body, created_at, updated_at)
           VALUES (?, 'guests', 'marketing', 'New dates',
                   'Dates are up.', ?, ?)""", (TAG + " Dates", now, now))
    template_id = conn.execute(
        "SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    body = oc.get(f"/admin/emails/{template_id}").get_data(as_text=True)
    s.check("the workshop's own alumni are offered as an audience",
            f'value="workshop:{wid}"' in body,
            detail="'everybody who ever attended anything' was the only "
                   "workshop audience there was, and it is the wrong "
                   "list for a new date")
    s.check("named after the workshop, so the owner knows which",
            f"Did {TAG} Cooking" in body)
    s.check("and the newsletter has a box of its own",
            'value="newsletter"' in body)
    s.check("with a real count beside it, not a placeholder",
            str(len(reached)) in body,
            detail="the count is the number typed back to confirm the "
                   "send, so it cannot be decorative")

    s.section("A workshop nobody has done yet is not offered")
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, created_at)
           VALUES (?, '', 300, 12, 1, ?)""", (TAG + " Brand New", now))
    fresh = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    # And the harder case, which is the one the date condition is actually
    # for: a workshop whose only session is still to come, with somebody
    # already booked on it. They have not done it — they are coming to it,
    # and "you loved this, here are new dates" is nonsense to them.
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, created_at)
           VALUES (?, '', 300, 12, 1, ?)""", (TAG + " Still To Come", now))
    coming_w = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                          capacity, created_at)
           VALUES (?, ?, ?, 12, ?)""",
        (coming_w, (today + timedelta(days=40)).isoformat(),
         (today + timedelta(days=43)).isoformat(), now))
    coming_s = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                                          party_size, status, reference_code,
                                          manage_token, created_at)
           VALUES (?, 'Gilles', ?, 1, 'confirmed', ?, ?, ?)""",
        (coming_s, TAG.lower() + "gilles@example.invalid", TAG + "GILLES",
         "tok-" + TAG.lower() + "-gilles", now))
    conn.commit()

    body = oc.get(f"/admin/emails/{template_id}").get_data(as_text=True)
    s.check("one with no sessions at all is absent",
            f'value="workshop:{fresh}"' not in body)
    s.check("and so is one whose only session has not happened yet",
            f'value="workshop:{coming_s}"' not in body
            and f'value="workshop:{coming_w}"' not in body,
            detail="somebody booked on a session still to come has not done "
                   "the workshop, and this is the case the date condition "
                   "exists for \u2014 the previous check was being kept true "
                   "by the join instead")

    s.section("A malformed audience token is refused")
    # No fall-back-to-everybody on this route, so nothing widens — but the
    # tokens now carry an argument and the blast route already learned this
    # lesson the expensive way.
    s.check("a well-formed one survives",
            m.valid_segments([f"workshop:{wid}"]) == [f"workshop:{wid}"])
    for junk in ("workshop:", "workshop:abc", "everyone"):
        s.check(f"{junk!r} is dropped", m.valid_segments([junk]) == [])
    s.check("and the newsletter passes as itself",
            m.valid_segments(["newsletter"]) == ["newsletter"])

    # Checking the helper proves the helper. Whether the ROUTE calls it is a
    # different question, and the answer used to be invisible: taking the
    # call out left this suite green.
    #
    # Safe to exercise. With validation the junk is dropped, no audience is
    # left, and the route stops at "choose at least one audience" before it
    # can send anything. Without it, the junk reaches the audience builder,
    # which ignores it, and the route carries on to the confirm-count step
    # instead -- a different message, and still no send.
    r = oc.post(f"/admin/emails/{template_id}/send",
                data={"segments": "workshop:not-a-number", "mode": "real"},
                follow_redirects=True)
    said = " ".join(flashes(r)).lower()
    s.check("the send route drops it before counting an audience",
            "at least one audience" in said,
            detail=f"{said!r} \u2014 anything else means the junk reached "
                   "the audience builder")

    s.section("The step is findable from the session it is about")
    listing = oc.get("/admin/workshops").get_data(as_text=True)
    s.check("an upcoming session offers it", "Announce" in listing,
            detail="a promise nobody can act on is the same as no promise")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
