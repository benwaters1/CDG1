"""Where staff can get to, and what a guest cannot eat, wherever it matters.

Found by reading every staff page on a seeded busy day (back-end review,
27 November 2026):

  Reach       ten pages an employee opens carried links to pages that refuse
              them: the arrivals sheet's "The booking" and "Arrival card", the
              guest list's "Stays", "Fix" on arrivals we cannot prepare for,
              the head links on lost property, cleaning and meters. The
              kitchen's own screen, the allergy sheet and the standing menu
              were in no menu; the upkeep schedule, the weather's jobs, What's
              On, Money ahead, the taxe de séjour and mileage were reachable
              only by search; and twenty pages lit the wrong menu or none.
              Seventeen pages still said "← Management".
  Allergies   the Arrivals sheet matched a guest's profile by email; the Front
              Desk, the booking page, Breakfast, the kitchen's screen and the
              allergy sheet used the booking's link alone, so "No nuts - a real
              allergy" reached only the Arrivals sheet. Breakfast counted the
              guests sleeping here TONIGHT and listed their room requests.

Every check below was broken on purpose once and seen to fail.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZB3"


def _now(delta_h=0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_h)).isoformat()


def _main(html):
    hit = re.search(r"<main\b.*?</main>", html, re.S)
    return hit.group(0) if hit else html


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (f"{TAG}%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (f"{TAG.lower()}%",))
    conn.commit()
    conn.close()


def _stay(conn, room, arrive, nights, name, email, eta=None):
    return conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, total_price,
           created_at, source, estimated_arrival_time, arrival_prepped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 600, ?, 'direct', ?, ?)""",
        (room["id"], f"{TAG}{_harness.secrets_token()[:6].upper()}", _harness.secrets_token(),
         name, email, arrive.isoformat(), (arrive + timedelta(days=nights)).isoformat(),
         _now(-24 * 9), eta, _now(-24))).lastrowid


def run():
    s = Suite("Reach, and what a guest cannot eat")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = house_today()
    room = ensure_room()
    adapter = m.app.url_map.bind("localhost")
    employee = dict(db().execute("SELECT * FROM users WHERE id = ?", (emp["id"],)).fetchone())
    try:
        conn = db()
        arriving = _stay(conn, room, today, 2, f"{TAG} Isabelle Dufresne",
                         f"{TAG.lower()}.dufresne@example.invalid", eta="18:00")
        slept = _stay(conn, room, today - timedelta(days=2), 2, f"{TAG} Charlotte Hartley",
                      f"{TAG.lower()}.hartley@example.invalid")
        # Profiles that match by email only -- no link set, as for most guests
        # whose booking and profile were made separately.
        conn.execute("INSERT INTO guests (name, email, dietary_notes, vip, created_at) "
                     "VALUES (?, ?, 'No nuts - a real allergy', 1, ?)",
                     (f"{TAG} Isabelle Dufresne", f"{TAG.lower()}.dufresne@example.invalid", _now()))
        conn.execute("INSERT INTO guests (name, email, dietary_notes, created_at) "
                     "VALUES (?, ?, 'Gluten free', ?)",
                     (f"{TAG} Charlotte Hartley", f"{TAG.lower()}.hartley@example.invalid", _now()))
        conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
               guest_email, party_size, dinner_date, status, created_at, booking_id, dietary_notes)
               VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?, ?, 'Shellfish too')""",
            (f"{TAG}R", _harness.secrets_token(), f"{TAG} Isabelle Dufresne",
             f"{TAG.lower()}.dufresne@example.invalid", m.service_day().isoformat(), _now(),
             arriving))
        conn.commit()
        conn.close()

        s.section("What a guest cannot eat, wherever it matters")
        flags = m.stay_flags(db(), [arriving]).get(arriving, {})
        s.check("a profile matched by email gives what they cannot eat",
                flags.get("dietary") == "No nuts - a real allergy", detail=str(flags))
        s.check("and that they are a VIP", flags.get("vip") is True)
        desk = visible_text(_main(oc.get("/admin/desk").get_data(as_text=True)))
        s.check("the front desk says it beside the guest being checked in",
                "Cannot eat: No nuts - a real allergy" in desk,
                detail=desk[desk.find("Isabelle"):][:160])
        page = visible_text(_main(oc.get(f"/admin/bookings/{arriving}").get_data(as_text=True)))
        s.check("so does the booking's own page", "Cannot eat No nuts - a real allergy" in page,
                detail=page[:300])
        chef = visible_text(_main(ec.get("/chef").get_data(as_text=True)))
        s.check("the kitchen's screen has the allergy and what was written on the table",
                "No nuts - a real allergy" in chef and "Shellfish too" in chef,
                detail=chef[chef.find("Isabelle"):][:160])
        sheet = visible_text(_main(ec.get("/kitchen/dietary").get_data(as_text=True)))
        s.check("the allergy sheet has it for the guest staying",
                "No nuts - a real allergy" in sheet, detail=sheet[:300])
        breakfast = visible_text(_main(ec.get("/breakfast").get_data(as_text=True)))
        s.check("breakfast lists who slept here and cannot eat what",
                "Cannot eat: Gluten free" in breakfast, detail=breakfast[:400])
        s.check("and not the guest who arrives this evening",
                f"{TAG} Isabelle Dufresne" not in breakfast)

        s.section("Links an employee follows open for them")
        bad = []
        for path in ("/admin/arrivals", "/admin/arrivals-incomplete", "/admin/linen",
                     "/admin/reply-times", "/admin/turnarounds", "/guests",
                     "/management/cleaning", "/management/lost-property", "/management/meters",
                     f"/guests"):
            html = _main(ec.get(path).get_data(as_text=True))
            for href in set(re.findall(r'href="(/[^"#?]*)', html)):
                try:
                    ep, _args = adapter.match(href, method="GET")
                except Exception:
                    continue
                if m.view_is_guarded(ep) and not m.can_reach(employee, ep):
                    bad.append(f"{path} -> {href}")
        s.check("no link on a page staff open leads to a page that refuses them", not bad,
                detail="; ".join(sorted(set(bad))[:8]))

        s.section("Pages that were in no menu")
        with m.app.test_request_context("/"):
            want_owner = {ep: m.url_for(ep) for ep in (
                "chef_screen", "kitchen_dietary", "admin_restaurant_menu", "maintenance_page",
                "weather_jobs_page", "admin_whats_on", "money_ahead_page", "admin_city_tax",
                "mileage", "management_social", "roll_call")}
            want_staff = {ep: m.url_for(ep) for ep in ("chef_screen", "kitchen_dietary")}
        for who, client, want in (("the owner", oc, want_owner), ("an employee", ec, want_staff)):
            nav = re.search(r'<aside class="sidebar".*?</aside>',
                            client.get("/today").get_data(as_text=True), re.S).group(0)
            missing = [ep for ep, url in want.items() if f'href="{url}"' not in nav]
            s.check(f"each is in the sidebar for {who}", not missing, detail=str(missing))
        s.check("Extras & Menu is called what it opens, Booking Extras",
                "Booking Extras" in oc.get("/today").get_data(as_text=True))

        s.section("A page lights the menu line it belongs under")
        for ep, line in (("channel_stay_page", "channel_stays_page"),
                         ("management_cancellations", "admin_bookings"),
                         ("still_out_page", "admin_access"),
                         ("exit_interviews", "admin_hr"), ("photo_tray", "camera_roll"),
                         ("admin_terms", "management")):
            owner_menu = m.MENU_AREA_OF.get(ep)
            s.check(f"{ep} opens the menu that lists {line}",
                    owner_menu is not None and owner_menu == m.MENU_AREA_OF.get(line),
                    detail=f"{owner_menu} vs {m.MENU_AREA_OF.get(line)}")

        s.section("No page says Back to Management")
        import glob
        import os
        stale = []
        for path in glob.glob(os.path.join(_harness.ROOT, "templates", "*.html")):
            text = open(path, encoding="utf-8").read()
            if "← Management" in text or "Back to Management" in text:
                stale.append(os.path.basename(path))
        s.check("the old back links are gone or say All settings", not stale, detail=str(stale))
    finally:
        _cleanup()
    return s
