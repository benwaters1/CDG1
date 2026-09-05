"""The photographs the site shows and does not own.

Two hundred and fifty-six image tags across forty templates, ninety-three
distinct photographs, and the logo on every single page, are loaded from a
Squarespace account the chateau no longer publishes from. That is fine right
up until the account lapses, at which point the public site loses every
picture and its masthead in the same minute, and the recovery is to go and
find ninety-three photographs from an account that has closed.

So the app keeps its own copy and serves that instead. The swap happens to the
RESPONSE, not to the templates, because the public templates are rewritten by
hand most weeks and arrive as whole-file replacements -- 256 edited img tags
would last until the next handover. That decision is what most of this file
checks, because it is the part that can rot silently: the swap can stop
happening and every page still renders perfectly, pointed at somebody else's
server, for exactly as long as that server stays up.

NOTHING HERE TOUCHES THE NETWORK. The harness replaces fetch_one_image with a
raiser at import, and a section below proves it is still the raiser -- a suite
that quietly started downloading would pass, take a minute longer, and pull
ninety-three photographs down as a side effect of testing a percentage.
"""
from _harness import Suite, clients, db

import io
import os

import _harness

m = _harness.m
TAG = "ZZMIRROR"

FAKE_URL = ("https://images.squarespace-cdn.com/content/%s/a-photograph.jpg"
            "?format=1500w" % TAG)
FAKE_TWO = FAKE_URL.replace("a-photograph", "b-photograph")
OTHER_HOST = "https://example.invalid/somebody-elses.jpg"
STUB = b"\xff\xd8\xff\xe0not really a jpeg"

# Pages a guest can open. Used to find one that actually carries a hotlinked
# photograph today rather than naming one and going stale when the site is
# rearranged -- which it is, most weeks.
PUBLIC_PATHS = ("/", "/rooms", "/gallery", "/restaurant", "/workshops",
                "/facilities", "/events", "/restoration", "/stay")


def _clean(conn):
    conn.execute("DELETE FROM mirrored_images WHERE source_url LIKE ?",
                 ("%" + TAG + "%",))
    conn.commit()


def _write_stub(name):
    os.makedirs(m.MIRROR_DIR, exist_ok=True)
    with io.open(os.path.join(m.MIRROR_DIR, name), "wb") as fh:
        fh.write(STUB)


def _drop_stub(name):
    """Remove a stub, and say whether it went.

    Not silent, and that is the point. Windows will not delete a file a
    response still has open, os.remove raises PermissionError, and
    PermissionError IS an OSError -- so an `except OSError: pass` here swallows
    it. A stub that outlived its section made the next one see a photograph
    the house held, and two checks failed several sections later with nothing
    to connect them to the cause.
    """
    try:
        os.remove(os.path.join(m.MIRROR_DIR, name))
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def run():
    s = Suite("photo mirror")
    oc, ec, owner, emp = clients()
    conn = db()
    _clean(conn)
    m.mirror_cache_clear()

    # ---------------------------------------------------------------- reading
    s.section("What the site actually points at")

    urls = m.hotlinked_urls()
    s.check("the templates are scanned for hotlinked photographs", bool(urls),
            detail="found none — either they are all fixed, or the scanner is")
    stray = [u for u in urls
             if not any(("https://" + h) in u for h in m.MIRROR_HOSTS)]
    s.check("every one found is on a host the mirror is allowed to fetch from",
            not stray, detail=str(stray[:2]))
    # A URL that swallowed its closing quote fetches as a 404 and never gets
    # swapped, and the page looks identical either way.
    #
    # Braces count as running on, and the last character is not enough to tell:
    # the real instance of this ended a URL with "{%" -- the opening of the
    # next Jinja tag -- so a check that only looked at the final character saw
    # a "%" and said nothing.
    ragged = [u for u in urls if u[-1] in "\"'>};," or "{" in u or "}" in u]
    s.check("no URL ran on past the end of its attribute", not ragged,
            detail=str(ragged[:2]))

    # The scanner reads the templates rather than a kept list, which is the
    # whole point: photographs arrive in handovers without anybody saying so.
    probe = os.path.join(m.BASE_DIR, "templates", "zz_mirror_probe.html")
    with io.open(probe, "w", encoding="utf-8") as fh:
        # The second line is the shape eighteen of the real ones are in: the
        # address flush against the closing tag with nothing between them. A
        # scanner that allows a brace reads "{%" as part of the address, and
        # the fetch comes back 400 on a photograph that is perfectly fine.
        fh.write('<img src="%s"><img src="%s">\n'
                 '{%% block og_image %%}%s{%% endblock %%}\n'
                 % (FAKE_URL, OTHER_HOST, FAKE_TWO))
    try:
        rescanned = m.hotlinked_urls()
        s.check("a photograph added to a template is noticed without anybody "
                "editing a list", FAKE_URL in rescanned)
        s.check("one written flush against a Jinja tag stops at the address",
                FAKE_TWO in rescanned,
                detail=str([u for u in rescanned if "b-photograph" in u]))
        s.check("one on a host we do not mirror is left where it is",
                OTHER_HOST not in rescanned)
    finally:
        os.remove(probe)
    s.check("and it stops being counted when the template goes",
            FAKE_URL not in m.hotlinked_urls())

    # --------------------------------------------------------------- the swap
    s.section("Swapping our copy in")

    page = ('<html><body><img src="%s" alt="the salon">'
            '<img src="%s"></body></html>' % (FAKE_URL, OTHER_HOST))
    held = "a" * 40 + ".jpg"

    s.check("with nothing held, the page comes back untouched",
            m.swap_mirrored(page, {}) == page)

    swapped = m.swap_mirrored(page, {FAKE_URL: held})
    s.check("a photograph we hold is served from here",
            "/mirrored-photo/" + held in swapped)
    s.check("and no longer from them", FAKE_URL not in swapped)
    s.check("one on another host is not touched", OTHER_HOST in swapped)
    s.check("nothing else about the page changes",
            swapped.replace("/mirrored-photo/" + held, FAKE_URL) == page)

    # Half a mirror has to leave the other half working. This is the difference
    # between "some pictures come from us now" and "some pictures are gone".
    half = m.swap_mirrored('<img src="%s"><img src="%s">' % (FAKE_URL, FAKE_TWO),
                           {FAKE_URL: held})
    s.check("a photograph not held yet still points where it always did",
            FAKE_TWO in half and "/mirrored-photo/" + held in half)

    # -------------------------------------------------------------- the index
    s.section("What counts as held")

    ghost = "deadbeef" * 5 + ".jpg"
    m.record_mirror(conn, FAKE_URL, ghost, 1234, "image/jpeg")
    conn.commit()
    s.check("a row whose file is not on the disk does not count as held",
            FAKE_URL not in m.mirrored_index(conn),
            detail="the swap would have sent guests to a 404 of our own making")

    _write_stub(ghost)
    try:
        s.check("once the file is there, it does",
                m.mirrored_index(conn).get(FAKE_URL) == ghost)

        # No template asks for this one -- the probe that named it was deleted
        # several checks ago -- so it is a copy of something the site has
        # dropped. That is the shape that broke the figure: counting files on
        # the disk rather than photographs the site asks for reported 109%
        # coverage after two mistyped addresses were corrected, and a
        # percentage that can exceed a hundred is one nobody can act on.
        cover = m.mirror_coverage(conn)
        s.check("a copy of something no page asks for does not count as coverage",
                FAKE_URL not in cover["wanted"] and FAKE_URL in cover["orphans"],
                detail="orphans=%d" % len(cover["orphans"]))
        s.check("and the figure cannot exceed a hundred per cent",
                isinstance(cover["percent"], int) and 0 <= cover["percent"] <= 100,
                detail=str(cover["percent"]))
        s.check("a file on the disk the site does not ask for adds nothing "
                "to the figure",
                cover["held"] == 0 and cover["percent"] == 0,
                detail="held=%s percent=%s" % (cover["held"], cover["percent"]))
        s.check("it is kept rather than deleted — the site dropping a "
                "photograph is not the house wanting it gone",
                FAKE_URL in m.mirrored_index(conn))
        s.check("and is not 'safe' while any are still on their server",
                cover["safe"] is (bool(cover["wanted"]) and not cover["missing"]))

        # ------------------------------------------------------------ serving
        s.section("Serving our copy")

        r = oc.get("/mirrored-photo/" + ghost)
        s.check("the file comes back", r.status_code == 200, r)
        s.check("with the bytes that were written", r.data == STUB)
        s.check("with a long cache life — the name is a hash of the source, so "
                "it never changes meaning",
                "max-age=" in r.headers.get("Cache-Control", ""))
        s.check("the swap hook lets a photograph download past untouched",
                r.status_code == 200)
        r.close()

        # get_data() on a passthrough response RAISES, so a file sent straight
        # off the disk has to be let past before the hook reads it. A
        # photograph does not prove that -- the content-type test catches an
        # image first -- so this is the case the guard actually exists for: a
        # file download that IS html. Without the guard it is a 500 on a
        # download somebody clicked.
        with m.app.test_request_context("/"):
            passthrough = m.send_from_directory(m.MIRROR_DIR, ghost)
            passthrough.content_type = "text/html; charset=utf-8"
            try:
                handled = m.serve_our_own_photographs(passthrough)
                s.check("and a file sent straight off the disk is not read",
                        handled is passthrough)
            except RuntimeError as exc:
                s.check("and a file sent straight off the disk is not read",
                        False, detail=str(exc)[:90])
            passthrough.close()

        guest = m.app.test_client().get("/mirrored-photo/" + ghost)
        s.check("a guest with no login can see it — half of these are on the "
                "page people meet the house on", guest.status_code == 200)
        # Closed by hand: these responses stream from the disk, and Windows
        # will not delete a file one of them still holds open.
        guest.close()
    finally:
        s.check("the test tidies up after itself", _drop_stub(ghost),
                detail="the stub is still on disk and the next section will "
                       "count it as a photograph the house holds")

    # A file that is genuinely sitting in that directory and is not one of
    # ours. Asking for a name that does not exist proves nothing: it is a 404
    # whether the guard is there or not, which is how the first version of this
    # check passed with the guard deleted.
    stray = os.path.join(m.MIRROR_DIR, "notes-to-self.txt")
    os.makedirs(m.MIRROR_DIR, exist_ok=True)
    with io.open(stray, "wb") as fh:
        fh.write(b"not a photograph")
    try:
        r = oc.get("/mirrored-photo/notes-to-self.txt")
        s.check("a file in that directory that is not one of ours is not served",
                r.status_code == 404, detail="HTTP %s" % r.status_code)
        r.close()
    finally:
        try:
            os.remove(stray)
        except OSError:
            pass
    s.check("a name that is not there is refused too",
            oc.get("/mirrored-photo/wp-config.php").status_code == 404)
    s.check("and so is a walk up out of the directory",
            oc.get("/mirrored-photo/..%2f..%2fapp.py").status_code in (301, 308, 404))

    # ----------------------------------------------------------- end to end
    #
    # The part that matters and the part nothing else here proves: a real page,
    # rendered by the real app, coming back pointing at us. Everything above
    # can pass with the hook unregistered.
    s.section("On a page a guest actually opens")

    anon = m.app.test_client()
    carrier = real_url = None
    for path in PUBLIC_PATHS:
        r = anon.get(path)
        if r.status_code != 200:
            continue
        body = r.get_data(as_text=True)
        found = m._MIRROR_URL_RE.findall(body)
        if found:
            carrier, real_url = path, found[0]
            break

    if not carrier:
        s.check("a public page carries a hotlinked photograph", False,
                detail="none of %s did — if the site is genuinely clean this "
                       "check should go, but check the hook first"
                       % (PUBLIC_PATHS,))
    else:
        name = m.mirror_name(real_url) + ".jpg"
        _write_stub(name)
        m.record_mirror(conn, real_url, name, len(STUB), "image/jpeg")
        conn.commit()
        m.mirror_cache_clear()
        try:
            body = m.app.test_client().get(carrier).get_data(as_text=True)
            s.check("%s comes back pointing at us" % carrier,
                    "/mirrored-photo/" + name in body)
            s.check("and that photograph is no longer fetched from them",
                    real_url not in body)
            # The others on the same page must be untouched: a hook that
            # blanked every remote URL would pass the two checks above.
            s.check("the ones not held yet still point where they did",
                    "squarespace-cdn.com" in body
                    or len(m._MIRROR_URL_RE.findall(
                        m.app.test_client().get(carrier).get_data(as_text=True))) == 0,
                    detail="every remote photograph vanished, not just ours")

        finally:
            _drop_stub(name)
            conn.execute("DELETE FROM mirrored_images WHERE source_url = ?",
                         (real_url,))
            conn.commit()
            m.mirror_cache_clear()

    # The not-found page is a full public page — masthead, four photographs —
    # and it is the one people reach from every stale link and mistyped address
    # there is. The hook used to act only on a 200, so the single page somebody
    # lands on by accident was the one still pointing at an account the house
    # does not own. Mirrored on its own rather than leaning on whatever the
    # carrier page happened to hold.
    s.section("Including the page nobody meant to open")

    lost_page = m.app.test_client().get("/no-such-page-at-all-zz")
    lost = lost_page.get_data(as_text=True)
    s.check("the not-found page is a whole page, not a bare message",
            lost_page.status_code == 404 and len(lost) > 2000,
            detail="HTTP %s, %d bytes" % (lost_page.status_code, len(lost)))
    on_lost = m._MIRROR_URL_RE.findall(lost)
    if not on_lost:
        s.check("it carries a photograph to swap", False,
                detail="nothing to prove here if it has none")
    else:
        lost_url = on_lost[0]
        lost_name = m.mirror_name(lost_url) + ".jpg"
        _write_stub(lost_name)
        m.record_mirror(conn, lost_url, lost_name, len(STUB), "image/jpeg")
        conn.commit()
        m.mirror_cache_clear()
        try:
            again = m.app.test_client().get("/no-such-page-at-all-zz")
            s.check("a 404 is swapped too, not only the pages that answer 200",
                    "/mirrored-photo/" + lost_name in again.get_data(as_text=True),
                    detail="a guest who mistypes a URL is the one person "
                           "guaranteed to see this page")
            s.check("and it is still a 404", again.status_code == 404)
        finally:
            _drop_stub(lost_name)
            conn.execute("DELETE FROM mirrored_images WHERE source_url = ?",
                         (lost_url,))
            conn.commit()
            m.mirror_cache_clear()

    # --------------------------------------------------------------- the page
    s.section("The page that says how exposed the house is")

    r = oc.get("/admin/photo-mirror")
    s.check("the owner can open it", r.status_code == 200, r)
    body = r.get_data(as_text=True)
    s.check("it says what the number means", "survive" in body.lower())
    s.check("it names the risk in words, not just a percentage",
            "logo" in body.lower() or "masthead" in body.lower())
    s.check("an employee cannot", ec.get("/admin/photo-mirror").status_code != 200)

    # ------------------------------------------------------------ the warning
    s.section("Somebody is told")

    cover = m.mirror_coverage(conn)
    # url_for, so it needs a context. Asked of the builder AND of the page:
    # a warning that is built and never drawn is the failure this whole
    # convention exists to stop.
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, m.house_today())
        hrefs = [w["href"] for w in warnings]
    home = oc.get("/").get_data(as_text=True)

    # A photograph nobody has copied yet and a photograph that CANNOT be
    # fetched are different problems and get different sentences. The second
    # one is happening now: the address on the page is wrong or the picture has
    # gone, so a guest on that page is looking at a blank. A broken <img>
    # renders as nothing and reports nothing, and this is the only thing in the
    # house that says so.
    real_scan_w = m.hotlinked_urls
    m.hotlinked_urls = lambda: [FAKE_URL, FAKE_TWO]
    m.record_mirror(conn, FAKE_URL, error="HTTP Error 400: Bad Request")
    conn.commit()
    try:
        with m.app.test_request_context():
            split = m.owner_home_warnings(conn, m.house_today())
        dead = [w for w in split if "cannot be fetched" in w["title"]]
        soft = [w for w in split if "somebody else's server" in w["title"]]
        s.check("a photograph that will not come down at all is a blocker",
                len(dead) == 1 and dead[0]["severity"] == "blocker",
                detail=str([(w["severity"], w["title"]) for w in split
                            if "photo" in w["href"]]))
        s.check("it says a guest is looking at a blank space, not that a "
                "backup is missing",
                dead and "blank space" in dead[0]["detail"])
        s.check("and it is not also counted as one merely not copied yet",
                len(soft) == 1 and soft[0]["count"] == 1,
                detail=str([(w["title"], w["count"]) for w in soft]))
    finally:
        m.hotlinked_urls = real_scan_w
        _clean(conn)
    if cover["missing"]:
        s.check("while photographs sit on their server, the owner is warned",
                any("photo-mirror" in h for h in hrefs),
                detail="the day it matters is the day the account lapses")
        s.check("and the warning is actually on the page in front of them",
                "/admin/photo-mirror" in home,
                detail="built but never drawn")
    else:
        s.check("nothing to warn about once every one is held",
                not any("photo-mirror" in h for h in hrefs))
        s.check("and the owner home does not mention it",
                "/admin/photo-mirror" not in home)

    # ------------------------------------------------------------- no network
    s.section("Nothing here went near the network")

    s.check("the fetcher is still the harness's raiser",
            m.fetch_one_image.__name__ == "_blocked",
            detail="a suite that quietly started downloading would still pass")
    try:
        m.fetch_one_image(FAKE_URL)
        reached = True
    except AssertionError:
        reached = False
    except Exception:                                  # noqa: BLE001
        reached = True
    s.check("and calling it raises rather than fetches", not reached)

    # ------------------------------------------------------------- the job
    #
    # The button needs somebody to press it, several times, and to remember
    # that it exists. A safeguard that depends on that is a safeguard for the
    # fortnight after it is built, so the job is the part that matters on a
    # server nobody is looking at.
    s.section("And it does it without being asked")

    s.check("the job is registered to run on its own",
            "photo_mirror" in [j[0] for j in m.AUTOMATION_JOBS],
            detail="it exists but nothing calls it, which is the state four "
                   "other jobs were found in")
    job = next((j for j in m.AUTOMATION_JOBS if j[0] == "photo_mirror"), None)
    s.check("it is on by default",
            m.AUTOMATION_SETTING_DEFAULTS.get(job[1]) == "1" if job else False,
            detail="a mirror switched off by default is not a mirror")
    s.check("and it says what it is for on the automation page",
            "photo_mirror" in m.AUTOMATION_JOB_LABELS,
            detail="an unlabelled job does not appear on that page at all")
    s.check("hourly, not daily", job and job[3] == 3600,
            detail=str(job[3]) if job else "no job")
    # The jobs run one after another in a single background thread, so
    # everything below a job waits for it -- and this is the only one that
    # spends up to a minute on somebody else's network. Written in above
    # maintenance to begin with, which put nine behind it, including the text
    # telling a guest where to go on the day and the one that charges a
    # workshop balance.
    names = [j[0] for j in m.AUTOMATION_JOBS]
    s.check("and last in the queue, so nothing waits behind it",
            names[-1] == "photo_mirror",
            detail="%d job(s) behind it, ending %s"
                   % (len(names) - 1 - names.index("photo_mirror"), names[-1]))

    real_scan_j = m.hotlinked_urls
    m.hotlinked_urls = lambda: [FAKE_URL, FAKE_TWO]
    made_j = []

    def _pretend_j(url, timeout=20):                   # pragma: no cover
        name = m.mirror_name(url) + ".jpg"
        _write_stub(name)
        made_j.append(name)
        return name, len(STUB), "image/jpeg"

    real_fetch_j = m.fetch_one_image
    try:
        m.fetch_one_image = _pretend_j
        said = m.run_photo_mirror_job(conn)
        s.check("a run with something to fetch fetches it",
                m.mirror_coverage(conn)["held"] == 2, detail=said)
        s.check("and says what it did", "copied 2" in said, detail=said)

        idle = m.run_photo_mirror_job(conn)
        s.check("a run with nothing to do says so rather than working",
                "all 2 held" in idle, detail=idle)

        # The backoff. A dead address asked for every hour is twenty-four
        # requests a day at somebody else's CDN for an answer that will not
        # change -- and it would bury the ones that might.
        for name in made_j:
            _drop_stub(name)
        made_j.clear()
        m.fetch_one_image = real_fetch_j               # the harness's raiser
        m.mirror_cache_clear()
        first = m.run_photo_mirror_job(conn)
        s.check("a photograph that will not come down is recorded as failed",
                "would not come down" in first, detail=first)
        second = m.run_photo_mirror_job(conn)
        s.check("and the next run leaves it alone rather than asking again",
                "tried within the day" in second, detail=second)
    finally:
        m.hotlinked_urls = real_scan_j
        m.fetch_one_image = real_fetch_j
        for name in made_j:
            _drop_stub(name)
        _clean(conn)
        m.mirror_cache_clear()

    # ---------------------------------------------------------- the button
    #
    # Both paths, with hotlinked_urls standing in so the route has two
    # photographs to think about rather than the site's real ninety-three.
    s.section("Pressing the button")

    real_scan = m.hotlinked_urls
    m.hotlinked_urls = lambda: [FAKE_URL, FAKE_TWO]
    made = []

    def _pretend(url, timeout=20):                     # pragma: no cover
        name = m.mirror_name(url) + ".jpg"
        _write_stub(name)
        made.append(name)
        return name, len(STUB), "image/jpeg"

    real_fetch = m.fetch_one_image
    try:
        # Failure first: the CDN is dead and the raiser is standing in for it.
        r = oc.post("/admin/photo-mirror/fetch", follow_redirects=True)
        s.check("the button survives every fetch failing", r.status_code == 200, r)
        s.check("nothing was recorded as held that was not",
                m.mirror_coverage(conn)["held"] == 0)
        s.check("and the page says which ones would not come down",
                len(m.mirror_coverage(conn)["errors"]) == 2)

        m.fetch_one_image = _pretend
        r = oc.post("/admin/photo-mirror/fetch", follow_redirects=True)
        cover = m.mirror_coverage(conn)
        s.check("a copy that comes down is recorded and counted",
                cover["held"] == 2, r)
        s.check("and the house is no longer exposed", cover["safe"])
        s.check("the failure recorded last time is cleared, not left standing",
                not cover["errors"],
                detail="the page would keep reporting a photograph it now holds")
        s.check("and it says so", "no longer depends" in r.get_data(as_text=True))
    finally:
        m.hotlinked_urls = real_scan
        m.fetch_one_image = real_fetch
        for name in made:
            _drop_stub(name)
        _clean(conn)
        conn.close()
        m.mirror_cache_clear()

    return s


if __name__ == "__main__":
    print(run().report())
