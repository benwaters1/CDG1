"""Nothing goes out that nobody approved, and nothing goes out quietly wrong.

The owner asked for real publishing rather than a reminder to paste something
in — so this code puts photographs on the house's actual Instagram and its
actual Page. That is the highest-consequence thing in the app: there is no
undo beyond deleting it afterwards and hoping nobody was looking.

  THE GATE IS approved_at AND NOTHING ELSE. Not a status, which gets edited;
  a column saying who signed their name and when, so the answer to "who said
  yes to this" survives every later edit. A post nobody approved is never due,
  however carefully it was scheduled and however long it waits.

  HALF-PUBLISHED IS NOT PUBLISHED. A post bound for Instagram and the Page
  that reaches only one of them says so. The cheerful half — "posted!" — is
  how the failure that matters becomes the one nobody goes looking for.

  META HAS TO SEE THE PHOTOGRAPH, so one is served publicly. Every other
  photograph route here is behind a login because UPLOAD_DIR holds contracts
  and doctors' notes, so this one is scoped to a single token standing for a
  single post: no directory, no filename, nothing to walk from.

  AND THE TOKEN RUNS OUT. Sixty days, everything works, everybody forgets, and
  one evening a post does not go and nothing says so. The owner home warns
  first, and it closes itself when the token is renewed.

The Graph call itself is stood down in the harness — `meta_request` raises, so
a run that reached Meta would not merely cost money, it would PUBLISH to the
real account. What is checked here is everything around it, with the call
stood in for.
"""
import io
import json
import os

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPUB"


def _cleanup():
    conn = db()
    for row in conn.execute("SELECT image_filename FROM social_posts "
                            "WHERE caption LIKE ?", (TAG + "%",)).fetchall():
        if row["image_filename"]:
            try:
                os.remove(os.path.join(m.UPLOAD_DIR, row["image_filename"]))
            except OSError:
                pass
    conn.execute("DELETE FROM social_posts WHERE caption LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key IN "
                 "('meta_page_id','meta_ig_user_id','meta_access_token',"
                 "'meta_token_expires_at')")
    conn.commit()
    conn.close()


def _connect_meta(expires_in_days=40):
    conn = db()
    when = (m.house_today() + m.timedelta(days=expires_in_days)).isoformat()
    for key, value in (("meta_page_id", "1234567890"),
                       ("meta_ig_user_id", "17841400000000000"),
                       ("meta_access_token", "not-a-real-token"),
                       ("meta_token_expires_at", when)):
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) "
                     "VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def _post(caption, platform="Instagram", with_photo=True, status="scheduled",
          scheduled_date=None, scheduled_time=None):
    conn = db()
    name = None
    if with_photo:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (1200, 900), (120, 100, 80)).save(buf, "JPEG")
        normalised, _t = m.photo_master(buf.getvalue(), "frame.jpg")
        name = "photo_%s_%s.jpg" % (m.secrets.token_hex(4), TAG.lower())
        os.makedirs(m.UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(m.UPLOAD_DIR, name), "wb") as fh:
            fh.write(normalised)
    conn.execute(
        """INSERT INTO social_posts (platform, caption, image_filename, status,
             scheduled_date, scheduled_time, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (platform, caption, name, status, scheduled_date, scheduled_time,
         m.datetime.now(m.timezone.utc).isoformat()))
    pid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    return pid


def _row(pid):
    conn = db()
    try:
        return conn.execute("SELECT * FROM social_posts WHERE id = ?",
                            (pid,)).fetchone()
    finally:
        conn.close()


def _publish(pid):
    """Call the publisher directly, with the Graph call stood in for."""
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.publish_social_post(conn, _row(pid))
    finally:
        conn.commit()
        conn.close()


def run():
    s = Suite("Putting a post out")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    s.section("The Graph call is not reachable from a test")
    s.check("meta_request is stood down",
            m.meta_request.__name__ == "_blocked",
            detail="a real call does not cost money, it PUBLISHES to the "
                   "house's Instagram, and the database here is a copy of the "
                   "real one")

    s.section("Nothing goes out that nobody approved")
    _connect_meta()
    pid = _post(TAG + " The east front, this morning.")
    s.check("it starts unapproved", not _row(pid)["approved_at"])
    # STOOD IN FOR, EVEN THOUGH THIS MUST NOT PUBLISH — and that is the point.
    # With the real blocked call in place, deleting the gate made this suite
    # CRASH rather than fail: the harness caught it, but it reported 0/0 and
    # said nothing about the single most important rule here. With a working
    # call underneath, removing the gate lets the post sail through and the
    # checks below say so in plain words.
    watched = []

    def _watch(path, params):
        watched.append((path, params))
        return True, {"id": "watched"}

    real_first = m.meta_request
    m.meta_request = _watch
    try:
        ok, said = _publish(pid)
    finally:
        m.meta_request = real_first
    s.check("and it will not publish", not ok, detail=f"{said}")
    s.check("saying so in as many words", "approved" in said.lower(),
            detail=f"{said!r} — 'could not publish' tells whoever reads it "
                   "tomorrow nothing they can act on")
    s.check("and it is not marked as posted", _row(pid)["status"] != "posted")
    s.check("Meta is not even asked",
            watched == [],
            detail=f"{[c[0] for c in watched]} — refusing after the call has "
                   "gone is not refusing, and there is no undo at the far end")

    s.section("Approving is a person putting their name to it")
    oc.post(f"/management/social/{pid}/approve", follow_redirects=True)
    approved = _row(pid)
    s.check("it is approved", bool(approved["approved_at"]))
    s.check("and by somebody in particular",
            approved["approved_by_user_id"] is not None,
            detail="'who said yes to this' has to survive the post being "
                   "edited afterwards")
    s.check("an employee cannot approve",
            ec.post(f"/management/social/{pid}/approve").status_code != 200)

    s.section("An empty post cannot be approved")
    empty = _post(TAG + "x", with_photo=True)
    conn = db()
    conn.execute("UPDATE social_posts SET caption = '' WHERE id = ?", (empty,))
    conn.commit()
    conn.close()
    refused = oc.post(f"/management/social/{empty}/approve", follow_redirects=True)
    s.check("approving nothing is refused",
            not _row(empty)["approved_at"],
            detail="approving an empty post is approving whatever gets typed "
                   "into it later")
    s.check("and it says why", "no words" in refused.get_data(as_text=True).lower())

    s.section("Approval can be withdrawn right up until it goes")
    oc.post(f"/management/social/{pid}/unapprove", follow_redirects=True)
    s.check("it is no longer approved", not _row(pid)["approved_at"])
    ok, said = _publish(pid)
    s.check("and it will not go out", not ok, detail=f"{said}")
    oc.post(f"/management/social/{pid}/approve", follow_redirects=True)

    s.section("Publishing, with Meta stood in for")
    calls = []

    def _fake(path, params):
        calls.append((path, params))
        return True, {"id": "fake_%d" % len(calls)}

    real = m.meta_request
    m.meta_request = _fake
    try:
        ok, said = _publish(pid)
        s.check("it goes out", ok, detail=f"{said}")
        s.check("as two steps, because the second is the one that publishes",
                len(calls) == 2 and calls[1][0].endswith("/media_publish"),
                detail=f"{[c[0] for c in calls]} — a container made and never "
                       "published is a photograph at Meta nobody can see, "
                       "which looks exactly like success from here")
        # `calls and ...` throughout: when an earlier guard is broken this
        # post has already gone out, `calls` is empty, and an IndexError here
        # would crash the file and report 0/0 -- burying the four failures
        # above that name the actual fault.
        first_call = calls[0][1] if calls else {}
        s.check("the caption goes with it",
                first_call.get("caption", "").startswith(TAG),
                detail=f"{first_call.get('caption')!r}")
        s.check("and a PUBLIC link to the photograph, because Meta fetches it",
                "/social-photo/" in (first_call.get("image_url") or ""),
                detail=f"{first_call.get('image_url')}")
        s.check("it is recorded as posted", _row(pid)["status"] == "posted")
        s.check("with the time it went", bool(_row(pid)["posted_at"]))

        s.section("Half-published is not published")
        both = _post(TAG + " Both places.", platform="Instagram and Facebook")
        conn = db()
        conn.execute("UPDATE social_posts SET approved_at = ? WHERE id = ?",
                     (m.datetime.now(m.timezone.utc).isoformat(), both))
        conn.commit()
        conn.close()
        calls.clear()

        def _ig_only(path, params):
            calls.append((path, params))
            if path.startswith("1234567890"):
                return False, "the Page refused it"
            return True, {"id": "fake"}

        m.meta_request = _ig_only
        ok2, said2 = _publish(both)
        s.check("what failed is said out loud",
                "Page refused" in said2 or "Facebook" in said2,
                detail=f"{said2!r} — the cheerful half is how the failure "
                       "that matters becomes the one nobody looks for")
        s.check("and it is written on the post for tomorrow",
                _row(both)["publish_error"],
                detail=f"{_row(both)['publish_error']!r}")
    finally:
        m.meta_request = real
    s.check("the stand-in is put back",
            m.meta_request.__name__ == "_blocked",
            detail="leaving it patched would let a later suite publish")

    s.section("The button that sends it now")
    # THE ROUTE, not just the helper underneath it. Everything above called
    # publish_social_post directly, so the page the owner actually presses had
    # never once answered — which the coverage check named, and rightly: a
    # helper that works behind a route nobody has exercised is half a feature.
    now_post = _post(TAG + " straight out")
    conn = db()
    conn.execute("UPDATE social_posts SET approved_at = ? WHERE id = ?",
                 (m.datetime.now(m.timezone.utc).isoformat(), now_post))
    conn.commit()
    conn.close()
    real_now = m.meta_request
    m.meta_request = _fake
    try:
        pressed = oc.post(f"/management/social/{now_post}/publish-now",
                          follow_redirects=True)
    finally:
        m.meta_request = real_now
    s.check("the page answers", pressed.status_code == 200,
            detail=f"HTTP {pressed.status_code}")
    s.check("and it went out", _row(now_post)["status"] == "posted",
            detail=f"{_row(now_post)['status']}")
    s.check("an employee cannot press it",
            ec.post(f"/management/social/{now_post}/publish-now").status_code != 200)
    s.check("and a post that is not there is a 404",
            oc.post("/management/social/99887766/publish-now").status_code == 404)

    s.section("The public link to the photograph")
    token = _row(pid)["publish_token"]
    s.check("a post that has gone out has one", bool(token))
    anon = m.app.test_client()
    shown = anon.get(f"/social-photo/{token}.jpg")
    s.check("and a stranger can fetch it, because Meta cannot sign in",
            shown.status_code == 200, detail=f"HTTP {shown.status_code}")
    served = _size(shown.data)
    s.check("it is the size Instagram accepts",
            served and max(served) <= 1440, detail=f"{served}")
    s.check("it asks not to be indexed",
            "noindex" in (shown.headers.get("X-Robots-Tag") or ""),
            detail="a link that leaks into a referrer can be indexed without "
                   "ever being crawled, which robots.txt does not cover")
    s.check("a token nobody issued gets nothing",
            anon.get("/social-photo/" + "a" * 30 + ".jpg").status_code == 404)
    s.check("and there is no filename to ask for",
            anon.get("/social-photo/%s.jpg"
                     % (_row(pid)["image_filename"] or "x")).status_code == 404,
            detail="UPLOAD_DIR holds contracts and doctors' notes beside the "
                   "photographs")

    s.section("The token that runs out")
    conn = db()
    with m.app.test_request_context("/"):
        s.check("with plenty of time left, the owner home says nothing",
                not _meta_warnings(conn),
                detail="a panel that can never be empty becomes furniture")
    conn.close()
    _connect_meta(expires_in_days=5)
    conn = db()
    with m.app.test_request_context("/"):
        soon = _meta_warnings(conn)
    conn.close()
    s.check("close to the end, it warns", len(soon) == 1,
            detail=f"{[w['title'] for w in soon]}")
    _connect_meta(expires_in_days=-3)
    conn = db()
    with m.app.test_request_context("/"):
        gone = _meta_warnings(conn)
    conn.close()
    s.check("once it has lapsed it is a blocker, not a note",
            gone and gone[0]["severity"] == "blocker",
            detail=f"{gone[0] if gone else None}")
    ok3, said3 = _publish(_post(TAG + " after the token died"))
    s.check("and nothing goes out on a dead token", not ok3, detail=f"{said3}")
    _connect_meta(expires_in_days=40)

    s.section("The scheduled run")
    conn = db()
    yesterday = (m.house_today() - m.timedelta(days=1)).isoformat()
    due = _post(TAG + " due yesterday", scheduled_date=yesterday)
    not_approved = _post(TAG + " due but unapproved", scheduled_date=yesterday)
    later = _post(TAG + " next month",
              scheduled_date=(m.house_today() + m.timedelta(days=30)).isoformat())
    for pid_ in (due, later):
        conn.execute("UPDATE social_posts SET approved_at = ? WHERE id = ?",
                     (m.datetime.now(m.timezone.utc).isoformat(), pid_))
    conn.commit()
    conn.close()
    calls.clear()
    m.meta_request = _fake
    try:
        conn = db()
        with m.app.test_request_context("/"):
            sent = m.run_social_publish_job(conn)
        conn.commit()
        conn.close()
    finally:
        m.meta_request = real
    s.check("the one that was due went", _row(due)["status"] == "posted",
            detail=f"{_row(due)['status']} — {sent} sent")
    s.check("the one nobody approved did not",
            _row(not_approved)["status"] != "posted",
            detail="a post nobody approved is never due, however long it waits")
    s.check("and neither did the one that is not due yet",
            _row(later)["status"] != "posted")

    _cleanup()
    return s


def _size(data):
    """The size of an image, or None when what came back was not one."""
    from PIL import Image
    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:
        return None


def _meta_warnings(conn):
    """Only the Meta lines out of the owner's panel."""
    return [w for w in m.owner_home_warnings(conn, m.house_today())
            if "Meta token" in w["title"]]
