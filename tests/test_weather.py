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
    # 9 Nov 2026, evening (the owner's second review of the site, 5): "Remove the live weather and the
    # month card from room pages" -- "16 degrees, overcast" and "Cold evenings in the house" beside the
    # booking form, where the owner does not want winter or the cold to be prominent. The reading stays
    # on /book, under the dates, and on the Estate and In the Ariège below. _expected_weather.html, which
    # drew the room pages' block, went with it; MONTHS_AT_GUDANES keeps the owner's month writing.
    s.section("The room pages carry no weather and no month card")
    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if not room:
        s.check("a room exists to look at", False, detail="reported rather than skipped")
    else:
        _set(conn, m.json.dumps(
            {"c": 17, "code": 0, "at": m.datetime.now(m.timezone.utc).isoformat()}))
        page = anon.get("/book/%d" % room["id"]).get_data(as_text=True)
        this_month = m.MONTHS_AT_GUDANES[m.house_today().month][0]
        s.check("a fresh reading in the cache does not reach a room page",
                "17&deg;C" not in page and 'class="g-wx"' not in page)
        s.check("nor does the month's card", "What to expect" not in page and this_month[:40] not in page)
        aug = anon.get("/book/%d?arrival=2027-08-02&departure=2027-08-05" % room["id"]).get_data(as_text=True)
        s.check("with dates chosen either", m.MONTHS_AT_GUDANES[8][0][:40] not in aug)

    # 9 October (owner): the Estate and In the Ariège show the reading as
    # Stay does. Both templates call the partial; until that day neither route
    # passed it anything, so the partial drew nothing and nothing said so.
    s.section("The Estate and In the Ariège show it too")
    for path, name in (("/facilities", "the Estate"), ("/whats-on", "In the Ariège")):
        _set(conn, m.json.dumps({"c": 21, "code": 61, "at": now.isoformat()}))
        r = anon.get(path)
        body = r.get_data(as_text=True)
        s.check(f"{name} shows a fresh reading",
                r.status_code == 200 and 'class="g-wx"' in body and "light rain" in body,
                detail=f"{path} {r.status_code}")
        _set(conn, m.json.dumps(
            {"c": 30, "code": 0, "at": (now - timedelta(hours=5)).isoformat()}))
        body = anon.get(path).get_data(as_text=True)
        s.check(f"and on {name} a stale one leaves no box behind",
                'class="g-wx"' not in body and "30&deg;C" not in body)

    if keep is None:
        _clear(conn)
    else:
        _set(conn, keep)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
