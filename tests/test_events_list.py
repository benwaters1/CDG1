"""The events list: what is current first, and the standard toolbar.

The last customer list with one status dropdown and nothing else. It split
itself by hand into "upcoming" and a fold of past events -- the History chip,
done once, on one page -- with no search, no counted chips, and an export of
every enquiry there had ever been whatever the page was showing. And the
"past" badge read an event's first day, so a three-day wedding was badged
past on its second.
"""
import csv
import html as _html
import io
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZEV"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM event_payments WHERE event_id IN "
                 "(SELECT id FROM event_inquiries WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _event(ref, *, on=None, ends=None, status="confirmed", quote=None, paid=0.0, kind="wedding",
           phone="", message="ZZ test", created_offset=0):
    conn = db()
    created = (datetime.now(timezone.utc) + timedelta(minutes=created_offset)).isoformat()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
           contact_email, contact_phone, preferred_date, end_date, guest_count, message, status,
           quoted_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 40, ?, ?, ?, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), kind, f"{TAG} {ref}",
         f"{TAG.lower()}.{ref.lower()}@example.invalid", phone,
         (house_today() + timedelta(days=on)).isoformat() if on is not None else None,
         (house_today() + timedelta(days=ends)).isoformat() if ends is not None else None,
         message, status, quote, paid, created))
    conn.commit()
    conn.close()


def _listed(page, ref):
    return f"{TAG}-{ref}" in page


def _shown(page):
    found = re.search(r"Showing (\d+) of (\d+)", page)
    return int(found.group(1)) if found else None


def run():
    s = Suite("The events list")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    _event("Soon", on=60, quote=9000.0, paid=3000.0, phone="06 11 22 33 44")
    _event("Paid", on=90, quote=4000.0, paid=4000.0, kind="photoshoot")
    _event("Undated", on=None, status="new", message="a long table under the lime trees")
    _event("Running", on=-1, ends=1, quote=5000.0, paid=5000.0)
    _event("Last", on=-10, quote=2000.0, paid=2000.0)
    _event("Older", on=-40, quote=2000.0, paid=2000.0)

    s.section("It opens on what is current")
    page = oc.get("/admin/events").get_data(as_text=True)
    s.check("an event still to come is there", _listed(page, "Soon"))
    s.check("so is an enquiry with no date yet", _listed(page, "Undated"))
    s.check("and a three-day event on its second day", _listed(page, "Running"))
    s.check("one that is over is not", not _listed(page, "Last") and not _listed(page, "Older"))
    s.check("and new enquiries come first", page.find(f"{TAG}-Undated") < page.find(f"{TAG}-Soon"),
            detail="an enquiry nobody has answered is the one with a clock running")
    hist = oc.get("/admin/events?when=History").get_data(as_text=True)
    s.check("History has what is over, most recent first",
            _listed(hist, "Last") and _listed(hist, "Older") and not _listed(hist, "Soon")
            and hist.find(f"{TAG}-Last") < hist.find(f"{TAG}-Older"))

    s.section("The badge says past when the last day has passed, not the first")
    card = page[page.find(f"{TAG} Running"):page.find(f"{TAG}-Running") + 20]
    s.check("a three-day event on its second day is not badged past",
            ">past</span>" not in card, detail="it read the first day")

    s.section("Search")
    for q, ref in (("+33611223344", "Soon"), (f"{TAG}-Paid", "Paid"), ("lime trees", "Undated")):
        got = oc.get("/admin/events", query_string={"q": q}).get_data(as_text=True)
        s.check(f"{q!r} finds its event", _listed(got, ref))

    s.section("The chips count what they give")
    chip = re.search(r'<a href="([^"]+)"\s*class="chip[^"]*">Owes money '
                     r'<span class="chip-n">(\d+)</span>', page)
    got = oc.get(_html.unescape(chip.group(1))).get_data(as_text=True) if chip else ""
    s.check("Owes money gives what it counts",
            chip is not None and _shown(got) == int(chip.group(2)),
            detail=f"chip {chip.group(2) if chip else None}, shown {_shown(got)}")
    s.check("and is the event with a balance, not the one paid in full",
            _listed(got, "Soon") and not _listed(got, "Paid"))
    kind = oc.get("/admin/events?kind=Photoshoot").get_data(as_text=True)
    s.check("a kind is a chip", _listed(kind, "Paid") and not _listed(kind, "Soon"))
    new = oc.get("/admin/events?status=new").get_data(as_text=True)
    s.check("an old ?status= link is the Status chip", _listed(new, "Undated") and not _listed(new, "Soon"))

    s.section("Export this view")
    r = oc.get("/admin/events/export.csv?when=History")
    refs = sorted(x["reference_code"] for x in csv.DictReader(io.StringIO(r.get_data(as_text=True)))
                  if x["reference_code"].startswith(TAG))
    s.check("History exports what is over, and only that",
            refs == [f"{TAG}-Last", f"{TAG}-Older"], detail=f"{refs}")

    _cleanup()
    return s
