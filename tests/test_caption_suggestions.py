"""Words for a photograph — suggested, and never written in.

The intake page has refused to write captions since it was built, and its
docstring says why: a generated sentence about a French château reads like
every other generated sentence, and the writing is the reason the site works.
The owner asked for suggestions anyway. That is a different thing, and the
difference is the whole of this file.

  A SUGGESTION LIVES IN ITS OWN FIELD. `suggested_json` is not `caption`, and
  nothing in the app writes `caption` except a person choosing one of the
  offered lines or typing their own. A post that arrives already looking
  finished is one nobody reads before it goes out.

  THE POST STAYS AN IDEA until somebody picks. An idea is an honest thing to
  finish; a draft is a thing that looks done.

  AND IT IS ASKED FOR. Nothing suggests on arrival, because forty frames off
  one shoot would be forty calls for the thirty-six nobody chose.

The API itself is stood down in this harness — `anthropic.Anthropic` raises
and `claude_configured()` is False — so what is checked here is everything
around the call: that it is refused politely when unconfigured, that a stored
suggestion reaches the page as a suggestion, and that choosing one is what
moves the caption. The model's own words are not this file's business.
"""
import io
import json
import os

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZSUGG"


def _frame():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (500, 400), (100, 110, 90)).save(buf, "JPEG")
    return buf.getvalue()


def _cleanup():
    conn = db()
    for row in conn.execute("SELECT image_filename FROM social_posts "
                            "WHERE alt_text LIKE ? OR caption LIKE ?",
                            (TAG + "%", TAG + "%")).fetchall():
        if row["image_filename"]:
            try:
                os.remove(os.path.join(m.UPLOAD_DIR, row["image_filename"]))
            except OSError:
                pass
    conn.execute("DELETE FROM social_posts WHERE alt_text LIKE ? OR caption LIKE ?",
                 (TAG + "%", TAG + "%"))
    conn.commit()
    conn.close()


def _post_with_photo(alt=None):
    conn = db()
    name = "photo_%s_%s.jpg" % (m.secrets.token_hex(4), TAG.lower())
    os.makedirs(m.UPLOAD_DIR, exist_ok=True)
    normalised, _taken = m.photo_master(_frame(), "frame.jpg")
    with open(os.path.join(m.UPLOAD_DIR, name), "wb") as fh:
        fh.write(normalised)
    conn.execute(
        """INSERT INTO social_posts (platform, caption, image_filename, alt_text,
             status, created_at) VALUES ('instagram', '', ?, ?, 'idea', ?)""",
        (name, alt, m.datetime.now(m.timezone.utc).isoformat()))
    pid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    return pid, name


def _row(pid):
    conn = db()
    try:
        return conn.execute("SELECT * FROM social_posts WHERE id = ?",
                            (pid,)).fetchone()
    finally:
        conn.close()


SUGGESTION = {
    "what_is_shown": "A stone staircase with a worn handrail.",
    "captions": [TAG + " The staircase, as it is now.",
                 TAG + " Stone worn into a curve by three hundred years.",
                 TAG + " Morning on the back stairs."],
    "alt_text": TAG + " A stone spiral staircase lit from a window above.",
}


def run():
    s = Suite("Suggested words for a photograph")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    s.section("Nothing suggests anything on its own")
    pid, _name = _post_with_photo()
    s.check("a post with a photograph starts with no suggestion",
            not _row(pid)["suggested_json"],
            detail="forty frames off one shoot would be forty calls for the "
                   "thirty-six nobody chose")
    s.check("and no caption", not (_row(pid)["caption"] or "").strip())

    s.section("With no key set, it says so")
    # The harness stands the API down, which is exactly the shape of a
    # deployment where the key has not been put in yet.
    s.check("the model really is unreachable here",
            not m.claude_configured(),
            detail="if this ever stops being true the checks below start "
                   "spending real money on every test run")
    asked = oc.post(f"/management/social/{pid}/suggest-words",
                    follow_redirects=True)
    s.check("asking is answered rather than swallowed", asked.status_code == 200,
            detail=f"HTTP {asked.status_code}")
    s.check("it says the key is missing",
            "ANTHROPIC_API_KEY" in asked.get_data(as_text=True),
            detail="a suggestion that silently never arrives is one somebody "
                   "waits for forever")
    s.check("and nothing was written to the post",
            not _row(pid)["suggested_json"]
            and not (_row(pid)["caption"] or "").strip(),
            detail="a failure that half-fills the post is worse than one that "
                   "does nothing")

    s.section("A post with no photograph on it")
    conn = db()
    conn.execute(
        """INSERT INTO social_posts (platform, caption, status, created_at)
           VALUES ('instagram', ?, 'idea', ?)""",
        (TAG + " nothing attached", m.datetime.now(m.timezone.utc).isoformat()))
    bare = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    bare_ask = oc.post(f"/management/social/{bare}/suggest-words",
                       follow_redirects=True)
    s.check("is told there is nothing to write about",
            "no photograph" in bare_ask.get_data(as_text=True).lower(),
            detail="rather than asking the model to describe nothing")

    s.section("A suggestion coming back, with the model stood in for")
    # THE ROUTE'S OWN WRITE PATH, which nothing else here reaches: with the
    # API stood down it returns before it writes anything, so putting the
    # suggestion straight into the table tested the table and not the route.
    # A control that made the route write the suggestion INTO the caption
    # passed cleanly against that, which is what a check aimed at code that
    # never runs looks like. So the model is stood in for and the route is
    # made to do its job.
    real_configured, real_suggest = m.claude_configured, m.suggest_photo_caption
    m.claude_configured = lambda: True
    m.suggest_photo_caption = lambda data: dict(SUGGESTION)
    try:
        answered = oc.post(f"/management/social/{pid}/suggest-words",
                           follow_redirects=True)
        s.check("the route stores what came back",
                bool(_row(pid)["suggested_json"]),
                detail=f"HTTP {answered.status_code}")
        s.check("and stamps when it was asked for",
                bool(_row(pid)["suggested_at"]),
                detail="a suggestion with no date on it cannot be told from "
                       "one written before the photograph was changed")
        s.check("but it does NOT touch the caption",
                not (_row(pid)["caption"] or "").strip(),
                detail=f"{_row(pid)['caption']!r} — `suggested_json` is not "
                       "`caption`, and nothing but a person fills the second")
        s.check("and does not promote the post to a draft",
                _row(pid)["status"] == "idea",
                detail=f"{_row(pid)['status']} — a post that looks finished is "
                       "one nobody reads before it goes out")
        s.check("it sends the small copy, not the master",
                m.PHOTO_SIZES["vision"][0] <= 1568,
                detail="anything longer is resized and discarded before the "
                       "model looks at it, so it is paid for and thrown away")
    finally:
        m.claude_configured, m.suggest_photo_caption = real_configured, real_suggest
    s.check("and the stand-in is put back",
            not m.claude_configured(),
            detail="leaving it patched would let every suite after this one "
                   "believe the API is reachable")

    conn = db()
    conn.execute("UPDATE social_posts SET suggested_json = ? WHERE id = ?",
                 (json.dumps(SUGGESTION), pid))
    conn.commit()
    conn.close()
    page = oc.get("/management/social").get_data(as_text=True)
    s.check("all of the options are offered, not just one",
            all(c in page for c in SUGGESTION["captions"]),
            detail="three near-identical lines is no choice, and one line is "
                   "not a suggestion, it is an instruction")
    s.check("the photograph is shown beside them",
            "/photo/thumb/" in page,
            detail="choosing words for a picture you cannot see is guessing")
    s.check("and the caption is STILL empty",
            not (_row(pid)["caption"] or "").strip(),
            detail="having a suggestion is not having a caption")
    s.check("the post is still an idea",
            _row(pid)["status"] == "idea",
            detail="an idea is an honest thing to finish; a draft looks done")

    s.section("A person picks one")
    oc.post(f"/management/social/{pid}/use-words", data={"which": "1"},
            follow_redirects=True)
    chosen = _row(pid)
    s.check("that one becomes the caption",
            chosen["caption"] == SUGGESTION["captions"][1],
            detail=f"{chosen['caption']!r}")
    s.check("and NOT the first one, which is what an off-by-one would give",
            chosen["caption"] != SUGGESTION["captions"][0],
            detail="picking the third and getting the first is a mistake "
                   "nobody would spot until it had gone out")
    s.check("the post becomes a draft now there are words in it",
            chosen["status"] == "drafted", detail=f"{chosen['status']}")
    s.check("and the alt text nobody ever fills in is filled in",
            chosen["alt_text"] == SUGGESTION["alt_text"],
            detail=f"{chosen['alt_text']!r}")

    s.section("Alt text somebody wrote is not overwritten")
    pid2, _n2 = _post_with_photo(alt=TAG + " mine, written by hand")
    conn = db()
    conn.execute("UPDATE social_posts SET suggested_json = ? WHERE id = ?",
                 (json.dumps(SUGGESTION), pid2))
    conn.commit()
    conn.close()
    oc.post(f"/management/social/{pid2}/use-words", data={"which": "0"},
            follow_redirects=True)
    s.check("what a person wrote stays",
            _row(pid2)["alt_text"] == TAG + " mine, written by hand",
            detail=f"{_row(pid2)['alt_text']!r} — a suggestion that overwrites "
                   "work is one you stop pressing")

    s.section("Choosing one that is not there")
    out_of_range = oc.post(f"/management/social/{pid2}/use-words",
                           data={"which": "99"}, follow_redirects=True)
    s.check("does not fall over", out_of_range.status_code == 200,
            detail=f"HTTP {out_of_range.status_code}")
    s.check("and does not blank the caption",
            (_row(pid2)["caption"] or "").strip() == SUGGESTION["captions"][0],
            detail=f"{_row(pid2)['caption']!r}")
    s.check("nor does a word where a number should be",
            oc.post(f"/management/social/{pid2}/use-words",
                    data={"which": "banana"},
                    follow_redirects=True).status_code == 200)

    s.section("Not for anybody who is not the owner")
    # WRITTEN THE WRONG WAY ROUND FIRST TIME. It read `status != 302 or
    # "login" in Location`, which is true the moment the page returns 200 --
    # so it would have passed loudest in exactly the case it exists to catch.
    refused = ec.post(f"/management/social/{pid}/suggest-words")
    s.check("an employee cannot ask for suggestions",
            refused.status_code != 200,
            detail=f"HTTP {refused.status_code} — the social list is the "
                   "owner's, and so is the bill for asking")
    s.check("a post that does not exist is a 404",
            oc.post("/management/social/99991234/use-words").status_code == 404)

    _cleanup()
    return s
