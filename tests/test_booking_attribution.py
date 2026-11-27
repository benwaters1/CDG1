"""Which letter, post or site brought in a booking, and what it was worth.

What there was: the visitors page counted bookings against where a visit came
from, but only as a number -- "Instagram: 3" -- because the page views keep no
link to any booking. A request later declined counted the same as a wedding,
and nothing said what any of it was worth. And the house's own newsletters and
Facebook posts carried their links bare, so a guest who came from one arrived
as "Gmail" or "Direct": the letter that brought them was never named at all.

What this holds:

  - A booking read on its confirmation, in a visit that began elsewhere on the
    site that day, keeps that visit's source, medium and campaign -- three
    words, and nothing else of the visit. First wins: a reload, or a visit a
    week later from another link, changes nothing.
  - A confirmation that WAS the visit's first page is booked here from
    somewhere not known, never "Direct" or Stripe's card page.
  - Not written: a booking more than a day old, a browser asking not to be
    tracked, the house's own people. And none of it lands in the changes log.
  - The figures: worth is the confirmed price only, the source lines add up
    to the total, "Not known" is its own line and last.
  - The pages: the visitors page and the one-by-one list with its chips and
    spreadsheet, owner only.
  - Links to the public site in a campaign letter and a Facebook caption are
    tagged with where they were sent from; a guest's private link, a staff
    page, a file, another site and an already-tagged link are left alone.
  - The privacy notice says the booking keeps the three words.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, db, visible_text, clients
import _harness

m = _harness.m
TAG = "ZZBA"
DAY = "2099-07-15"
AGENT = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 {TAG}"


def _cleanup():
    conn = db()
    try:
        conn.execute("DELETE FROM page_views WHERE day = ?", (DAY,))
        conn.execute("DELETE FROM app_settings WHERE key = ?", (f"analytics_salt:{DAY}",))
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM campaign_sends WHERE recipient_email LIKE ?",
                     (TAG.lower() + "%",))
        conn.commit()
    finally:
        conn.close()


def run():
    s = Suite("Which campaign brought in a booking")
    _cleanup()
    real_day, real_send = m.house_today_iso, m.send_email
    m.house_today_iso = lambda: DAY            # the page views land on a day of their own
    try:
        _run(s)
    finally:
        m.house_today_iso, m.send_email = real_day, real_send
        _cleanup()
    return s


def _stay(conn, room, suffix, *, status="confirmed", price=400.0, age_days=0):
    made = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    key = f"{TAG.lower()}key{suffix.lower()}xx"
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, '2099-08-01', '2099-08-03', 2, ?, ?, ?)""",
        (room["id"], f"{TAG}{suffix}", key, f"{TAG} Guest {suffix}",
         f"{TAG.lower()}{suffix.lower()}@example.invalid", status, price, made))
    return key


def _row(table, reference):
    conn = db()
    try:
        return dict(conn.execute(f"SELECT * FROM {table} WHERE reference_code = ?",
                                 (reference,)).fetchone())
    finally:
        conn.close()


def _run(s):
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    conn = db()
    k_insta = _stay(conn, room, "INSTA", price=640.0)
    k_cold = _stay(conn, room, "COLD", price=300.0)
    k_old = _stay(conn, room, "OLD", age_days=3)
    k_dnt = _stay(conn, room, "DNT")
    k_owner = _stay(conn, room, "OWNER")
    k_declined = _stay(conn, room, "DECL", status="declined", price=900.0)
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
           party_size, dinner_date, status, total_price, created_at)
           VALUES (?, ?, ?, ?, 2, '2099-08-02', 'confirmed', 120, ?)""",
        (f"{TAG}TABLE", f"{TAG.lower()}tablekeyxx", f"{TAG} Diner",
         f"{TAG.lower()}table@example.invalid", _harness.datetime_now()))
    before_log = conn.execute("SELECT COALESCE(MAX(id), 0) AS n FROM change_log").fetchone()["n"]
    conn.commit()
    conn.close()

    def visit(client, path, address, referrer=None, **headers):
        head = {"User-Agent": AGENT, "Accept-Language": "en-GB,en;q=0.9"}
        if referrer:
            head["Referer"] = referrer
        head.update(headers)
        return client.get(path, headers=head, environ_base={"REMOTE_ADDR": address})

    s.section("A booking keeps where its visit came from")
    a = m.app.test_client()
    visit(a, "/?utm_source=instagram&utm_medium=social&utm_campaign=spring-rooms", "203.0.113.11")
    visit(a, "/privacy", "203.0.113.11", referrer="http://localhost/")
    r = visit(a, f"/book/confirmation/{k_insta}", "203.0.113.11",
              referrer="https://checkout.stripe.com/")
    row = _row("bookings", f"{TAG}INSTA")
    s.check("the confirmation read in that visit gives the stay its source, medium and campaign",
            r.status_code == 200 and (row["visit_source"], row["visit_medium"],
                                      row["visit_campaign"]) == ("instagram", "social",
                                                                 "spring-rooms"),
            r, detail=str((row["visit_source"], row["visit_medium"], row["visit_campaign"])))
    b = m.app.test_client()
    visit(b, "/", "203.0.113.12", referrer="https://www.google.fr/")
    visit(b, f"/book/confirmation/{k_insta}", "203.0.113.12")
    row = _row("bookings", f"{TAG}INSTA")
    s.check("and the first one wins: read again in a visit from somewhere else, it is unchanged",
            row["visit_source"] == "instagram", detail=str(row["visit_source"]))
    visit(m.app.test_client(), "/", "203.0.113.13", referrer="https://www.google.fr/")
    visit(m.app.test_client(), f"/restaurant/confirmation/{TAG.lower()}tablekeyxx", "203.0.113.13")
    table = _row("restaurant_bookings", f"{TAG}TABLE")
    s.check("a table booked in a visit from Google is a table from Google",
            (table["visit_source"], table["visit_medium"]) == ("Google", None),
            detail=str((table["visit_source"], table["visit_medium"])))

    s.section("When the house cannot know, it says so")
    visit(m.app.test_client(), f"/book/confirmation/{k_cold}", "203.0.113.14",
          referrer="https://checkout.stripe.com/")
    cold = _row("bookings", f"{TAG}COLD")
    s.check("a confirmation that was the visit's first page is booked here, from somewhere not "
            "known -- not Stripe's card page, and not Direct",
            cold["visit_source"] == "" and cold["visit_medium"] is None,
            detail=repr(cold["visit_source"]))
    c = m.app.test_client()
    visit(c, "/?utm_source=newsletter&utm_campaign=late", "203.0.113.15")
    visit(c, f"/book/confirmation/{k_old}", "203.0.113.15")
    s.check("a stay booked three days ago is not credited to a visit today",
            _row("bookings", f"{TAG}OLD")["visit_source"] is None)
    d = m.app.test_client()
    visit(d, "/?utm_source=instagram", "203.0.113.16", DNT="1")
    visit(d, f"/book/confirmation/{k_dnt}", "203.0.113.16", DNT="1")
    s.check("a browser that asks not to be tracked leaves nothing on its booking",
            _row("bookings", f"{TAG}DNT")["visit_source"] is None)
    oc.get("/?utm_source=instagram", headers={"User-Agent": AGENT},
           environ_base={"REMOTE_ADDR": "203.0.113.17"})
    oc.get(f"/book/confirmation/{k_owner}", headers={"User-Agent": AGENT},
           environ_base={"REMOTE_ADDR": "203.0.113.17"})
    s.check("nor does the house's own person opening a guest's confirmation",
            _row("bookings", f"{TAG}OWNER")["visit_source"] is None)
    e = m.app.test_client()
    visit(e, "/?utm_source=instagram&utm_medium=social&utm_campaign=spring-rooms", "203.0.113.18")
    visit(e, f"/book/confirmation/{k_declined}", "203.0.113.18")
    conn = db()
    logged = conn.execute(
        """SELECT COUNT(*) AS n FROM change_log WHERE id > ? AND table_name IN
           ('bookings', 'restaurant_bookings') AND (new_values LIKE '%visit_source%'
           OR old_values LIKE '%visit_source%')""", (before_log,)).fetchone()["n"]
    views = [dict(v) for v in conn.execute("SELECT * FROM page_views WHERE day = ?", (DAY,))]
    conn.close()
    s.check("none of it is written into the changes log as though somebody changed a booking",
            logged == 0, detail=f"{logged} entries")
    s.check("and the page views stay unjoined: no booking's reference, key or name among them",
            views and not any(TAG in str(v) or "key" in str(v.get("path")) for v in views),
            detail=str([v["path"] for v in views][:12]))

    s.section("What it was worth")
    today = m.house_today()
    conn = db()
    p = m.resolve_period("day", today.isoformat())
    rows = [b for b in m.website_bookings(conn, p["start_at"], p["end_at"])
            if (b["reference"] or "").startswith(TAG)]
    conn.close()
    totals = m.website_booking_totals(rows)
    by = dict(totals["sources"])
    s.check("the stays and the table booked on the site today are listed, the ones nobody "
            "can say anything about are not",
            sorted(b["reference"] for b in rows) == sorted(
                [f"{TAG}INSTA", f"{TAG}COLD", f"{TAG}DECL", f"{TAG}TABLE"]),
            detail=str([b["reference"] for b in rows]))
    s.check("worth is the confirmed price: the declined request is a booking and worth nothing",
            by.get("instagram") == {"bookings": 2, "confirmed": 1, "worth": 640.0}
            and by.get("Google", {}).get("worth") == 120.0,
            detail=str(by))
    s.check("the source lines add up to the total, Not known included and last",
            totals["sources"][-1][0] == "Not known"
            and sum(t["bookings"] for _n, t in totals["sources"]) == totals["total"]["bookings"] == 4
            and round(sum(t["worth"] for _n, t in totals["sources"]), 2)
            == totals["total"]["worth"] == 1060.0,
            detail=str(totals))
    s.check("and the campaign line names the letter that did it",
            dict(totals["campaigns"]).get("instagram / social / spring-rooms", {}).get("worth")
            == 640.0, detail=str(totals["campaigns"]))

    s.section("The pages")
    q = f"period=day&date={today.isoformat()}"
    r = oc.get(f"/admin/analytics?{q}")
    text = visible_text(r.get_data(as_text=True))
    s.check("the visitors page says what the site's bookings were worth, by source and campaign",
            r.status_code == 200 and "What the site's bookings were worth" in text
            and "spring-rooms" in text and "Not known" in text, r)
    r = oc.get(f"/admin/analytics/bookings?{q}&source=instagram")
    text = visible_text(r.get_data(as_text=True))
    s.check("the one-by-one list opens on a source's chip with only its bookings",
            r.status_code == 200 and f"{TAG}INSTA" in text and f"{TAG}DECL" in text
            and f"{TAG}COLD" not in text, r)
    r = oc.get(f"/admin/analytics/bookings.csv?{q}&q={TAG}")
    body = r.get_data(as_text=True)
    s.check("and comes away as a spreadsheet with the same rows",
            r.status_code == 200 and body.startswith("booked_on,kind,reference")
            and f"{TAG}INSTA" in body and "spring-rooms" in body and f"{TAG}OLD" not in body, r)
    r = ec.get(f"/admin/analytics/bookings?{q}")
    s.check("an employee sees none of it", r.status_code in (302, 403), r)

    s.section("The house's own links say where they were sent from")
    tag = m.tag_house_link
    s.check("a link to the public site gains its source, medium and campaign, and keeps the "
            "full stop after it",
            tag("https://www.chateaugudanes.com/book?room=3.", "newsletter", "email", "noel")
            == "https://www.chateaugudanes.com/book?room=3&utm_source=newsletter"
               "&utm_medium=email&utm_campaign=noel.")
    left = ["https://www.chateaugudanes.com/?utm_source=press",
            "https://admin.chateaugudanes.com/admin/bookings",
            "https://www.chateaugudanes.com/book/manage/Zq3xT9wLmN4pR7vK2sY8bH5cJ1dF6gA0",
            "https://www.chateaugudanes.com/static/menu.pdf",
            "https://www.chateaugudanes.com/photo/large/hall.jpg",
            "https://www.ariege.com/"]
    s.check("a tagged link, a staff page, a guest's private link, a file and another site are "
            "left exactly as written",
            [tag(u, "newsletter", "email", "noel") for u in left] == left,
            detail=str([tag(u, "newsletter", "email", "noel") for u in left]))
    s.check("a campaign's name goes into a link the same every time",
            m.campaign_tag("Noël at Gudanes!") == "noel-at-gudanes" and m.campaign_tag("") is None)

    sent = []
    m.send_email = lambda to, subject, body, *a, **kw: sent.append(body) or True
    conn = db()
    letter = {"template_id": None, "template_name": "Spring Rooms", "subject": "Spring",
              "body": "Come and see us: https://www.chateaugudanes.com/book\n"
                      "Or read https://www.ariege.com/ first.",
              "area": None, "extra_context": None, "user_id": None, "send_id": None,
              "dedupe_key": None}
    outcome, _why = m.campaign_letter(conn, letter, f"{TAG.lower()}letter@example.invalid",
                                      "Reader", held_back=frozenset(), as_test=True)
    conn.commit()
    conn.close()
    body = sent[0] if sent else ""
    s.check("a campaign letter's link to the site says which letter it was",
            "https://www.chateaugudanes.com/book?utm_source=newsletter&utm_medium=email"
            "&utm_campaign=spring-rooms" in body and "https://www.ariege.com/ first" in body,
            detail=f"{outcome}: {body[:300]}")

    asked = []
    saved = {n: getattr(m, n) for n in ("meta_configured", "meta_token_state", "meta_setting",
                                        "photo_rendition", "ensure_publish_token",
                                        "meta_request", "close_social_task")}
    m.meta_configured = lambda conn: True
    m.meta_token_state = lambda conn: {"kind": "ok", "days_left": 40, "problem": None}
    m.meta_setting = lambda conn, key: {"meta_access_token": "t", "meta_ig_user_id": "ig",
                                        "meta_page_id": "page"}.get(key)
    m.photo_rendition = lambda name, size: "/tmp/x.jpg"
    m.ensure_publish_token = lambda conn, post_id: "PhotoKey1234567890ABCdef"
    m.meta_request = lambda path, data: asked.append((path, dict(data))) or (True, {"id": "1"})
    m.close_social_task = lambda conn, post_id: None
    post = {"id": 987654, "status": "drafted", "approved_at": "2099-01-01",
            "image_filename": "x.jpg", "platform": "Instagram, Facebook",
            "caption": "Rooms for spring: https://www.chateaugudanes.com/book"}
    conn = db()
    try:
        with m.app.test_request_context("/"):
            m.publish_social_post(conn, post)
    finally:
        conn.rollback()
        conn.close()
        for n, f in saved.items():
            setattr(m, n, f)
    captions = {path.split("/")[-1]: data.get("caption") for path, data in asked}
    s.check("a Facebook caption's link says which post it was; Instagram's, which cannot be "
            "clicked, is left as written",
            captions.get("photos") == "Rooms for spring: https://www.chateaugudanes.com/book"
            "?utm_source=facebook&utm_medium=social&utm_campaign=post-987654"
            and captions.get("media") == post["caption"],
            detail=str(captions))

    s.section("The notice says so")
    text = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    text = text.replace("\u2019", "'")          # the page sets its apostrophes curly
    s.check("the privacy notice says a booking keeps where its visit came from, and nothing else "
            "of the visit",
            "the booking itself keeps three words about where that visit came from" in text
            and "not the pages you read nor the visit's code" in text
            and "a browser that asks not to be tracked leaves nothing on its booking" in text)
