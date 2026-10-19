"""The Guest button in a shared inbox, and a Guest pane that can be pinned.

THE FAULT (19 Oct 2026). In the new Outlook on Windows, a guest's email open
in experience@ -- a shared mailbox -- offered no Gudanes at all: the Apps list
beside Forward was empty. Writing a reply worked, because Outlook needs
nothing in a manifest for compose in a shared mailbox, and needs
<SupportsSharedFolders> for READ. And the Guest pane closed every time the
email changed.

What this holds:
  - the manifest still parses and is versioned on from 2.1.0.0;
  - <SupportsSharedFolders>true</SupportsSharedFolders> is in the VersionOverrides
    1.1 desktop layer, as its FIRST child, which is where Microsoft's schema
    puts it. Not in the 1.0 layer: the schema allows it in Mail 1.1 only, so
    there it would make the whole manifest invalid -- the admin centre refused
    an invalid one on 13 Oct -- and an Outlook that reads only 1.0 is too old
    for shared-mailbox add-ins in any case. Not on the phone either, which
    Microsoft does not support for this;
  - <SupportsPinning>true</SupportsPinning> on the READ pane's ShowTaskpane,
    straight after its SourceLocation, in 1.1 only (Mail 1.1 only, again), and
    on nothing else -- a compose pane does not pin;
  - the requirement sets stay at Mailbox 1.5, so the phone keeps its panel;
  - and the pane FOLLOWS a pinned move: the real panel, as the app serves it,
    run in headless Chrome with Outlook stood in. It registers for
    ItemChanged; moving to another email redraws for that guest; an answer
    about the last email that arrives after the move is dropped rather than
    drawn over the new one; and moving to no email says so. Each of these has
    a NEGATIVE CONTROL -- the panel with that promise broken -- which must
    fail, or the check proves nothing.

Needs a browser (find_browser(), or GUDANES_CHROME); without one it FAILS
rather than skips. Nothing leaves the machine: the panel's calls to the app
are stood in, and Chrome resolves no host at all.
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from _harness import Suite
import _harness
from test_staff_header_on_a_phone import find_browser

m = _harness.m
NS = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1",
      "bt": "http://schemas.microsoft.com/office/officeappbasictypes/1.0",
      "v0": "http://schemas.microsoft.com/office/mailappversionoverrides",
      "v1": "http://schemas.microsoft.com/office/mailappversionoverrides/1.1"}
XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"

FAKE_OFFICE = r"""
window.__handlers = {};
window.__slow = {};
function __item(email, name) {
  return { from: { emailAddress: email, displayName: name },
           to: [{ emailAddress: "experience@chateaugudanes.com", displayName: "Experience" }],
           cc: [], subject: "About " + name, conversationId: "c-" + email };
}
window.Office = {
  EventType: { ItemChanged: "olkItemSelectedChanged" },
  context: {
    platform: "OfficeOnline",
    requirements: { isSetSupported: function () { return false; } },
    roamingSettings: { get: function () { return null; }, set: function () {},
                       remove: function () {},
                       saveAsync: function (cb) { if (cb) { cb({ status: "succeeded" }); } } },
    mailbox: {
      item: __item("anne.zzpin@example.invalid", "Anne Zzpin"),
      userProfile: { emailAddress: "staff@chateaugudanes.com", displayName: "Staff" },
      addHandlerAsync: function (type, fn, cb) {
        window.__handlers[type] = fn;
        if (cb) { cb({ status: "succeeded" }); }
      }
    }
  },
  onReady: function (cb) {
    setTimeout(function () { cb({ host: "Outlook" }); }, 0);
    return Promise.resolve({ host: "Outlook" });
  }
};
"""

# The app, stood in: a guest named after their address, and a history that
# says whose it is. Slow for whoever __slow names.
STAND_IN = r"""
Gudanes.call = function (url, params) {
  var email = params.email;
  var wait = window.__slow[email] || 10;
  return new Promise(function (done) {
    setTimeout(function () {
      if (url === GUDANES.lookup_url) {
        done({ email: email, can_create_profile: false,
               who: { name: "Staff", how: "key", sees: "everything" },
               dossier: { profile: { name: "Name of " + email, email: email },
                          has_profile: true, known: true, warnings: [], items: [],
                          account: null, notes: [], letters: [], follow_ups: [],
                          contacts: [], feedback: [] } });
      } else if (url === GUDANES.history_url) {
        done({ available: false, why: "History of " + email });
      } else { done({}); }
    }, wait);
  });
};
"""

DRIVER = r"""
<script>
(function () {
  var out = {};
  var A = "anne.zzpin@example.invalid", B = "bob.zzpin@example.invalid";
  function name() { return document.getElementById("guestName").textContent; }
  function emails() { return document.getElementById("emails").textContent; }
  function shown() { return !document.getElementById("guest").hidden; }
  function status() {
    var el = document.getElementById("status");
    return el.hidden ? "" : el.textContent;
  }
  function move(email, words) {
    Office.context.mailbox.item = email ? __item(email, words) : null;
    var fn = window.__handlers[Office.EventType.ItemChanged];
    if (fn) { fn({ type: Office.EventType.ItemChanged }); }
  }
  function report() {
    var pre = document.createElement("pre");
    pre.id = "zzpin";
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  }
  setTimeout(function () {
    out.registered = typeof window.__handlers[Office.EventType.ItemChanged] === "function";
    out.first = { name: name(), emails: emails(), shown: shown() };
    move(B, "Bob Zzpin");
    setTimeout(function () {
      out.moved = { name: name(), emails: emails(), shown: shown() };
      // Back to Anne, whose answer is slow; on to Bob before it arrives.
      window.__slow[A] = 600;
      move(A, "Anne Zzpin");
      setTimeout(function () { move(B, "Bob Zzpin"); }, 20);
      setTimeout(function () {
        out.raced = { name: name(), emails: emails() };
        move(null);
        setTimeout(function () {
          out.none = { shown: shown(), status: status() };
          report();
        }, 200);
      }, 1200);
    }, 300);
  }, 300);
})();
</script>
"""

# Each promise, deliberately broken. Every break string must appear exactly
# once, or the "broken" panel is the real one and the control proves nothing.
BREAKS = {
    "never listens for a move":
        ("Office.context.mailbox.addHandlerAsync(Office.EventType.ItemChanged, itemChanged);",
         "/* NEGCTL */"),
    "draws a late answer over the new guest":
        ("    if (mine !== seen) { return; }\n    state.dossier = data.dossier;",
         "    state.dossier = data.dossier;"),
    "does not notice there is no email":
        ("  if (!Office.context.mailbox.item) {\n"
         "    setStatus(\"Choose an email to see who it is from.\", \"quiet\");\n"
         "    return;\n  }\n",
         ""),
}


def _standalone(pane, common):
    """The served panel with Outlook, the sign-in library and the app stood in."""
    pane = pane.replace("\r\n", "\n")
    out = re.sub(r'<script src="https://appsforoffice\.microsoft\.com/[^"]*"></script>',
                 lambda _m: "<script>" + FAKE_OFFICE + "</script>", pane)
    out = re.sub(r'<script src="[^"]*" integrity="[^"]*" crossorigin="anonymous"></script>',
                 "", out)
    out = re.sub(r'<script src="[^"]*common\.js[^"]*"></script>',
                 lambda _m: "<script>" + common + "\n" + STAND_IN + "</script>", out)
    return out.replace("</body>", DRIVER + "</body>")


def _run_panel(chrome, page):
    work = tempfile.mkdtemp(prefix="gudanes_pin_")
    try:
        path = os.path.join(work, "pane.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions",
               "--user-data-dir=" + os.path.join(work, "profile"),
               "--host-resolver-rules=MAP * ~NOTFOUND",
               "--virtual-time-budget=20000", "--dump-dom",
               "file:///" + path.replace("\\", "/").lstrip("/")]
        if sys.platform.startswith("linux"):
            cmd.insert(1, "--no-sandbox")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
        found = re.search(r'<pre id="zzpin">(.*?)</pre>', proc.stdout, re.S)
        return json.loads(htmllib.unescape(found.group(1))) if found else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _verdicts(r):
    """Each promise, as (words, kept?)."""
    r = r or {}
    first, moved, raced, none = (r.get(k) or {} for k in ("first", "moved", "raced", "none"))
    return [
        ("it listens for the email changing", r.get("registered") is True),
        ("the first email's guest is drawn",
         first.get("name") == "Name of anne.zzpin@example.invalid" and first.get("shown") is True),
        ("moved to another email, it draws that guest, and that guest's history",
         moved.get("name") == "Name of bob.zzpin@example.invalid"
         and "History of bob.zzpin@example.invalid" in (moved.get("emails") or "")),
        ("a late answer about the last email is not drawn over the new one",
         raced.get("name") == "Name of bob.zzpin@example.invalid"
         and "anne" not in (raced.get("emails") or "")),
        ("moved to no email, it says to choose one and shows nobody",
         none.get("shown") is False and "Choose an email" in (none.get("status") or "")),
    ]


def run():
    s = Suite("the add-in in a shared inbox, pinned")
    anon = m.app.test_client()

    s.section("The manifest")
    r = anon.get("/outlook-addin/manifest.xml")
    try:
        root = ET.fromstring(r.get_data())
    except ET.ParseError as e:
        s.check("it parses", False, detail=str(e))
        return s
    s.check("it parses", r.status_code == 200)
    version = root.find("o:Version", NS).text
    s.check("versioned on, so Outlook fetches it again",
            version == m.OUTLOOK_ADDIN_VERSION
            and tuple(int(x) for x in version.split(".")) > (2, 1, 0, 0), detail=version)
    v0 = root.find("v0:VersionOverrides", NS)
    v1 = v0.find("v1:VersionOverrides", NS)
    desk0 = v0.find("v0:Hosts/v0:Host/v0:DesktopFormFactor", NS)
    desk1 = v1.find("v1:Hosts/v1:Host/v1:DesktopFormFactor", NS)
    mob = v1.find("v1:Hosts/v1:Host/v1:MobileFormFactor", NS)

    shared = desk1.findall("v1:SupportsSharedFolders", NS)
    s.check("SupportsSharedFolders is on, in the 1.1 desktop layer",
            [x.text for x in shared] == ["true"], detail=str([x.text for x in shared]))
    s.check("as its first child, where the schema has it",
            len(desk1) and desk1[0].tag == f"{{{NS['v1']}}}SupportsSharedFolders",
            detail=desk1[0].tag if len(desk1) else "")
    s.check("and nowhere the schema does not allow it: not the 1.0 layer, not the phone",
            not list(v0.iter(f"{{{NS['v0']}}}SupportsSharedFolders"))
            and not list(mob.iter(f"{{{NS['v1']}}}SupportsSharedFolders")))

    def pane_action(desk, ns, surface):
        for ep in desk.findall(f"{ns}:ExtensionPoint", NS):
            if ep.get(XSI) == surface:
                return ep.find(f".//{ns}:Action", NS)
    read1 = pane_action(desk1, "v1", "MessageReadCommandSurface")
    pin = read1.findall("v1:SupportsPinning", NS) if read1 is not None else []
    s.check("the Guest pane can be pinned", [x.text for x in pin] == ["true"])
    s.check("pinning straight after its SourceLocation",
            read1 is not None and [c.tag.split("}")[1] for c in read1]
            == ["SourceLocation", "SupportsPinning"],
            detail=str(read1 is not None and [c.tag for c in read1]))
    s.check("on the read pane only: not compose, not the 1.0 layer, not the phone",
            len(list(root.iter(f"{{{NS['v1']}}}SupportsPinning"))) == 1
            and not list(v0.iter(f"{{{NS['v0']}}}SupportsPinning")))
    sets = [(st.get("DefaultMinVersion"), [x.get("Name") for x in st])
            for st in root.iter(f"{{{NS['bt']}}}Sets")]
    s.check("the requirement sets stay at Mailbox 1.5, so the phone keeps its panel",
            sets == [("1.5", ["Mailbox"]), ("1.5", ["Mailbox"])], detail=str(sets))

    s.section("The pane follows the email when pinned")
    chrome = find_browser()
    s.check("there is a browser to run the panel in", bool(chrome),
            detail="set GUDANES_CHROME; this suite fails rather than skips without one")
    if not chrome:
        return s
    pane = anon.get("/outlook-addin/taskpane").get_data(as_text=True)
    common = anon.get("/outlook-addin/common.js").get_data(as_text=True)
    page = _standalone(pane, common)
    s.check("the panel was put together with Outlook stood in",
            "window.Office = {" in page and "Gudanes.call = function" in page
            and "appsforoffice.microsoft.com" not in page)
    result = _run_panel(chrome, page)
    s.check("the panel ran and reported", result is not None)
    for words, kept in _verdicts(result):
        s.check(words, kept, detail=json.dumps(result))

    s.section("Negative controls: the panel with each promise broken")
    for what, (good, bad) in BREAKS.items():
        source = page.replace("\r\n", "\n")
        n = source.count(good)
        s.check(f"'{what}': the line to break is there exactly once", n == 1, detail=str(n))
        if n != 1:
            continue
        broken = _run_panel(chrome, source.replace(good, bad))
        failed = [w for w, kept in _verdicts(broken) if not kept]
        s.check(f"'{what}' is caught", bool(failed),
                detail="the checks above passed a broken panel: " + json.dumps(broken))
    return s
