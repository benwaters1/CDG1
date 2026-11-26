"""The weather on Stay the Night: what it is like now, and what their dates will be like.

The owner, 25 November 2026: "we can do this a bit better". Under the
"Typical in March" box on /book a loose line read "AT THE CHÂTEAU 8°C
overcast". That was the reading at that moment, but sitting under a box about
March it read as March's weather -- two different things drawn as one.

Now one card holds both, and says which is which:
  - "Right now at the château · 8°C, overcast · 13:40", the house's hourly
    reading with the house's time on it and its age when it is old;
  - for dates within a fortnight, "Forecast for your stay", one line a night
    from the forecast the house already fetches for the outdoor jobs;
    further out, "Typical in March" as before.

What this guards:
  - the forecast is chosen by the dates: inside fourteen days a line a night,
    beyond it the month's averages, and a stay longer than the forecast says
    "your first 3 nights" rather than passing a part for the whole;
  - the reading is inside the card, labelled as now, with a time; with no
    reading there is no "Right now" row, never an empty one;
  - nothing reaches the network: the forecast is read from the cache, and a
    forecast too old to stand behind is not shown;
  - the owner's outdoor-jobs page still shows eight days, though the house now
    fetches sixteen for the guests;
  - the room page keeps its plain box (owner, 9 Nov: no live weather there);
  - and, in a browser, the page's own script redraws the card when the dates
    in the bar change -- the same nights, words and notes the server writes --
    and the card fits a 320px phone.
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# The harness first: it pins the clock to its test day, and the datetime taken
# from the stdlib after it is the pinned one, so this suite and the app agree
# about what day and what hour it is however the suite is run.
import _harness
from _harness import Suite, clients, db

from datetime import datetime as real_datetime, time as real_time, timedelta, timezone  # noqa: E402

m = _harness.m

KEYS = (m.FORECAST_SETTING, m.WEATHER_SETTING)


def _put(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def forecast_hours(first_day, weather, days=16):
    """Hour by hour from midnight at the house on first_day, as the cache keeps
    them: [UTC moment, °C, gust km/h, code]. Stepped in UTC, so a clock-change
    night has the hours it really has."""
    tz = m.LOCAL_TZ
    out = []
    t = real_datetime.combine(first_day, real_time(0), tzinfo=tz).astimezone(timezone.utc)
    end = real_datetime.combine(first_day + timedelta(days=days), real_time(0),
                                tzinfo=tz).astimezone(timezone.utc)
    while t < end:
        temp, code = weather(t.astimezone(tz))
        out.append([t.isoformat(), temp, 10, code])
        t += timedelta(hours=1)
    return out


def house_weather(today):
    """A fortnight the checks can predict. Day i (from today) peaks at 6 + i °C
    at two in the afternoon. An hour of valley fog every morning at eight, which
    must not name the day. Rain at midday on day 1; snow in the morning on
    day 2, and a frost that night, coldest at five on day 3. Otherwise 2°C at
    five in the morning and an overcast sky."""
    def weather(local):
        i = (local.date() - today).days
        if 8 <= local.hour < 20:
            temp = 4.0 + i + (2.0 if local.hour == 14 else 0.0)
            code = 45 if local.hour == 8 else 3
            if i == 1 and 10 <= local.hour <= 13:
                code = 63
            if i == 2 and 9 <= local.hour <= 11:
                code = 73
            return temp, code
        frosty = (i == 2 and local.hour >= 20) or (i == 3 and local.hour < 8)
        if local.hour == 5:
            return (-2.0 if frosty else 2.0), 0
        return (0.0 if frosty else 3.0), 0
    return weather


def put_forecast(conn, today, fetched):
    _put(conn, m.FORECAST_SETTING, json.dumps({
        "at": fetched.astimezone(timezone.utc).isoformat(),
        "hours": forecast_hours(today, house_weather(today))}))


def put_reading(conn, c, code, at):
    _put(conn, m.WEATHER_SETTING, json.dumps({"c": c, "code": code,
                                              "at": at.astimezone(timezone.utc).isoformat()}))


def card_of(body):
    """The weather card, from its opening tag to the nights kept for the script."""
    start = body.find('class="g-typwx g-typwx--card"')
    if start < 0:
        return ""
    start = body.rfind("<div", 0, start)
    end = body.find("<template data-typwx-nights>", start)
    return body[start:end if end > 0 else len(body)]


def dated_row(body):
    """The card's first row: the forecast or, left empty for the script, the month."""
    card = card_of(body)
    mo = re.search(r'<div class="g-typwx__row" data-typwx[^>]*>(.*?)</div>', card, re.S)
    return mo.group(1).strip() if mo else None


def text(fragment):
    """The words, as read: tags gone, spaces collapsed, no space left before a comma
    where a tag closed ("<b>8°C</b>, overcast")."""
    flat = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()
    return re.sub(r" ,", ",", flat)


def labels(start, nights):
    """What the night lines are headed: the month on the first and on the first of
    a new month -- 'Fri 28 Nov', 'Sat 29', 'Mon 1 Dec'."""
    out, prev = [], None
    for i in range(nights):
        day = start + timedelta(days=i)
        dow, mon = m.WHATS_ON_DAYS[day.weekday()][:3], m.MONTH_NAMES[day.month - 1][:3]
        out.append(f"{dow} {day.day}" + (f" {mon}" if prev is None or prev.month != day.month else ""))
        prev = day
    return out


def run():
    s = Suite("the weather on Stay the Night")
    conn = db()
    kept = {k: (conn.execute("SELECT value FROM app_settings WHERE key = ?", (k,)).fetchone()
                or {"value": None})["value"] for k in KEYS}
    anon = m.app.test_client()
    now = real_datetime.now(timezone.utc)
    today = m.house_today()
    day = lambda n: today + timedelta(days=n)  # noqa: E731
    iso = lambda n: day(n).isoformat()  # noqa: E731
    try:
        _checks(s, conn, anon, now, today, day, iso)
        _browser(s, conn, anon, now, today, day, iso)
    finally:
        for k, v in kept.items():
            _put(conn, k, v)
        conn.close()
    return s


def _checks(s, conn, anon, now, today, day, iso):
    s.section("Nothing here reaches the network")
    s.check("the forecast fetch is stood down under test",
            m.fetch_forecast.__name__ == "_blocked",
            detail="_harness.py refuses it; a page that called it would raise here")
    import inspect
    for fn in (m.forecast_nights, m.stay_forecast):
        src = inspect.getsource(fn)
        s.check(f"{fn.__name__} reads the cache and fetches nothing",
                "fetch_forecast" not in src and "urlopen" not in src)
    s.check("the house fetches far enough ahead for a fortnight's arrivals",
            m.FORECAST_DAYS >= m.STAY_FORECAST_AHEAD_DAYS + 2,
            detail=f"{m.FORECAST_DAYS} days fetched, arrivals up to "
                   f"{m.STAY_FORECAST_AHEAD_DAYS} days off shown a forecast")

    s.section("A day's one word is what the sky did, not its worst hour")
    fog_morning = [45] + [3] * 11
    s.check("an hour of valley fog does not call an overcast day fog",
            m.forecast_day_code(fog_morning) == 3,
            detail=str(m.forecast_day_code(fog_morning)))
    s.check("one passing hour of drizzle does not either",
            m.forecast_day_code([3] * 11 + [51]) == 3)
    s.check("two hours of rain make it a day of rain",
            m.forecast_day_code([3] * 10 + [61, 63]) == 63)
    s.check("snow beats rain on the same day: it is the one that decides the drive",
            m.forecast_day_code([3] * 8 + [63, 63, 71, 71]) == 71)
    s.check("a single hour of thunder names the day",
            m.forecast_day_code([0] * 11 + [95]) == 95)

    s.section("The nights, read from the cached forecast")
    put_forecast(conn, today, now)
    nights = m.forecast_nights(conn)
    n0, n1, n2 = nights.get(day(0)), nights.get(day(1)), nights.get(day(2))
    s.check("tonight is there", bool(n0), detail=str(sorted(nights)[:3]))
    if n0 and n1 and n2:
        s.check("the high is the afternoon's", n0["high"] == "6°C" and n1["high"] == "7°C",
                detail=f"{n0['high']} / {n1['high']}")
        s.check("the low is the night after, in the small hours", n0["low"] == "2°C",
                detail=str(n0["low"]))
        s.check("an overcast day with a foggy hour reads overcast", n0["words"] == "overcast",
                detail=n0["words"])
        s.check("the rainy day reads rain", n1["words"] == "rain", detail=n1["words"])
        s.check("the snowy day reads snow, and is marked for the drive",
                n2["words"] == "snow" and n2["snow"], detail=n2["words"])
        s.check("its frost night reads below zero, with a real minus sign",
                n2["low"] == "\u22122°C" and n2["frost"], detail=str(n2["low"]))
    last = nights.get(day(15))
    s.check("the forecast's last day is there", bool(last))
    s.check("but with no low: half a night is warmer than the night",
            last and last["low"] is None, detail=str(last))
    s.check("and nothing past the end of the forecast", day(16) not in nights)

    s.section("A stay's nights: within a fortnight a line a night, beyond it nothing")
    st = m.stay_forecast(conn, day(1), day(4))
    s.check("three nights from tomorrow are three lines",
            st and st["shown"] == 3 and st["total"] == 3 and len(st["rows"]) == 3, detail=str(st))
    s.check("in order, from the first night",
            st and [r["iso"] for r in st["rows"]] == [day(1).isoformat(), day(2).isoformat(),
                                                       day(3).isoformat()])
    s.check("with the frost and the snow to say something about",
            st and st["notes"] == ["frost", "snow"], detail=str(st and st["notes"]))
    s.check("an arrival today counts", bool(m.stay_forecast(conn, day(0), day(1))))
    s.check("fourteen days off still has its forecast",
            bool(m.stay_forecast(conn, day(14), day(15))))
    s.check("fifteen days off is the month's averages",
            m.stay_forecast(conn, day(15), day(16)) is None,
            detail="beyond a fortnight a forecast is a guess dressed as a fact")
    s.check("a past arrival has none", m.stay_forecast(conn, day(-1), day(1)) is None)
    s.check("nor do dates the wrong way round", m.stay_forecast(conn, day(3), day(2)) is None)
    long = m.stay_forecast(conn, day(2), day(12))
    s.check("a ten-night stay is shown its first seven nights",
            long and long["shown"] == 7 and long["total"] == 10, detail=str(long and long["shown"]))
    edge = m.stay_forecast(conn, day(14), day(18))
    s.check("a stay running past the forecast stops where it stops",
            edge and edge["shown"] == 2 and edge["total"] == 4,
            detail=str(edge and [r["iso"] for r in edge["rows"]]))
    synthetic = {d: {"iso": d.isoformat(), "frost": False, "snow": False, "hot": False}
                 for d in (real_datetime(2027, 1, 30).date() + timedelta(days=i) for i in range(4))}
    cross = m.stay_forecast(conn, real_datetime(2027, 1, 30).date(), real_datetime(2027, 2, 3).date(),
                            today=real_datetime(2027, 1, 29).date(), nights=synthetic)
    s.check("the month is printed on the first night and the first of a new month",
            cross and [r["show_month"] for r in cross["rows"]] == [True, False, True, False],
            detail=str(cross and [r["show_month"] for r in cross["rows"]]))

    s.section("A forecast too old to stand behind is not shown")
    put_forecast(conn, today, now - timedelta(hours=m.FORECAST_FRESH_HOURS + 1))
    s.check("no nights come back", m.forecast_nights(conn) == {})
    body = anon.get(f"/book?arrival={iso(1)}&departure={iso(3)}").get_data(as_text=True)
    s.check("and the page falls back to the month's averages",
            "Forecast for your" not in text(card_of(body)) and dated_row(body) == "",
            detail=text(card_of(body))[:120])
    _put(conn, m.FORECAST_SETTING, "not json at all")
    s.check("rubbish in the cache reads as no forecast", m.forecast_nights(conn) == {})
    s.check("and the page still opens",
            anon.get(f"/book?arrival={iso(1)}&departure={iso(3)}").status_code == 200)

    s.section("On /book, dates within a fortnight are shown their forecast")
    put_forecast(conn, today, now)
    put_reading(conn, 8, 3, now - timedelta(minutes=10))
    r = anon.get(f"/book?arrival={iso(1)}&departure={iso(4)}")
    body = r.get_data(as_text=True)
    row = dated_row(body)
    s.check("the page opens", r.status_code == 200, r)
    s.check("the card says 'Forecast for your stay'",
            row is not None and "Forecast for your stay" in text(row), detail=text(row)[:160])
    s.check("with one line a night", row is not None and row.count('class="g-typwx__night"') == 3,
            detail=str(row and row.count('class="g-typwx__night"')))
    want = labels(day(1), 3)
    got = [text(x) for x in re.findall(r'<span class="g-typwx__d">(.*?)</span> <span', row or "")]
    s.check("each headed by its night, the month printed once", got == want,
            detail=f"{got} wanted {want}")
    s.check("each with the day's high, the night's low and the words",
            row is not None and "7°C by day, 2°C at night, rain" in text(row)
            and "8°C by day, \u22122°C at night, snow" in text(row), detail=text(row))
    s.check("and the lines a guest needs about frost and the drive",
            row is not None and "Frost is forecast at night" in text(row)
            and "The drive may want care" in text(row))
    s.check("the script is told these dates are drawn, so it leaves them be",
            f'data-drawn="{iso(1)}|{iso(4)}"' in card_of(body))
    s.check("'Typical in' is not drawn over it by the page",
            "Typical in" not in text(row or ""))

    long_page = anon.get(f"/book?arrival={iso(2)}&departure={iso(12)}").get_data(as_text=True)
    s.check("a ten-night stay is headed 'Forecast for your first 7 nights'",
            "Forecast for your first 7 nights" in text(dated_row(long_page) or ""),
            detail=text(dated_row(long_page) or "")[:80])
    edge_page = anon.get(f"/book?arrival={iso(14)}&departure={iso(18)}").get_data(as_text=True)
    s.check("and one running past the forecast says how many it has",
            "Forecast for your first 2 nights" in text(dated_row(edge_page) or ""),
            detail=text(dated_row(edge_page) or "")[:80])

    s.section("Beyond a fortnight, the month's averages as before")
    far = anon.get(f"/book?arrival={iso(30)}&departure={iso(33)}").get_data(as_text=True)
    s.check("no forecast is drawn", "Forecast for your" not in text(card_of(far)),
            detail=text(card_of(far))[:120])
    s.check("the row is left for the script's 'Typical in'",
            dated_row(far) == "" and "Typical in" in far and "data-drawn" not in card_of(far))
    nodates = anon.get("/book").get_data(as_text=True)
    s.check("and with no dates at all, the same", dated_row(nodates) == ""
            and "Forecast for your" not in text(card_of(nodates)))

    s.section("'Right now at the château' sits inside the card, with a time")
    reading_at = now - timedelta(minutes=10)
    house_time = reading_at.astimezone(m.LOCAL_TZ).strftime("%H:%M")
    card = card_of(body)
    s.check("the card holds the reading, labelled as now",
            "Right now at the château" in text(card), detail=text(card)[-160:])
    s.check("with the temperature and the words",
            "8°C, overcast" in text(card), detail=text(card)[-120:])
    s.check("and the house's time it was taken", house_time in text(card),
            detail=f"wanted {house_time}: {text(card)[-80:]}")
    s.check("after the dates' weather, not mixed into it",
            card.find("Right now at the château") > card.find("Forecast for your stay") >= 0)
    s.check("the loose line under the box is gone", 'class="g-wx"' not in body,
            detail="it read as the month's weather")
    s.check("and the reading is on the page once",
            text(body).count("Right now at the château") == 1 and text(body).count("8°C, overcast") == 1)
    s.check("a fresh reading carries no age", "as of" not in text(card))
    put_reading(conn, 8, 3, now - timedelta(hours=2, minutes=10))
    old = card_of(anon.get(f"/book?arrival={iso(1)}&departure={iso(4)}").get_data(as_text=True))
    s.check("one two hours old says so", "as of 2 hours ago" in text(old), detail=text(old)[-120:])
    put_reading(conn, -3, 73, now)
    cold = card_of(anon.get("/book").get_data(as_text=True))
    s.check("and keeps its line about the frost", "Frost on the valley" in text(cold))
    s.check("a reading with no dates shows the card, the month left to the script",
            "Right now at the château" in text(cold) and " hidden" not in cold.split(">")[0])

    s.section("No reading, no 'Right now' row")
    _put(conn, m.WEATHER_SETTING, None)
    bare = anon.get(f"/book?arrival={iso(1)}&departure={iso(4)}").get_data(as_text=True)
    s.check("no 'Right now' row", "Right now at the château" not in bare
            and "g-typwx__now" not in bare)
    s.check("the forecast stands alone", "Forecast for your stay" in text(card_of(bare)))
    put_reading(conn, 30, 0, now - timedelta(hours=5))
    stale = anon.get("/book").get_data(as_text=True)
    s.check("a stale reading is no reading either", "Right now at the château" not in stale
            and "30&deg;C" not in stale and "30°C" not in text(card_of(stale)))
    _put(conn, m.WEATHER_SETTING, None)
    _put(conn, m.FORECAST_SETTING, None)
    neither = anon.get("/book").get_data(as_text=True)
    head = card_of(neither).split(">")[0]
    s.check("with neither, the card waits hidden for the month's averages",
            head.endswith(" hidden") and "°C" not in text(card_of(neither)),
            detail=head[-80:])

    s.section("The room page keeps its plain box")
    room = _harness.ensure_room()
    put_forecast(conn, today, now)
    put_reading(conn, 17, 0, now)
    page = anon.get(f"/book/{room['id']}?arrival={iso(1)}&departure={iso(3)}").get_data(as_text=True)
    s.check("no card, no reading and no forecast beside the booking form",
            "g-typwx--card" not in page and "Right now at the château" not in page
            and "Forecast for your" not in page,
            detail="owner, 9 Nov: no live weather on the room pages")
    s.check("but the month's box is still there", 'class="g-typwx" data-typwx' in page)

    s.section("The owner's outdoor-jobs page still reads eight days")
    oc, _ec, _owner, _emp = clients()
    jobs = oc.get("/management/weather-jobs")
    jpage = jobs.get_data(as_text=True)
    s.check("it opens", jobs.status_code == 200, jobs)
    s.check("the eighth day ahead is on it", m.weather_day_words(day(7)) in jpage)
    s.check("the ninth is not, though the house now fetches sixteen",
            m.weather_day_words(day(8)) not in jpage and m.weather_day_words(day(12)) not in jpage,
            detail="the extra days are for the guests' dates, not the jobs")


# ---------------------------------------------------------------------------
# IN A BROWSER: the script's half. The server draws the card for the dates it
# was asked about; when the guest changes the dates in the bar, the page's own
# script chooses nights from the ones the server wrote into a <template>. The
# choosing is written twice (stay_forecast and the script), so it is checked
# here against the server's own answer for the same dates.
# ---------------------------------------------------------------------------

PROBE = """<script>
(function () {
  function go() {
    var out = {w: +(/w=(\\d+)/.exec(location.hash) || [0, 0])[1]};
    try {
      var card = document.querySelector('.g-typwx--card'), row = card.querySelector('[data-typwx]');
      var a = document.getElementById('s_arrival'), d = document.getElementById('s_departure');
      var root = document.documentElement, r = card.getBoundingClientRect();
      out.vw = root.clientWidth; out.page_w = root.scrollWidth;
      out.card_l = r.left; out.card_r = r.right; out.card_over = card.scrollWidth - card.clientWidth;
      /* the words a guest is shown: textContent less anything hidden. Not innerText, which hands back the label in
         the capitals the stylesheet draws it in. */
      function shown(node) {
        var c = node.cloneNode(true);
        [].forEach.call(c.querySelectorAll('[hidden]'), function (h) { h.parentNode.removeChild(h); });
        return c.textContent;
      }
      function look() {
        var days = [].map.call(row.querySelectorAll('.g-typwx__night .g-typwx__d'), function (e) { return shown(e).trim(); });
        return {text: shown(row).trim(), nights: days.length, days: days, hidden: row.hidden, card_hidden: card.hidden};
      }
      function set(x, y) {
        a.value = x; d.value = y;
        a.dispatchEvent(new Event('change')); d.dispatchEvent(new Event('change'));
        return look();
      }
      out.first = look();
      out.now = (card.querySelector('.g-typwx__now') || {textContent: ''}).textContent;
      out.far = set(%(far_a)s, %(far_d)s);
      out.near = set(%(near_a)s, %(near_d)s);
      out.back = set(%(orig_a)s, %(orig_d)s);
    } catch (e) { out.error = String(e && e.stack || e); }
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 0); });
})();
</script>"""

WRAPPER = """<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<script>
window.addEventListener('message', function (e) {
  var pre = document.createElement('pre'); pre.className = 'r'; pre.textContent = e.data;
  document.body.appendChild(pre);
});
</script>
%s
</body></html>"""


def _standalone(page, probe):
    """The page with the site's own stylesheets put inline and no web fonts, so it
    lays out with exactly the CSS the site serves and touches no network."""
    def inline(match):
        tag = match.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        if not href or "stylesheet" not in tag:
            return tag
        url = href.group(1)
        if "fonts.googleapis" in url:
            return ""
        if url.startswith("/static/"):
            path = os.path.join(_harness.ROOT, "static", url[len("/static/"):].split("?")[0])
            with open(path, encoding="utf-8") as fh:
                return "<style>\n" + fh.read() + "\n</style>"
        return tag
    page = re.sub(r"<link\b[^>]*>", inline, page)
    return page.replace("</body>", probe + "</body>", 1)


def _browser(s, conn, anon, now, today, day, iso):
    s.section("In a browser: the bar's dates redraw the card, and it fits a phone")
    from test_staff_header_on_a_phone import find_browser
    chrome = find_browser()
    if not s.check("Chrome is on this machine to run the page's own script", chrome,
                   detail="install Chrome or Chromium, or set GUDANES_CHROME to one "
                          "-- a check that could not run is not a pass"):
        return
    put_forecast(conn, today, now)
    put_reading(conn, 8, 3, now)
    orig, near, far = (iso(5), iso(7)), (iso(1), iso(4)), (iso(30), iso(33))
    page = anon.get(f"/book?arrival={orig[0]}&departure={orig[1]}").get_data(as_text=True)
    server_near = text(dated_row(anon.get(f"/book?arrival={near[0]}&departure={near[1]}")
                                 .get_data(as_text=True)) or "")
    server_orig = text(dated_row(page) or "")
    probe = PROBE % {"far_a": json.dumps(far[0]), "far_d": json.dumps(far[1]),
                     "near_a": json.dumps(near[0]), "near_d": json.dumps(near[1]),
                     "orig_a": json.dumps(orig[0]), "orig_d": json.dumps(orig[1])}
    work = tempfile.mkdtemp(prefix="gudanes-wx-")
    try:
        with open(os.path.join(work, "book.html"), "w", encoding="utf-8") as fh:
            fh.write(_standalone(page, probe))
        widths = (320, 375)
        frames = "\n".join(
            f'<iframe src="book.html#w={w}" style="display:block;border:0;width:{w}px;height:900px"></iframe>'
            for w in widths)
        wrapper = os.path.join(work, "phones.html")
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write(WRAPPER % frames)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               "--user-data-dir=" + os.path.join(work, "profile"), "--hide-scrollbars",
               "--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
               "primaryHoverType=1,availableHoverTypes=1",
               "--host-resolver-rules=MAP * ~NOTFOUND",
               "--virtual-time-budget=30000", "--dump-dom",
               "file:///" + wrapper.replace("\\", "/").lstrip("/")]
        if sys.platform.startswith("linux"):
            cmd.insert(1, "--no-sandbox")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
        seen = {}
        for blob in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S):
            d = json.loads(htmllib.unescape(blob))
            seen[d.get("w")] = d
    finally:
        shutil.rmtree(work, ignore_errors=True)

    flat = lambda t: re.sub(r"\s+", "", t or "")  # noqa: E731
    for w in (320, 375):
        out = seen.get(w)
        if not s.check(f"{w}px: the page ran its script", out and not out.get("error"),
                       detail=str(out)[:300] if out else (proc.stderr or "")[-300:]):
            continue
        s.check(f"{w}px: the card fits the screen",
                out["card_l"] >= 0 and out["card_r"] <= out["vw"] + 0.5 and out["card_over"] <= 0,
                detail=f"card {out['card_l']}-{out['card_r']} in {out['vw']}, "
                       f"{out['card_over']}px of it hidden")
        s.check(f"{w}px: and the page does not scroll sideways", out["page_w"] <= out["vw"],
                detail=f"{out['page_w']}px of page in a {out['vw']}px screen")
        first = out["first"]
        s.check(f"{w}px: on load the script leaves the server's forecast as it was",
                not first["hidden"] and flat(first["text"]) == flat(server_orig)
                and "Forecast for your stay" in first["text"],
                detail=f"{first['text'][:120]!r} vs {server_orig[:120]!r}")
        s.check(f"{w}px: 'Right now' is in the card with its time",
                "Right now at the château" in out["now"] and re.search(r"\d\d:\d\d", out["now"]),
                detail=out["now"])
        s.check(f"{w}px: dates beyond a fortnight redraw it as 'Typical in'",
                out["far"]["text"].startswith("Typical in") and out["far"]["nights"] == 0,
                detail=out["far"]["text"][:80])
        s.check(f"{w}px: dates within one redraw the forecast the server would draw",
                out["near"]["nights"] == 3 and flat(out["near"]["text"]) == flat(server_near),
                detail=f"{out['near']['text'][:160]!r} vs {server_near[:160]!r}")
        s.check(f"{w}px: headed by the nights, the month once",
                out["near"]["days"] == labels(day(1), 3),
                detail=f"{out['near']['days']} wanted {labels(day(1), 3)}")
        s.check(f"{w}px: and back on the searched dates, the server's card again",
                flat(out["back"]["text"]) == flat(server_orig), detail=out["back"]["text"][:120])


if __name__ == "__main__":
    print(run().report())
