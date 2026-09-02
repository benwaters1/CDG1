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

    s.section("With nothing cached, the written line stands")
    _clear(conn)
    s.check("there is no reading", m.weather_now(conn) is None)
    body = anon.get("/book").get_data(as_text=True)
    s.check("the page still says something true",
            "Nine hundred metres up" in body,
            detail="an empty box is worse than a sentence")
    s.check("and offers no temperature", "&deg;C" not in body
            and "°C" not in body.split("What Guests Say")[0])

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
    s.check("the page falls back to the sentence",
            "Nine hundred metres up" in body)
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
    s.check("and below freezing the page says the fires are lit",
            "Fires lit in the salons" in body)

    s.section("What it means, not only the number")
    # At -3 the freezing line wins, so the snow line needs its own reading --
    # otherwise dropping it changes nothing any check can see. The number is
    # the least useful part of this block; what it means for the drive and
    # for dinner is the rest.
    for c, code, expect in ((2, 73, "Snow. The drive may want care."),
                            (21, 0, "Dinner will be outdoors."),
                            (31, 0, "cooler inside")):
        _set(conn, m.json.dumps({"c": c, "code": code, "at": now.isoformat()}))
        page = anon.get("/book").get_data(as_text=True)
        s.check(f"at {c}°C with code {code} it says what that means",
                expect in page, detail=expect)

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

    if keep is None:
        _clear(conn)
    else:
        _set(conn, keep)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
