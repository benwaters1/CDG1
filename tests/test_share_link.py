"""A read-only link for the rest of the party.

One person books and forwards the confirmation email to everybody else. That
email carries the manage link, which carries the bill and the cancel button —
so telling five people what time to arrive has meant handing five people the
ability to cancel the stay and the total somebody paid for it.

THE PAGE'S OWN PROMISE IS THE SPECIFICATION: "Anyone with the link can see the
practical details. Nobody with it can see money or change the booking." This
file checks that sentence rather than checking the page renders.

Three structural decisions carry it, and each is chosen over an easier one
that would pass a lazier test:

  - ITS OWN ROUTE AND ITS OWN TEMPLATE, not the manage page with figures
    hidden. A hidden figure is one {% if %} away from visible, and whoever
    adds the next row to that page will not know the rule. The query behind
    this page does not SELECT a money column at all, so there is nothing to
    leak — which is why the check below looks for the amount the guest
    actually paid, not for the word "total".
  - GET ONLY. Not "the POST handlers check a flag": there are no POST
    handlers on it, and the suite asserts the methods.
  - MINTED ON REQUEST AND REVOCABLE. A token on every booking is a live URL
    for every booking that has ever existed, wanted or not.
"""
from datetime import timedelta

from _harness import Suite, db, house_today

import _harness

m = _harness.m
TAG = "ZZSHARE"


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the read-only link")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    _cleanup(conn)
    room = conn.execute("SELECT id, name FROM rooms ORDER BY id LIMIT 1").fetchone()

    PAID = 1234.56
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token,
                   guest_name, guest_email, arrival_date, departure_date,
                   party_size, status, total_price, amount_paid,
                   deposit_amount, estimated_arrival_time, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 4, 'confirmed', ?, ?, ?, 'around 6pm', ?)""",
        (room["id"], TAG + "ONE", TAG.lower() + "-manage", TAG + " Beatrice",
         TAG.lower() + "@example.invalid",
         (today + timedelta(days=20)).isoformat(),
         (today + timedelta(days=23)).isoformat(), PAID, PAID, 370.37, now))
    booking_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token,
                   guest_name, guest_email, party_size, dinner_date, status,
                   total_price, booking_id, created_at)
           VALUES (?, ?, ?, ?, 4, ?, 'confirmed', 240, ?, ?)""",
        (TAG + "DIN", TAG.lower() + "-din", TAG + " Beatrice",
         TAG.lower() + "@example.invalid",
         (today + timedelta(days=21)).isoformat(), booking_id, now))
    conn.commit()

    anon = m.app.test_client()
    guest = m.app.test_client()
    manage = f"/book/manage/{TAG.lower()}-manage"

    s.section("Nothing is made until somebody asks")
    s.check("a new booking has no link",
            conn.execute("SELECT share_token FROM bookings WHERE id = ?",
                         (booking_id,)).fetchone()["share_token"] is None,
            detail="a token on every booking is a live URL for every booking "
                   "that has ever existed, wanted or not")
    body = guest.get(manage).get_data(as_text=True)
    s.check("the page offers to make one", "Make a link" in body)

    s.section("Making one")
    guest.post(manage + "/share", follow_redirects=True)
    token = conn.execute("SELECT share_token FROM bookings WHERE id = ?",
                         (booking_id,)).fetchone()["share_token"]
    s.check("a token is minted", bool(token))
    s.check("and it is long enough not to be guessed",
            token and len(token) >= 24, detail=f"{len(token or '')} characters")
    body = guest.get(manage).get_data(as_text=True)
    s.check("the link is shown to whoever booked", f"/stay/{token}" in body)
    s.check("with a way to switch it off", "Stop the link working" in body)

    s.section("Asking twice does not mint a second one")
    guest.post(manage + "/share", follow_redirects=True)
    s.check("the same token stands",
            conn.execute("SELECT share_token FROM bookings WHERE id = ?",
                         (booking_id,)).fetchone()["share_token"] == token,
            detail="a new token every time silently breaks the link already "
                   "sent to five people")

    s.section("What the others see")
    r = anon.get(f"/stay/{token}")
    whole = r.get_data(as_text=True)
    # Sliced to THIS booking's section. Every public page carries a booking
    # search form, a newsletter box, a "change or cancel a reservation" link
    # and a footer note about who handles card payments -- none of which is
    # about this stay, and checking the whole document would have been
    # checking the site chrome.
    shared = whole[whole.find('g-shared-stay'):whole.find('</section>',
                                                          whole.find('g-shared-stay'))]
    s.check("the page opens with no login at all", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("and the section is there to check", bool(shared.strip()),
            detail="without it every check below is against an empty string, "
                   "which passes everything")
    s.check("the room", room["name"] in shared)
    s.check("the dates", (today + timedelta(days=20)).isoformat() in shared)
    s.check("what time they are expected", "around 6pm" in shared)
    s.check("the dinner booking",
            (today + timedelta(days=21)).isoformat() in shared)
    s.check("and the last stretch of the drive",
            "gates are on the left" in shared)

    s.section("And no money, anywhere")
    # The figure this guest actually paid, not the word "total". A check for
    # the word passes on a page that shows 1234.56 under a different label.
    for figure in ("1234.56", "1,234.56", "1234", "370.37"):
        s.check(f"{figure} does not appear", figure not in shared,
                detail="the whole promise of the page")
    s.check("no bill section", "Your bill" not in shared and "Balance" not in shared)
    s.check("and nothing to pay with", "stripe" not in shared.lower())

    s.section("And nothing that changes anything")
    s.check("no cancel", "cancel" not in shared.lower())
    s.check("no form at all on the page", "<form" not in shared,
            detail="a read-only page with a form on it is a read-only page "
                   "somebody has stopped thinking about")
    rule = [r for r in m.app.url_map.iter_rules() if r.endpoint == "shared_stay"][0]
    s.check("the route accepts GET only",
            rule.methods - {"HEAD", "OPTIONS"} == {"GET"},
            detail=str(sorted(rule.methods)))

    s.section("It is a different page, not the manage page in disguise")
    # The structural point. Hiding a figure is one {% if %} away from showing
    # it again; a template with no bill in scope has nothing to leak.
    import inspect
    import re as _re
    src = inspect.getsource(m.app.view_functions["shared_stay"])
    # Comments and the docstring stripped. Both name the columns they are
    # promising not to fetch, and reading those as code made this fail on the
    # very thing it exists to prove.
    src = _re.sub(r'''"""[\s\S]*?"""''', "", src, count=1)
    src = " ".join(line.split("#")[0] for line in src.splitlines())
    for money in ("total_price", "amount_paid", "deposit_amount"):
        s.check(f"the query does not even fetch {money}", money not in src,
                detail="what is not fetched cannot leak")
    s.check("it renders its own template", "shared_stay.html" in src)

    s.section("Switching it off really switches it off")
    guest.post(manage + "/share", data={"off": "1"}, follow_redirects=True)
    s.check("the token is gone",
            conn.execute("SELECT share_token FROM bookings WHERE id = ?",
                         (booking_id,)).fetchone()["share_token"] is None)
    r = anon.get(f"/stay/{token}")
    s.check("and the link somebody was sent stops working",
            r.status_code == 404, detail=f"HTTP {r.status_code}")

    s.section("A cancelled stay's link stops working too")
    guest.post(manage + "/share", follow_redirects=True)
    token2 = conn.execute("SELECT share_token FROM bookings WHERE id = ?",
                          (booking_id,)).fetchone()["share_token"]
    s.check("the new link works while the booking stands",
            anon.get(f"/stay/{token2}").status_code == 200)
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?",
                 (booking_id,))
    conn.commit()
    s.check("and stops the moment the booking is cancelled",
            anon.get(f"/stay/{token2}").status_code == 404,
            detail="a page still telling four people to drive to the Ariège "
                   "for a stay that is off is worse than no page")

    s.section("A made-up token is a 404, not a hint")
    s.check("nonsense gets nothing",
            anon.get("/stay/not-a-real-token").status_code == 404)

    s.section("And search engines are told to stay away")
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?",
                 (booking_id,))
    conn.commit()
    again = anon.get(f"/stay/{token2}").get_data(as_text=True)
    s.check("the page is noindex", "noindex" in again,
            detail="a URL that leaks into a referrer or a forwarded email can "
                   "be indexed without ever being crawled, which robots.txt "
                   "does nothing about")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
