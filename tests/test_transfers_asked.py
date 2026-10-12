"""A guest who asked to be collected is on the transfers page until a run is arranged.

WHAT WENT WRONG. /transfers read vehicle runs only. A guest who chose an
airport transfer with their stay, or gave a flight on their own page, was
nowhere on it: the page said "0 upcoming" while stays had asked to be
collected, and the run most likely to be missed was the one it could not see.

WHAT THIS PINS. A stay that asked is listed, with its flight; staff see it
too (drivers open this page); arranging a run from it fills the stay in and
ties the run to it; and once tied it comes off the list. A stay that did not
ask, or has been cancelled, is never on it.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZTRA"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM vehicle_transfers WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, arrive, *, extras="", flight=None, landing=None, status="confirmed"):
    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, extras_summary,
           transfer_flight_number, transfer_arrival_time, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 500, ?, ?, ?, ?)""",
        (room, f"{TAG}{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}{ref.lower()}@example.invalid", arrive.isoformat(),
         (arrive + timedelta(days=2)).isoformat(), status, extras, flight, landing,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    conn.close()
    return bid


def run():
    s = Suite("Transfers asked for")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    arrive = house_today() + timedelta(days=30)
    asked = _stay("Asked", arrive, extras="Airport transfer (Toulouse)", flight="BA123",
                  landing="14:30")
    flight_only = _stay("Flight", arrive + timedelta(days=1), flight="EZY55")
    _stay("Plain", arrive)
    _stay("Gone", arrive, extras="Airport transfer", status="cancelled")
    conn = db()
    conn.execute("INSERT INTO vehicles (name, created_at) VALUES (?, ?)",
                 (TAG + " Van", datetime.now(timezone.utc).isoformat()))
    van = conn.execute("SELECT id FROM vehicles WHERE name = ?", (TAG + " Van",)).fetchone()["id"]
    conn.commit()
    conn.close()

    s.section("A stay that asked is on the page")
    page = oc.get("/transfers").get_data(as_text=True)
    s.check("a transfer chosen with the stay puts it there, with its flight",
            f"{TAG} Asked" in page and "BA123" in page and "landing 14:30" in page)
    s.check("and so does a flight given on the guest's page", f"{TAG} Flight" in page)
    s.check("a stay that asked for nothing is not", f"{TAG} Plain" not in page)
    s.check("nor one that was cancelled", f"{TAG} Gone" not in page)
    staff = ec.get("/transfers").get_data(as_text=True)
    s.check("the driver sees it too, without the owner's links",
            f"{TAG} Asked" in staff and "Arrange in" not in staff)

    s.section("Arranging it from there fills the stay in")
    link = f"/management/vehicles/{van}/transfers?booking_id={asked}"
    s.check("the owner has a way to arrange it", link.replace("&", "&amp;") in page or link in page,
            detail=link)
    form = oc.get(link).get_data(as_text=True)
    s.check("the form arrives with the guest, the landing time and the flight",
            f'value="{TAG} Asked"' in form and f'value="{arrive.isoformat()}T14:30"' in form
            and "Flight BA123" in form and f'name="booking_id" value="{asked}"' in form)
    oc.post(f"/management/vehicles/{van}/transfers/new", data={
        "booking_id": str(asked), "guest_name": f"{TAG} Asked", "direction": "pickup",
        "scheduled_at": f"{arrive.isoformat()}T14:30", "transfer_type": "airport"})
    conn = db()
    run_row = conn.execute("SELECT booking_id FROM vehicle_transfers WHERE guest_name = ?",
                           (f"{TAG} Asked",)).fetchone()
    conn.close()
    s.check("the run is tied to the stay", run_row is not None and run_row["booking_id"] == asked,
            detail=str(dict(run_row) if run_row else None))
    page = oc.get("/transfers").get_data(as_text=True)
    listed = page[page.find("Asked for, nothing arranged yet"):page.find("Today —")]
    s.check("and it comes off the asked-for list, onto the runs",
            f"{TAG} Asked" not in listed and f"{TAG} Flight" in listed,
            detail=" ".join(listed.split())[:300])
    oc.post(f"/management/vehicles/{van}/transfers/new", data={
        "booking_id": "999999999", "guest_name": f"{TAG} Nobody", "direction": "pickup",
        "scheduled_at": f"{arrive.isoformat()}T10:00"})
    conn = db()
    stray = conn.execute("SELECT booking_id FROM vehicle_transfers WHERE guest_name = ?",
                         (f"{TAG} Nobody",)).fetchone()
    conn.close()
    s.check("a stay that does not exist ties nothing",
            stray is not None and stray["booking_id"] is None)
    s.check("(the flight-only stay is still waiting)",
            any(r["id"] == flight_only for r in _asked()))

    _cleanup()
    return s


def _asked():
    conn = db()
    try:
        return m.transfers_asked_for(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    print(run().report())
