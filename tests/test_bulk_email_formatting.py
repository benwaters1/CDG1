"""Send bulk email: in the menu, and written with fonts and sizes (25 Nov 2026).

The owner, beside RetreatGuru's programme page and its "Send bulk email": "where do i send batch emails to workshop
guests", "add in the font and stuff like that and size", "its not very intuitive the menus either".

WHAT IS HELD

  - A page of its own, Send Bulk Email, in the owner's menu (under the retreats and under email): every retreat
    coming up, party of rooms and dinner night, each with how many a letter reaches and its button. The counts are
    the send page's own. Staff who are not the owner are turned away, as from the send page.
  - Every button to it says "Send bulk email".
  - The message is written in a formatting editor: style, font, size, bold, italic, underline, colour, lining up,
    lists, links, and an Insert menu of the person's own details by name. In a browser, choosing them writes what
    the server keeps (headless Chrome runs the page's own script).
  - What goes out is only what a letter may carry (email_rich_html): scripts and frames go with their contents, so
    do event handlers and javascript: links; only the toolbar's fonts, sizes and colours survive, re-drawn in the
    letter's own inline styles; links are http(s), mailto or a merge tag.
  - The details are filled in escaped, in text and in a link, so a name with "&" in it cannot break the letter.
  - Each letter carries a plain copy made from the same words, and the house's letter is drawn around the formatted
    one; the preview shows that letter, sandboxed.
  - Without the editor (no script) the plain box is the letter, exactly as before.
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta

from _harness import Suite, db, clients, visible_text
import _harness

m = _harness.m
house_today = m.house_today
TAG = "ZZBEF"


class _Mail:
    """send_email, stood in for: every call kept, every one 'delivered'."""
    def __enter__(self):
        self.real, self.calls = m.send_email, []
        m.send_email = lambda to, subject, body, *a, **kw: self.calls.append(
            {"to": to, "subject": subject, "body": body, **kw}) or True
        return self.calls

    def __exit__(self, *exc):
        m.send_email = self.real
        return False


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute(f"DELETE FROM workshop_messages WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _session():
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity, active,
           sort_order, created_at, deposit_percent) VALUES (?, '', 2000, 30, 1, 96, ?, 30)""",
        (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()[0]
    start = house_today() + timedelta(days=61)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 30, ?, ?)""", (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(),
                                           f"{TAG} s", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} s",)).fetchone()[0]
    for ref, name, dne in (("A", "Tom & Jerry", 0), ("B", "Ann", 0), ("C", "Cy", 1)):
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
               reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount, do_not_email)
               VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, 2000, 600, 1400, ?)""",
            (sid, name, f"{TAG.lower()}{ref.lower()}@example.invalid", f"{TAG}{ref}", f"tok{TAG}{ref}x&y", now, dne))
    conn.commit()
    conn.close()
    return sid


def _opened(html_text):
    """Tags left open in some HTML: none, if it is well formed."""
    stack = []
    for close, tag in re.findall(r"<(/?)([a-z0-9]+)\b[^>]*>", html_text):
        if tag in ("br", "img", "hr"):
            continue
        if close:
            if not stack or stack[-1] != tag:
                return ["</%s> out of place" % tag]
            stack.pop()
        else:
            stack.append(tag)
    return stack


def run():
    s = Suite("Send bulk email: in the menu, and written with fonts and sizes")
    _cleanup()
    try:
        _run(s)
    finally:
        _cleanup()
    return s


def _run(s):
    keep = m.email_rich_html

    s.section("What a formatted letter may carry")
    out = keep('Hello <b>there</b><script>alert(1)</script> <a href="javascript:alert(2)" onclick="x()">click</a>'
               '<iframe src="https://evil.test"></iframe><img src=x onerror=alert(3)><style>p{color:red}</style>')
    s.check("a script, a frame, an image and a stylesheet go, with what is inside them",
            not re.search(r"script|iframe|<img|onerror|alert|<style|color:red", out, re.I), detail=out)
    s.check("an event handler and a javascript: link go, and their words stay",
            "onclick" not in out and "javascript" not in out.lower() and "click" in out and "<strong>there</strong>" in out,
            detail=out)
    chrome = ('<p style="position:absolute; font-family: Georgia, serif; color: rgb(20, 48, 95)">One</p>'
              '<p>Two <span style="font-size: x-large; color: #ff0000; font-family: Comic Sans MS">big</span> '
              '<span style="font-size: 18px">near</span> <span style="color: rgb(115, 86, 45)">gold</span></p>'
              '<p style="text-align: center;">middle</p><div><br></div>')
    out = keep(chrome)
    s.check("the toolbar's font survives, re-drawn in full", "font-family:Georgia,&#x27;Times New Roman&#x27;,serif" in out
            or "font-family:Georgia,'Times New Roman',serif" in out, detail=out)
    s.check("and its colour", "color:#14305F" in out and "color:#73562D" in out, detail=out)
    s.check("a size the browser names, and one in pixels, become the toolbar's own",
            "font-size:24px" in out and "font-size:19px" in out, detail=out)
    s.check("a colour or font the toolbar does not offer goes",
            "#ff0000" not in out.lower() and "comic" not in out.lower(), detail=out)
    s.check("and so does anything that is not lettering", "position" not in out, detail=out)
    s.check("centring is kept", "text-align:center" in out, detail=out)
    s.check("an empty line adds nothing", "<br>" not in out, detail=out)
    out = keep('<h1>Title</h1><ul><li>one</li><li>two <a href="{{manage_url}}">your page</a></li></ul>'
               '<ol><li>first</li></ol><p>Unclosed <strong>bold <em>both</p><p>next')
    s.check("headings, lists and a link to a merge tag are kept",
            "<h2 " in out and "<ul " in out and "<ol " in out and 'href="{{manage_url}}"' in out, detail=out)
    s.check("and every tag is closed, in order", not _opened(out), detail=str(_opened(out)))
    s.check("everything it writes carries the letter's own style, inline",
            all("style=" in t for t in re.findall(r"<(?:p|h2|ul|ol|li|a)\b[^>]*>", out)), detail=out)
    s.check("nothing at all is nothing", keep("<p><br></p><div> </div><script>x</script>") == "")
    text = m.rich_html_text(out)
    s.check("its plain copy has the paragraphs apart, the lists as lines and the link's address",
            "Title\n\n- one\n- two your page ({{manage_url}})" in text and "1. first" in text
            and "Unclosed bold both\n\nnext" in text, detail=repr(text))
    subject, filled = m.render_campaign_html("Hello {{first_name}}", '<p>Dear {{first_name}}, <a href="{{manage_url}}">here</a></p>',
                                             {"first_name": "Tom & <Jerry>", "manage_url": "https://x.test/m?a=1&b=2"})
    s.check("a detail is filled in escaped, in the words and in a link",
            "Dear Tom &amp; &lt;Jerry&gt;," in filled and 'href="https://x.test/m?a=1&amp;b=2"' in filled
            and subject == "Hello Tom & <Jerry>", detail=f"{subject} / {filled}")

    sid = _session()
    oc, ec, _owner, _emp = clients()
    with m.app.test_request_context():
        email_url = m.url_for("workshop_session_email", session_id=sid)
        hub_url = m.url_for("bulk_email_hub")

    s.section("Send Bulk Email is in the menu, and lists what it can reach")
    r = oc.get(hub_url)
    hub = r.get_data(as_text=True)
    s.check("the page opens for the owner", r.status_code == 200, r)
    row = re.search(r'<tr>\s*<th scope="row">' + re.escape(TAG) + r' Atelier, [^<]*</th>(.*?)</tr>', hub, re.S)
    s.check("the retreat is on it, with its own button", row and f'href="{email_url}"' in row.group(1)
            and "Send bulk email" in row.group(1), detail=(row.group(0)[:300] if row else hub[:0]))
    s.check("saying it goes to two, and leaves one out (emails off)",
            row and "2 people" in row.group(1) and re.search(r"<td>1</td>", row.group(1)),
            detail=row.group(1)[:300] if row else "")
    menu = oc.get("/admin/workshops/registrations").get_data(as_text=True)
    s.check("the owner's menu has Send Bulk Email", re.search(r'href="%s"[^>]*>.*?Send Bulk Email' % re.escape(hub_url),
                                                          menu, re.S) is not None)
    s.check("someone who is not the owner is turned away", ec.get(hub_url).status_code in (302, 403))
    reg = oc.get(f"/admin/workshops/registrations?session_id={sid}").get_data(as_text=True)
    s.check("the session's own button says Send bulk email",
            re.search(r'href="%s"[^>]*>Send bulk email<' % re.escape(email_url), reg) is not None)

    s.section("The send page: a formatting editor")
    page = oc.get(email_url).get_data(as_text=True)
    s.check("its heading says what it does", "Send bulk email to everyone on this session" in page)
    for what in ("Style", "Font", "Size", "Colour", "Insert"):
        s.check(f"a menu named {what}", re.search(r'<span class="rte__pick-k">%s</span>' % what, page) is not None)
    for label in ("Bold", "Italic", "Underline", "Centre", "List with dots", "Numbered list", "Link", "Clear formatting"):
        s.check(f"a button for {label}", f'aria-label="{label}"' in page)
    s.check("Insert offers the details by name, not in braces",
            ">First name<" in page and ">Their dates<" in page and ">Their reference<" in page)
    s.check("and the plain box is still there for a browser with no script",
            re.search(r'<textarea id="gm-body" name="body"[^>]*required', page) is not None)

    s.section("Sent formatted, to the right people, with their own details")
    rich = ('<p>Dear {{guest_name}},</p><p><strong>Bold</strong> and <span style="font-family: Georgia">Georgia</span>'
            ' and <span style="font-size: x-large">large</span>.</p><p><a href="{{manage_url}}">Your page</a></p>'
            '<script>alert(9)</script>')
    r = oc.post(email_url, data={"action": "preview", "subject": "Hello {{guest_name}}", "body": "",
                                 "body_html": rich, "expect": "2"})
    preview = r.get_data(as_text=True)
    letter = re.search(r'srcdoc="([^"]*)"', preview)
    drawn = htmllib.unescape(letter.group(1)) if letter else ""
    s.check("the preview is the letter itself, drawn and sandboxed",
            letter is not None and re.search(r"<iframe[^>]*\bsandbox\b", preview) is not None
            and "font-family:Georgia" in drawn and "<strong>Bold</strong>" in drawn, detail=drawn[:200])
    s.check("for the first person, with their name filled in", "Dear Ann," in drawn or "Tom &amp; Jerry" in drawn,
            detail=drawn[:400])
    s.check("and no script in it", "alert(9)" not in drawn and "<script" not in drawn.lower().split("</head>")[-1])
    with _Mail() as calls:
        r = oc.post(email_url, data={"action": "send", "subject": "Hello {{guest_name}}", "body": "",
                                     "body_html": rich, "expect": "2"})
    to = sorted(c["to"] for c in calls)
    s.check("it goes to the two, and not to the one with emails off",
            to == [f"{TAG.lower()}a@example.invalid", f"{TAG.lower()}b@example.invalid"], detail=str(to))
    tom = next((c for c in calls if c["to"].startswith(f"{TAG.lower()}a")), {})
    html_out = tom.get("html") or ""
    s.check("as the house's formatted letter", "<strong>Bold</strong>" in html_out and "font-family:Georgia" in html_out
            and "font-size:24px" in html_out, detail=html_out[:200])
    s.check("with Tom & Jerry's name escaped, and the subject in plain words",
            "Dear Tom &amp; Jerry," in html_out and tom.get("subject") == "Hello Tom & Jerry", detail=tom.get("subject"))
    s.check("their own booking page, escaped in the link",
            re.search(r'href="[^"]*tok' + TAG + r'Ax&amp;y', html_out) is not None, detail=html_out[:0])
    s.check("and nothing a mail app would run", "alert(9)" not in html_out and "<script" not in html_out.lower())
    s.check("with a plain copy saying the same",
            tom.get("body", "").startswith("Dear Tom & Jerry,") and "Your page (http" in tom.get("body", ""),
            detail=repr(tom.get("body", "")[:200]))

    s.section("And without the editor, the plain box is the letter, as before")
    with _Mail() as calls:
        oc.post(email_url, data={"action": "send", "subject": "Plain", "body": "Dear {{first_name}},\n\nSee you.",
                                 "body_html": "", "expect": "2"})
    ann = next((c for c in calls if c["to"].startswith(f"{TAG.lower()}b")), {})
    s.check("it goes as words, drawn by the house's letter as any letter is",
            ann.get("body") == "Dear Ann,\n\nSee you." and ann.get("html") is None, detail=str(ann)[:200])

    _browser(s, oc, email_url)


PROBE = r"""<script>
window.addEventListener('load', function () { setTimeout(function () {
  var out = {};
  try {
    var root = document.querySelector('[data-rte]'), area = document.querySelector('[data-rte-area]');
    out.shown = !root.hidden;
    out.plain_hidden = document.querySelector('[data-rte-plain]').hidden;
    area.innerHTML = '<p>Hello world</p>';
    var r = document.createRange(); r.selectNodeContents(area.querySelector('p'));
    area.focus(); var sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    setTimeout(function () {
      document.querySelector('[data-rte-cmd="bold"]').click();
      function pick(q, v) { var el = document.querySelector(q); el.value = v; el.dispatchEvent(new Event('change')); }
      pick('[data-rte-font]', document.querySelector('[data-rte-font] option:nth-child(3)').value);
      pick('[data-rte-size]', '5');
      pick('[data-rte-colour]', '#14305F');
      out.bold_on = document.querySelector('[data-rte-cmd="bold"]').getAttribute('aria-pressed');
      var s2 = getSelection(); s2.collapse(area.querySelector('p'), area.querySelector('p').childNodes.length);
      setTimeout(function () {
        pick('[data-rte-insert]', 'first_name');
        out.html = document.querySelector('[data-rte-out]').value;
        out.plain = document.querySelector('[data-rte-plain]').value;
        parent.postMessage(JSON.stringify(out), '*');
      }, 80);
    }, 80);
  } catch (e) { out.error = String(e); parent.postMessage(JSON.stringify(out), '*'); }
}, 300); });
</script>"""


def _browser(s, oc, email_url):
    s.section("In a browser, the toolbar writes what the server keeps")
    from test_staff_header_on_a_phone import find_browser
    from test_public_margins import _localise
    chrome = find_browser()
    s.check("headless Chrome is on this machine to run the page's own script", bool(chrome),
            detail="set GUDANES_CHROME; a check that could not run is not a pass")
    if not chrome:
        return
    work = tempfile.mkdtemp(prefix="gudanes_rte_")
    try:
        shutil.copy(os.path.join(_harness.ROOT, "static", "style.css"), os.path.join(work, "style.css"))
        page = _localise(oc.get(email_url).get_data(as_text=True)).replace("</body>", PROBE + "</body>", 1)
        with open(os.path.join(work, "p.html"), "w", encoding="utf-8") as fh:
            fh.write(page)
        with open(os.path.join(work, "w.html"), "w", encoding="utf-8") as fh:
            fh.write('<body><script>addEventListener("message",function(e){var p=document.createElement("pre");'
                     'p.className="r";p.textContent=e.data;document.body.appendChild(p)})</script>'
                     '<iframe src="p.html" style="width:1100px;height:900px"></iframe></body>')
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--allow-file-access-from-files",
               "--host-resolver-rules=MAP * ~NOTFOUND", "--user-data-dir=" + os.path.join(work, "prof"),
               "--virtual-time-budget=10000", "--dump-dom",
               "file:///" + os.path.join(work, "w.html").replace("\\", "/").lstrip("/")]
        if sys.platform.startswith("linux"):
            cmd.insert(1, "--no-sandbox")
        dom = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=300).stdout
        found = re.search(r'<pre class="r">(.*?)</pre>', dom, re.S)
        got = json.loads(htmllib.unescape(found.group(1))) if found else {}
        s.check("the page's script ran", got and not got.get("error"), detail=str(got)[:300])
        s.check("the editor takes the plain box's place", got.get("shown") and got.get("plain_hidden"), detail=str(got)[:200])
        kept = m.email_rich_html(got.get("html") or "")
        s.check("Bold, chosen on the toolbar, is kept", "<strong>" in kept or "font-weight:bold" in kept, detail=kept)
        s.check("and shows as on", got.get("bold_on") == "true", detail=str(got.get("bold_on")))
        s.check("a font chosen from the menu is kept", "font-family:Georgia" in kept, detail=kept)
        s.check("a size, Larger, is kept as the toolbar's own", "font-size:24px" in kept, detail=kept)
        s.check("a colour, Navy, is kept", "color:#14305F" in kept, detail=kept)
        s.check("Insert puts in the detail, ready to be filled", "{{first_name}}" in kept, detail=kept)
        s.check("and the plain copy goes with it", "Hello world" in (got.get("plain") or ""), detail=str(got.get("plain")))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    print(run().report())
