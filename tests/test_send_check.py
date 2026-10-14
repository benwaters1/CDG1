"""The check on Send, as Outlook runs it.

THE FAULT IT IS HERE FOR (14 October 2026). In Outlook on the web, pressing
Send showed "Gudanes is taking longer than expected" with one button, Don't
send, and nothing could be sent -- internal mail included. office.js in a
browser does not start until the page calls Office.onReady(), and the event
page never did: so no handler ran, Outlook waited, and SoftBlock left nobody
a way out. Nothing errored and no test went red, because every check before
this one read the script's SOURCE, and the source looked fine.

So this RUNS it: the real launchevent.js, exactly as the app serves it, in
headless Chrome, against the real app on a local port. Outlook is stood in by
a small fake which, like office.js, hands no event to a page that never said
it was ready. Each promise the handler makes is a scenario, and most have a
NEGATIVE CONTROL -- the same scenario against the script with that promise
deliberately broken -- which must fail, or the scenario proves nothing:

  - mail only to the house finishes at once and never reaches the server;
  - a wrong price stops the email, with the right price named;
  - the right price goes, and so does an email with nothing to check;
  - a server slower than the budget is still answered inside it, as "could
    not check", and reported;
  - with no key and no way to sign in, the person is answered at once and
    can send anyway, and it is reported;
  - with no key and Microsoft 365 there, it signs itself in, keeps the key
    and checks;
  - a key signed out on the add-in page is forgotten;
  - a server that cannot be reached is answered at once, and reported.

And on the server: the manifest is PromptUser and its version moved; classic
Outlook's well-known file names the script; the sign-in library is the pinned
release byte for byte; the script is plain enough for an old classic Outlook
to load; the check reads only what the person wrote; it writes nothing before
it answers; every check is logged with its time; and one that cannot run
reaches the owner's home and the task list, and leaves both when that
Outlook's next email is checked.

Needs a browser (test_staff_header_on_a_phone's find_browser(), or
GUDANES_CHROME); without one it FAILS rather than skips. Nothing leaves the
machine: Chrome resolves no host but 127.0.0.1, and Microsoft's signing keys
are the test's own (test_addin_sign_in's _SignInOn).
"""
import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from werkzeug.serving import make_server

from _harness import Suite, clients, db
import _harness
from test_addin_sign_in import CLIENT as _SIGN_IN_CLIENT, _SignInOn, claims, token
from test_staff_header_on_a_phone import find_browser

m = _harness.m
_ORIGINAL_AFTER_ANSWER = m.addin_after_answer
TAG = "zzsc"
GUEST = "zzsc.guest@example.invalid"
NOTED_GUEST = "zzsc.noted@example.invalid"
NS = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1",
      "v0": "http://schemas.microsoft.com/office/mailappversionoverrides",
      "v1": "http://schemas.microsoft.com/office/mailappversionoverrides/1.1",
      "bt": "http://schemas.microsoft.com/office/officeappbasictypes/1.0"}
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def _cleanup(conn):
    conn.execute("DELETE FROM addin_send_checks")
    conn.execute("DELETE FROM addin_sessions WHERE label LIKE ? OR user_id IN "
                 "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG.upper() + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title LIKE ?",
                 (m.WATCH_TASK_ORIGIN, "Get the check on Send working%"))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'check_send%' "
                 "OR action LIKE 'send_check%' OR action LIKE 'addin_%'")
    conn.commit()


def _post(client, path, data, key=None):
    """POST, then close the answer: the check's writes are made when its
    answer closes, as a real server closes every one."""
    r = client.post(path, data=data, headers={"Authorization": f"Bearer {key}"} if key else {})
    status, body = r.status_code, r.get_json(silent=True)
    r.close()
    return status, body


# ---- the script's syntax ----------------------------------------------------

def plain_js_problems(source):
    """What in this JavaScript an old classic Outlook would refuse to load.

    Microsoft's troubleshooting page: before Version 2403, classic Outlook on
    Windows times out on async/await and will not load a file with the ?:
    operator in it. Read with strings and comments taken out, so "?" in a URL
    or a sentence is not counted. The lines the server fills in ({{ }}) are
    JSON and a string, not code anybody wrote.
    """
    text = "\n".join(line for line in source.splitlines() if "{{" not in line)
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            out.append('""')
            continue
        out.append(c)
        i += 1
    bare = "".join(out)
    found = []
    for label, pattern in (("the ?: operator", r"\?"), ("an arrow function", r"=>"),
                           ("async or await", r"\b(?:async|await)\b"),
                           ("let or const", r"\b(?:let|const)\b"),
                           ("a template literal", "`"), ("a class", r"\bclass\b")):
        if re.search(pattern, bare):
            found.append(label)
    return found


# ---- Outlook, stood in ------------------------------------------------------

# The parts of Office the handlers touch, and the one rule of office.js that
# took Outlook on the web down: an event reaches the page only once the page
# has called Office.onReady() (or set Office.initialize). office.js polls for
# that for twenty seconds and then gives up; the host never dispatches.
FAKE_OFFICE = r"""
(function () {
  var sc = window.__scenario;
  var store = sc.roaming || {};
  window.__store = store;
  window.__notes = [];
  window.__saves = 0;
  function atLeast(have, want) {
    var a = String(have).split("."), b = String(want).split(".");
    for (var i = 0; i < Math.max(a.length, b.length); i++) {
      var x = parseInt(a[i] || "0", 10), y = parseInt(b[i] || "0", 10);
      if (x !== y) { return x > y; }
    }
    return true;
  }
  function ok(value) { return { status: "succeeded", value: value }; }
  function later(cb, value) { setTimeout(function () { if (cb) { cb(ok(value)); } }, 1); }
  function people(list) {
    var out = [];
    for (var i = 0; i < (list || []).length; i++) { out.push({ emailAddress: list[i], displayName: list[i] }); }
    return out;
  }
  window.Office = {
    onReady: function () { window.__ready = true; return Promise.resolve({}); },
    actions: { associate: function (name, fn) { (window.__handlers = window.__handlers || {})[name] = fn; } },
    AsyncResultStatus: { Succeeded: "succeeded", Failed: "failed" },
    MailboxEnums: { ItemNotificationMessageType: { InformationalMessage: "informationalMessage" },
                    SendModeOverride: { PromptUser: "promptUser" } },
    context: {
      platform: sc.platform,
      requirements: { isSetSupported: function (set, v) {
        return !!(sc.sets && sc.sets[set] && atLeast(sc.sets[set], v)); } },
      roamingSettings: {
        get: function (k) { return store[k]; },
        set: function (k, v) { store[k] = v; },
        remove: function (k) { delete store[k]; },
        saveAsync: function (cb) { window.__saves += 1; later(cb); }
      },
      mailbox: {
        userProfile: { emailAddress: sc.profile },
        item: {
          to: { getAsync: function (cb) { later(cb, people(sc.to)); } },
          cc: { getAsync: function (cb) { later(cb, people(sc.cc)); } },
          bcc: { getAsync: function (cb) { later(cb, people(sc.bcc)); } },
          subject: { getAsync: function (cb) { later(cb, sc.subject || ""); } },
          body: { getAsync: function (kind, cb) { later(cb, sc.body || ""); } },
          notificationMessages: {
            replaceAsync: function (key, msg, cb) { window.__notes.push(msg.message); later(cb); },
            removeAsync: function (key, cb) { later(cb); }
          }
        }
      }
    }
  };
  // Microsoft 365, as nested app authentication answers from inside Outlook:
  // a token for this app, for whoever is signed in, without a window.
  if (sc.ms_token) {
    window.msal = { createNestablePublicClientApplication: function (cfg) {
      window.__msal_config = cfg;
      return Promise.resolve({ acquireTokenSilent: function (req) {
        window.__msal_scopes = req.scopes;
        return Promise.resolve({ accessToken: sc.ms_token });
      } });
    } };
  }
})();
"""

DRIVER = r"""
(function () {
  var sc = window.__scenario;
  var sent = false;
  function report(result) {
    if (sent) { return; }
    sent = true;
    result.roaming = window.__store;
    result.notes = window.__notes;
    result.msal = typeof window.msal;
    fetch("/__harness__/result/" + sc.name, { method: "POST", body: JSON.stringify(result) });
  }
  if (sc.check_url) { CONFIG.check_url = sc.check_url; }
  setTimeout(function () {
    if (!window.__ready) {
      report({ completed: false, why: "office.js never started: the page did not call Office.onReady" });
      return;
    }
    var handler = (window.__handlers || {})[sc.handler];
    if (!handler) { report({ completed: false, why: "nothing registered as " + sc.handler }); return; }
    var t0 = performance.now();
    var calls = 0;
    var event = { completed: function (options) {
      calls += 1;
      var ms = performance.now() - t0;
      if (calls === 1) {
        setTimeout(function () {
          report({ completed: true, ms: ms, options: options || null, calls: calls });
        }, sc.settle_ms || 400);
      }
    } };
    try { handler(event); } catch (e) { report({ completed: false, why: "threw " + e }); }
    setTimeout(function () {
      report({ completed: false, why: "never completed", ms: performance.now() - t0 });
    }, 9000);
  }, 30);
})();
"""

SCENARIO_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script>window.__scenario = %(scenario)s;</script>
<script src="/__harness__/office.js"></script>
<script src="%(script)s"></script>
<script src="/__harness__/driver.js"></script>
</head><body></body></html>"""


class _Outlooks:
    """The app on a local port, with a few pages of its own for the browser:
    one frame per scenario, and a picture that holds the page's load until
    every frame has reported, so Chrome does not leave early."""

    def __init__(self, scenarios):
        self.scenarios = {s["name"]: s for s in scenarios}
        self.scripts = {}
        self.results = {}
        self.hits = []
        self.lock = threading.Lock()
        self.all_in = threading.Event()

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.startswith("/__harness__/"):
            return self.own(path[len("/__harness__/"):], environ, start_response)
        if environ.get("REQUEST_METHOD") == "POST":
            length = int(environ.get("CONTENT_LENGTH") or 0)
            body = environ["wsgi.input"].read(length) if length else b""
            environ["wsgi.input"] = io.BytesIO(body)
            form = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
            with self.lock:
                self.hits.append((path, (form.get("platform") or [""])[0]))
        return m.app(environ, start_response)

    def own(self, what, environ, start_response):
        def reply(body, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            start_response("200 OK", [("Content-Type", ctype), ("Content-Length", str(len(data))),
                                      ("Cache-Control", "no-store")])
            return [data]
        if what == "run":
            frames = "".join(f'<iframe src="/__harness__/scenario/{n}"></iframe>'
                             for n in self.scenarios)
            return reply("<!doctype html><html><head><meta charset='utf-8'></head><body>"
                         + frames + "<img src='/__harness__/wait' alt=''></body></html>")
        if what == "office.js":
            return reply(FAKE_OFFICE, "application/javascript")
        if what == "driver.js":
            return reply(DRIVER, "application/javascript")
        if what.startswith("scenario/"):
            sc = self.scenarios[what.split("/", 1)[1]]
            script = ("/outlook-addin/launchevent.js" if sc.get("script", "real") == "real"
                      else "/__harness__/js/" + sc["script"])
            return reply(SCENARIO_PAGE % {"scenario": json.dumps(sc).replace("</", "<\\/"),
                                          "script": script})
        if what.startswith("js/"):
            return reply(self.scripts[what[3:]], "application/javascript")
        if what.startswith("result/"):
            length = int(environ.get("CONTENT_LENGTH") or 0)
            data = environ["wsgi.input"].read(length) if length else b"{}"
            with self.lock:
                self.results[what.split("/", 1)[1]] = json.loads(data.decode("utf-8") or "{}")
                if set(self.results) >= set(self.scenarios):
                    self.all_in.set()
            return reply("", "text/plain")
        if what == "wait":
            self.all_in.wait(timeout=60)
            return reply(GIF, "image/gif")
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"no"]

    def hits_for(self, platform, path=None):
        with self.lock:
            return [p for p, plat in self.hits if plat == platform and (path is None or p == path)]


def _run_chrome(chrome, url):
    work = tempfile.mkdtemp(prefix="gudanes_sendcheck_")
    try:
        r = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
             "--user-data-dir=" + os.path.join(work, "profile"),
             # Nothing but this machine. Microsoft's own library is in the
             # page; if it reaches for the internet it finds nothing.
             "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
             "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        return r.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---- the run ---------------------------------------------------------------

def run():
    s = Suite("the check on Send")
    oc, _ec, owner, _emp = clients()
    anon = m.app.test_client()
    conn = db()
    _cleanup(conn)
    saved = {n: getattr(m, n) for n in ("guess_email_conflict", "addin_after_answer",
                                        "MS_GRAPH_MAILBOXES")}
    try:
        _server_side(s, anon, oc, conn)
        _in_a_browser(s, conn)
    finally:
        for n, v in saved.items():
            setattr(m, n, v)
        _cleanup(conn)
        conn.close()
    return s


def _free_stay(conn, room, nights=2):
    today = m.house_today()
    for offset in range(60, 400, 9):
        a = today + timedelta(days=offset)
        b = a + timedelta(days=nights)
        if m.is_range_available(conn, room["id"], a, b)[0]:
            return a, b
    return None, None


def _server_side(s, anon, oc, conn):
    s.section("The manifest keeps Send Anyway on every one of Outlook's dialogs")
    root = ET.fromstring(anon.get("/outlook-addin/manifest.xml").get_data())
    sends = [e for e in root.iter(f"{{{NS['v1']}}}LaunchEvent") if e.get("Type") == "OnMessageSend"]
    s.check("the check on Send is PromptUser",
            len(sends) == 1 and sends[0].get("SendMode") == "PromptUser",
            detail=str([e.attrib for e in sends]) + " -- under SoftBlock, 'taking longer than "
                   "expected' offers only Don't Send, and that stopped every email")
    version = root.find("o:Version", NS).text
    s.check("and the version moved on, or Microsoft 365 keeps the old one",
            version == m.OUTLOOK_ADDIN_VERSION and version != "2.0.0.1", detail=version)
    js_url = next((u.get("DefaultValue") for u in root.iter(f"{{{NS['bt']}}}Url")
                   if u.get("id") == "JSRuntime.Url"), None)

    s.section("Classic Outlook on Windows may ask the app for things")
    r = anon.get("/.well-known/microsoft-officeaddins-allowed.json")
    allowed = (r.get_json(silent=True) or {}).get("allowed")
    s.check("the well-known file answers, as JSON", r.status_code == 200 and r.is_json,
            detail=str(r.status_code))
    s.check("and names exactly the script the manifest gives classic Outlook",
            js_url and allowed == [js_url], detail=f"{allowed} vs {js_url}")

    s.section("Microsoft's sign-in library is the pinned release, byte for byte")
    with open(m.MSAL_VENDORED_PATH, "rb") as fh:
        vendored = fh.read()
    fingerprint = "sha384-" + base64.b64encode(hashlib.sha384(vendored).digest()).decode()
    s.check("the copy kept here has the pinned fingerprint", fingerprint == m.MSAL_BROWSER_SRI,
            detail=f"{fingerprint} -- line endings turned to CRLF by a Windows checkout do this; "
                   "see .gitattributes")
    served = anon.get("/outlook-addin/launchevent.js")
    script = served.get_data(as_text=True)
    s.check("and it travels inside the script, as a string", "@azure/msal-browser v4.30.0" in script
            and "var MSAL_SOURCE = \"" in script)

    s.section("The page Outlook on the web runs the handlers in")
    page = anon.get("/outlook-addin/launchevent.html")
    html = page.get_data(as_text=True)
    office_at = html.find("appsforoffice.microsoft.com/lib/1/hosted/office.js")
    tag = (served.headers.get("ETag") or "").strip('"')
    ours_at = html.find(f"/outlook-addin/launchevent.js?v={tag}")
    s.check("office.js from Microsoft first, then the script by its fingerprint",
            0 <= office_at < ours_at and tag, detail=html[-300:])
    s.check("the page is never kept stale", page.headers.get("Cache-Control") == "no-cache")
    by_tag = anon.get(f"/outlook-addin/launchevent.js?v={tag}")
    s.check("the script asked for by fingerprint is kept for a day",
            "max-age=86400" in (by_tag.headers.get("Cache-Control") or ""))
    s.check("asked for plainly it is checked each time",
            served.headers.get("Cache-Control") == "no-cache")
    s.check("and an unchanged one is a 304, not a third of a megabyte",
            anon.get("/outlook-addin/launchevent.js",
                     headers={"If-None-Match": served.headers.get("ETag")}).status_code == 304)

    s.section("The script is plain enough for an old classic Outlook to load")
    with open(os.path.join(m.app.root_path, m.app.template_folder,
                           "outlook_addin_launchevent.js"), encoding="utf-8") as fh:
        source = fh.read()
    s.check("no ?:, =>, async, await, let, const or template literals",
            plain_js_problems(source) == [], detail=str(plain_js_problems(source)))
    s.check("(negative control: the reader does see a ?: in code)",
            plain_js_problems("var a = b ? c : d;") == ["the ?: operator"])
    s.check("(and not one in a string or a comment)",
            plain_js_problems("var u = 'https://x/?q=1'; // is it? yes\n/* or? */") == [])
    s.check("it tells office.js it is ready", "Office.onReady(" in source,
            detail="without it office.js never starts in a browser, and nothing here runs")
    for fn in ("onMessageSendHandler", "onNewMessageComposeHandler",
               "onMessageRecipientsChangedHandler"):
        s.check(f"{fn} is registered", f'Office.actions.associate("{fn}", {fn})' in source)

    s.section("It reads what the person wrote, not the conversation quoted under it")
    mine = "The stay is 1,140 euros.\n"
    for label, quoted in (
            ("Outlook on the web", "\n________________________________\nFrom: Guest <g@x.com>\n"
                                   "Sent: 13 October 2026 14:02\nTo: us\n\nI saw 1 January 2026"),
            ("Outlook for Windows", "\nFrom: Guest <g@x.com>\nSent: Monday, 13 October 2026 14:02\n"
                                    "To: us\nSubject: Re: stay\n\n2 January 2026"),
            ("Outlook in French", "\nDe : Invité <g@x.com>\nEnvoyé : lundi 13 octobre 2026\n"
                                  "À : nous\n\n3 janvier"),
            ("Gmail and Apple Mail", "\nOn Mon, 13 Oct 2026 at 14:02, Guest <g@x.com>\nwrote:\n> 4 Jan"),
            ("Gmail in French", "\nLe lun. 13 oct. 2026 à 14:02, Invité <g@x.com> a écrit :\n> 5 jan"),
            ("an old forward", "\n-----Original Message-----\nFrom: Guest\n6 January")):
        s.check(f"{label}: cut where the quote starts",
                m.own_words(mine + quoted).strip() == mine.strip(),
                detail=repr(m.own_words(mine + quoted))[:120])
    quote = "Your stay\nFrom: 12 August 2027\nTo: 15 August 2027\nPrice: €1,950"
    s.check("but 'From: 12 August' in a quote is the stay, not a header",
            m.own_words(quote) == quote, detail=repr(m.own_words(quote)))

    s.section("A stopped email says what is wrong and what is right")
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 AND price_per_night > 0 "
                        "ORDER BY id LIMIT 1").fetchone()
    a, b = _free_stay(conn, room)
    real = m.compute_room_total(conn, room, a, b)
    dmy = lambda d: d.strftime("%d/%m/%Y")  # noqa: E731
    wrong = f"The {room['name']} from {dmy(a)} to {dmy(b)} is €{real + 300:.2f}."
    got = m.guess_email_conflict(conn, [GUEST], "Your quote", wrong,
                                 datetime.now(timezone.utc).isoformat())
    note = (got or {}).get("note") or ""
    s.check("the figure the email quotes", m.money(real + 300) in note, detail=note)
    s.check("the right one, for those nights", m.money(real) in note
            and "price for those nights" in note, detail=note)
    s.check("and the stay it is for", room["name"] in note and "2 nights" in note, detail=note)
    taken_a, taken_b = a, b
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, 'zzsc Taken', 'zzsc.taken@example.invalid', ?, ?, 2, 'confirmed', ?)""",
        (room["id"], TAG.upper() + "B1", TAG + "tok", taken_a.isoformat(), taken_b.isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    m._availability_picture(conn, fresh=True)
    right = f"The {room['name']} from {dmy(a)} to {dmy(b)} is €{real:.2f}."
    got = m.guess_email_conflict(conn, [GUEST], "Your quote", right,
                                 datetime.now(timezone.utc).isoformat())
    note = (got or {}).get("note") or ""
    s.check("dates that are taken: the stay, and that it is not free",
            got and got["availability_conflict"] and "is not free" in note
            and room["name"] in note, detail=note)
    s.check("and which rooms are, or that none is",
            "Free for those nights:" in note or "No other room is free" in note, detail=note)
    conn.execute("DELETE FROM bookings WHERE reference_code = ?", (TAG.upper() + "B1",))
    conn.commit()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, contact_name, contact_email,
             event_type, quoted_price, status, created_at)
           VALUES ('ZZSCE1', 'zzsc-event-token', 'zzsc Couple', 'zzsc.partner@example.invalid',
             'wedding', 9000, 'quoted', ?)""", (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    got = m.guess_email_conflict(conn, ["zzsc.first@example.invalid", "zzsc.partner@example.invalid"],
                                 "Your wedding", "The wedding comes to €8,000.",
                                 datetime.now(timezone.utc).isoformat())
    s.check("a quote on file under the SECOND person written to is still found",
            got and got["computed_price"] == 9000 and m.money(9000) in (got["note"] or ""),
            detail=str(got))

    s.section("The quoted conversation does not decide")
    thread = (f"{right}\n\n________________________________\nFrom: Guest <g@x.com>\n"
              "Sent: 13 October 2026 14:02\n\nCould we do 1 January 2026 for €5?")
    with_quote = m.guess_email_conflict(conn, [GUEST], "Re: stay", thread,
                                        datetime.now(timezone.utc).isoformat())
    without = m.guess_email_conflict(conn, [GUEST], "Re: stay", m.own_words(thread),
                                     datetime.now(timezone.utc).isoformat())
    s.check("read whole, the guest's dates and figure would stop a right reply",
            with_quote and (with_quote["price_conflict"] or with_quote["availability_conflict"]),
            detail=str(with_quote))
    s.check("read as the person wrote it, it passes",
            not without or not (without["price_conflict"] or without["availability_conflict"]),
            detail=str(without))

    s.section("Mail only to the house is not checked, and the house is only the house")
    staff = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    key, _ = m.addin_issue_session(conn, staff["id"], "test", label=TAG + " owner")
    conn.commit()
    status, got = _post(anon, "/api/check-send-conflict", {
        "recipients": "reservations@chateaugudanes.com,zzsc.someone@chateaugudanes.com",
        "subject": "Quote", "body": wrong, "source": "send", "platform": "zzsc-internal"}, key)
    s.check("all house addresses: nothing to check", status == 200 and got
            and got.get("conflict") is False and "house's own" in got.get("checked", ""),
            detail=str(got))
    status, got = _post(anon, "/api/check-send-conflict", {
        "recipients": GUEST, "subject": "Re: stay", "body": thread, "platform": "zzsc-thread"}, key)
    s.check("the check itself reads a reply the same way: the right price over a quoted "
            "thread passes", status == 200 and got and got.get("conflict") is False,
            detail=str(got))
    m.MS_GRAPH_MAILBOXES = list(m.MS_GRAPH_MAILBOXES) + ["zzsc.bookings@gmail.com"]
    s.check("(an inbox at gmail.com makes gmail.com one of the house's domains)",
            "gmail.com" in m.house_domains())
    s.check("but never one the check waves through", "gmail.com" not in m.addin_house_domains())
    status, got = _post(anon, "/api/check-send-conflict", {
        "recipients": "zzsc.guest@gmail.com", "subject": "Quote", "body": wrong,
        "source": "send", "platform": "zzsc-gmail"}, key)
    s.check("so a guest at gmail.com is still checked", got and got.get("conflict") is True,
            detail=str(got))
    m.MS_GRAPH_MAILBOXES = [a for a in m.MS_GRAPH_MAILBOXES if a != "zzsc.bookings@gmail.com"]

    s.section("It writes nothing before it answers")
    # Somebody else holds SQLite's one write lock -- a mailbox scan, a job. A
    # write in the check would wait five seconds for it, which is all of
    # Outlook's patience.
    holder = sqlite3.connect(m.DB_PATH, timeout=0.1, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        t0 = time.perf_counter()
        r = anon.post("/api/check-send-conflict", headers={"Authorization": f"Bearer {key}"}, data={
            "recipients": GUEST, "subject": "Quote", "body": right, "source": "send",
            "platform": "zzsc-locked", "mailbox": "zzsc.locked@chateaugudanes.com",
            "waited_ms": "80"})
        took = time.perf_counter() - t0
        answer = r.get_json(silent=True) or {}
        s.check("with the write lock held elsewhere, it still answers at once",
                r.status_code == 200 and took < 1.5, detail=f"{took:.2f}s, {r.status_code}")
        s.check("and correctly", answer.get("conflict") is False, detail=str(answer))
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    r.close()
    row = conn.execute("SELECT * FROM addin_send_checks WHERE platform = 'zzsc-locked'").fetchone()
    s.check("its line in the log is written once the answer has gone",
            row and row["outcome"] == "clear" and row["server_ms"] is not None
            and row["client_ms"] == 80 and row["user_id"] == staff["id"]
            and row["mailbox"] == "zzsc.locked@chateaugudanes.com",
            detail=str(dict(row)) if row else "no row")

    def inline(response, write):
        try:
            c2 = m.get_db()
            try:
                write(c2)
                c2.commit()
            finally:
                c2.close()
        except sqlite3.OperationalError:
            pass
        return response
    m.addin_after_answer = inline
    holder = sqlite3.connect(m.DB_PATH, timeout=0.1, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        t0 = time.perf_counter()
        _post(anon, "/api/check-send-conflict", {"recipients": GUEST, "subject": "Quote",
                                                 "body": right, "platform": "zzsc-inline"}, key)
        took = time.perf_counter() - t0
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        m.addin_after_answer = _ORIGINAL_AFTER_ANSWER
    s.check("(negative control: the same writes made before answering wait for the lock)",
            took >= 4.0, detail=f"{took:.2f}s")

    s.section("Every check is logged with its time; a sign-in ping is not a check")
    before = conn.execute("SELECT COUNT(*) FROM addin_send_checks").fetchone()[0]
    _post(anon, "/api/check-send-conflict", {"recipient_email": "", "subject": "", "body": ""}, key)
    s.check("the panel's empty sign-in check writes no line",
            conn.execute("SELECT COUNT(*) FROM addin_send_checks").fetchone()[0] == before)
    _post(anon, "/api/check-send-conflict", {"recipients": GUEST, "subject": "Quote", "body": wrong,
                                             "platform": "zzsc-button"}, key)
    row = conn.execute("SELECT * FROM addin_send_checks WHERE platform = 'zzsc-button'").fetchone()
    s.check("a stopped email is logged as stopped, with what it was told",
            row and row["outcome"] == "conflict" and row["source"] == "button"
            and m.money(real) in (row["detail"] or ""), detail=str(dict(row)) if row else "none")
    _post(anon, "/api/check-send-conflict", {"recipients": GUEST, "subject": "Quote", "body": right,
                                             "source": "send", "platform": "zzsc-late",
                                             "waited_ms": str(m.ADDIN_SEND_BUDGET_MS)}, key)
    row = conn.execute("SELECT outcome FROM addin_send_checks WHERE platform = 'zzsc-late'").fetchone()
    s.check("an answer after the add-in gave up is logged as late, not as checked",
            row and row["outcome"] == "too_late", detail=str(dict(row)) if row else "none")
    for i in range(3):
        conn.execute("INSERT INTO submission_log (ip_address, action, created_at) VALUES (?, ?, ?)",
                     ("127.0.0.1", f"check_send_conflict:u{staff['id']}",
                      datetime.now(timezone.utc).isoformat()))
    conn.commit()
    saved_limit = conn.execute("SELECT COUNT(*) FROM submission_log WHERE action = ?",
                               (f"check_send_conflict:u{staff['id']}",)).fetchone()[0]
    conn.executemany("INSERT INTO submission_log (ip_address, action, created_at) VALUES (?, ?, ?)",
                     [("127.0.0.1", f"check_send_conflict:u{staff['id']}",
                       datetime.now(timezone.utc).isoformat())] * max(0, 1200 - saved_limit))
    conn.commit()
    status, got = _post(anon, "/api/check-send-conflict", {"recipients": GUEST, "subject": "Q",
                                                           "body": right, "source": "send",
                                                           "platform": "zzsc-limit"}, key)
    row = conn.execute("SELECT * FROM addin_send_checks WHERE platform = 'zzsc-limit'").fetchone()
    s.check("over the hourly allowance: refused, and logged as could not check",
            status == 429 and got and got.get("recorded") and row
            and row["outcome"] == "could_not_check" and row["reason"] == "rate_limited",
            detail=f"{status} {got} {dict(row) if row else None}")
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'check_send_conflict%'")
    conn.execute("DELETE FROM addin_send_checks")
    conn.commit()

    s.section("An Outlook saying it could not check")
    s.check("a GET is nothing", anon.get("/api/addin/send-check-failed").status_code == 404)
    status, _ = _post(anon, "/api/addin/send-check-failed",
                      {"reason": "made up", "mailbox": "zzsc.k@chateaugudanes.com"})
    s.check("a reason not on the list is kept nowhere", status == 204 and not conn.execute(
        "SELECT 1 FROM addin_send_checks").fetchone())
    _post(anon, "/api/addin/send-check-failed",
          {"reason": "no_identity", "mailbox": "zzsc.someone@gmail.com", "platform": "Mac"})
    s.check("nor an address that is not the house's", not conn.execute(
        "SELECT 1 FROM addin_send_checks").fetchone())
    _post(anon, "/api/addin/send-check-failed",
          {"reason": "no_identity", "mailbox": "ZZSC.Karina@chateaugudanes.com",
           "platform": "OfficeOnline", "client_ms": "35", "detail": "<b>not a sentence</b>"})
    row = conn.execute("SELECT * FROM addin_send_checks").fetchone()
    s.check("a house address with no key is kept: that is the failure that matters most",
            row and row["mailbox"] == "zzsc.karina@chateaugudanes.com"
            and row["reason"] == "no_identity" and row["client_ms"] == 35
            and row["user_id"] is None, detail=str(dict(row)) if row else "none")
    s.check("and what it said beyond the code is not kept", row and not row["detail"])
    _post(anon, "/api/addin/send-check-failed", {"reason": "timeout", "platform": "PC"}, key)
    row = conn.execute("SELECT * FROM addin_send_checks WHERE platform = 'PC'").fetchone()
    s.check("with a key, it is put down to the person", row and row["user_id"] == staff["id"])
    conn.execute("DELETE FROM addin_send_checks")
    conn.commit()

    s.section("It reaches the owner's home and a task, and leaves both when it works")
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at,
             m365_upn) VALUES ('zzsc.karina@example.invalid', 'x', 'employee', 'zzsc Karina',
             'Reservations', 'active', ?, 'zzsc.karina@chateaugudanes.com')""", (now.isoformat(),))
    conn.commit()
    today = m.house_today()

    def warnings():
        with m.app.test_request_context():
            return [w for w in m.owner_home_warnings(conn, today) if "check on Send" in w["title"]]

    def findings():
        return [f for f in m.watch_task_findings(conn, today)[0] if f[0] == "sendcheck"]

    s.check("(nothing wrong, nothing said: the panel can be empty)", not warnings() and not findings())
    for minutes in (50, 30, 10):
        m.record_send_check(conn, mailbox="zzsc.karina@chateaugudanes.com", platform="Mac",
                            outcome="could_not_check", reason="no_identity", client_ms=20)
        conn.execute("UPDATE addin_send_checks SET at = ? WHERE id = last_insert_rowid()",
                     ((now - timedelta(minutes=minutes)).isoformat(),))
    m.record_send_check(conn, mailbox="zzsc.karina@chateaugudanes.com", platform="PC",
                        outcome="clear", server_ms=40)
    conn.commit()
    lines = warnings()
    s.check("the owner's home says which Outlook, whose, and why",
            len(lines) == 1 and "zzsc Karina" in lines[0]["detail"]
            and "Outlook for Mac" in lines[0]["detail"] and "not signed in" in lines[0]["detail"]
            and "3 emails" in lines[0]["detail"], detail=str(lines))
    s.check("and links to the add-in page's own section",
            lines and lines[0]["href"].endswith("/admin/outlook-addin#send-checks"),
            detail=str(lines and lines[0]["href"]))
    found = findings()
    s.check("a task for it, saying what puts it right",
            len(found) == 1 and "zzsc Karina" in found[0][1] and "Outlook for Mac" in found[0][1]
            and "Open the Gudanes panel" in found[0][2], detail=str(found))
    m.generate_watch_tasks(conn)
    open_task = conn.execute("SELECT id FROM tasks WHERE origin = ? AND title LIKE ? "
                             "AND status != 'done'",
                             (m.WATCH_TASK_ORIGIN, "Get the check on Send working%")).fetchone()
    s.check("raised on the list", open_task is not None)
    page = oc.get("/admin/outlook-addin").get_data(as_text=True)
    s.check("the add-in page names it too", 'id="send-checks"' in page and "zzsc Karina" in page
            and "Open the Gudanes panel" in page)
    m.record_send_check(conn, mailbox="zzsc.karina@chateaugudanes.com", platform="Mac",
                        outcome="nothing", server_ms=30)
    conn.commit()
    s.check("her Mac's next email is checked: the line goes", not warnings())
    m.generate_watch_tasks(conn)
    s.check("and the task closes itself", conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (open_task["id"],)).fetchone()["status"] == "done"
        if open_task else False)

    s.section("Slow is said too")
    for i in range(10):
        m.record_send_check(conn, mailbox="zzsc.karina@chateaugudanes.com", platform="Mac",
                            outcome="clear", server_ms=2400)
    conn.commit()
    with m.app.test_request_context():
        slow = [w for w in m.owner_home_warnings(conn, today) if w["title"] == "The check on Send is slow"]
    s.check("half of a week's checks over 1.5 seconds on the server reaches the home page",
            len(slow) == 1, detail=str(slow))
    conn.execute("UPDATE addin_send_checks SET server_ms = 120")
    conn.commit()
    with m.app.test_request_context():
        slow = [w for w in m.owner_home_warnings(conn, today) if w["title"] == "The check on Send is slow"]
    s.check("and fast again, it does not", not slow)
    conn.execute("DELETE FROM addin_send_checks")
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title LIKE ?",
                 (m.WATCH_TASK_ORIGIN, "Get the check on Send working%"))
    conn.commit()


# ---- the real handler, in a real browser -----------------------------------

def _scenarios(conn, room, wrong, right, keys, ms_token):
    house = "zzsc.sender@chateaugudanes.com"
    base = {"handler": "onMessageSendHandler", "profile": house, "to": [GUEST],
            "subject": "Your stay", "sets": {"Mailbox": "1.14"}}

    def sc(name, **over):
        out = dict(base, name=name, platform="zzsc-" + name)
        out.update(over)
        return out
    good = {"gudanesSession": keys["good"][0], "gudanesSessionExpires": keys["good"][1]}
    revoked = {"gudanesSession": keys["revoked"][0], "gudanesSessionExpires": keys["revoked"][1]}
    return [
        sc("internal", to=["reservations@chateaugudanes.com"], cc=["zzsc.colleague@chateaugudanes.com"],
           body=wrong, roaming=dict(good)),
        sc("neg-internal", script="house_is_anyone", to=["reservations@chateaugudanes.com"],
           body=wrong, roaming=dict(good)),
        sc("clean", body="Looking forward to seeing you.", roaming=dict(good)),
        sc("neg-clean", script="no_onready", body="Looking forward to seeing you.", roaming=dict(good)),
        sc("conflict", body=wrong, roaming=dict(good)),
        sc("neg-conflict", script="ignores_conflict", body=wrong, roaming=dict(good)),
        sc("neg-override", script="no_override", body=wrong, roaming=dict(good)),
        sc("right", body=right, roaming=dict(good)),
        sc("slow", body=right + " ZZSLOW", roaming=dict(good), settle_ms=700),
        sc("neg-slow", script="no_budget", body=right + " ZZSLOW", roaming=dict(good)),
        sc("nokey", body=right, roaming={}, settle_ms=700),
        sc("neg-nokey", script="no_way_out", body=right, roaming={}),
        sc("signin", body="Looking forward to seeing you.", roaming={}, ms_token=ms_token,
           sets={"Mailbox": "1.14", "NestedAppAuth": "1.1"}),
        sc("neg-signin", script="never_signs_in", body="Looking forward to seeing you.", roaming={},
           ms_token=ms_token, sets={"Mailbox": "1.14", "NestedAppAuth": "1.1"}),
        sc("realmsal", body=right, roaming={}, sets={"Mailbox": "1.14", "NestedAppAuth": "1.1"},
           settle_ms=700),
        sc("revoked", body=right, roaming=dict(revoked), settle_ms=700),
        sc("down", body=right, roaming=dict(good), check_url="http://127.0.0.1:9/nothing-here",
           settle_ms=700, sets={}),
        sc("compose", handler="onNewMessageComposeHandler", to=[NOTED_GUEST], roaming={},
           ms_token=ms_token, sets={"Mailbox": "1.14", "NestedAppAuth": "1.1"}),
    ]


# Each negative control: one promise broken, on one line of the script.
BREAKS = {
    "no_onready": ('try { Office.onReady(function () {}); } catch (e) { /* the JavaScript-only runtime */ }',
                   "/* Office.onReady was never called */"),
    "house_is_anyone": ("function isHouse(address) {", "function isHouse(address) { return false;"),
    "ignores_conflict": ("if (data.conflict) {", "if (false) {"),
    "no_override": ('if (supports("Mailbox", "1.14") && Office.MailboxEnums.SendModeOverride) {',
                    "if (false) {"),
    "no_budget": ("var SEND_BUDGET_MS = CONFIG.send_budget_ms || 3000;", "var SEND_BUDGET_MS = 9000;"),
    "no_way_out": ("if (!identity) { cannotCheck(failure); return; }", "if (!identity) { return; }"),
    "never_signs_in": ('if (budgetMs < 600) { done(null, "no_identity"); return; }',
                       'done(null, "no_identity"); return;'),
}


def _in_a_browser(s, conn):
    s.section("The real handler, in a browser, against the real app")
    chrome = find_browser()
    if not s.check("there is a browser to run it in", chrome,
                   detail="none found -- set GUDANES_CHROME; nothing below was checked"):
        return

    room = conn.execute("SELECT * FROM rooms WHERE active = 1 AND price_per_night > 0 "
                        "ORDER BY id LIMIT 1").fetchone()
    a, b = _free_stay(conn, room)
    real = m.compute_room_total(conn, room, a, b)
    dmy = lambda d: d.strftime("%d/%m/%Y")  # noqa: E731
    wrong = f"Dear guest, the {room['name']} from {dmy(a)} to {dmy(b)} is €{real + 300:.2f}."
    right = f"Dear guest, the {room['name']} from {dmy(a)} to {dmy(b)} is €{real:.2f}."
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at,
             m365_upn) VALUES ('zzsc.jasmine@example.invalid', 'x', 'employee', 'zzsc Jasmine',
             'Experience', 'active', ?, 'zzsc.jasmine@chateaugudanes.com')""", (now,))
    jasmine = conn.execute("SELECT id FROM users WHERE name = 'zzsc Jasmine'").fetchone()["id"]
    conn.execute("INSERT INTO guests (name, email, vip, created_at) VALUES ('zzsc Noted Guest', ?, 1, ?)",
                 (NOTED_GUEST, now))
    good = m.addin_issue_session(conn, jasmine, "test", label=TAG + " good")
    revoked = m.addin_issue_session(conn, jasmine, "test", label=TAG + " revoked")
    conn.execute("UPDATE addin_sessions SET revoked_at = ? WHERE token_hash = ?",
                 (now, m._addin_key_hash(revoked[0])))
    conn.commit()
    ms_token = token(claims(aud=_SIGN_IN_CLIENT, azp=_SIGN_IN_CLIENT,
                            preferred_username="zzsc.jasmine@chateaugudanes.com"))
    scenarios = _scenarios(conn, room, wrong, right, {"good": good, "revoked": revoked}, ms_token)

    real_guess = m.guess_email_conflict

    def slow_guess(c, addresses, subject, body, at):
        if "ZZSLOW" in (body or ""):
            time.sleep(4.0)
        return real_guess(c, addresses, subject, body, at)
    m.guess_email_conflict = slow_guess

    outlooks = _Outlooks(scenarios)
    quiet = logging.getLogger("werkzeug")
    was = quiet.level
    quiet.setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, outlooks, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with _SignInOn():
            served = m.app.test_client().get("/outlook-addin/launchevent.js",
                                             base_url=base).get_data(as_text=True)
            for name, (old, new) in BREAKS.items():
                broken = served.replace(old, new)
                s.check(f"(the {name} control really breaks the script)", broken != served,
                        detail=f"{old!r} not found -- a control that changes nothing proves nothing")
                outlooks.scripts[name] = broken
            started = time.perf_counter()
            _run_chrome(chrome, base + "/__harness__/run")
            took = time.perf_counter() - started
    finally:
        server.shutdown()
        quiet.setLevel(was)
        m.guess_email_conflict = real_guess
    s.check("every scenario reported back", set(outlooks.results) == {x["name"] for x in scenarios},
            detail=f"missing {sorted({x['name'] for x in scenarios} - set(outlooks.results))} "
                   f"after {took:.0f}s")
    time.sleep(0.5)   # the last reports, sent without waiting

    def result(name):
        return outlooks.results.get(name) or {}

    def opts(name):
        return result(name).get("options") or {}

    def rows(name):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM addin_send_checks WHERE platform = ? ORDER BY id", ("zzsc-" + name,))]

    def checked(name):
        return outlooks.hits_for("zzsc-" + name, "/api/check-send-conflict")

    budget = m.ADDIN_SEND_BUDGET_MS

    def internal_ok(name):
        r = result(name)
        return (r.get("completed") and opts(name).get("allowEvent") is True
                and not checked(name) and r.get("ms", 9e9) < 300)

    def clean_ok(name):
        return result(name).get("completed") and opts(name).get("allowEvent") is True

    # Every stop says PromptUser itself, where Outlook can be told: Send Anyway
    # is on the dialog even while an older SoftBlock manifest is still served.
    def prompts(name):
        return opts(name).get("sendModeOverride") == "promptUser"

    def conflict_ok(name):
        msg = opts(name).get("errorMessage") or ""
        return (result(name).get("completed") and opts(name).get("allowEvent") is False
                and m.money(real) in msg and m.money(real + 300) in msg and "Send Anyway" in msg
                and len(msg) <= 500 and prompts(name))

    def slow_ok(name):
        r = result(name)
        msg = opts(name).get("errorMessage") or ""
        return (r.get("completed") and r.get("ms", 9e9) <= budget + 250
                and opts(name).get("allowEvent") is False and "could not check" in msg
                and prompts(name))

    def nokey_ok(name):
        r = result(name)
        return (r.get("completed") and r.get("ms", 9e9) < 500
                and opts(name).get("allowEvent") is False
                and opts(name).get("cancelLabel") == "Sign in"
                and opts(name).get("commandId") == "msgComposeOpenPaneButton"
                and prompts(name))

    def signin_ok(name):
        r = result(name)
        return (r.get("completed") and opts(name).get("allowEvent") is True
                and (r.get("roaming") or {}).get("gudanesSession")
                and checked(name))

    s.section("Mail only to the house")
    s.check("finishes at once and never asks the server", internal_ok("internal"),
            detail=str(result("internal"))[:300] + f" checks: {checked('internal')}")
    s.check("(negative control: if anybody counted as outside, it would ask)",
            not internal_ok("neg-internal"), detail=str(result("neg-internal"))[:200])

    s.section("office.js is told the page is ready")
    s.check("an email with nothing to check goes", clean_ok("clean"),
            detail=str(result("clean"))[:300])
    s.check("and the server logged that it looked", [r["outcome"] for r in rows("clean")] == ["nothing"],
            detail=str(rows("clean")))
    s.check("(negative control: without Office.onReady, nothing ever runs -- 14 October)",
            not clean_ok("neg-clean") and "Office.onReady" in (result("neg-clean").get("why") or ""),
            detail=str(result("neg-clean"))[:200])

    s.section("A wrong price is stopped, with the right one named")
    s.check("stopped, saying the quoted price and the booking system's",
            conflict_ok("conflict"), detail=str(opts("conflict"))[:400])
    s.check("logged as stopped", [r["outcome"] for r in rows("conflict")] == ["conflict"],
            detail=str(rows("conflict")))
    s.check("(negative control: a handler that ignored the answer would let it go)",
            not conflict_ok("neg-conflict"), detail=str(opts("neg-conflict"))[:200])
    s.check("(negative control: a stop that left the send mode to the manifest is caught)",
            not conflict_ok("neg-override") and opts("neg-override").get("allowEvent") is False,
            detail=str(opts("neg-override"))[:200])
    s.check("the right price goes", clean_ok("right") and
            [r["outcome"] for r in rows("right")] == ["clear"], detail=str(result("right"))[:300])
    timed = [r for r in rows("right") if r["server_ms"] is not None and r["client_ms"] is not None]
    s.check("and its time is logged: the server's, and what the person had waited",
            len(timed) == 1, detail=str(rows("right")))

    s.section("A slow server is answered inside the budget, never left to Outlook")
    s.check(f"done within {budget} ms of its first line, saying it could not check",
            slow_ok("slow"), detail=str(result("slow"))[:400])
    s.check("and reported as a timeout",
            "timeout" in [r["reason"] for r in rows("slow") if r["outcome"] == "could_not_check"],
            detail=str(rows("slow")))
    s.check("(negative control: with no budget of its own, Outlook's dialog would come first)",
            not slow_ok("neg-slow"), detail=str(result("neg-slow"))[:200])

    s.section("No key and no way to sign in: the person can still send")
    s.check("answered at once, saying why, with a button to sign in",
            nokey_ok("nokey"), detail=str(result("nokey"))[:400])
    s.check("never sent to the server unasked", not checked("nokey"))
    s.check("and reported, with the Outlook's own address, for the owner's home",
            [(r["outcome"], r["reason"], r["mailbox"]) for r in rows("nokey")] ==
            [("could_not_check", "no_identity", "zzsc.sender@chateaugudanes.com")],
            detail=str(rows("nokey")))
    s.check("(negative control: a handler that gave up quietly would leave Outlook waiting)",
            not nokey_ok("neg-nokey"), detail=str(result("neg-nokey"))[:200])

    s.section("No key, and Microsoft 365 there: it signs itself in")
    s.check("signed in, kept the key, checked, and let it go",
            signin_ok("signin"), detail=str(result("signin"))[:400])
    s.check("as the person Microsoft says it is", conn.execute(
        "SELECT 1 FROM addin_sessions WHERE user_id = ? AND how = 'microsoft' AND label LIKE ?",
        (jasmine, "%zzsc-signin%")).fetchone() is not None)
    s.check("(negative control: a handler that never signed in would not have checked)",
            not signin_ok("neg-signin"), detail=str(result("neg-signin"))[:200])
    s.check("and the same while writing: the note, and a key ready before Send",
            result("compose").get("completed")
            and any("zzsc Noted Guest" in (n or "") for n in result("compose").get("notes") or [])
            and (result("compose").get("roaming") or {}).get("gudanesSession"),
            detail=str(result("compose"))[:400])

    s.section("Microsoft's own library, where nothing can sign in")
    s.check("it loads from its string and is started",
            result("realmsal").get("msal") == "object", detail=str(result("realmsal"))[:200])
    s.check("and failing to sign in is answered inside the budget",
            result("realmsal").get("completed") and result("realmsal").get("ms", 9e9) <= budget + 250
            and opts("realmsal").get("allowEvent") is False, detail=str(result("realmsal"))[:400])
    s.check("and reported as such", "sign_in_failed" in [r["reason"] for r in rows("realmsal")],
            detail=str(rows("realmsal")))

    s.section("A key signed out on the add-in page, and a server that cannot be reached")
    s.check("the signed-out key is forgotten, and the person told",
            result("revoked").get("completed") and opts("revoked").get("allowEvent") is False
            and "gudanesSession" not in result("revoked").get("roaming", {"gudanesSession": "unread"}),
            detail=str(result("revoked"))[:400])
    s.check("reported as signed out", "signed_out" in [r["reason"] for r in rows("revoked")],
            detail=str(rows("revoked")))
    s.check("unreachable: answered at once, and the person can send anyway",
            result("down").get("completed") and result("down").get("ms", 9e9) < 1500
            and opts("down").get("allowEvent") is False
            and "could not check" in (opts("down").get("errorMessage") or ""),
            detail=str(result("down"))[:400])
    s.check("reported as unreachable", "unreachable" in [r["reason"] for r in rows("down")],
            detail=str(rows("down")))
    s.check("and an Outlook older than Mailbox 1.14 is not handed options it does not know",
            "sendModeOverride" not in opts("down") and "cancelLabel" not in opts("down"),
            detail=str(opts("down")))

    s.section("Outlook is answered once")
    s.check("no handler called completed twice",
            all((r.get("calls") or 1) == 1 for r in outlooks.results.values()),
            detail=str({k: v.get("calls") for k, v in outlooks.results.items()}))


if __name__ == "__main__":
    print(run().report())
