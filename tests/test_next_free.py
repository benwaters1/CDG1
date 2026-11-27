"""What is open next stays off /book (owner, 26 November 2026).

The booking page used to list, before anybody had typed a date, the next
stretch each room had free ("From Sunday 29 November"). The owner had it
removed: "i wanted this removed". The route stopped working it out that day,
and next_free_nights itself went on the 27th, when nothing asked it any more.

The macro (_availability_strip.html) and its styles were left where they were,
because /book and the stylesheets arrive whole from the design side: a
handover that draws the strip again finds a macro that renders nothing without
its list, rather than a missing file and a booking page that will not open.
So this is what says so if it comes back -- before anybody has searched, and
after.

The "next free from" line a guest is shown when their own dates are refused is
a different thing, and stays: next_free_words, asked of the booking gate
itself. test_called_off_frees_the_house reads it.
"""
from datetime import timedelta

from _harness import Suite, house_today

import _harness

m = _harness.m


def run():
    s = Suite("What is open next stays off /book")
    anon = m.app.test_client()
    today = house_today()

    s.section("Not on the booking page")
    body = anon.get("/book").get_data(as_text=True)
    s.check("the booking page answers", "<form" in body)
    s.check("What is open next is not on /book",
            "What is open next" not in body and "What Is Open Next" not in body
            and "rooms are open from" not in body and 'class="g-nextf' not in body,
            detail="the owner had it removed on 26 November 2026")

    s.section("Nor on a page of search results")
    searched = anon.get(
        "/book?arrival=" + (today + timedelta(days=60)).isoformat()
        + "&departure=" + (today + timedelta(days=62)).isoformat()
    ).get_data(as_text=True)
    s.check("it is not on a page of search results",
            "What Is Open Next" not in searched and "What is open next" not in searched
            and 'class="g-nextf' not in searched)

    s.section("And the app no longer works it out")
    s.check("next_free_nights is gone, so nothing can draw it by accident",
            not hasattr(m, "next_free_nights"))
    s.check("the line on refused dates is still there",
            callable(getattr(m, "next_free_words", None)))
    return s


if __name__ == "__main__":
    print(run().report())
