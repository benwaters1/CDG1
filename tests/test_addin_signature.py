"""The signature, put in by the add-in rather than the server (10 Nov 2026).

Five Exchange transport rules used to sign the house's email. A transport
rule can only add to the very end of a message, so a reply was signed under
the whole quoted thread; and the logo was a hosted picture, which Outlook
blocks until the reader asks. The add-in now puts the signature in while the
email is written, with setSignatureAsync, and carries the logo inside it.

What this file holds the code to:

  THE RIGHT SIGNATURE FOR THE ADDRESS THE EMAIL IS FROM. In a shared inbox
  that is the inbox, not the person: reservations@ is the Chateau Team
  whoever writes. Run in a real browser against a stand-in Office.js.

  "2 Route de Beille" IN EVERY ONE. The server's rules skip an email that
  already contains it, and that is the only thing stopping an email being
  signed twice. A signature without it is not saved; nor is one with a data:
  picture in it.

  THE LOGO INLINE, the hosted copy only where Outlook cannot attach it.

  THE MANIFEST STILL GOES THROUGH THE ADMIN CENTRE, with the new event, and
  its version moved on so Microsoft 365 fetches it.

Needs a browser (test_staff_header_on_a_phone's find_browser(), or
GUDANES_CHROME). It fails rather than skips without one.
"""
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET

from werkzeug.serving import make_server

from _harness import Suite, clients, db
import _harness
from test_staff_header_on_a_phone import find_browser

m = _harness.m
NS = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1",
      "v0": "http://schemas.microsoft.com/office/mailappversionoverrides",
      "v1": "http://schemas.microsoft.com/office/mailappversionoverrides/1.1",
      "bt": "http://schemas.microsoft.com/office/officeappbasictypes/1.0"}
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
PNG = b"\x89PNG\r\n\x1a\n"


def _settings(conn):
    return conn.execute("SELECT key FROM app_settings WHERE key LIKE 'addin_signature_%'")


def run():
    s = Suite("The signature the add-in puts in")
    oc, ec, _owner, _emp = clients()
    conn = db()
    put_back = _harness.keep_rows(conn, "app_settings", "key LIKE 'addin_signature_%'")
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'addin_signature_%'")
        conn.commit()
        _stored(s)
        _owner_page(s, oc, ec, conn)
        _manifest(s)
        _in_a_browser(s, conn)
    finally:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'addin_signature_%'")
        conn.commit()
        put_back()
        conn.close()
    return s


# ---- what is stored ---------------------------------------------------------

def _stored(s):
    s.section("Five signatures, each able to stop the server signing twice")
    keys = [spec["key"] for spec in m.ADDIN_SIGNATURES]
    s.check("five of them", keys == ["team", "experience", "ariege", "accounts", "benw"],
            detail=str(keys))
    for spec in m.ADDIN_SIGNATURES:
        html = spec["default"]
        s.check(f"{spec['label']}: says \"2 Route de Beille\", word for word",
                "2 Route de Beille, 09310 Château-Verdun, France" in html,
                detail="the server rules look for it; without it the email is signed twice")
        s.check(f"{spec['label']}: no picture carried as data:", "data:" not in html)
        s.check(f"{spec['label']}: passes the check a save has to",
                m.addin_signature_problem(html) is None,
                detail=str(m.addin_signature_problem(html)))
        s.check(f"{spec['label']}: the logo at 104px with the gold rule beside it",
                m.ADDIN_SIGNATURE_LOGO_URL in html and 'width="104"' in html
                and "border-right:1px solid #C5A24A" in html)
    # The v5 HTML that was live in the transport rules, where it is beside the
    # repository: the numbers are the house's, not anybody's guess.
    folder = os.path.join(os.path.dirname(_harness.ROOT), "Gudanes", "signatures-v5")
    for root in (os.path.join(_harness.ROOT, "signatures-v5"), folder,
                 r"D:\Gudanes\signatures-v5"):
        if os.path.isdir(root):
            files = {"team": "reservations", "experience": "experience", "ariege": "ariege",
                     "accounts": "accounts", "benw": "benw"}
            same = []
            for spec in m.ADDIN_SIGNATURES:
                with open(os.path.join(root, f"sig-{files[spec['key']]}-v5.html"),
                          encoding="utf-8") as fh:
                    same.append(fh.read().strip() == spec["default"])
            s.check("each is the v5 HTML from the transport rules, byte for byte", all(same),
                    detail=str(dict(zip(keys, same))))
            break
    phones = {"team": ["+33 6 28 06 97 76"], "experience": ["+33 6 28 06 97 76"],
              "ariege": ["+33 6 28 06 97 76"], "accounts": ["+33 6 31 37 44 26"],
              "benw": ["+33 6 31 37 44 26", "+61 405 073 432"]}
    for spec in m.ADDIN_SIGNATURES:
        s.check(f"{spec['label']}: the telephone the house gave", all(
            p in spec["default"] for p in phones[spec["key"]]))
    who = {local: spec["key"] for spec in m.ADDIN_SIGNATURES for local in spec["mailboxes"]}
    s.check("reservations@, house@ and chateauteam@ are the Chateau Team",
            who.get("reservations") == who.get("house") == who.get("chateauteam") == "team")
    s.check("experience@ is Jasmine, ariege@ Karina, accounts@ Accounts, benw@ Ben",
            (who.get("experience"), who.get("ariege"), who.get("accounts"), who.get("benw"))
            == ("experience", "ariege", "accounts", "benw"), detail=str(who))

    s.section("A signature that would be signed twice is refused")
    good = m.ADDIN_SIGNATURES[0]["default"]
    s.check("one without the address line",
            m.addin_signature_problem(good.replace("2 Route de Beille", "2 Rte de Beille")))
    s.check("one carrying its logo as data:",
            m.addin_signature_problem(good.replace(m.ADDIN_SIGNATURE_LOGO_URL,
                                                   "data:image/png;base64,AAAA")))
    s.check("one with script in it",
            m.addin_signature_problem(good + '<img src="x" onerror="alert(1)">'))
    s.check("one Outlook would refuse for length",
            m.addin_signature_problem(good + "x" * m.ADDIN_SIGNATURE_MAX))
    text = m.addin_signature_text(good)
    s.check("its plain-text copy keeps the address line too",
            "2 Route de Beille" in text and "<" not in text, detail=text[:120])


# ---- the owner's page -------------------------------------------------------

def _owner_page(s, oc, ec, conn):
    s.section("The owner edits them on the add-in page")
    page = oc.get("/admin/outlook-addin").get_data(as_text=True)
    for spec in m.ADDIN_SIGNATURES:
        s.check(f"{spec['label']} has its own box and a preview",
                f'name="signature_{spec["key"]}"' in page
                and f'id="sig-preview-{spec["key"]}"' in page)
    s.check("and the page says why the address line must stay",
            "2 Route de Beille" in page and "signs it again" in page)

    mine = m.ADDIN_SIGNATURES[0]["default"].replace("The Château Team", "ZZSIG The Front Desk")
    r = oc.post("/admin/outlook-addin/signatures", data={"signature_team": mine})
    s.check("a change is saved", r.status_code == 302 and
            m.addin_signature_html(conn, "team") == (mine, True), response=r)
    script = m.app.test_client().get("/outlook-addin/launchevent.js").get_data(as_text=True)
    s.check("and the add-in has it on the next email", "ZZSIG The Front Desk" in script)

    bad = mine.replace("2 Route de Beille", "Route de Beille")
    r = oc.post("/admin/outlook-addin/signatures",
                data={"signature_team": bad, "signature_benw": m.ADDIN_SIGNATURES[4]["default"]},
                follow_redirects=True)
    body = r.get_data(as_text=True)
    s.check("one without the address line is not saved",
            m.addin_signature_html(conn, "team")[0] == mine)
    s.check("and the page says which one and why",
            "Not saved" in body and "The Château Team" in body and "2 Route de Beille" in body)
    data_pic = mine.replace(m.ADDIN_SIGNATURE_LOGO_URL, "data:image/png;base64,AAAA")
    oc.post("/admin/outlook-addin/signatures", data={"signature_team": data_pic})
    s.check("nor one with a data: picture", m.addin_signature_html(conn, "team")[0] == mine)

    r = oc.post("/admin/outlook-addin/signatures", data={"put_back": "team"})
    s.check("Put back returns it to the original",
            m.addin_signature_html(conn, "team") == (m.ADDIN_SIGNATURES[0]["default"], False))
    oc.post("/admin/outlook-addin/signatures",
            data={"signature_team": m.ADDIN_SIGNATURES[0]["default"]})
    s.check("and saving the original unchanged stores nothing",
            not _settings(conn).fetchall())
    s.check("a member of staff cannot change them",
            ec.post("/admin/outlook-addin/signatures",
                    data={"signature_team": mine}).status_code in (302, 403)
            and m.addin_signature_html(conn, "team")[1] is False)
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                 ("addin_signature_team", bad))
    conn.commit()
    s.check("a bad one already in the database is never served: the original is",
            m.addin_signature_html(conn, "team") == (m.ADDIN_SIGNATURES[0]["default"], False))
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'addin_signature_%'")
    conn.commit()


# ---- the manifest -----------------------------------------------------------

def _manifest(s):
    s.section("The manifest carries the new event and still validates")
    anon = m.app.test_client()
    root = ET.fromstring(anon.get("/outlook-addin/manifest.xml").get_data())
    version = root.find("o:Version", NS).text
    s.check("the version moved on, so Microsoft 365 fetches it",
            version == m.OUTLOOK_ADDIN_VERSION and
            tuple(int(x) for x in version.split(".")) > (2, 2, 0, 0), detail=version)
    v11 = root.find(".//v1:VersionOverrides", NS)
    desk = v11.find(".//v1:DesktopFormFactor", NS)
    events = {e.get("Type"): e.get("FunctionName") for e in desk.iter(f"{{{NS['v1']}}}LaunchEvent")}
    s.check("the From changing redoes the signature on desktop",
            events.get("OnMessageFromChanged") == "onMessageFromChangedHandler", detail=str(events))
    s.check("the new email still signs (the same compose event)",
            events.get("OnNewMessageCompose") == "onNewMessageComposeHandler")
    mobile = v11.find(".//v1:MobileFormFactor", NS)
    mob = {e.get("Type") for e in mobile.iter(f"{{{NS['v1']}}}LaunchEvent")}
    s.check("the phone keeps only the events Microsoft runs there",
            mob == {"OnNewMessageCompose", "OnMessageRecipientsChanged"}, detail=str(mob))
    base = root.find("o:Requirements/o:Sets/o:Set", NS)
    s.check("the base requirement is still Mailbox 1.5: the new calls are asked about at "
            "run time", base is not None and base.get("MinVersion") == "1.5")
    for layer in root.iter():
        if layer.tag.endswith("}Sets") and layer.get("DefaultMinVersion"):
            s.check(f"no override asks for less than the base ({layer.get('DefaultMinVersion')})",
                    tuple(int(x) for x in layer.get("DefaultMinVersion").split(".")) >= (1, 5))
    script = anon.get("/outlook-addin/launchevent.js").get_data(as_text=True)
    s.check("the handler the manifest names is registered",
            'Office.actions.associate("onMessageFromChangedHandler"' in script)
    start = script.index("var SIGNATURES = ") + len("var SIGNATURES = ")
    line = script[start:script.index("\n", start)].rstrip().rstrip(";")
    data = json.loads(line)
    s.check("the script carries all five", sorted(data["html"]) == sorted(
        spec["key"] for spec in m.ADDIN_SIGNATURES))
    logo = base64.b64decode(data["logo_base64"] or "")
    s.check("and the logo, as the PNG itself", logo.startswith(PNG) and len(logo) > 1000,
            detail=f"{len(logo)} bytes")
    s.check("the support table says what the signature does where",
            any("signature" in row["feature"] for row in m.ADDIN_SUPPORT))


# ---- in a browser -----------------------------------------------------------

FAKE_OFFICE = r"""
(function () {
  var sc = window.__scenario;
  window.__sig = [];
  window.__attached = [];
  window.__disabled = 0;
  window.__session = sc.session || {};
  function ok(value) { return { status: "succeeded", value: value }; }
  function no() { return { status: "failed", error: { message: "no" } }; }
  function later(cb, r) { setTimeout(function () { if (cb) { cb(r); } }, 1); }
  var body = {
    getAsync: function (kind, cb) { later(cb, ok("")); }
  };
  if (!sc.no_set_signature) {
    body.setSignatureAsync = function (data, options, cb) {
      var kind = options && options.coercionType;
      if (sc.plain_text && kind === "html") { later(cb, no()); return; }
      window.__sig.push({ data: data, kind: kind });
      later(cb, ok());
    };
  }
  var item = {
    to: { getAsync: function (cb) { later(cb, ok([])); } },
    cc: { getAsync: function (cb) { later(cb, ok([])); } },
    bcc: { getAsync: function (cb) { later(cb, ok([])); } },
    subject: { getAsync: function (cb) { later(cb, ok("")); } },
    body: body,
    from: { getAsync: function (cb) { later(cb, ok({ emailAddress: sc.from, displayName: sc.from })); } },
    notificationMessages: {
      replaceAsync: function (k, msg, cb) { later(cb, ok()); },
      removeAsync: function (k, cb) { later(cb, ok()); }
    },
    sessionData: {
      getAsync: function (k, cb) { later(cb, ok(window.__session[k] || "")); },
      setAsync: function (k, v, cb) { window.__session[k] = v; later(cb, ok()); }
    },
    disableClientSignatureAsync: function (cb) { window.__disabled += 1; later(cb, ok()); },
    getAttachmentsAsync: function (cb) { later(cb, ok(sc.attachments || [])); }
  };
  if (!sc.no_inline) {
    item.addFileAttachmentFromBase64Async = function (b64, name, options, cb) {
      window.__attached.push({ name: name, inline: !!(options && options.isInline), size: b64.length });
      later(cb, ok("att-1"));
    };
  }
  window.Office = {
    onReady: function () { window.__ready = true; return Promise.resolve({}); },
    actions: { associate: function (name, fn) { (window.__handlers = window.__handlers || {})[name] = fn; } },
    AsyncResultStatus: { Succeeded: "succeeded", Failed: "failed" },
    CoercionType: { Html: "html", Text: "text" },
    MailboxEnums: { ItemNotificationMessageType: { InformationalMessage: "informationalMessage" } },
    context: {
      platform: "OfficeOnline",
      requirements: { isSetSupported: function () { return false; } },
      roamingSettings: { get: function () { return undefined; }, set: function () {},
                         remove: function () {}, saveAsync: function (cb) { later(cb, ok()); } },
      mailbox: { userProfile: { emailAddress: sc.profile || sc.from }, item: item }
    }
  };
})();
"""

DRIVER = r"""
(function () {
  var sc = window.__scenario;
  var sent = false;
  function report(result) {
    if (sent) { return; }
    sent = true;
    result.signatures = window.__sig;
    result.attached = window.__attached;
    result.disabled = window.__disabled;
    result.session = window.__session;
    fetch("/__harness__/result/" + sc.name, { method: "POST", body: JSON.stringify(result) });
  }
  setTimeout(function () {
    var handler = (window.__handlers || {})[sc.handler];
    if (!handler) { report({ completed: 0, why: "nothing registered as " + sc.handler }); return; }
    var calls = 0;
    var event = { completed: function () {
      calls += 1;
      if (calls === 1) { setTimeout(function () { report({ completed: calls }); }, 300); }
    } };
    try { handler(event); } catch (e) { report({ completed: 0, why: "threw " + e }); }
    setTimeout(function () { report({ completed: calls, why: "never completed" }); }, 9500);
  }, 30);
})();
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script>window.__scenario = %(scenario)s;</script>
<script src="/__harness__/office.js"></script>
<script src="%(script)s"></script>
<script src="/__harness__/driver.js"></script>
</head><body></body></html>"""


class _Outlooks:
    """The app on a local port, with one page per scenario and a picture that
    holds the run open until every scenario has reported."""

    def __init__(self, scenarios):
        self.scenarios = {sc["name"]: sc for sc in scenarios}
        self.scripts = {}
        self.results = {}
        self.lock = threading.Lock()
        self.all_in = threading.Event()

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if not path.startswith("/__harness__/"):
            return m.app(environ, start_response)
        what = path[len("/__harness__/"):]

        def reply(body, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            start_response("200 OK", [("Content-Type", ctype), ("Content-Length", str(len(data))),
                                      ("Cache-Control", "no-store")])
            return [data]
        if what == "run":
            frames = "".join(f'<iframe src="/__harness__/scenario/{n}"></iframe>'
                             for n in self.scenarios)
            return reply("<!doctype html><html><body>" + frames
                         + "<img src='/__harness__/wait' alt=''></body></html>")
        if what == "office.js":
            return reply(FAKE_OFFICE, "application/javascript")
        if what == "driver.js":
            return reply(DRIVER, "application/javascript")
        if what.startswith("scenario/"):
            sc = self.scenarios[what.split("/", 1)[1]]
            script = ("/outlook-addin/launchevent.js" if sc.get("script", "real") == "real"
                      else "/__harness__/js/" + sc["script"])
            return reply(PAGE % {"scenario": json.dumps(sc).replace("</", "<\\/"),
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


def _run_chrome(chrome, url):
    work = tempfile.mkdtemp(prefix="gudanes_signature_")
    try:
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
             "--user-data-dir=" + os.path.join(work, "profile"),
             "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
             "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# Each control breaks the served script in one place; the scenario named
# beside it must then fail. A control that changes nothing proves nothing,
# so each is checked to have changed the script first.
BREAKS = {
    "wrong_inbox": ("var key = SIGNATURES.by_mailbox[a.slice(0, at)];",
                    'var key = SIGNATURES.by_mailbox["reservations"];'),
    "no_inline": ('html = html.split(SIGNATURES.logo_url).join("cid:" + SIGNATURES.logo_name);',
                  "html = html;"),
}


def _in_a_browser(s, conn):
    s.section("The handler picks the signature by the address the email is from")
    chrome = find_browser()
    if not s.check("there is a browser to run it in", chrome,
                   detail="none found -- set GUDANES_CHROME; nothing below was checked"):
        return
    domains = m.addin_house_domains()
    s.check("chateaugudanes.com is one of the house's own domains here",
            "chateaugudanes.com" in domains, detail=str(domains))
    at = "@chateaugudanes.com"
    compose = "onNewMessageComposeHandler"
    changed = "onMessageFromChangedHandler"
    scenarios = [
        {"name": "reservations", "handler": compose, "from": "reservations" + at},
        {"name": "house", "handler": compose, "from": "house" + at},
        {"name": "chateauteam", "handler": compose, "from": "chateauteam" + at},
        {"name": "experience", "handler": compose, "from": "experience" + at},
        {"name": "ariege", "handler": compose, "from": "ariege" + at},
        {"name": "accounts", "handler": compose, "from": "accounts" + at},
        {"name": "benw", "handler": compose, "from": "BenW" + at},
        # The person, not the inbox: Jasmine writing from her own address,
        # which has no signature of its own -- left for the server.
        {"name": "own_address", "handler": compose, "from": "jasmine" + at},
        {"name": "outsider", "handler": compose, "from": "someone@example.invalid"},
        {"name": "switched_to_own", "handler": changed, "from": "jasmine" + at,
         "session": {"gudanesSignature": "experience"}},
        {"name": "switched_inbox", "handler": changed, "from": "reservations" + at,
         "session": {"gudanesSignature": "experience"},
         "attachments": [{"name": m.ADDIN_SIGNATURE_LOGO_NAME, "isInline": True}]},
        {"name": "no_inline", "handler": compose, "from": "reservations" + at, "no_inline": True},
        {"name": "plain_text", "handler": compose, "from": "accounts" + at, "plain_text": True},
        {"name": "old_outlook", "handler": compose, "from": "reservations" + at,
         "no_set_signature": True},
        {"name": "ctl_wrong_inbox", "handler": compose, "from": "ariege" + at,
         "script": "wrong_inbox"},
        {"name": "ctl_no_inline", "handler": compose, "from": "reservations" + at,
         "script": "no_inline"},
    ]
    outlooks = _Outlooks(scenarios)
    quiet = logging.getLogger("werkzeug")
    was = quiet.level
    quiet.setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, outlooks, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        served = m.app.test_client().get("/outlook-addin/launchevent.js",
                                         base_url=base).get_data(as_text=True)
        for name, (old, new) in BREAKS.items():
            broken = served.replace(old, new)
            s.check(f"(the {name} control really changes the script)", broken != served,
                    detail=f"{old!r} not found")
            outlooks.scripts[name] = broken
        _run_chrome(chrome, base + "/__harness__/run")
    finally:
        server.shutdown()
        quiet.setLevel(was)
    res = outlooks.results
    html = {spec["key"]: spec["default"] for spec in m.ADDIN_SIGNATURES}
    cid = "cid:" + m.ADDIN_SIGNATURE_LOGO_NAME

    def signed(name):
        r = res.get(name) or {}
        sigs = r.get("signatures") or []
        return sigs[-1]["data"] if sigs else None

    def as_sent(key):
        return html[key].replace(m.ADDIN_SIGNATURE_LOGO_URL, cid)

    for name, key in (("reservations", "team"), ("house", "team"), ("chateauteam", "team"),
                      ("experience", "experience"), ("ariege", "ariege"),
                      ("accounts", "accounts"), ("benw", "benw")):
        r = res.get(name) or {}
        s.check(f"from {name}@: the {key} signature", signed(name) == as_sent(key),
                detail=(signed(name) or str(r.get("why") or "nothing set"))[:160])
    r = res.get("reservations") or {}
    s.check("as HTML", (r.get("signatures") or [{}])[-1].get("kind") == "html")
    s.check("with the logo attached inside the email, inline",
            r.get("attached") == [{"name": m.ADDIN_SIGNATURE_LOGO_NAME, "inline": True,
                                   "size": len(m.addin_signature_logo_base64())}],
            detail=str(r.get("attached")))
    s.check("and pointed at as cid:, not the hosted copy",
            cid in (signed("reservations") or "")
            and m.ADDIN_SIGNATURE_LOGO_URL not in (signed("reservations") or ""))
    s.check("a personal Outlook signature is switched off first", r.get("disabled") == 1)
    s.check("and the email remembers which one it carries",
            (r.get("session") or {}).get("gudanesSignature") == "team")
    s.check("Outlook is told it is done, once", r.get("completed") == 1, detail=str(r.get("why")))
    for name in ("own_address", "outsider"):
        r = res.get(name) or {}
        s.check(f"from {name.replace('_', ' ')}: no signature put in, so the server signs it",
                r.get("completed") == 1 and not r.get("signatures") and not r.get("attached"),
                detail=str(r.get("signatures"))[:120])

    s.section("When the From changes")
    r = res.get("switched_to_own") or {}
    s.check("to an address with none of its own: ours is taken out",
            r.get("signatures") == [{"data": "", "kind": "html"}], detail=str(r.get("signatures")))
    s.check("and forgotten", (r.get("session") or {}).get("gudanesSignature") == "")
    r = res.get("switched_inbox") or {}
    s.check("to another inbox: that inbox's signature", signed("switched_inbox") == as_sent("team"))
    s.check("without attaching the logo a second time", r.get("attached") == [],
            detail=str(r.get("attached")))
    s.check("and Outlook told it is done", r.get("completed") == 1, detail=str(r.get("why")))

    s.section("An Outlook that cannot do all of it")
    s.check("no inline attachments (a phone): the hosted logo is used",
            signed("no_inline") == html["team"], detail=(signed("no_inline") or "")[:120])
    r = res.get("plain_text") or {}
    s.check("an email in plain text gets the text signature, address line and all",
            signed("plain_text") == m.addin_signature_text(html["accounts"])
            and (r.get("signatures") or [{}])[-1].get("kind") == "text",
            detail=str(r.get("signatures"))[:160])
    r = res.get("old_outlook") or {}
    s.check("no setSignatureAsync at all: nothing breaks, and the server signs it",
            r.get("completed") == 1 and not r.get("signatures") and not r.get("attached"),
            detail=str(r.get("why")))

    s.section("(negative controls: each break is caught)")
    s.check("(a script that ignores the From gives ariege@ the wrong signature)",
            signed("ctl_wrong_inbox") not in (None, as_sent("ariege")),
            detail="the check above would pass a handler that signs everything as the team")
    s.check("(a script that forgets the cid: sends the blocked hosted logo)",
            m.ADDIN_SIGNATURE_LOGO_URL in (signed("ctl_no_inline") or ""))


if __name__ == "__main__":
    print(run().report())
