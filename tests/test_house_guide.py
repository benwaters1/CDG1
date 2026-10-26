"""The house, for a guest who is staying: Wi-Fi, breakfast, who to call.

The owner asked on 22 October 2026 for "a general house thing to start with
(wifi code, breakfast time etc)", to grow into a plan of the house with the
accessible areas marked. Stage one is sections he writes on /admin/house-guide
and a guest reads on their own stay's link. Five things carry this file.

  NOTHING IS THE APP'S WORDING. The page starts empty, a section with nothing
  written in it is not shown, and no line of house fact is seeded. The guest
  texts shipped with wording nobody at the house wrote and were switched off
  for it the same day; a wrong breakfast hour is worse than none, because a
  guest plans round it.

  WHAT THE APP KNOWS IS READ, NOT COPIED. Arrival and departure come from
  CHECK_IN_TIME / CHECK_OUT_TIME and the dinner hour from the restaurant's
  settings, so changing either moves this page with it. Proved by changing
  them, not by reading the figure off the page once.

  THE WI-FI CODE HAS A WINDOW. From the owner's number of days before arrival
  until checkout, on a confirmed stay only; never on a cancelled one, a past
  one, or after they are checked out -- and never in an address, an audit line
  or the page-translation table, which sends what it collects to a model.

  IT IS THEIRS. A live booking's manage link or share link and nothing else,
  noindex, and not kept in a browser's cache.

  A HANDOVER CANNOT LOSE THE WAY IN. manage_booking.html and shared_stay.html
  arrive from the design side whole; the card is one include line in each, and
  this suite is what notices if a new version drops it.
"""
import io
import os
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZHG"
CODE = "zzhg-Pyrenees-4471"
NETWORK = "ZZHG-Chateau-Guests"


def _cleanup(conn):
    conn.execute("DELETE FROM house_guide_sections")
    conn.execute("DELETE FROM page_translations WHERE source_text LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()


def _stay(conn, room_id, key, arrival, departure, status="confirmed", share=False,
          checked_out=False):
    token = f"tok{TAG}{key}".lower()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, share_token,
           guest_name, guest_email, guest_phone, arrival_date, departure_date,
           party_size, status, payment_status, total_price, amount_paid, created_at,
           checked_out_at)
           VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 2, ?, 'paid', 600, 600, ?, ?)""",
        (room_id, f"{TAG}-{key}", token, f"share{TAG}{key}".lower() if share else None,
         f"{TAG} Guest {key}", f"zzhg.{key}@example.invalid".lower(),
         arrival.isoformat(), departure.isoformat(), status,
         datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat() if checked_out else None))
    return token


def _section(conn, kind="text", sort=10, shown=1, **cols):
    keys = ["kind", "sort_order", "shown", "created_at"] + list(cols)
    vals = [kind, sort, shown, datetime.now(timezone.utc).isoformat()] + list(cols.values())
    cur = conn.execute(
        f"INSERT INTO house_guide_sections ({', '.join(keys)}) "
        f"VALUES ({', '.join('?' * len(keys))})", vals)
    conn.commit()
    return cur.lastrowid


def _attr_values(html):
    """Every value of every attribute that can carry an address."""
    return re.findall(r'\b(?:href|src|action|srcset|content|data-[\w-]+)\s*=\s*"([^"]*)"', html)


def run():
    s = Suite("The house guide")
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    today = _harness.house_today()
    conn = db()
    put_back_sections = _harness.keep_rows(conn, "house_guide_sections", "1 = 1")
    put_back_days = _harness.keep_rows(conn, "app_settings", "key = ?",
                                       (m.HOUSE_GUIDE_WIFI_DAYS_SETTING,))
    put_back_restaurant = _harness.keep_rows(conn, "restaurant_settings", "id = 1")
    put_back_dining = _harness.keep_rows(conn, "app_settings", "key = 'dining_mode'")
    saved_times = (m.CHECK_IN_TIME, m.CHECK_OUT_TIME)
    try:
        _cleanup(conn)
        _run(s, oc, ec, conn, room, today)
    finally:
        m.CHECK_IN_TIME, m.CHECK_OUT_TIME = saved_times
        _cleanup(conn)
        put_back_sections()
        put_back_days()
        put_back_restaurant()
        put_back_dining()
        conn.close()
    return s


def _run(s, oc, ec, conn, room, today):
    d = timedelta
    soon = _stay(conn, room["id"], "soon", today + d(days=1), today + d(days=3), share=True)
    later = _stay(conn, room["id"], "later", today + d(days=10), today + d(days=12))
    cancelled = _stay(conn, room["id"], "cancelled", today + d(days=1), today + d(days=3),
                      status="cancelled")
    pending = _stay(conn, room["id"], "pending", today + d(days=1), today + d(days=3),
                    status="pending")
    past = _stay(conn, room["id"], "past", today - d(days=4), today - d(days=1))
    gone = _stay(conn, room["id"], "gone", today - d(days=1), today + d(days=1),
                 checked_out=True)
    conn.commit()
    share = f"share{TAG}soon".lower()
    pub = m.app.test_client()

    # Always with a language: ?lang= is remembered in the session, so a page
    # asked for in French would otherwise make every later one French too.
    def page(token, lang="en", client=pub):
        return client.get(f"/book/manage/{token}/house?lang={lang}")

    def booking_page(token):
        return pub.get(f"/book/manage/{token}?lang=en").get_data(as_text=True)

    # ------------------------------------------------------------------ empty
    s.section("It starts empty, and the app writes none of it")
    src = io.open(os.path.join(_harness.ROOT, "app.py"), encoding="utf-8").read()
    inserts = re.findall(r"INSERT INTO house_guide_sections \(([^)]*)\)", src)
    s.check("one place in the app adds a section", len(inserts) == 1,
            detail=f"{len(inserts)} -- anything else that inserts is the app writing "
                   "house facts of its own")
    s.check("and it adds an empty one: no title, no text, no Wi-Fi",
            inserts and not re.search(r"title|body|wifi", inserts[0]),
            detail=str(inserts[:1]))
    r = page(soon)
    body = r.get_data(as_text=True)
    s.check("with nothing written the page still opens for a live stay",
            r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and says so rather than inventing something",
            "Nothing has been written here yet." in body)
    manage = booking_page(soon)
    s.check("and the booking page has no card for it yet",
            f"/book/manage/{soon}/house" not in manage,
            detail="a card leading to an empty page")

    # ------------------------------------------------- empty sections hidden
    s.section("A section with nothing written in it is not shown")
    r = oc.post("/admin/house-guide/add", data={"kind": "text"})
    s.check("the owner can add a section", r.status_code == 302, response=r,
            detail=f"HTTP {r.status_code}")
    sid = conn.execute("SELECT MAX(id) FROM house_guide_sections").fetchone()[0]
    s.check("it is added empty", sid and not conn.execute(
        "SELECT COALESCE(title_en, '') || COALESCE(body_en, '') FROM house_guide_sections "
        "WHERE id = ?", (sid,)).fetchone()[0])
    r = oc.post(f"/admin/house-guide/{sid}/save",
                data={"title_en": f"{TAG} Breakfast", "shown": "1"})
    s.check("a title can be saved on its own", r.status_code == 302, response=r)
    body = page(soon).get_data(as_text=True)
    s.check("a title over no text is not shown", f"{TAG} Breakfast" not in body,
            detail="a heading over nothing")
    s.check("and the page is still empty", "Nothing has been written here yet." in body)
    manage = booking_page(soon)
    s.check("so still no card on the booking page", f"/book/manage/{soon}/house" not in manage)

    words_en = f"{TAG} breakfast is laid in the kitchen from eight"
    words_fr = f"{TAG} le petit-déjeuner est servi dans la cuisine"
    oc.post(f"/admin/house-guide/{sid}/save",
            data={"title_en": f"{TAG} Breakfast", "title_fr": f"{TAG} Petit-déjeuner",
                  "body_en": words_en, "body_fr": words_fr, "shown": "1"})
    body = page(soon).get_data(as_text=True)
    s.check("once there is text, it is shown", words_en in body and f"{TAG} Breakfast" in body)
    manage = booking_page(soon)
    s.check("and the booking page leads to it", f"/book/manage/{soon}/house" in manage,
            detail="the card is missing")
    s.check("naming what is inside in the owner's words", f"{TAG} Breakfast" in manage)
    oc.post(f"/admin/house-guide/{sid}/save",
            data={"title_en": f"{TAG} Breakfast", "body_en": words_en, "body_fr": words_fr})
    s.check("switched off, it is not shown", words_en not in page(soon).get_data(as_text=True))
    oc.post(f"/admin/house-guide/{sid}/save",
            data={"title_en": f"{TAG} Breakfast", "title_fr": f"{TAG} Petit-déjeuner",
                  "body_en": words_en, "body_fr": words_fr, "shown": "1"})

    # ------------------------------------------------------------ languages
    s.section("Each language is the owner's own")
    fr = page(soon, "fr").get_data(as_text=True)
    s.check("a French reader gets his French", words_fr in fr and words_en not in fr)
    es = page(soon, "es").get_data(as_text=True)
    s.check("a Spanish reader, with no Spanish written, gets his English",
            words_en in es, detail="and never a translation nobody wrote")
    s.check("marked as English, so it is read in the right voice",
            re.search(r'lang="en"[^>]*>\s*' + re.escape(words_en), es))
    s.check("the labels the app adds are French on a French page",
            "La Maison" in fr, detail="translations.py has the page's labels")
    filed = conn.execute(
        "SELECT COUNT(*) FROM page_translations WHERE source_text LIKE ?",
        (f"%{TAG}%",)).fetchone()[0]
    s.check("and nothing he wrote is filed for the machine translator", filed == 0,
            detail=f"{filed} rows -- that table is sent to a model and kept")

    # --------------------------------------------------------- the Wi-Fi code
    s.section("The Wi-Fi code shows only from a few days before arrival until checkout")
    wid = _section(conn, "wifi", sort=20, title_en=f"{TAG} Wi-Fi",
                   wifi_network=NETWORK, wifi_code=CODE)
    r = oc.post("/admin/house-guide/settings", data={"wifi_days_before": "3"})
    s.check("the owner sets how many days", r.status_code == 302 and
            m.house_wifi_days_before(conn) == 3, response=r)
    r = oc.post("/admin/house-guide/settings", data={"wifi_days_before": "99"})
    s.check("and an impossible number is refused, not stored",
            m.house_wifi_days_before(conn) == 3)

    r = page(soon)
    body = r.get_data(as_text=True)
    s.check("arriving tomorrow, they see the code", CODE in body, detail="code missing")
    s.check("and the network", NETWORK in body)
    s.check("never in an address on the page",
            not any(CODE in v for v in _attr_values(body)),
            detail="an href, src or action carrying the code")
    s.check("not kept in a browser's cache",
            "no-store" in (r.headers.get("Cache-Control") or ""),
            detail=r.headers.get("Cache-Control"))
    fr = page(soon, "fr").get_data(as_text=True)
    filed = conn.execute(
        "SELECT COUNT(*) FROM page_translations WHERE source_text LIKE ? OR source_text LIKE ?",
        (f"%{CODE}%", f"%{NETWORK}%")).fetchone()[0]
    s.check("a French page does not file the code for translation", filed == 0,
            detail=f"{filed} rows")

    body = page(later).get_data(as_text=True)
    s.check("ten days out, the code is not on the page at all", CODE not in body,
            detail="anywhere in the response -- a comment or a hidden field is still there")
    s.check("but the network is, so they know there is one", NETWORK in body)
    opens = m.house_guide_date(today + timedelta(days=7), "en")
    s.check("with the day the code will show", f"from {opens}" in body,
            detail=f"expected 'from {opens}'")

    body = page(gone).get_data(as_text=True)
    s.check("once they are checked out, it is gone", CODE not in body)
    for label, token in (("a cancelled stay", cancelled), ("a stay nobody has confirmed", pending),
                         ("a stay that is over", past)):
        r = page(token)
        s.check(f"{label} gets no page at all", r.status_code == 404
                and CODE not in r.get_data(as_text=True), detail=f"HTTP {r.status_code}")

    # The edges, at the minute, through the function the page asks.
    arrive, leave = today + timedelta(days=5), today + timedelta(days=7)
    stay = {"status": "confirmed", "arrival_date": arrive.isoformat(),
            "departure_date": leave.isoformat(), "checked_out_at": None}
    opens_at = datetime.combine(arrive - timedelta(days=3), datetime.min.time(),
                                tzinfo=m.LOCAL_TZ).astimezone(timezone.utc)
    closes_at = datetime.combine(leave, datetime.strptime(m.CHECK_OUT_TIME, "%H:%M").time(),
                                 tzinfo=m.LOCAL_TZ).astimezone(timezone.utc)
    second = timedelta(seconds=1)
    w = lambda when, row=stay: m.house_wifi_window(conn, row, now=when)
    s.check("a second before midnight three days out: not yet",
            not w(opens_at - second)["show"] and w(opens_at - second)["state"] == "before")
    s.check("at midnight, house time: shown", w(opens_at)["show"])
    s.check("a second before checkout on the last day: shown", w(closes_at - second)["show"])
    s.check("at checkout: gone", not w(closes_at)["show"] and w(closes_at)["state"] == "over")
    s.check("cancelled, inside the window: never",
            not w(opens_at + timedelta(hours=1), dict(stay, status="cancelled"))["show"])
    s.check("checked out early, inside the window: never",
            not w(opens_at + timedelta(hours=1), dict(stay, checked_out_at="x"))["show"])
    oc.post("/admin/house-guide/settings", data={"wifi_days_before": "0"})
    # Midnight on the arrival day worked out on its own, not as opens_at plus
    # three days: the clocks go back on 25 October, and that night is 25 hours.
    arrival_midnight = datetime.combine(arrive, datetime.min.time(),
                                        tzinfo=m.LOCAL_TZ).astimezone(timezone.utc)
    s.check("at nought days it waits for the arrival day itself",
            not w(arrival_midnight - second)["show"] and w(arrival_midnight)["show"])
    oc.post("/admin/house-guide/settings", data={"wifi_days_before": "3"})

    s.section("The code goes nowhere it should not")
    oc.post(f"/admin/house-guide/{wid}/save",
            data={"title_en": f"{TAG} Wi-Fi", "wifi_network": NETWORK,
                  "wifi_code": CODE, "shown": "1"})
    logged = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE COALESCE(details, '') LIKE ? "
        "OR COALESCE(target, '') LIKE ?", (f"%{CODE}%", f"%{CODE}%")).fetchone()[0]
    s.check("not into the audit log when it is saved", logged == 0, detail=f"{logged} lines")
    admin = oc.get("/admin/house-guide").get_data(as_text=True)
    form = re.search(r'<form[^>]*action="[^"]*/save"[^>]*>(?:(?!</form>).)*wifi_code',
                     admin, re.S)
    s.check("and the owner's form sends it in the body, not the address",
            form and 'method="post"' in form.group(0))

    # ------------------------------------------------------ whose page it is
    s.section("Only a live stay's own link opens it")
    for path in (f"/book/manage/tok{TAG}nobody/house".lower(), f"/stay/share{TAG}nobody/house".lower()):
        r = pub.get(path)
        s.check(f"a made-up link is refused ({path.split('/')[1]})", r.status_code == 404,
                detail=f"HTTP {r.status_code}")
    r = pub.get(f"/stay/{share}/house")
    s.check("the party's share link opens it", r.status_code == 200 and CODE in r.get_data(as_text=True),
            detail=f"HTTP {r.status_code}")
    shared = pub.get(f"/stay/{share}").get_data(as_text=True)
    s.check("and the share page leads to it", f"/stay/{share}/house" in shared)
    s.check("never to the booker's own manage link", f"/book/manage/{soon}" not in shared,
            detail="the share page would hand the party the booking itself")
    conn.execute("UPDATE bookings SET share_token = NULL WHERE manage_token = ?", (soon,))
    conn.commit()
    s.check("taking the share link away takes this page with it",
            pub.get(f"/stay/{share}/house").status_code == 404)
    # As if the site were live: while it is staging every page is noindex,
    # which would let this pass with the page's own tag gone.
    was_live = m.SITE_IS_LIVE
    try:
        m.SITE_IS_LIVE = True
        body = page(soon).get_data(as_text=True)
        home = pub.get("/?lang=en").get_data(as_text=True)
    finally:
        m.SITE_IS_LIVE = was_live
    robots = re.compile(r'<meta[^>]+name="robots"[^>]+noindex')
    s.check("the page is noindex, even with the site live", bool(robots.search(body)))
    s.check("which is the page's own doing: the home page is not",
            not robots.search(home), detail="otherwise the check above proves nothing")

    # ------------------------------------------------------------- the times
    s.section("The times come from where the app keeps them")
    aid = _section(conn, "arrival", sort=5)
    body = page(soon).get_data(as_text=True)
    s.check("an arrival section with nothing written still shows the house's times",
            f"Arrival from {m.clock_words(m.CHECK_IN_TIME)}" in body
            and f"Departure by {m.clock_words(m.CHECK_OUT_TIME)}" in body)
    orig = (m.CHECK_IN_TIME, m.CHECK_OUT_TIME)
    half_ten = datetime.combine(leave, datetime.strptime("10:30", "%H:%M").time(),
                                tzinfo=m.LOCAL_TZ).astimezone(timezone.utc)
    try:
        m.CHECK_IN_TIME, m.CHECK_OUT_TIME = "16:30", "11:00"
        body = page(soon).get_data(as_text=True)
        s.check("and moves when the app's time moves", "Arrival from 4.30pm" in body
                and "Departure by 11am" in body,
                detail="a copy typed into the guide would still say the old time")
        fr = page(soon, "fr").get_data(as_text=True)
        s.check("written the French way on a French page",
                "16h30" in fr and "11h" in fr)
        inside = m.house_wifi_window(conn, stay, now=half_ten)["show"]
        m.CHECK_OUT_TIME = "10:00"
        outside = m.house_wifi_window(conn, stay, now=half_ten)["show"]
        s.check("and the code goes at the app's checkout time, whatever it is",
                inside and not outside,
                detail=f"10:30 on the last day: shown with checkout at 11:00 ({inside}), "
                       f"gone with it at 10:00 ({outside})")
    finally:
        m.CHECK_IN_TIME, m.CHECK_OUT_TIME = orig

    conn.execute("""INSERT INTO restaurant_settings (id, dinner_time, capacity, enabled)
                    VALUES (1, '20:15', 20, 1)
                    ON CONFLICT(id) DO UPDATE SET dinner_time = '20:15', enabled = 1""")
    conn.execute("DELETE FROM app_settings WHERE key = 'dining_mode'")
    conn.commit()
    _section(conn, "dinner", sort=30)
    body = page(soon).get_data(as_text=True)
    s.check("a dinner section reads the restaurant's hour", "Dinner at 8.15pm" in body)
    conn.execute("UPDATE restaurant_settings SET enabled = 0 WHERE id = 1")
    conn.commit()
    body = page(soon).get_data(as_text=True)
    s.check("and says nothing when the restaurant is not serving",
            "Dinner at 8.15pm" not in body, detail="a dinner hour for a kitchen that is shut")
    admin = oc.get("/admin/house-guide")
    text = admin.get_data(as_text=True)
    s.check("the owner's page opens", admin.status_code == 200, detail=f"HTTP {admin.status_code}")
    s.check("and says where each time comes from",
            "Set in the app itself" in text and "/admin/restaurant/settings" in text
            and "/admin/terms" in text)
    s.check("and that breakfast hours are stored nowhere else",
            "Breakfast hours are not stored anywhere in the app" in text)

    # ------------------------------------------------------ the owner's page
    s.section("The owner orders, previews and removes sections")
    def order():
        b = page(soon).get_data(as_text=True)
        marks = ("Arrival from", f"{TAG} Breakfast", f"{TAG} Wi-Fi")
        return [x for _, x in sorted((b.find(x), x) for x in marks if x in b)]
    first = order()
    s.check("sections show in his order", first == ["Arrival from", f"{TAG} Breakfast", f"{TAG} Wi-Fi"],
            detail=str(first))
    oc.post(f"/admin/house-guide/{wid}/move", data={"direction": "up"})
    moved = order()
    s.check("and Up moves one up a place", moved == ["Arrival from", f"{TAG} Wi-Fi", f"{TAG} Breakfast"],
            detail=str(moved))
    oc.post(f"/admin/house-guide/{aid}/move", data={"direction": "down"})
    moved = order()
    s.check("and Down one down", moved == [f"{TAG} Wi-Fi", "Arrival from", f"{TAG} Breakfast"],
            detail=str(moved))
    r = oc.get("/admin/house-guide/preview?lang=fr")
    text = r.get_data(as_text=True)
    s.check("his preview shows the page in the language he picks",
            r.status_code == 200 and words_fr in text, detail=f"HTTP {r.status_code}")
    s.check("with the code, as a stay inside its window would see it", CODE in text)
    r = oc.post(f"/admin/house-guide/{aid}/delete")
    s.check("a section can be removed", r.status_code == 302 and not conn.execute(
        "SELECT 1 FROM house_guide_sections WHERE id = ?", (aid,)).fetchone())
    s.check("and the page no longer has it", "Arrival from" not in page(soon).get_data(as_text=True))
    s.check("a member of staff without the area is refused the editor",
            ec.get("/admin/house-guide").status_code in (302, 403))

    # --------------------------------------------- the way in survives a handover
    s.section("The card is one include line in each page a handover replaces")
    for name in ("manage_booking.html", "shared_stay.html"):
        tpl = io.open(os.path.join(_harness.ROOT, "templates", name), encoding="utf-8").read()
        s.check(f"{name} still includes the card", '{% include "_house_card.html" %}' in tpl,
                detail="a new version of this page arrived without the line; put it back")
    guide = io.open(os.path.join(_harness.ROOT, "templates", "house_guide.html"),
                    encoding="utf-8").read()
    s.check("the page itself is not one a handover has delivered",
            "templates/house_guide.html" not in _design_surface(),
            detail="it would be overwritten by the next zip")
    s.check("and keeps the owner's words from the page translator",
            guide.count('translate="no"') >= 2)
    # The page styles itself in its own <style>, so its class names must be
    # its own. It first used .g-house, which gudanes.css already had as a
    # 64px-wide ornament: every section was poured down a 64px column, over
    # the footer, and nothing errored.
    own = set(re.findall(r"\.(g-hguide[\w-]*)", guide))
    shared = "".join(io.open(os.path.join(_harness.ROOT, "static", f), encoding="utf-8").read()
                     for f in ("gudanes.css", "style.css"))
    clash = sorted(c for c in own if re.search(r"\." + re.escape(c) + r"\b", shared))
    s.check("whose class names the shared stylesheets do not already use",
            own and not clash, detail=f"{clash} -- the page would take on their rules")

    # ------------------------------------------------- the translator itself
    s.section("The page translator leaves translate=\"no\" alone")
    html = ('<p>Words outside here</p><div translate="no"><p>Held words one</p>'
            '<div>Held words two</div><br>Held words three</div><p>Words after here</p>')
    spans = [t.strip() for _a, _b, t in m.page_text_spans(html)]
    s.check("text inside it is not offered for translation",
            not any(x.startswith("Held") for x in spans), detail=str(spans))
    s.check("a nested element of the same kind does not end it early",
            "Held words three" not in spans)
    s.check("and the page around it still is",
            "Words outside here" in spans and "Words after here" in spans, detail=str(spans))


def _design_surface():
    """What tools/export_for_design.py would hand the design side."""
    import sys
    tools = os.path.join(_harness.ROOT, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        import export_for_design
        return set(export_for_design.design_surface())
    except Exception:
        return set()


if __name__ == "__main__":
    print(run().report())
