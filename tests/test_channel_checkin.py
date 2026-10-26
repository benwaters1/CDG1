"""Getting what Booking.com does not pass on (owner, 22 October 2026: "yes to
1 and 2").

  1. FROM THE EXTRANET. A Booking.com stay has its own page, where the guest's
     details are typed in, or the extranet's reservation page pasted and read:
     the @guest.booking.com address, telephone, country, arrival time,
     requests. With an address the stay joins the guest's profile.
  2. FROM THE GUEST. Each stay has an online check-in link, sent by itself
     once there is an address to send it to (and by hand from the stay), or
     pasted into Booking.com's messages. The guest gives how to reach them,
     their home address, when they arrive, what they cannot eat, and -- not
     being French -- the police fiche, which is made from it.

And what the privacy notice promises: what they cannot eat goes once they
have left.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZOTACI"

EXTRANET_EN = """Property contact: frontdesk@chateaugudanes.example
Guest name
Hana Novak
Email: hnovak.123456@guest.booking.com
Phone: +420 601 234 567
Country/region: Czech Republic
Estimated arrival time between 16:00 and 17:00
Special requests
We would like a quiet room, it is our anniversary.
"""
EXTRANET_FR = """Nom du client : Jean Dupuis
jdupuis.778899@guest.booking.com
Téléphone : +33 6 11 22 33 44
Pays : France
Heure d'arrivée estimée : 18h00
"""


def _cleanup(conn):
    ids = "(SELECT id FROM ota_reservations WHERE reservation_number LIKE '6600%')"
    conn.execute(f"DELETE FROM channel_police_register WHERE ota_reservation_id IN {ids}")
    conn.execute(f"DELETE FROM ota_mail WHERE ota_reservation_id IN {ids}")
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE '6600%'")
    conn.execute("DELETE FROM mail_log WHERE to_address LIKE '%@guest.booking.com' AND to_address LIKE 'zz%'")
    conn.execute("DELETE FROM guests WHERE email LIKE 'zz%@guest.booking.com' OR email LIKE 'zzotaci.%'")
    conn.commit()


def _stay(conn, number, arrive, nights=2, name=None):
    now = datetime.now(timezone.utc).isoformat()
    room = conn.execute("SELECT id, name FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    cur = conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name, arrival_date,
               departure_date, room_label, room_id, guests, amount, currency, first_seen_at, updated_at)
           VALUES ('booking.com', ?, 'confirmed', ?, ?, ?, ?, ?, 2, 320, 'EUR', ?, ?)""",
        (number, name or f"{TAG} Guest", arrive.isoformat(), (arrive + timedelta(days=nights)).isoformat(),
         room["name"], room["id"], now, now))
    conn.commit()
    return cur.lastrowid


def _letters(conn, to):
    return [r["template_key"] for r in conn.execute(
        "SELECT template_key FROM mail_log WHERE to_address = ? ORDER BY id", (to,))]


def run():
    s = Suite("What Booking.com does not pass on")
    conn = db()
    oc, ec, _o, _e = clients()
    _cleanup(conn)
    try:
        _checks(s, conn, oc, ec)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn, oc, ec):
    today = m.house_today()

    s.section("Reading the extranet")
    en = m.parse_extranet_text(EXTRANET_EN)
    s.check("its @guest.booking.com address", en.get("guest_email") == "hnovak.123456@guest.booking.com", detail=str(en))
    s.check("the telephone", en.get("guest_phone") == "+420 601 234 567", detail=str(en))
    s.check("the country, as the house's forms spell it", en.get("guest_country") == "Czechia", detail=str(en))
    s.check("when they arrive", en.get("estimated_arrival_time") == "16:00 – 17:00", detail=str(en))
    s.check("the name and the request", en.get("guest_name") == "Hana Novak"
            and "anniversary" in (en.get("special_requests") or ""), detail=str(en))
    fr = m.parse_extranet_text(EXTRANET_FR)
    s.check("and the page in French", fr.get("guest_email") == "jdupuis.778899@guest.booking.com"
            and fr.get("guest_phone") == "+33 6 11 22 33 44" and fr.get("guest_country") == "France"
            and fr.get("estimated_arrival_time") == "18:00", detail=str(fr))
    s.check("nothing found is nothing written", m.parse_extranet_text("hello") == {})

    s.section("The stay's own page")
    sid = _stay(conn, "660011", today + timedelta(days=10))
    page = oc.get(f"/admin/channel-stays/{sid}")
    s.check("it opens for the owner", page.status_code == 200
            and "Online check-in" in page.get_data(as_text=True))
    s.check("and not for staff without the owner's rights",
            ec.get(f"/admin/channel-stays/{sid}").status_code in (302, 403))
    oc.post(f"/admin/channel-stays/{sid}/details", data={"paste": EXTRANET_EN})
    row = conn.execute("SELECT * FROM ota_reservations WHERE id = ?", (sid,)).fetchone()
    s.check("a paste fills the stay", row["guest_email"] == "hnovak.123456@guest.booking.com"
            and row["guest_phone"] == "+420 601 234 567" and row["guest_country"] == "Czechia")
    s.check("and joins it to a guest profile", bool(row["linked_guest_id"]))
    r = oc.post(f"/admin/channel-stays/{sid}/details", data={"guest_email": "not an address"},
                follow_redirects=True)
    s.check("a typed address that is not one is refused", "does not look right" in " ".join(flashes(r)))
    oc.post(f"/admin/channel-stays/{sid}/details", data={"guest_phone": "+420 700 000 000"})
    s.check("a typed field is saved", conn.execute("SELECT guest_phone FROM ota_reservations WHERE id = ?",
            (sid,)).fetchone()[0] == "+420 700 000 000")

    s.section("The check-in link goes by itself, once")
    with m.app.test_request_context("/"):
        n = m.send_channel_checkin_links(conn, today)
    to = "hnovak.123456@guest.booking.com"
    s.check("sent to the guest's Booking.com address", n >= 1 and "channel_checkin" in _letters(conn, to),
            detail=str(_letters(conn, to)))
    with m.app.test_request_context("/"):
        m.send_channel_checkin_links(conn, today)
    s.check("and not twice", _letters(conn, to).count("channel_checkin") == 1, detail=str(_letters(conn, to)))
    quiet = _stay(conn, "660012", today + timedelta(days=20), name=f"{TAG} No Address")
    r = oc.post(f"/admin/channel-stays/{quiet}/checkin-link", follow_redirects=True)
    s.check("a stay with no address cannot be sent it, and says why",
            "no address" in " ".join(flashes(r)), detail=" ".join(flashes(r)))
    # The address a message from the guest came from is enough.
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute("""INSERT INTO ota_mail (channel, source, received_at, created_at, kind, from_address,
                    reply_to, reservation_number, ota_reservation_id, from_booking_com, subject)
                    VALUES ('booking.com', 'test', ?, ?, 'message', 'noreply@booking.com',
                            'zzmsg.555@guest.booking.com', '660012', ?, 1, 'A question')""",
                 (stamp, stamp, quiet))
    conn.commit()
    r = oc.post(f"/admin/channel-stays/{quiet}/checkin-link", follow_redirects=True)
    s.check("an address their message came from is used", "channel_checkin" in _letters(conn, "zzmsg.555@guest.booking.com"),
            detail=" ".join(flashes(r)))

    s.section("The guest checks in")
    token = conn.execute("SELECT checkin_token FROM ota_reservations WHERE id = ?", (sid,)).fetchone()[0]
    guest = m.app.test_client()
    url = f"/checkin/stay/{token}"
    body = guest.get(url).get_data(as_text=True)
    s.check("their page opens", "Check in online" in body)
    s.check("never indexed", 'name="robots" content="noindex' in body)
    # Booking.com's "Czech Republic" matched nothing on the form, which then
    # took the browser's region and offered a Czech guest the United States.
    s.check("their country is chosen for them", 'value="Czechia" data-iso="CZ" selected' in body)
    s.check("and their nationality suggested from it", 'name="nationality" required value="Czech"' in body)
    s.check("a link that is not one is a 404", guest.get("/checkin/stay/not-a-token").status_code == 404)
    base = {"first_names": "Hana", "surname": "Novak", "guest_email": "zzotaci.hana@example.invalid",
            "guest_phone": "+420 601 234 567", "guest_address": "Na Prikope 1", "guest_city": "Praha",
            "guest_postcode": "11000", "guest_country": "Czech Republic", "nationality": "Czech",
            "estimated_arrival_time": "16:00-17:00", "dietary_notes": "No nuts", "agree": "on",
            # R. 814-1: the fiche is signed by the guest (23 October 2026).
            # Typed, as a guest with no box to draw in signs.
            "signed_name": "Hana Novak"}
    refused = guest.post(url, data=base)
    s.check("not French and no date of birth: refused, saying why",
            refused.status_code == 200 and "date and place of birth" in refused.get_data(as_text=True)
            and not conn.execute("SELECT checkin_done_at FROM ota_reservations WHERE id = ?",
                                 (sid,)).fetchone()[0], detail=str(refused.status_code))
    guest.post(url, data=dict(base, born_on="1990-04-12", born_at="Brno"))
    row = conn.execute("SELECT * FROM ota_reservations WHERE id = ?", (sid,)).fetchone()
    s.check("checked in", bool(row["checkin_done_at"]))
    s.check("with their own address and details", row["guest_email"] == "zzotaci.hana@example.invalid"
            and row["guest_city"] == "Praha" and row["dietary_notes"] == "No nuts")
    fiche = conn.execute("SELECT * FROM channel_police_register WHERE ota_reservation_id = ?", (sid,)).fetchone()
    s.check("and the police fiche made from it", fiche and fiche["surname"] == "Novak"
            and fiche["born_on"] == "1990-04-12" and fiche["nationality"] == "Czech")
    s.check("signed by the guest, with the mobile and email the law lists",
            fiche and fiche["signature"] == "Hana Novak" and fiche["signed_at"]
            and fiche["phone"] == "+420 601 234 567"
            and fiche["email"] == "zzotaci.hana@example.invalid",
            detail=str(dict(fiche) if fiche else None))
    prof = conn.execute("SELECT * FROM guests WHERE id = ?", (row["linked_guest_id"],)).fetchone()
    s.check("their profile is the one with their own address", prof and prof["email"] == "zzotaci.hana@example.invalid")
    s.check("the page thanks them", "you are checked in" in guest.get(url).get_data(as_text=True))
    s.check("the stay's page says so", "checked in online" in oc.get(f"/admin/channel-stays/{sid}").get_data(as_text=True))

    s.section("A French guest needs no fiche")
    fr_id = _stay(conn, "660013", today + timedelta(days=30), name=f"{TAG} French")
    with m.app.test_request_context("/"):
        fr_token = m.ota_checkin_token(conn, fr_id)
    conn.commit()
    guest.post(f"/checkin/stay/{fr_token}", data=dict(base, first_names="Jean", surname="Dupuis",
               guest_email="zzotaci.jean@example.invalid", nationality="France"))
    s.check("checked in without a date of birth", bool(conn.execute(
        "SELECT checkin_done_at FROM ota_reservations WHERE id = ?", (fr_id,)).fetchone()[0]))
    s.check("and no fiche made", not conn.execute(
        "SELECT 1 FROM channel_police_register WHERE ota_reservation_id = ?", (fr_id,)).fetchone())

    s.section("What they cannot eat goes once they have left")
    conn.execute("UPDATE ota_reservations SET arrival_date = ?, departure_date = ? WHERE id = ?",
                 ((today - timedelta(days=5)).isoformat(), (today - timedelta(days=2)).isoformat(), sid))
    conn.commit()
    with m.app.test_request_context("/"):
        m.purge_health_notes(conn, today)
    conn.commit()
    s.check("cleared after the stay", conn.execute(
        "SELECT dietary_notes FROM ota_reservations WHERE id = ?", (sid,)).fetchone()[0] is None)


if __name__ == "__main__":
    print(run().report())
