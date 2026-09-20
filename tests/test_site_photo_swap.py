"""Putting one of the house's own photographs on the public site.

NOT BY EDITING A TEMPLATE, and that is the whole of it. The public pages are
rewritten by hand most weeks and arrive as whole-file replacements, so a
swapped img tag would survive exactly one handover. Done to the response, the
way the mirror already is, it needs nothing from anybody and cannot be undone
by accident.

  A SWAP GETS ITS OWN NAME, and this is the trap the mirror sets for anything
  built on top of it. /mirrored-photo/<hash>.jpg is named by a hash of the
  SOURCE URL and served with a thirty-day cache, on the promise that a given
  name always means the same bytes -- "nothing served here is ever rewritten
  in place", as it says. A swap that reused one of those names would be a
  photograph the house has replaced and the world keeps showing, for a month,
  with nothing anybody could do about it. So every swap has a token of its
  own, and the token changes every time the picture does.

  THE OVERRIDE BEATS THE MIRROR. The mirror says "the same picture, our
  copy". An override says "not that picture, this one". They key off the same
  URLs, so the order matters and is checked.

  PUTTING IT BACK GOES TO THE MIRROR, never to hotlinking. Undoing a swap must
  leave the site in the state the rest of it is in, not in the state it was in
  before the mirror existed.

  AND ONLY THE SITE'S OWN PICTURES CAN BE REPLACED. The list is read out of
  the templates, so a URL somebody posts that is not on the site is refused
  rather than quietly recorded as an override that never fires.
"""
import io
import os

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zzsite"


def _frame(colour=(40, 90, 160)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1400, 1000), colour).save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _cleanup():
    conn = db()
    rows = conn.execute("SELECT filename FROM photo_inbox WHERE original_name "
                        "LIKE ?", (TAG + "%",)).fetchall()
    for row in rows:
        conn.execute("DELETE FROM site_photo_overrides WHERE filename = ?",
                     (row["filename"],))
        try:
            os.remove(os.path.join(m.UPLOAD_DIR, row["filename"]))
        except OSError:
            pass
    conn.execute("DELETE FROM photo_inbox WHERE original_name LIKE ?",
                 (TAG + "%",))
    conn.commit()
    conn.close()
    m.forget_site_overrides()


def _arrive(name, colour=(40, 90, 160)):
    conn = db()
    try:
        arrival_id, _what = m.store_arriving_photograph(
            conn, _frame(colour), name, source="camera")
        conn.commit()
        return arrival_id
    finally:
        conn.close()


def _override_row(source_url):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM site_photo_overrides WHERE source_url = ?",
            (source_url,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("A photograph of ours, on the public site")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    site_urls = sorted(m.hotlinked_urls())
    s.check("the site's own photographs can be found to choose between",
            len(site_urls) > 10,
            detail=f"{len(site_urls)} — read out of the templates, because a "
                   "kept list goes stale the first time a handover adds one")
    target = site_urls[0]

    arrival = _arrive(TAG + "-P1050001.JPG")
    conn = db()
    filename = conn.execute("SELECT filename FROM photo_inbox WHERE id = ?",
                            (arrival,)).fetchone()["filename"]
    conn.close()

    s.section("Choosing which picture it replaces")
    picker = oc.get(f"/admin/photos/tray/{arrival}/use-on-site")
    s.check("the owner can see the site's pictures", picker.status_code == 200,
            detail=f"HTTP {picker.status_code}")
    s.check("an employee cannot",
            ec.get(f"/admin/photos/tray/{arrival}/use-on-site").status_code != 200)

    s.section("Making the swap")
    oc.post(f"/admin/photos/tray/{arrival}/use-on-site",
            data={"source_url": target}, follow_redirects=True)
    row = _override_row(target)
    s.check("it is recorded against the picture it replaces", bool(row))
    s.check("pointing at the photograph from the camera",
            row and row["filename"] == filename)
    s.check("and the tray knows what became of it",
            _tray_state(arrival) == "site",
            detail=f"{_tray_state(arrival)}")

    s.section("What the page actually sends")
    # The response layer, asked the way a real page asks it.
    html = '<img src="%s" alt="">' % target
    with m.app.test_request_context("/"):
        swapped = m.swap_mirrored(html, m.mirror_cache_index(),
                                  m.site_override_cache_index())
    s.check("the page points at ours instead",
            "/site-photo/" in swapped and target not in swapped,
            detail=f"{swapped}")
    s.check("and NOT at the mirrored copy of the old picture",
            "/mirrored-photo/" not in swapped,
            detail=f"{swapped} — the mirror says 'the same picture, our copy'; "
                   "an override says 'not that picture, this one', and the "
                   "override has to win")
    s.check("no template was touched to do it",
            target in io.open(_template_holding(target), encoding="utf-8").read(),
            detail="the public pages arrive as whole-file replacements, so an "
                   "edited img tag survives exactly one handover")

    s.section("The picture it now serves")
    anon = m.app.test_client()
    shown = anon.get("/site-photo/%s.jpg" % row["token"])
    s.check("a visitor can fetch it, because the site is public",
            shown.status_code == 200, detail=f"HTTP {shown.status_code}")
    s.check("it is the web size, not the master",
            _size(shown.data) and max(_size(shown.data)) <= m.PHOTO_SIZES["web"][0],
            detail=f"{_size(shown.data)}")
    s.check("a token nobody issued gets nothing",
            anon.get("/site-photo/" + "b" * 30 + ".jpg").status_code == 404)

    s.section("Replacing it again gets a NEW name")
    # THE THIRTY-DAY CACHE. /mirrored-photo promises a name always means the
    # same bytes and is cached for a month on that promise. Reusing a name
    # here would be a picture the house has replaced and the world keeps
    # showing, with no way to tell anybody's browser otherwise.
    was_token = row["token"]
    # A DIFFERENT COLOUR, because the ingest deduplicates on content and
    # two identical frames are one photograph — which is right, and which
    # made this check fail against a fixture that sent the same picture twice.
    second = _arrive(TAG + "-P1050002.JPG", colour=(200, 120, 30))
    oc.post(f"/admin/photos/tray/{second}/use-on-site",
            data={"source_url": target}, follow_redirects=True)
    now = _override_row(target)
    s.check("the same place on the page, a different picture",
            now and now["filename"] != filename)
    s.check("and a different link, so no cache can hold the old one",
            now and now["token"] != was_token,
            detail=f"{now['token'][:8]}… against {was_token[:8]}… — a month is "
                   "a long time to show a photograph you have replaced")
    s.check("there is still only one override for that picture",
            _override_count(target) == 1,
            detail="two rows for one place on the page is two answers to "
                   "which photograph goes there")
    s.check("and the old link stops working",
            anon.get("/site-photo/%s.jpg" % was_token).status_code == 404)

    s.section("A URL that is not one of the site's")
    oc.post(f"/admin/photos/tray/{arrival}/use-on-site",
            data={"source_url": "https://example.invalid/not-ours.jpg"},
            follow_redirects=True)
    s.check("is refused rather than recorded",
            not _override_row("https://example.invalid/not-ours.jpg"),
            detail="an override that can never fire is a row nobody will ever "
                   "understand the presence of")

    s.section("Putting it back")
    page = oc.get("/admin/site-photos")
    # THE PAGE THAT REPORTS THE SWAPS WAS HAVING ITS OWN TEXT SWAPPED. The
    # response layer rewrites every one of these URLs in any HTML it sends,
    # including one printed as words in a table cell — so the page whose whole
    # job is to say WHICH picture was replaced was showing the replacement's
    # path instead. It names the picture by its own filename now, which the
    # rewrite does not touch and is the readable half anyway.
    label = target.rsplit("/", 1)[-1]
    s.check("the swaps are listed, and say which picture",
            page.status_code == 200 and label in page.get_data(as_text=True),
            detail=f"HTTP {page.status_code} — looking for {label!r}")
    oc.post(f"/admin/site-photos/{now['id']}/put-back", follow_redirects=True)
    s.check("the override is gone", not _override_row(target))
    with m.app.test_request_context("/"):
        after = m.swap_mirrored(html, m.mirror_cache_index(),
                                m.site_override_cache_index())
    s.check("the page stops pointing at our replacement",
            "/site-photo/" not in after, detail=f"{after}")
    with m.app.test_request_context("/"):
        held = target in m.mirror_cache_index()
    s.check("and goes to the mirrored copy wherever one is held",
            ("/mirrored-photo/" in after) == held,
            detail=f"held={held}: {after} — putting a swap back means the "
                   "state the rest of the site is in, which is the mirror "
                   "where there is one and the original where there is not")
    s.check("the photograph returns to the tray, unused",
            _tray_state(second) is None,
            detail=f"{_tray_state(second)}")

    _cleanup()
    return s


def _size(data):
    from PIL import Image
    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:
        return None


def _tray_state(arrival_id):
    conn = db()
    try:
        row = conn.execute("SELECT used_as FROM photo_inbox WHERE id = ?",
                           (arrival_id,)).fetchone()
        return row["used_as"] if row else None
    finally:
        conn.close()


def _override_count(source_url):
    conn = db()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM site_photo_overrides WHERE source_url = ?",
            (source_url,)).fetchone()["n"]
    finally:
        conn.close()


def _template_holding(url):
    """The template file that still carries this URL, untouched."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates")
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not name.endswith(".html"):
            continue
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            if url in fh.read():
                return path
    raise AssertionError("no template carries %s" % url)
