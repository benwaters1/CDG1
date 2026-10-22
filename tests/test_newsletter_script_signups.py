"""Script sign-ups already on the newsletter list, and taking them off.

Between subscribe-as-you-press going live (19 October 2026) and the bot guards
the next day, a script typed Gmail addresses spelt with a dot between most of
their letters into the sign-up box. Each became a confirmed subscriber and was
thanked; the confirm-first days before that left more as pending rows. The
guards stop new ones. These were already in, and the next campaign to
"Newsletter subscribers" would write to every one of them.

Checked here, each with the failure it guards:

  - the page flags exactly what the sign-up box would refuse today: a Gmail
    address with three dots or more, and a later spelling of a Gmail inbox
    already on the list -- and NOT the spelling that got there first, NOT a
    plain spelling whose only rival is a scattered one, NOT dots at another
    provider, and NOT anybody who has already left;
  - taking them off is a bulk action that names what it did not do and why,
    and re-checks the rules itself rather than trusting a page drawn earlier;
  - taken off, not deleted, and not put on the do-not-write list -- the inbox
    behind the spelling is a real person's, who can still sign up and is
    thanked when they do;
  - the consent history says how, at the list's own moment, so the startup
    backfill finds nothing to add;
  - the list stops writing to them, and nothing is sent by taking them off.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, form_stamp
import _harness

m = _harness.m
TAG = "zzscr"

# Before the guards: 19 October, the evening the box started subscribing.
NIGHT = datetime(2026, 10, 19, 21, 0, tzinfo=timezone.utc)

KEEPER = TAG + "alice@gmail.com"              # on the list first: a person
SPELLING = TAG + ".alice@gmail.com"           # the same inbox, later
PLUS = TAG + "alice+news@googlemail.com"      # the same inbox, later still
WAITING = TAG + "ali.ce@gmail.com"            # the same inbox, confirm-first days
SCATTERED = TAG + "c.a.r.o.l@gmail.com"       # a dot between most of the letters
SCATTERED_PENDING = TAG + "d.a.v.e@gmail.com"
SCATTERED_FIRST = TAG + "b.o.b.x@gmail.com"   # scattered, and before the person
BOB = TAG + "bobx@gmail.com"                  # the person, after it
TWO_DOTS = TAG + ".mary.jane@gmail.com"       # a person's own two dots
OUTLOOK = TAG + ".e.r.i.c@outlook.com"        # dots are the name at Outlook
LEFT = TAG + "f.r.e.d.a@gmail.com"            # scattered, but already gone
FRANK_A = TAG + "frank@gmail.com"             # two pending spellings, neither
FRANK_B = TAG + "fr.ank@gmail.com"            #   on the list
CAROL = TAG + "carol@gmail.com"               # the inbox behind SCATTERED


def _at(minutes):
    return (NIGHT + timedelta(minutes=minutes)).isoformat()


def _cleanup():
    conn = db()
    for table in ("newsletter_subscribers", "email_optouts", "consent_events"):
        conn.execute(f"DELETE FROM {table} WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE ip_address = '198.51.100.222'")
    conn.commit()
    conn.close()


def _put(email, minutes, *, confirmed=True, left=False):
    conn = db()
    cur = conn.execute(
        """INSERT INTO newsletter_subscribers (email, token, source, created_at,
                                               confirmed_at, confirm_sent_at, unsubscribed_at)
           VALUES (?, ?, 'site', ?, ?, ?, ?)""",
        (email, "tok-" + email, _at(minutes), _at(minutes) if confirmed else None,
         _at(minutes), _at(minutes + 30) if left else None))
    conn.commit()
    conn.close()
    return cur.lastrowid


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


def run():
    s = Suite("Newsletter: script sign-ups already on the list")
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
    ids = {
        KEEPER: _put(KEEPER, 0),
        SCATTERED_FIRST: _put(SCATTERED_FIRST, 5),
        SPELLING: _put(SPELLING, 10),
        PLUS: _put(PLUS, 20),
        WAITING: _put(WAITING, -600, confirmed=False),
        SCATTERED: _put(SCATTERED, 30),
        SCATTERED_PENDING: _put(SCATTERED_PENDING, -900, confirmed=False),
        BOB: _put(BOB, 40),
        TWO_DOTS: _put(TWO_DOTS, 50),
        OUTLOOK: _put(OUTLOOK, 60),
        LEFT: _put(LEFT, 70, left=True),
        FRANK_A: _put(FRANK_A, -800, confirmed=False),
        FRANK_B: _put(FRANK_B, -700, confirmed=False),
    }

    s.section("What the page flags")
    conn = db()
    flagged = m.newsletter_script_signups(conn)
    conn.close()
    mine = {e: flagged.get(i) for e, i in ids.items()}
    should = {SCATTERED, SCATTERED_PENDING, SCATTERED_FIRST, SPELLING, PLUS, WAITING}
    s.check("exactly the addresses the sign-up box would refuse today are flagged",
            {e for e, why in mine.items() if why} == should,
            detail=str(sorted(e for e, why in mine.items() if why)))
    s.check("a dot between most of the letters is said as such",
            mine[SCATTERED] == m.SCRIPT_SIGNUP_DOTS, detail=str(mine[SCATTERED]))
    s.check("a later spelling names the one on the list it copies",
            KEEPER in (mine[SPELLING] or "") and KEEPER in (mine[PLUS] or ""),
            detail=f"{mine[SPELLING]} / {mine[PLUS]}")
    s.check("the spelling that got onto the list first is the person, and is kept",
            mine[KEEPER] is None)
    s.check("a pending spelling of an inbox on the list is flagged too -- the box "
            "would refuse it", bool(mine[WAITING]), detail=str(mine[WAITING]))
    s.check("a plain spelling whose only rival is a scattered one is the person, and "
            "is kept even though the scattered one came first", mine[BOB] is None,
            detail=str(mine[BOB]))
    s.check("a person's own two dots are not flagged", mine[TWO_DOTS] is None)
    s.check("dots at another provider are the person's name, not a trick",
            mine[OUTLOOK] is None)
    s.check("an address that has already left is not on the list, so not flagged",
            mine[LEFT] is None)
    s.check("two pending spellings, neither ever on the list, make neither a copy",
            mine[FRANK_A] is None and mine[FRANK_B] is None)

    oc, ec, owner, emp = clients()
    r = oc.get("/admin/newsletter")
    page = r.get_data(as_text=True)
    s.check("the list page opens", r.status_code == 200, r, detail=str(r.status_code))
    s.check("and says how many look like scripts", "Look like scripts" in page)
    s.check("with a button to take the ticked off",
            'id="take-off-scripts"' in page and "Take the ticked off the list" in page)
    boxes = {e for e, i in ids.items()
             if f'name="subscriber_ids" value="{i}"' in page}
    s.check("every flagged row carries a tick, and no other row does",
            should <= boxes and not (boxes - should),
            detail=f"missing {sorted(should - boxes)}, extra {sorted(boxes - should)}")
    s.check("the reason is on the row", "Another spelling of " + KEEPER in page)
    r = oc.get("/admin/newsletter?script=" + m.SCRIPT_SIGNUP_CHIP)
    chip = r.get_data(as_text=True)
    s.check("the chip shows only the flagged ones",
            SCATTERED in chip and KEEPER + "<" not in chip and TWO_DOTS not in chip,
            detail="keeper or a person shown under the chip")

    s.section("Taking them off")
    before = len(letters.sent)
    posted = [ids[SCATTERED], ids[SCATTERED_PENDING], ids[SPELLING], ids[PLUS],
              ids[KEEPER], ids[LEFT], 999999999]
    r = oc.post("/admin/newsletter/take-off-scripts",
                data={"subscriber_ids": [str(i) for i in posted]}, follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("the press is answered", r.status_code == 200, r, detail=str(r.status_code))
    s.check("it says how many of how many it took off", "Removed 4 of 7 subscribers" in said,
            detail=said)
    s.check("it names the one it left on, and why", KEEPER in said
            and "do not call it a script" in said, detail=said)
    s.check("it names the one already gone", LEFT in said and "already off the list" in said,
            detail=said)
    s.check("and the one that is not there at all", "subscriber #999999999" in said,
            detail=said)
    s.check("and is an error, because some were not done", 'flash-error' in r.get_data(as_text=True))

    taken = [SCATTERED, SCATTERED_PENDING, SPELLING, PLUS]
    rows = {e: _row(e) for e in taken}
    s.check("the rows are still there -- taken off, not deleted",
            all(rows[e] is not None for e in taken))
    s.check("each is off the list as the unsubscribe link leaves it",
            all(rows[e]["unsubscribed_at"] and rows[e]["confirmed_at"] is None for e in taken))
    s.check("and marked as taken off at the same moment",
            all(rows[e]["script_removed_at"] == rows[e]["unsubscribed_at"] for e in taken))
    s.check("the spelling that was ticked but is a person is untouched",
            _row(KEEPER)["unsubscribed_at"] is None and _row(KEEPER)["confirmed_at"])
    s.check("the one not ticked is still flagged and still on",
            _row(WAITING)["unsubscribed_at"] is None)

    conn = db()
    optouts = conn.execute("SELECT email FROM email_optouts WHERE email LIKE ?",
                           (TAG + "%",)).fetchall()
    consent = {c["email"]: c for c in conn.execute(
        "SELECT * FROM consent_events WHERE email LIKE ? AND channel = 'newsletter'",
        (TAG + "%",)).fetchall()}
    recipients = {r["email"] for r in m.newsletter_recipients(conn)}
    conn.close()
    s.check("none is put on the do-not-write list -- the inbox is a real person's",
            not optouts, detail=str([o["email"] for o in optouts]))
    s.check("each no is in the consent history with how",
            all(e in consent and consent[e]["granted"] == 0
                and "script sign-up" in consent[e]["how"] for e in taken),
            detail=str({e: consent.get(e) and consent[e]["how"] for e in taken}))
    s.check("at the list's own moment",
            all(consent[e]["created_at"] == rows[e]["unsubscribed_at"] for e in taken
                if e in consent))
    s.check("the newsletter no longer writes to them",
            not (recipients & set(taken)), detail=str(recipients & set(taken)))
    s.check("and still writes to the person it kept", KEEPER in recipients)
    s.check("taking them off sends nobody anything", len(letters.sent) == before,
            detail=str(letters.sent[before:]))

    conn = db()
    m.backfill_consent_history(conn)
    lines = conn.execute(
        "SELECT email, COUNT(*) AS n FROM consent_events WHERE email LIKE ? AND channel = "
        "'newsletter' AND granted = 0 GROUP BY email", (TAG + "%",)).fetchall()
    conn.commit()
    conn.close()
    s.check("the startup backfill finds nothing more to say about them",
            all(l["n"] == 1 for l in lines if l["email"] in taken),
            detail=str([(l["email"], l["n"]) for l in lines]))

    r = oc.get("/admin/newsletter")
    page = r.get_data(as_text=True)
    s.check("the list says Taken off for them", "Taken off" in page)
    s.check("and no longer offers to tick them",
            not any(f'name="subscriber_ids" value="{ids[e]}"' in page for e in taken))
    conn = db()
    states = {r["email"]: r["state"] for r in m.newsletter_rows(conn)}
    conn.close()
    s.check("each is Taken off, and the one who left by the link is still Left",
            all(states[e] == "Taken off" for e in taken) and states[LEFT] == "Left",
            detail=str({e: states.get(e) for e in taken + [LEFT]}))

    s.section("Pressed twice, pressed empty, pressed by staff")
    r = oc.post("/admin/newsletter/take-off-scripts",
                data={"subscriber_ids": [str(ids[SCATTERED])]}, follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("a second press takes nothing off, and says why",
            "Nothing was removed" in said and "already off the list" in said, detail=said)
    r = oc.post("/admin/newsletter/take-off-scripts", data={}, follow_redirects=True)
    s.check("an empty press says nothing was selected",
            "Nothing was selected" in " ".join(flashes(r)), detail=str(flashes(r)))
    if emp:
        r = ec.post("/admin/newsletter/take-off-scripts",
                    data={"subscriber_ids": [str(ids[WAITING])]})
        s.check("staff without the owner's keys cannot press it",
                r.status_code in (302, 403), detail=str(r.status_code))
        s.check("and nothing came off when they tried", _row(WAITING)["unsubscribed_at"] is None)

    r = oc.post("/admin/newsletter/take-off-scripts",
                data={"subscriber_ids": [str(ids[WAITING])]}, follow_redirects=True)
    s.check("a press where every one comes off says so plainly, as good news",
            "Removed 1 subscriber." in flashes(r)
            and "flash-success" in r.get_data(as_text=True), r, detail=str(flashes(r)))
    s.check("and the last one is off", _row(WAITING)["unsubscribed_at"] is not None)

    s.section("The person behind the inbox can still join")
    c = m.app.test_client()
    r = c.post("/newsletter/subscribe",
               data={"email": CAROL, m.BOT_HONEYPOT_FIELD: "", m.BOT_STAMP_FIELD: form_stamp(60)},
               environ_base={"REMOTE_ADDR": "198.51.100.222"}, follow_redirects=True)
    carol = _row(CAROL)
    s.check("the real owner of a script's inbox signs up and is on the list",
            carol is not None and carol["confirmed_at"] and carol["unsubscribed_at"] is None,
            r, detail=str(dict(carol) if carol else None))
    s.check("and is thanked, once", [t for t, _ in letters.sent].count(CAROL) == 1,
            detail=str(letters.sent))

    s.section("A later leave or join is read as the person again")
    conn = db()
    conn.execute("UPDATE newsletter_subscribers SET unsubscribed_at = ? WHERE email = ?",
                 (_at(5000), SPELLING))
    conn.commit()
    states = {r["email"]: r["state"] for r in m.newsletter_rows(conn)}
    conn.close()
    s.check("once unsubscribed_at moves on, it is Left, not Taken off",
            states[SPELLING] == "Left", detail=str(states[SPELLING]))


if __name__ == "__main__":
    print(run().report())
