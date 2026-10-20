"""One newsletter, one sign-up form, one confirmation letter.

The owner, 19 October 2026, looking at the Resend log: "why's there 2 of lots
of emails, is it because they have multiple ticks on here, it should just be 1
thing." Four addresses in a row had each been sent "Confirm your subscription"
twice, two minutes apart.

It was not the ticks. newsletter_subscribe never read them -- "What would you
like to hear about?" with Rooms, Workshop dates and The restoration was drawn
on ten forms and thrown away on arrival. What doubled the letters was that the
route sent the confirmation again on EVERY post for an address still waiting to
confirm, with no window, and those addresses were each posted twice. Several
looked like scripts (a gmail address with a dot between every letter is the
signature of one), and no newsletter box carried the bot guard the booking
forms got the day before.

Checked here, each with the failure it guards:

  - one letter per address per NEWSLETTER_CONFIRM_RESEND_HOURS, however many
    posts, ticks or no ticks, including rows from before the column existed;
  - confirmed and opted-out addresses are never sent one, and the page says the
    same thing whichever happened;
  - the bot guard turns a script away from the newsletter and the room waiting
    list, writing nothing and sending nothing, and lets a person through;
  - double opt-in still works end to end: confirm link, confirmed page,
    unsubscribe;
  - the source: no tick on any template, every sign-up form guarded, and the
    footer and the Contact page carry the one form -- because public templates
    arrive from the design side as whole files, and a handover that drops the
    footer line renders perfectly;
  - the rendered pages: the footer sign-up on every public page, Contact's own,
    no repeated ids;
  - tools/repair_handover.py puts all of it back in a doctored copy;
  - the owner's list page.
"""
import glob
import importlib.util
import io
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, form_stamp
import _harness

m = _harness.m
TAG = "zznlsign"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATES = os.path.join(ROOT, "templates")
_ip = [0]


def _client():
    """A fresh visitor from an address of its own, so the hourly limit on one
    address never decides a check about something else."""
    _ip[0] += 1
    return m.app.test_client(), {"REMOTE_ADDR": f"198.51.100.{_ip[0]}"}


def _person():
    """The two fields a person's browser sends from a guarded form."""
    return {m.BOT_HONEYPOT_FIELD: "", m.BOT_STAMP_FIELD: form_stamp(60)}


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM newsletter_subscribers WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM consent_events WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE ip_address LIKE '198.51.100.%'")
    conn.commit()
    conn.close()


class _Letters:
    """send_email stood in for: what would have gone, to whom."""

    def __init__(self):
        self.sent = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.sent.append((to_address, subject))
        return True

    def to(self, email):
        return [s for t, s in self.sent if t == email]


def _row(email):
    conn = db()
    r = conn.execute("SELECT * FROM newsletter_subscribers WHERE email = ?", (email,)).fetchone()
    conn.close()
    return r


def _sub(email, extra=None, guard=True):
    c, env = _client()
    data = {"email": email}
    if guard:
        data.update(_person())
    data.update(extra or {})
    return c.post("/newsletter/subscribe", data=data, environ_base=env, follow_redirects=True)


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


SIGNUP_FORM = re.compile(
    r"<form\b[^>]*action=\"\{\{ url_for\('(newsletter_subscribe|join_waitlist)'\) \}\}\"[^>]*>(.*?)</form>",
    re.S)


def run():
    s = Suite("Newsletter sign-up: one list, one letter")
    letters = _Letters()
    real_send = m.send_email
    m.send_email = letters
    _cleanup()
    try:
        _run(s, letters)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, letters):
    thanks = "please check your email"

    s.section("One letter, however many times the address is typed")
    a = f"{TAG}.one@example.invalid"
    r1 = _sub(a, {"interests": ["stays", "restoration"]})
    s.check("a new address with two topics ticked is sent ONE letter, not one per tick",
            len(letters.to(a)) == 1, r1, detail=str(letters.to(a)))
    s.check("and it is the confirmation", letters.to(a)[:1] and "Confirm" in letters.to(a)[0],
            detail=str(letters.to(a)))
    row = _row(a)
    s.check("when it went is written down", row is not None and row["confirm_sent_at"],
            detail=str(dict(row) if row else None))
    s.check("the page thanks them", any(thanks in f for f in flashes(r1)), r1)
    r2 = _sub(a)
    s.check("signing up again two minutes later sends nothing more -- the Resend log's pairs",
            len(letters.to(a)) == 1, r2, detail=str(letters.to(a)))
    s.check("and the page says exactly the same thing", flashes(r2) == flashes(r1),
            detail=f"{flashes(r1)} / {flashes(r2)}")
    for _ in range(3):
        _sub(a)
    s.check("five posts in a row are still one letter", len(letters.to(a)) == 1,
            detail=str(letters.to(a)))

    s.section("The window, and the rows from before it existed")
    conn = db()
    old = (datetime.now(timezone.utc)
           - timedelta(hours=m.NEWSLETTER_CONFIRM_RESEND_HOURS + 1)).isoformat()
    conn.execute("UPDATE newsletter_subscribers SET confirm_sent_at = ? WHERE email = ?", (old, a))
    conn.commit()
    conn.close()
    _sub(a)
    s.check(f"past {m.NEWSLETTER_CONFIRM_RESEND_HOURS} hours a person can ask again, and gets one",
            len(letters.to(a)) == 2, detail=str(letters.to(a)))
    _sub(a)
    s.check("and only one", len(letters.to(a)) == 2, detail=str(letters.to(a)))

    legacy_new, legacy_old = f"{TAG}.legacy1@example.invalid", f"{TAG}.legacy2@example.invalid"
    conn = db()
    for email, hours in ((legacy_new, 2), (legacy_old, m.NEWSLETTER_CONFIRM_RESEND_HOURS + 6)):
        made = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        conn.execute("""INSERT INTO newsletter_subscribers (email, token, source, created_at)
                        VALUES (?, ?, 'site', ?)""", (email, m.secrets.token_urlsafe(16), made))
    conn.commit()
    conn.close()
    _sub(legacy_new)
    s.check("a row from before the column, signed up two hours ago, is not sent another",
            letters.to(legacy_new) == [], detail=str(letters.to(legacy_new)))
    _sub(legacy_old)
    s.check("one signed up over a day ago is", len(letters.to(legacy_old)) == 1,
            detail=str(letters.to(legacy_old)))

    s.section("Never to somebody already on the list, or who said no")
    conn = db()
    conn.execute("UPDATE newsletter_subscribers SET confirmed_at = ?, confirm_sent_at = ? "
                 "WHERE email = ?", (old, old, legacy_old))
    conn.commit()
    conn.close()
    before = len(letters.to(legacy_old))
    rc = _sub(legacy_old)
    s.check("a confirmed subscriber is sent nothing", len(letters.to(legacy_old)) == before,
            rc, detail=str(letters.to(legacy_old)))
    s.check("and told the same as a stranger", any(thanks in f for f in flashes(rc)), rc)
    no = f"{TAG}.no@example.invalid"
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'test', ?)",
                 (no, old))
    conn.commit()
    conn.close()
    _sub(no)
    s.check("an address on the do-not-write list is sent nothing", letters.to(no) == [],
            detail=str(letters.to(no)))

    s.section("Scripts are turned away; people are not")
    bot = f"{TAG}.bot@example.invalid"
    rb = _sub(bot, {m.BOT_HONEYPOT_FIELD: "http://spam.example", m.BOT_STAMP_FIELD: form_stamp(60)},
              guard=False)
    s.check("a post that filled the hidden field stores nothing", _row(bot) is None, rb)
    s.check("and sends nothing", letters.to(bot) == [], detail=str(letters.to(bot)))
    s.check("and is told to check and send again, not thanked",
            any("did not go through" in f for f in flashes(rb)), rb, detail=str(flashes(rb)))
    fast = f"{TAG}.fast@example.invalid"
    # The harness sets the minimum to 0 so other suites can post at once; the
    # shipped value is what a script meets.
    held, m.PUBLIC_FORM_MIN_SECONDS = m.PUBLIC_FORM_MIN_SECONDS, 4
    try:
        _sub(fast, {m.BOT_HONEYPOT_FIELD: "", m.BOT_STAMP_FIELD: form_stamp(0)}, guard=False)
    finally:
        m.PUBLIC_FORM_MIN_SECONDS = held
    s.check("a post sent the instant the page was drawn stores nothing",
            _row(fast) is None and letters.to(fast) == [])
    forged = f"{TAG}.forged@example.invalid"
    _sub(forged, {m.BOT_HONEYPOT_FIELD: "", m.BOT_STAMP_FIELD: "1700000000.0000"}, guard=False)
    s.check("a made-up stamp stores nothing", _row(forged) is None)
    conn = db()
    noted = conn.execute("SELECT COUNT(*) AS c FROM submission_log "
                         "WHERE action = 'newsletter_subscribe:bot' "
                         "AND ip_address LIKE '198.51.100.%'").fetchone()["c"]
    conn.close()
    s.check("each refusal is written down where the owner's count reads it", noted >= 3,
            detail=str(noted))
    bare = f"{TAG}.bare@example.invalid"
    rb = _sub(bare, guard=False)
    s.check("a post with no stamp at all never had the form in front of it, and is refused",
            _row(bare) is None and letters.to(bare) == [], rb)

    s.section("One inbox, however it is spelt")
    s.check("Gmail ignores dots and +tags, so these are one inbox",
            m.gmail_inbox("a.lice+news@googlemail.com") == m.gmail_inbox("alice@gmail.com")
            == "alice@gmail.com")
    s.check("and nowhere else does: at Outlook a dot is part of the name",
            m.gmail_inbox("a.lice@outlook.com") is None)
    scattered = "zz.n.l.s.ign@gmail.com"
    rs = _sub(scattered)
    s.check("a Gmail address with a dot between most of its letters is written to nobody",
            _row(scattered) is None and letters.to(scattered) == [], rs)
    s.check("and is thanked like anybody else, so the script learns nothing",
            any(thanks in f for f in flashes(rs)), rs, detail=str(flashes(rs)))
    two = "zznlsign.mary.jane@gmail.com"
    _sub(two)
    s.check("a person's own two dots are fine", len(letters.to(two)) == 1, detail=str(letters.to(two)))
    elsewhere = f"{TAG}.a.b.c@outlook.example"
    _sub(elsewhere)
    s.check("and dots at any other provider are the person's name, not a trick",
            len(letters.to(elsewhere)) == 1)
    variant = "zznlsignmary.jane@gmail.com"
    _sub(variant)
    _sub("zznlsignmaryjane+news@googlemail.com")
    s.check("another spelling of an inbox already written to today is sent nothing",
            letters.to(variant) == [] and letters.to("zznlsignmaryjane+news@googlemail.com") == [],
            detail=str(letters.sent[-3:]))

    human = f"{TAG}.human@example.invalid"
    _sub(human)
    s.check("a person a minute after the page was drawn goes straight through",
            _row(human) is not None and len(letters.to(human)) == 1)

    wl = f"{TAG}.wl@example.invalid"
    c, env = _client()
    arrive = (m.house_today() + timedelta(days=60)).isoformat()
    leave = (m.house_today() + timedelta(days=62)).isoformat()
    c.post("/waitlist/join", data={"name": "Bot", "email": wl, "desired_arrival": arrive,
                              "desired_departure": leave, m.BOT_HONEYPOT_FIELD: "x",
                              m.BOT_STAMP_FIELD: form_stamp(60)},
           environ_base=env, follow_redirects=True)
    conn = db()
    wl_rows = conn.execute("SELECT COUNT(*) AS c FROM waitlist_entries WHERE email = ?",
                           (wl,)).fetchone()["c"]
    conn.close()
    s.check("the room waiting list turns a script away too", wl_rows == 0, detail=str(wl_rows))
    c.post("/waitlist/join", data={"name": "Person", "email": wl, "desired_arrival": arrive,
                              "desired_departure": leave, **_person()},
           environ_base=env, follow_redirects=True)
    conn = db()
    wl_rows = conn.execute("SELECT COUNT(*) AS c FROM waitlist_entries WHERE email = ?",
                           (wl,)).fetchone()["c"]
    conn.close()
    s.check("and takes a person", wl_rows == 1, detail=str(wl_rows))

    s.section("Double opt-in still works end to end")
    pub = m.app.test_client()
    token = _row(human)["token"]
    rc = pub.get(f"/newsletter/confirm/{token}")
    s.check("the link in the letter confirms", rc.status_code == 200 and _row(human)["confirmed_at"],
            detail=str(rc.status_code))
    conn = db()
    on = [r["email"] for r in m.newsletter_recipients(conn)]
    conn.close()
    s.check("and they are on the one list", human in on)
    ru = pub.post(f"/newsletter/unsubscribe/{token}")
    s.check("the unsubscribe link takes them off", ru.status_code == 200
            and _row(human)["unsubscribed_at"] and not _row(human)["confirmed_at"],
            detail=str(ru.status_code))
    before = len(letters.to(human))
    _sub(human)
    s.check("and typing the address again afterwards does not write to them",
            len(letters.to(human)) == before)

    s.section("The source: one form, no ticks, every sign-up guarded")
    pages = sorted(glob.glob(os.path.join(TEMPLATES, "*.html")))
    ticked = [os.path.basename(p) for p in pages
              if re.search(r'name="interests"|class="g-interests"|What would you like to hear about',
                           _read(os.path.relpath(p, ROOT)))]
    s.check("no template asks what they would like to hear about", not ticked, detail=str(ticked))
    unguarded, forms = [], 0
    for p in pages:
        for mt in SIGNUP_FORM.finditer(_read(os.path.relpath(p, ROOT))):
            forms += 1
            if "bot_guard()" not in mt.group(2):
                unguarded.append(f"{os.path.basename(p)} ({mt.group(1)})")
    s.check("every form that signs somebody up carries the bot guard", forms and not unguarded,
            detail=f"{forms} forms; unguarded: {unguarded}")
    inline = [os.path.basename(p) for p in pages
              if os.path.basename(p) != "_newsletter.html"
              and re.search(r'class="g-journal__form"|class="g-navwrap__news"',
                            _read(os.path.relpath(p, ROOT)))]
    s.check("the newsletter form is drawn only by the partial, never copied inline",
            not inline, detail=str(inline))
    base = _read("templates/public_base.html")
    foot = base[base.find("<footer"):base.find("</footer>")]
    s.check("public_base's footer draws the sign-up -- the line a handover drops",
            "newsletter_block('footer')" in foot)
    s.check("the menu panel draws it too", "newsletter_form('panel')" in base)
    s.check("the Contact page draws its own", "newsletter_block('contact')"
            in _read("templates/contact.html"))
    s.check("the footer's rules are in the stylesheet",
            ".g-news--footer{" in _read("static/gudanes.css"))

    s.section("The rendered pages")
    with m.app.test_request_context():
        urls = ["/"] + [m.url_for(e) for e in (
            "contact_page", "book_rooms", "workshops_public", "privacy_page", "terms_page",
            "press", "events_info", "whats_on", "facilities_page", "story", "social_page")
            if e in m.app.view_functions]
    missing, ticks_seen, dup_ids, unguarded_r = [], [], [], []
    for url in urls:
        r = pub.get(url)
        html = r.get_data(as_text=True)
        if r.status_code != 200:
            missing.append(f"{url} {r.status_code}")
            continue
        foot = html[html.find("<footer"):html.find("</footer>")]
        if 'data-newsletter="footer"' not in foot:
            missing.append(url)
        if 'name="interests"' in html:
            ticks_seen.append(url)
        ids = re.findall(r'\bid="(nl-email[^"]*)"', html)
        if len(ids) != len(set(ids)):
            dup_ids.append(f"{url} {ids}")
        for f in re.findall(r'<form\b[^>]*data-newsletter="[^"]+".*?</form>', html, re.S):
            if m.BOT_HONEYPOT_FIELD not in f or m.BOT_STAMP_FIELD not in f:
                unguarded_r.append(url)
    s.check(f"the footer sign-up is on every public page tried ({len(urls)})", not missing,
            detail=str(missing))
    s.check("no page renders a tick", not ticks_seen, detail=str(ticks_seen))
    s.check("no page repeats an id, so each label points at its own box", not dup_ids,
            detail=str(dup_ids))
    s.check("every rendered sign-up carries both guard fields", not unguarded_r,
            detail=str(unguarded_r))
    contact = pub.get(urls[1]).get_data(as_text=True)
    s.check("Contact shows its own sign-up as well as the footer's",
            'data-newsletter="contact"' in contact and 'data-newsletter="footer"' in contact)
    home = pub.get("/").get_data(as_text=True)
    s.check("and the menu panel's is still there", 'data-newsletter="panel"' in home)

    s.section("A handover that brings the old form back is repaired")
    _repair_check(s)

    s.section("The owner's list")
    oc, ec, owner, emp = clients()
    r = oc.get("/admin/newsletter")
    page = r.get_data(as_text=True)
    s.check("the page opens", r.status_code == 200, r, detail=str(r.status_code))
    s.check("it lists an address waiting to confirm", a in page)
    s.check("and says where each stands", "Waiting to confirm" in page and "Left" in page)
    s.check("and counts the scripts turned away", "Scripts turned away" in page)
    r = oc.get("/admin/newsletter?q=" + legacy_old)
    s.check("its search finds one address", legacy_old in r.get_data(as_text=True)
            and a not in r.get_data(as_text=True))
    if emp:
        r = ec.get("/admin/newsletter")
        s.check("staff without the owner's keys are not shown it", r.status_code in (302, 403),
                detail=str(r.status_code))


def _repair_check(s):
    """Run the repair step on a throwaway copy whose templates carry what an
    old handover would: the footer and Contact sign-ups gone, the ticks back,
    an inline form with no guard, and the stylesheet without the footer rules."""
    tmp = tempfile.mkdtemp(prefix="nlrepair-")
    try:
        os.makedirs(os.path.join(tmp, "templates"))
        os.makedirs(os.path.join(tmp, "static"))
        for name in ("public_base.html", "contact.html", "_newsletter.html"):
            shutil.copy(os.path.join(TEMPLATES, name), os.path.join(tmp, "templates", name))
        shutil.copy(os.path.join(ROOT, "static", "gudanes.css"),
                    os.path.join(tmp, "static", "gudanes.css"))
        git = ("git", "-c", "user.email=t@example.invalid", "-c", "user.name=T",
               "-c", "core.autocrlf=false")
        subprocess.run(git + ("init", "-q"), cwd=tmp, check=True)
        subprocess.run(git + ("add", "."), cwd=tmp, check=True)
        subprocess.run(git + ("commit", "-qm", "base"), cwd=tmp, check=True)

        def edit(rel, fn):
            path = os.path.join(tmp, rel)
            with io.open(path, encoding="utf-8") as f:
                text = f.read().replace("\r\n", "\n")
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(fn(text))

        edit("templates/public_base.html", lambda t: re.sub(
            r"[^\n]*import newsletter_block with context %\}\n\s*\{\{ newsletter_block\('footer'\) \}\}\n", "", t))
        edit("templates/contact.html", lambda t: re.sub(
            r"\{% from '_newsletter.html' import newsletter_block with context %\}\n"
            r"<section[^\n]*\n\s*\{\{ newsletter_block\('contact'\) \}\}\n</div></section>\n", "", t))
        old_form = (
            '<form class="g-journal__form" method="post" action="{{ url_for(\'newsletter_subscribe\') }}">\n'
            '  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">\n'
            '  <input type="email" name="email" required>\n'
            '    <fieldset class="g-interests">\n'
            '      <legend>What would you like to hear about?</legend>\n'
            '      <label><input type="checkbox" name="interests" value="stays" checked> Rooms</label>\n'
            '    </fieldset>\n'
            '  <button type="submit">Subscribe</button>\n</form>\n')
        with io.open(os.path.join(tmp, "templates", "home.html"), "w", encoding="utf-8") as f:
            f.write("{% extends 'public_base.html' %}{% block content %}\n" + old_form
                    + "{% endblock %}\n")

        def drop_rules(t):
            at = t.index(".g-news--footer{")
            start = t.rindex("/* ====", 0, at)
            end = t.find("\n/* ====", at)
            return t[:start] + (t[end:] if end != -1 else "")
        edit("static/gudanes.css", drop_rules)

        doctored_base = _read_at(tmp, "templates/public_base.html")
        s.check("(the doctored copy really has lost the footer sign-up)",
                "newsletter_block('footer')" not in doctored_base)

        spec = importlib.util.spec_from_file_location(
            "repair_handover_nl", os.path.join(ROOT, "tools", "repair_handover.py"))
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        tool.ROOT = tmp
        tool.TEMPLATE_DIR = os.path.join(tmp, "templates")
        n = tool.repair_newsletter_signup()
        base = _read_at(tmp, "templates/public_base.html")
        foot = base[base.find("<footer"):base.find("</footer>")]
        s.check("the footer sign-up is put back inside the footer",
                "newsletter_block('footer')" in foot, detail=f"{n} repairs")
        s.check("the Contact page's is put back",
                "newsletter_block('contact')" in _read_at(tmp, "templates/contact.html"))
        home = _read_at(tmp, "templates/home.html")
        s.check("the ticks are taken out", "g-interests" not in home and "interests" not in home,
                detail=home)
        s.check("the inline form gains the bot guard", "bot_guard()" in home, detail=home)
        s.check("the footer's rules come back from git",
                ".g-news--footer{" in _read_at(tmp, "static/gudanes.css"))
        again = tool.repair_newsletter_signup()
        s.check("and a second run changes nothing", again == 0, detail=str(again))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_at(root, rel):
    with io.open(os.path.join(root, rel), encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    print(run().report())
