"""Whether discounting filled rooms, or sold cheaply what would have gone anyway.

promo_code_redemptions has counted redemptions since it was built —
original_amount, discount_amount, final_amount, all of it. Nothing ever
asked whether any of it WORKED, and it is the most expensive unanswered
question the house has: the money is given away either way, and only one of
the two outcomes is worth it.

WHAT THIS MUST NOT DO IS CLAIM CAUSATION, and that is most of what this
suite is guarding. No data can say whether a guest would have come at full
price; that guest does not exist to be asked. A page printing "this code
brought in €4,000" would be inventing a counterfactual, and a confident
wrong answer about marketing spend is worse than none because it gets
repeated.

What it can show is the one piece of real evidence: how full the house was
on the nights each discount was used. A code used when the house was nearly
full gave money away on rooms that were selling; one used when it was
nearly empty at least put somebody in a room. Neither is proof, and the
page says so.

The other thing held here: occupancy is averaged over the NIGHTS, not over
the bookings. A fortnight in a full house has to weigh more than one night
in an empty one, or a single quiet booking can drown out a season of
giving money away.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ZZDISC"


def _cleanup(conn):
    conn.execute("DELETE FROM promo_code_redemptions WHERE promo_code_id IN "
                 "(SELECT id FROM promo_codes WHERE code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Did the discounts work")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    today = date.today()
    now = m.datetime.now(m.timezone.utc).isoformat()

    rooms = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id").fetchall()
    if len(rooms) < 2:
        s.check("at least two rooms exist to measure occupancy against", False,
                detail=f"{len(rooms)} active room(s) — the busy/quiet reading "
                       "needs more than one")
        conn.close()
        return s

    def code(name, kind="percent", value=10):
        conn.execute(
            """INSERT INTO promo_codes (code, discount_type, discount_value,
                                        active, created_at)
               VALUES (?, ?, ?, 1, ?)""", (TAG + name, kind, value, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def stay(room_id, name, start, nights, promo=None, discount=0.0):
        conn.execute(
            """INSERT INTO bookings (room_id, guest_name, guest_email,
                       arrival_date, departure_date, party_size, status,
                       manage_token, reference_code, promo_code_id,
                       discount_amount, total_price, created_at)
               VALUES (?, ?, 'd@example.invalid', ?, ?, 2, 'confirmed', ?, ?,
                       ?, ?, 500, ?)""",
            (room_id, TAG + " " + name, start.isoformat(),
             (start + timedelta(days=nights)).isoformat(),
             f"tok-{TAG.lower()}-{name}", f"{TAG}{name.upper()}",
             promo, discount, now))

    def redeem(promo_id, discount, final):
        conn.execute(
            """INSERT INTO promo_code_redemptions (promo_code_id, category,
                       original_amount, discount_amount, final_amount, redeemed_at)
               VALUES (?, 'room', ?, ?, ?, ?)""",
            (promo_id, discount + final, discount, final, now))

    # A code used on a night when the house was FULL: every active room
    # taken on the same night.
    busy_code = code("BUSY")
    busy_night = today - timedelta(days=40)
    for i, r in enumerate(rooms):
        stay(r["id"], f"full{i}", busy_night, 1,
             promo=busy_code if i == 0 else None,
             discount=100.0 if i == 0 else 0.0)
    redeem(busy_code, 100.0, 400.0)

    # A code used on a night when only that one room was taken.
    quiet_code = code("QUIET")
    quiet_night = today - timedelta(days=80)
    stay(rooms[0]["id"], "alone", quiet_night, 1, promo=quiet_code,
         discount=50.0)
    redeem(quiet_code, 50.0, 450.0)
    conn.commit()

    # A stay that DEPARTS on the busy night. It does not occupy that night
    # -- the guest leaves in the morning -- and without this in the fixture
    # the departure-day rule cannot be broken observably, which a control
    # proved.
    stay(rooms[1]["id"], "leaving", busy_night - timedelta(days=2), 2)
    conn.commit()

    data = m.discount_outcomes(conn, months=12)
    by_code = {c["code"]["code"]: c for c in data["codes"]}

    s.section("What each code gave away")
    s.check("both codes are reported",
            TAG + "BUSY" in by_code and TAG + "QUIET" in by_code,
            detail=str(sorted(by_code)))
    s.check("with what was given away", by_code[TAG + "BUSY"]["given"] == 100.0,
            detail=str(by_code[TAG + "BUSY"]["given"]))
    s.check("and what was still taken", by_code[TAG + "BUSY"]["taken"] == 400.0,
            detail=str(by_code[TAG + "BUSY"]["taken"]))
    s.check("a code nobody used is left off entirely",
            code("UNUSED") and TAG + "UNUSED" not in
            {c["code"]["code"] for c in m.discount_outcomes(conn)["codes"]},
            detail="a row of zeroes is noise on a page meant to be read")

    s.section("How full the house was on those nights")
    busy, quiet = by_code[TAG + "BUSY"], by_code[TAG + "QUIET"]
    s.check("a full night reads as busy", busy["busy"] and not busy["quiet"],
            detail=f"{busy['occupancy_pct']}% — {busy['avg_rooms_full']} of "
                   f"{busy['rooms']}")
    s.check("and a near-empty one reads as quiet",
            quiet["quiet"] and not quiet["busy"],
            detail=f"{quiet['occupancy_pct']}% — {quiet['avg_rooms_full']} of "
                   f"{quiet['rooms']}")
    s.check("which is the whole distinction",
            busy["occupancy_pct"] > quiet["occupancy_pct"],
            detail="one gave money away on rooms that were selling; the "
                   "other at least put somebody in a room")

    s.section("Occupancy is weighed by nights, not by bookings")
    # A fortnight in a full house must outweigh one night in an empty one,
    # or a single quiet booking drowns out a season of giving money away.
    long_code = code("LONG")
    full_run = today - timedelta(days=200)
    # All but the last room, so the guest who leaves mid-fortnight has one
    # to themselves. Putting them in a room already booked for the whole run
    # would be a double-booking -- not a state the house can be in, and a
    # check built on an impossible state measures nothing.
    for i, r in enumerate(rooms[:-1]):
        stay(r["id"], f"long{i}", full_run, 14,
             promo=long_code if i == 0 else None,
             discount=300.0 if i == 0 else 0.0)
    # And one solitary night, quiet.
    stay(rooms[0]["id"], "longsolo", today - timedelta(days=150), 1,
         promo=long_code, discount=20.0)
    redeem(long_code, 320.0, 900.0)
    conn.commit()

    # A guest departing in the MIDDLE of that fortnight. For a single-night
    # window the SQL handles this already -- it asks for departure_date
    # after the first night -- so the per-date rule can only be exercised
    # inside a longer window. The two are not redundant; they cover
    # different cases.
    stay(rooms[-1]["id"], "leavesmid", full_run + timedelta(days=3), 2)
    conn.commit()

    lng = {c["code"]["code"]: c
           for c in m.discount_outcomes(conn, months=12)["codes"]}[TAG + "LONG"]
    s.check("fourteen busy nights outweigh one quiet one",
            lng["busy"],
            detail=f"{lng['occupancy_pct']}% over {lng['nights']} nights — "
                   "averaging over bookings would have made this two "
                   "bookings, one busy and one quiet, and called it even")

    s.section("A guest leaving does not occupy the night they leave on")
    # rooms[1] is booked for the whole fortnight AND has a stay departing
    # on the fifth night. Counting a departure as occupancy would put that
    # room in two places and the house above its own room count.
    # Checked on the WORST night rather than the average. One inflated
    # night in fifteen moves the mean by less than a tenth of a room, so
    # an average cannot see this -- which a control proved by staying
    # green while the rule was broken.
    fortnight = [full_run + timedelta(days=i) for i in range(15)]
    occ = m.occupancy_on(conn, fortnight)
    # Stated as the two numbers it is about rather than as an average,
    # which cannot see one inflated night in fifteen.
    last_night = occ[full_run + timedelta(days=4)]
    leaving_day = occ[full_run + timedelta(days=5)]
    s.check("the night before they leave counts them",
            last_night == len(rooms), detail=str(last_night))
    s.check("and the night they leave on does not",
            leaving_day == len(rooms) - 1,
            detail=f"{leaving_day} against {last_night} the night before — "
                   "a guest who goes in the morning is not in the room that "
                   "night, and counting them inflates exactly the nights this "
                   "page is judging")

    s.section("It refuses to invent a counterfactual")
    # The check that stops this becoming a return-on-investment figure.
    # Nothing here may claim revenue was caused, because nothing can know.
    for key in ("brought_in", "incremental", "roi", "uplift", "caused"):
        s.check(f"no {key} figure is produced", key not in busy,
                detail="whether a guest would have come at full price is "
                       "unknowable; that guest does not exist to be asked")

    body = oc.get("/management/discounts").get_data(as_text=True)
    # On the page that already existed and answers what discounting COST.
    # I built a second one without checking; the route sweep caught the
    # collision, and the two questions belong together anyway.
    s.check("and the page says so in as many words",
            "will not tell you a code" in body and "because nothing can" in body,
            detail="a confident wrong answer about marketing spend is worse "
                   "than none, because it gets repeated")

    s.section("The page")
    s.check("it opens", oc.get("/management/discounts").status_code == 200)
    # The merged page states the reading in prose and shows the occupancy
    # per code in the table; "likely selling anyway" was wording from the
    # duplicate page that has been removed.
    s.check("it shows how full the house was, per code",
            "House was" in body, detail="the column the whole reading rests on")
    s.check("and points at what a night costs, which is the other half",
            "/management/night-cost" in body,
            detail="a discounted night is still worth selling if it beats "
                   "what the night consumes")
    s.check("a junk window does not break it",
            oc.get("/management/discounts?months=abc").status_code == 200)
    r = ec.get("/management/discounts", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
