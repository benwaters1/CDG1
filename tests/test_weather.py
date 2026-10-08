"""What it is doing at the château, without asking the guest's browser.

The sketch fetched it client-side from Open-Meteo, which is the obvious way
and the wrong one here. Every visitor's browser would contact a third party
and hand its address to a company the guest has never heard of, on a page
whose privacy notice makes specific promises about who sees what — and it
would do it once per visitor, for a number that changes once an hour.

The house fetches it instead, hourly, and caches it. One call an hour no
matter how many people are reading, and no guest's address leaves the
building.

WHICH MEANS THE PAGE NEVER WAITS. weather_now() reads a cache and cannot make
a network call. A render that can block on somebody else's network is one
that eventually does, and this one is the booking page. The whole shape of
this file is about the three ways the cache can fail to have an answer —
empty, stale, or unreadable — and each of them leaving the written sentence
standing rather than an empty box or a wrong number.

Nothing here reaches the network: _harness.py stands fetch_weather down and
asserts it at import, like Stripe and the mail transports. It costs nothing
and needs no key, which is exactly why it is the one that gets forgotten — a
suite that reaches the network fails on an aeroplane and passes on a desk.
"""
from datetime import timedelta

from _harness import Suite, db

import _harness

m = _harness.m
KEY = "weather_snapshot"


def _set(conn, value):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (KEY, value))
    conn.commit()


def _clear(conn):
    conn.execute("DELETE FROM app_settings WHERE key = ?", (KEY,))
    conn.commit()


def run():
    s = Suite("what it is doing at the château")
    conn = db()
    keep = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                        (KEY,)).fetchone()
    keep = keep["value"] if keep else None
    now = m.datetime.now(m.timezone.utc)
    anon = m.app.test_client()

    s.section("The page never reaches the network")
    # The one that matters most. fetch_weather is stood down in the harness,
    # so if a render called it this whole suite would raise rather than pass.
    s.check("the fetch is blocked under test",
            m.fetch_weather.__name__ == "_blocked",
            detail="it needs no key and costs nothing, which is why it is the "
                   "one that gets forgotten")
    import inspect
    s.check("and weather_now does not call it",
            "fetch_weather" not in inspect.getsource(m.weather_now),
            detail="a render that can block on somebody else's network is one "
                   "that eventually does")

    s.section("With nothing cached, the page says nothing rather than something wrong")
    _clear(conn)
    s.check("there is no reading", m.weather_now(conn) is None)
    body = anon.get("/book").get_data(as_text=True)
    # Until 5 October a written line stood where the reading would be -- the
    # owner's approved "In the Ariège, cold winters and warm summers". The
    # design cut it that day, with its reason in the partial: alone between
    # two sections it read as an orphan. Whether it should come back is the
    # owner's call and is with him; it is not a thing a test decides.
    #
    # What a test does decide is that the absence is CLEAN. An empty
    # <div class="g-wx"> with nothing inside it reads to a guest as something
    # that failed to load, which is worse than either the sentence or
    # silence, and it is what a careless `{% if %}` around the inner markup
    # would leave behind.
    s.check("no empty weather box is left on the page",
            'class="g-wx"' not in body,
            detail="the wrapper renders with nothing in it, which reads as a "
                   "broken widget rather than as nothing to say")
    # WITH THE SCRIPTS TAKEN OUT, the way the room-page checks below read it.
    # The 8 October typical-weather block keeps its figures in javascript
    # string literals, so searching the raw document finds a degree sign that
    # no guest is ever shown. What this is about is a temperature RENDERED
    # when the house has no reading, and that is what stripping scripts asks.
    import re as _re0
    drawn = _re0.sub(r"(?is)<script.*?</script>", "", body)
    s.check("and offers no temperature", "&deg;C" not in drawn
            and "°C" not in drawn.split("What Guests Say")[0],
            detail="a figure in the markup with nothing cached behind it")

    s.section("A fresh reading is shown")
    _set(conn, m.json.dumps({"c": 14, "code": 61, "at": now.isoformat()}))
    wx = m.weather_now(conn)
    s.check("it comes back", wx is not None)
    s.check("with the temperature", wx and wx["c"] == 14)
    s.check("and the code read as words", wx and wx["words"] == "light rain",
            detail=str(wx["words"]) if wx else "")
    body = anon.get("/book").get_data(as_text=True)
    s.check("the page shows it", "14" in body and "light rain" in body)
    s.check("with a line about what it means",
            "Rain off the mountain" in body,
            detail="the number is the least useful part; what it means for "
                   "the drive and the dinner is the rest")

    s.section("A stale reading is not shown at all")
    # Older than three hours is not "right now". Telling somebody it is
    # fifteen degrees when that was yesterday afternoon is worse than saying
    # nothing, and it is the failure a cache invites.
    _set(conn, m.json.dumps(
        {"c": 30, "code": 0, "at": (now - timedelta(hours=5)).isoformat()}))
    s.check("nothing comes back", m.weather_now(conn) is None)
    body = anon.get("/book").get_data(as_text=True)
    s.check("and the box goes with it rather than standing empty",
            'class="g-wx"' not in body,
            detail="a stale reading must leave nothing behind, not an "
                   "outline where a figure used to be")
    s.check("and does not show the old figure", "30&deg;C" not in body)

    s.section("An hours-old reading says how old it is")
    _set(conn, m.json.dumps(
        {"c": 8, "code": 3, "at": (now - timedelta(hours=2, minutes=10)).isoformat()}))
    wx = m.weather_now(conn)
    s.check("it is still inside the window", wx is not None)
    s.check("and knows its age", wx and 125 <= wx["minutes_old"] <= 135,
            detail=str(wx["minutes_old"]) if wx else "")
    body = anon.get("/book").get_data(as_text=True)
    s.check("the page says how old", "as of 2 hours ago" in body,
            detail="presenting a two-hour-old reading as 'right now' is a "
                   "small lie, and a small lie about the weather is how a "
                   "page stops being believed about anything else")

    s.section("And a fresh one does not")
    _set(conn, m.json.dumps(
        {"c": 8, "code": 3, "at": (now - timedelta(minutes=12)).isoformat()}))
    body = anon.get("/book").get_data(as_text=True)
    # Scoped to the weather block. "as of" is an ordinary English phrase and
    # a whole-page search for it is a search of the whole site's copy.
    at = body.find("g-wx")
    block = body[at:body.find("</div>", at)] if at >= 0 else ""
    s.check("the block is there to check", bool(block.strip()))
    s.check("no age on a twelve-minute-old reading", "as of" not in block,
            detail="furniture on every page load")

    s.section("Rubbish in the cache is not a broken page")
    for junk in ("", "not json at all", '{"c": 12}', '{"at": "never"}'):
        _set(conn, junk)
        s.check(f"{junk[:22]!r} reads as no answer",
                m.weather_now(conn) is None)
        s.check("and the page still opens",
                anon.get("/book").status_code == 200)

    s.section("The job writes what the page reads")
    # Through the real job, with the fetch stood in for -- which is the only
    # way to know the two halves agree about the shape.
    real = m.fetch_weather
    m.fetch_weather = lambda *a, **k: {"c": -3, "code": 73,
                                       "at": now.isoformat()}
    try:
        out = m.run_weather_job(conn)
    finally:
        m.fetch_weather = real
    s.check("it says what it wrote", "-3" in out, detail=out)
    wx = m.weather_now(conn)
    s.check("and the page's reader can read it back", wx and wx["c"] == -3,
            detail="the job and the reader agreeing about the shape is the "
                   "only thing between a cache and an empty page")
    s.check("with the snow words", wx and wx["words"] == "snow",
            detail=str(wx["words"]) if wx else "")
    body = anon.get("/book").get_data(as_text=True)
    # "Fires lit in the salons" went on 1 October: the owner's facts say the
    # heating is limited and the house is cold in winter, so the line now
    # warns rather than reassures.
    s.check("and below freezing the page says so",
            "Frost on the valley" in body)

    s.section("What it means, not only the number")
    # At -3 the freezing line wins, so the snow line needs its own reading --
    # otherwise dropping it changes nothing any check can see. The number is
    # the least useful part of this block; what it means for the drive and
    # for dinner is the rest.
    for c, code, expect in ((2, 73, "Snow. The drive may want care."),
                            (31, 0, "cooler inside")):
        _set(conn, m.json.dumps({"c": c, "code": code, "at": now.isoformat()}))
        page = anon.get("/book").get_data(as_text=True)
        s.check(f"at {c}°C with code {code} it says what that means",
                expect in page, detail=expect)
    # A mild clear evening used to promise "Dinner will be outdoors." That is a
    # promise about La Table, which can now be closed for the season, so the
    # line went with the dining switch (1 October). Nothing may put it back.
    _set(conn, m.json.dumps({"c": 21, "code": 0, "at": now.isoformat()}))
    page = anon.get("/book").get_data(as_text=True)
    s.check("at 21°C with code 0 it promises no dinner outdoors",
            "Dinner will be outdoors" not in page)

    s.section("It is a registered job, so it can be turned off")
    names = {j[0] for j in m.AUTOMATION_JOBS}
    s.check("weather is in the registry", "weather" in names)
    s.check("with a switch of its own",
            "automation_weather_enabled" in m.AUTOMATION_SETTING_DEFAULTS,
            detail="ten of eighteen jobs once had no off switch")
    s.check("and a name on the job-status page",
            "weather" in m.AUTOMATION_JOB_LABELS)

    s.section("No third-party call is left in the markup")
    import io as _io
    import os as _os
    tpl = _io.open(_os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "templates", "_weather_live.html"),
        encoding="utf-8").read()
    # No CALL, rather than no mention. The comment at the top of the file
    # explains why the fetch was moved and names the service to do it, and a
    # check that forbids the name forbids the explanation.
    code = m.re.sub(r"\{#.*?#\}", "", tpl, flags=m.re.S)
    s.check("the template makes no request of its own",
            "fetch(" not in code and "api.open-meteo" not in code
            and "<script" not in code,
            detail="the sketch fetched it from the guest's own browser, "
                   "handing their address to a third party")
    s.check("and the page a guest loads names no third party",
            "open-meteo" not in anon.get("/book").get_data(as_text=True).lower())

    # ------------------------------------------------------------------
    # WHAT IT WILL BE LIKE WHEN THEY COME. Asked for on 7 October: weather
    # tied to the dates a guest is choosing, and the current reading
    # somewhere. The month writing already existed and was unreachable --
    # twelve paragraphs in a <script> on one page, invisible without a script
    # and never translated.
    # ------------------------------------------------------------------
    s.section("The month a guest is actually booking")
    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if not room:
        s.check("a room exists to price", False,
                detail="reported rather than skipped")
    else:
        rid = room["id"]

        def room_page(q=""):
            return anon.get("/book/%d%s" % (rid, q)).get_data(as_text=True)

        # Read with the scripts taken out. The whole point of moving this out
        # of _weather.html was that it used to exist only for a browser that
        # ran it.
        import re as _re
        def markup(q=""):
            return _re.sub(r"(?is)<script.*?</script>", "", room_page(q))

        plain = markup()
        s.check("the block is on the room page", "What to expect" in plain,
                detail="the page where somebody is choosing dates")
        s.check("and it is in the markup, not built by a script",
                "What to expect" in plain,
                detail="twelve paragraphs that only exist once javascript "
                       "runs are twelve paragraphs some guests never read")

        # With no dates it still answers, for the month it is now. A block
        # that is blank until somebody picks dates is a block most visitors
        # never see.
        this_month = m.MONTHS_AT_GUDANES[m.house_today().month][0]
        s.check("with no dates chosen it answers for this month",
                this_month[:40] in plain,
                detail="expected the writing for month %d"
                       % m.house_today().month)

        aug = markup("?arrival=2027-08-02&departure=2027-08-05")
        s.check("an August stay reads as August",
                m.MONTHS_AT_GUDANES[8][0][:40] in aug)
        s.check("and not as whatever month it is today",
                m.house_today().month == 8
                or this_month[:40] not in aug,
                detail="the dates have to change the answer, or they are "
                       "ornament")

        # A week across the end of a month is an ordinary booking, and late
        # June is not early July.
        both = markup("?arrival=2027-06-27&departure=2027-07-02")
        s.check("a stay crossing a month gets both months",
                m.MONTHS_AT_GUDANES[6][0][:40] in both
                and m.MONTHS_AT_GUDANES[7][0][:40] in both,
                detail="June and July are different answers")

        s.section("The live reading has somewhere to live now")
        # Its own fallback was removed on 5 October because the sentence sat
        # alone between two sections. Inside this block there is nothing to
        # orphan: the month's writing stands either way.
        _clear(conn)
        empty = markup()
        s.check("with no reading the block still says something true",
                "What to expect" in empty and this_month[:40] in empty,
                detail="this is the failure the stand-in sentence was deleted "
                       "for -- a part of the page that simply vanishes")
        s.check("and offers no temperature it does not have",
                "&deg;C" not in empty.split("What to expect")[-1][:900],
                detail="no reading must mean no figure, not a blank one")

        _set(conn, m.json.dumps(
            {"c": 17, "code": 0,
             "at": m.datetime.now(m.timezone.utc).isoformat()}))
        live = markup()
        s.check("and when there is a reading it is shown", "17&deg;C" in live,
                detail="the current reading the owner asked for")
        s.check("beside the month, not instead of it",
                this_month[:40] in live,
                detail="both answers at once is the whole point")

    if keep is None:
        _clear(conn)
    else:
        _set(conn, keep)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
