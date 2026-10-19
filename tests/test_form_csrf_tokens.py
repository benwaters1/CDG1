"""Every form that posts carries the token the app checks for.

On 17 Oct 2026 the owner tried three times on the live site to point the
technical mail at website@. Every Save answered "Your session timed out --
please try that again." and nothing changed: the form had no csrf_token input,
so CSRF protection refused it, and handle_csrf_error dresses that refusal up as
a timeout. The backups went on landing in accounts@.

test_tech_email was green the whole time. The suite turns CSRF off so suites
can post freely, so it posted straight to the route and never looked at what
the page drew. A form is only as good as what the browser can send from it.

What this holds:

  ON THE SOURCE: every <form method="post"> in templates/ carries a
  csrf_token field, or posts to a view the app has exempted from CSRF on
  purpose. The exemption is read from the app (CSRFProtect's own list), not a
  typed one, so a form can only go without a token if its route was written
  to go without one. A form with no action posts back to the page it is on;
  those are the only ones named here, each with its reason, and each is
  checked against the app too.

  ON THE PAGE: with CSRF switched ON, the owner opens the Guest email page,
  the token is read out of the rendered tech-address form, and the save posts
  exactly what that form holds. It must stick. Without the input it is
  refused, which is what happened on the live site.
"""
import glob
import os
import re

import _harness
from _harness import Suite

import app as m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TECH = "website-csrf@example.invalid"

# A form with no action posts back to whatever URL drew it. The template does
# not say which view that is, so it is named here, with why it goes without a
# token. The view must ALSO be exempt in the app, or this suite fails.
POSTS_BACK_TO = {
    # The link in a campaign letter. A guest pressing "unsubscribe" days later
    # has no session with us, so there is no token for them to carry; the
    # unguessable token in the URL is the proof it is them.
    "unsubscribe.html": "campaign_unsubscribe",
}

_FORM = re.compile(r"<form\b[^>]*>", re.I | re.S)


def _exempt(endpoint):
    view = m.app.view_functions.get(endpoint)
    if view is None:
        return False
    return f"{view.__module__}.{view.__name__}" in m.csrf._exempt_views


def tokenless_post_forms():
    """[(where, why)] for every post form that would be refused."""
    bad = []
    for path in sorted(glob.glob(os.path.join(ROOT, "templates", "**", "*.html"),
                                 recursive=True)):
        name = os.path.relpath(path, os.path.join(ROOT, "templates")).replace(os.sep, "/")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for mo in _FORM.finditer(src):
            tag = mo.group(0)
            if not re.search(r"""method\s*=\s*["']?post""", tag, re.I):
                continue
            end = src.find("</form>", mo.end())
            body = src[mo.end():end if end != -1 else len(src)]
            if "csrf_token" in body:
                continue
            where = f"{name}:{src.count(chr(10), 0, mo.start()) + 1}"
            target = re.search(r"""url_for\(\s*['"]([\w.]+)['"]""", tag)
            if target:
                if not _exempt(target.group(1)):
                    bad.append((where, f"posts to {target.group(1)}, which checks the token"))
            elif "action=" in tag:
                bad.append((where, "posts to a typed address the suite cannot resolve"))
            else:
                endpoint = POSTS_BACK_TO.get(name)
                if not endpoint:
                    bad.append((where, "posts back to its own page and is not on the list"))
                elif not _exempt(endpoint):
                    bad.append((where, f"listed as posting to {endpoint}, which is not exempt"))
    return bad


def run():
    s = Suite("form_csrf_tokens")
    conn = m.get_db()
    put_back = _harness.keep_rows(conn, "app_settings", "key = ?", (m.TECH_EMAIL_KEY,))
    try:
        _checks(s, conn)
    finally:
        m.app.config["WTF_CSRF_ENABLED"] = False
        conn.execute("DELETE FROM audit_log WHERE action = 'tech_email_changed' "
                     "AND details LIKE ?", ("%example.invalid%",))
        conn.commit()
        put_back()
        conn.close()
    return s


def _checks(s, conn):
    s.section("Every post form in templates/ carries the token, or is exempt on purpose")
    bad = tokenless_post_forms()
    s.check("no post form goes without a token", not bad,
            detail="; ".join(f"{w} ({why})" for w, why in bad[:8]))
    s.check("the posts-back list names only views the app really has",
            all(e in m.app.view_functions for e in POSTS_BACK_TO.values()),
            detail=str(POSTS_BACK_TO))

    s.section("The technical address saves from the page as drawn, CSRF on")
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.TECH_EMAIL_KEY,))
    conn.commit()
    oc, _ec, _owner, _emp = _harness.clients()
    m.app.config["WTF_CSRF_ENABLED"] = True
    try:
        page = oc.get("/admin/emails")
        s.check("the Guest email page answers", page.status_code == 200, page)
        html = page.get_data(as_text=True)
        form = next((f for f in _harness.forms_on(html)
                     if (f["action"] or "").endswith("/admin/emails/tech-address")), None)
        s.check("it draws the technical-address form", form is not None)
        fields = {f["name"]: f["value"] for f in (form or {}).get("fields", [])}
        s.check("and that form carries a token", bool(fields.get("csrf_token")),
                detail=str(sorted(fields)))
        fields["tech_email"] = TECH
        r = oc.post(form["action"] if form else "/admin/emails/tech-address",
                    data=fields, follow_redirects=True)
        said = " ".join(_harness.flashes(r))
        s.check("the save is not refused as a timed-out session",
                "timed out" not in said, detail=said)
        s.check("and the address is stored", m.tech_email_setting(conn) == TECH,
                detail=repr(m.tech_email_setting(conn)))

        # The same post without the token is the live site's 17 Oct: refused.
        conn.execute("DELETE FROM app_settings WHERE key = ?", (m.TECH_EMAIL_KEY,))
        conn.commit()
        bare = {k: v for k, v in fields.items() if k != "csrf_token"}
        r = oc.post("/admin/emails/tech-address", data=bare, follow_redirects=True)
        s.check("without the token the same save is refused and changes nothing",
                not m.tech_email_setting(conn)
                and "timed out" in " ".join(_harness.flashes(r)),
                detail=repr(m.tech_email_setting(conn)))
    finally:
        m.app.config["WTF_CSRF_ENABLED"] = False
