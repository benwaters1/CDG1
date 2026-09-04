"""What the house is owed did not include the ateliers.

outstanding_balances covers stays and events. Its own docstring says why
events were added -- "a wedding is the largest single thing the house is owed,
and until events had a money model at all they could not appear here" -- and a
workshop registration has had one from the start: a deposit, a dated balance,
a ledger, and a job that charges what is left. An unpaid one is money owed,
and the page called "What we're owed" had never shown a single one.

AND A REFUSED CARD IS NOT A GUEST WHO FORGOT.

run_workshop_autocharge_job sets autocharge_failed_at when a card declines,
tries once, and never tries again. That is right, and its own comment says
why: "a declined card retried daily is a guest with six bank alerts who still
has not paid". It emails the guest and it emails the house.

But an email is a moment and this is a state. The house has no email provider
configured, so that message is in the outbox; and once it is read, or lost,
nothing anywhere says which registrations went unpaid because a card was
refused. The column held exactly that and nothing read it.

Marking it is the point: chasing somebody who forgot and chasing somebody
whose card was refused are different jobs. The second has already been asked,
already been told, and the app will not try again — so nothing further happens
unless a person does it.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZCARD"


def _cleanup(conn):
    conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.commit()


def run():
    s = Suite("what the ateliers are owed, and the cards that were refused")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = house_today()

    session = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY id DESC LIMIT 1").fetchone()
    if not session:
        s.section("Setup")
        s.check("a workshop session exists to register on", False,
                detail="reported rather than skipped: every check below would "
                       "pass on an empty list")
        conn.close()
        return s

    def register(ref, *, due_days_ago=None, refused=False, paid=0.0,
                 total=4800.0):
        due = ((today - timedelta(days=due_days_ago)).isoformat()
               if due_days_ago is not None else None)
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code,
                       manage_token, guest_name, guest_email, party_size,
                       status, total_price, balance_amount, balance_due_date,
                       autocharge_failed_at, created_at)
               VALUES (?, ?, ?, ?, ?, 1, 'confirmed', ?, ?, ?, ?, ?)""",
            (session["id"], TAG + ref, (TAG + ref).lower(), TAG + " " + ref,
             f"{TAG}.{ref}@example.invalid".lower(), total, total, due,
             (now - timedelta(days=2)).isoformat() if refused else None,
             now.isoformat()))
        rid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        if paid:
            m.add_workshop_transaction(conn, rid, "payment", TAG + " paid",
                                       paid, method="bank")
        conn.commit()
        return rid

    owing = register("OWING", due_days_ago=10)
    declined = register("DECLINED", due_days_ago=20, refused=True)
    settled = register("SETTLED", due_days_ago=30, paid=4800.0)

    def owed_rows():
        return {r["reference"]: r for r in m.outstanding_balances(conn)}

    s.section("An unpaid registration is money the house is owed")
    rows = owed_rows()
    s.check("it is on the list at all", TAG + "OWING" in rows,
            detail="the page is called What we're owed, and an atelier "
                   "balance is owed")
    s.check("as a workshop rather than a stay",
            rows.get(TAG + "OWING", {}).get("kind") == "workshop",
            detail=str(rows.get(TAG + "OWING", {}).get("kind")))
    s.check("with the amount from the ledger",
            abs(rows[TAG + "OWING"]["owed"] - 4800.0) < 0.01
            if TAG + "OWING" in rows else False,
            detail=str(rows.get(TAG + "OWING", {}).get("owed")))
    s.check("and a link somebody can act on",
            bool(rows.get(TAG + "OWING", {}).get("link_endpoint")),
            detail=str(rows.get(TAG + "OWING", {}).get("link_endpoint")))

    s.check("one that has been paid is not on it", TAG + "SETTLED" not in rows,
            detail="a settled registration is not a debt, and a list that "
                   "shows one is a list nobody trusts")

    s.section("Late is said as late")
    s.check("a balance due ten days ago is overdue",
            rows.get(TAG + "OWING", {}).get("state") in ("overdue", "gone"),
            detail=str(rows.get(TAG + "OWING", {}).get("state")))
    s.check("and how late is counted",
            (rows.get(TAG + "OWING", {}).get("days_late") or 0) >= 1,
            detail=str(rows.get(TAG + "OWING", {}).get("days_late")))

    s.section("A refused card says so, and the others say they were not")
    s.check("the refused one is marked",
            rows.get(TAG + "DECLINED", {}).get("card_refused") is True)
    s.check("with when it was refused",
            bool(rows.get(TAG + "DECLINED", {}).get("refused_at")))
    s.check("the one nobody tried is not marked",
            rows.get(TAG + "OWING", {}).get("card_refused") is False,
            detail="unpaid is not the same as refused, and treating them the "
                   "same loses the distinction the marking is for")
    s.check("and a stay answers the question too rather than not having it",
            all("card_refused" in r for r in m.outstanding_balances(conn)),
            detail="a page has to be able to ask every row the same thing")

    s.section("And the refused ones are the same rows, not a second query")
    refused = {r["reference"] for r in m.refused_card_arrears(conn)}
    everything = {r["reference"] for r in m.outstanding_balances(conn)
                  if r["card_refused"]}
    s.check("it is a subset of what is owed", refused == everything,
            detail=f"{sorted(refused)} vs {sorted(everything)} — two queries "
                   "for one idea is how two figures come to disagree")
    # And the same MONEY, not only the same rows. Comparing references alone
    # let a version through that returned the right registrations with the
    # amounts zeroed, which is the disagreement this check is for.
    by_ref = {r["reference"]: round(r["owed"], 2)
              for r in m.outstanding_balances(conn) if r["card_refused"]}
    same = {r["reference"]: round(r["owed"], 2)
            for r in m.refused_card_arrears(conn)}
    s.check("and the same amounts against them", by_ref == same,
            detail=f"{by_ref} vs {same}")
    s.check("and it has the declined one in it", TAG + "DECLINED" in refused)

    s.section("The page shows it")
    body = oc.get("/management/outstanding").get_data(as_text=True)
    s.check("the registration is on the page", TAG + " DECLINED" in body,
            detail="the ateliers were absent from this page entirely")
    s.check("marked as refused", "Card refused" in body)
    s.check("with a chip that counts them",
            "Refused" in body and "Card" in body,
            detail="'three of these were refused' is the number that decides "
                   "whether this page is opened this morning")
    s.check("and somewhere to go for it",
            "On the registration" in body)

    s.section("The morning panel says it, because nothing else will")
    with m.app.test_request_context("/"):
        warns = m.owner_home_warnings(conn, today)
    hit = next((w for w in warns if "refused" in w["title"]), None)
    s.check("it is on the panel", hit is not None,
            detail=str([w["title"] for w in warns])[:170])
    s.check("as a blocker", hit and hit["severity"] == "blocker",
            detail="the app will not try again, so this does not resolve "
                   "itself")
    s.check("naming who and how much",
            hit and TAG + " DECLINED" in hit["detail"], detail=str(hit)[:190])
    s.check("and saying nothing more happens on its own",
            hit and "will not try the card again" in hit["detail"],
            detail=str(hit)[:190])
    s.check("with somewhere to act", hit and "outstanding" in hit["href"],
            detail=str(hit))

    s.section("And the ageing report adds up to what is owed")
    # The receivables total is the figure an accountant and a bank both ask
    # for. debtor_ageing used to build its own workshop query beside this
    # list; once the list held workshops too, keeping both would have counted
    # every unpaid registration twice and doubled that figure. Nothing
    # compared the two, so nothing would have noticed.
    with m.app.test_request_context("/"):
        ageing = m.debtor_ageing(conn)
    owed_total = round(sum(r["owed"] for r in m.outstanding_balances(conn)), 2)
    s.check("the ageing total equals what the house is owed",
            abs(ageing["total"] - owed_total) < 0.02,
            detail=f"ageing {ageing['total']} vs owed {owed_total} — two "
                   "sources for one figure is how they come to disagree")
    s.check("and each debt appears once, not twice",
            len(ageing["items"]) == len(m.outstanding_balances(conn)),
            detail=f"{len(ageing['items'])} aged vs "
                   f"{len(m.outstanding_balances(conn))} owed")
    s.check("the buckets add up to the total",
            abs(sum(ageing["totals"].values()) - ageing["total"]) < 0.02,
            detail=str(ageing["totals"]))

    s.section("Paying it takes it off both")
    m.add_workshop_transaction(conn, declined, "payment", TAG + " settled up",
                               4800.0, method="bank")
    conn.commit()
    s.check("it leaves the owed list",
            TAG + "DECLINED" not in owed_rows())
    s.check("and the refused list with it",
            TAG + "DECLINED" not in {r["reference"]
                                     for r in m.refused_card_arrears(conn)},
            detail="nothing here has a done action of its own; the money "
                   "arriving is what closes it")
    with m.app.test_request_context("/"):
        warns = m.owner_home_warnings(conn, today)
    s.check("and the panel goes quiet about it",
            not any("refused" in w["title"] for w in warns),
            detail=str([w["title"] for w in warns])[:170])

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
