"""The Outlook add-in as Outlook sees it: the manifest, the panels, the events.

THE MANIFEST IS A SET OF CLAIMS ABOUT WHERE THINGS RUN, and so is the add-in
page's "What works where" table. They are checked against each other: the
page may say a feature works on a phone only if the manifest gives Outlook on
a phone a way to run it, and must say it does not -- with what covers it --
where Microsoft gives none. The old manifest had only a desktop form factor
while a comment in app.py said it "works on mobile": nothing checked either.

Microsoft's rules that matter here, as of 13 October 2026:
  - VersionOverrides 1.1 REPLACES 1.0 on any Outlook that reads it, so it
    must repeat the desktop buttons or they vanish on every modern Outlook;
  - Outlook on a phone opens an add-in's panel only while reading (no compose
    surface), runs OnNewMessageCompose and OnMessageRecipientsChanged, and
    does not run OnMessageSend;
  - a phone button needs 25, 32 and 48 pixel icons at three scales.

The rest: every handler the manifest names is one the script registers; the
panels load Microsoft's sign-in library pinned to a fingerprint; nothing the
app sends back is put into a panel as HTML; and the note shown when a guest
is put in To, and the send check, answer as they should.
"""
import os
import re
import xml.etree.ElementTree as ET
from datetime import timedelta

from PIL import Image

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZOA"
NS = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1",
      "bt": "http://schemas.microsoft.com/office/officeappbasictypes/1.0",
      "v0": "http://schemas.microsoft.com/office/mailappversionoverrides",
      "v1": "http://schemas.microsoft.com/office/mailappversionoverrides/1.1"}
XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _types(node, ns):
    return [ep.get(XSI) for ep in node.findall(f"{ns}:ExtensionPoint", NS)]


def _events(node, ns):
    out = {}
    for ep in node.findall(f"{ns}:ExtensionPoint", NS):
        if ep.get(XSI) == "LaunchEvent":
            for ev in ep.iter(f"{{{NS[ns]}}}LaunchEvent"):
                out[ev.get("Type")] = ev
    return out


def _resids(override, ns):
    # Its own elements only: 1.1 sits inside 1.0 and has its own resources.
    nested = {id(el) for child in override for el in child.iter()
              if child.tag.endswith("VersionOverrides")}
    used = {el.get("resid") for el in override.iter()
            if el.get("resid") and id(el) not in nested}
    defined = {el.get("id") for el in override.find(f"{ns}:Resources", NS).iter() if el.get("id")}
    return used, defined


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM addin_sessions WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'recipient_note%' "
                 "OR action LIKE 'check_send%'")
    conn.commit()


def run():
    s = Suite("the Outlook add-in")
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    conn = db()
    _cleanup(conn)

    s.section("The manifest is well formed and versioned")
    r = anon.get("/outlook-addin/manifest.xml")
    s.check("Outlook can fetch it without signing in", r.status_code == 200
            and r.content_type.startswith("application/xml"))
    try:
        root = ET.fromstring(r.get_data())
    except ET.ParseError as e:
        root = None
        s.check("it parses", False, detail=str(e))
    if root is None:
        conn.close()
        return s
    s.check("it parses", True)
    version = root.find("o:Version", NS).text
    s.check("its version is the one the app announces", version == m.OUTLOOK_ADDIN_VERSION)
    s.check("and moved on from the first", version != "1.0.0.0",
            detail="Outlook caches a manifest by its version; an unchanged number reaches nobody")
    v0 = root.find("v0:VersionOverrides", NS)
    v1 = v0.find("v1:VersionOverrides", NS)
    desk0 = v0.find("v0:Hosts/v0:Host/v0:DesktopFormFactor", NS)
    desk1 = v1.find("v1:Hosts/v1:Host/v1:DesktopFormFactor", NS)
    mob = v1.find("v1:Hosts/v1:Host/v1:MobileFormFactor", NS)

    s.section("Microsoft's layering is kept")
    s.check("an old Outlook gets the read and compose buttons",
            {"MessageReadCommandSurface", "MessageComposeCommandSurface"} <= set(_types(desk0, "v0")))
    s.check("a current Outlook gets them again in 1.1, which replaces 1.0",
            {"MessageReadCommandSurface", "MessageComposeCommandSurface"} <= set(_types(desk1, "v1")),
            detail=str(_types(desk1, "v1")))
    for override, ns, label in ((v0, "v0", "1.0"), (v1, "v1", "1.1")):
        used, defined = _resids(override, ns)
        s.check(f"every resource {label} names is defined in {label}", used <= defined,
                detail=f"missing: {sorted(used - defined)}")

    s.section("The desktop and the web")
    desk_events = _events(desk1, "v1")
    s.check("the check on Send", "OnMessageSend" in desk_events)
    s.check("which never locks anybody out of email",
            desk_events.get("OnMessageSend") is not None
            and desk_events["OnMessageSend"].get("SendMode") == "SoftBlock")
    s.check("and the note when a guest is put in To",
            {"OnMessageRecipientsChanged", "OnNewMessageCompose"} <= set(desk_events))

    s.section("The phone")
    s.check("there is a phone form factor at all", mob is not None,
            detail="without it Outlook on iPhone and Android shows nothing")
    mob_types = _types(mob, "v1") if mob is not None else []
    s.check("the panel beside a message being read", "MobileMessageReadCommandSurface" in mob_types)
    mob_events = _events(mob, "v1") if mob is not None else {}
    s.check("the note when a guest is put in To",
            {"OnMessageRecipientsChanged", "OnNewMessageCompose"} <= set(mob_events))
    s.check("and no send check, which Microsoft does not run on a phone",
            "OnMessageSend" not in mob_events)
    s.check("and no compose surface, which Outlook on a phone does not have",
            not [t for t in mob_types if "Compose" in t])
    images = [(int(i.get("size")), int(i.get("scale")), i.get("resid"))
              for i in (mob.iter(f"{{{NS['bt']}}}Image") if mob is not None else [])]
    s.check("a phone button with its nine icons",
            sorted((sz, sc) for sz, sc, _r in images) ==
            sorted((sz, sc) for sz in (25, 32, 48) for sc in (1, 2, 3)), detail=str(images))

    s.section("Every icon is the size it says")
    res = v1.find("v1:Resources/bt:Images", NS)
    for img in res.findall("bt:Image", NS):
        size = int(re.search(r"(\d+)x\d+", img.get("id")).group(1))
        url = img.get("DefaultValue")
        path = os.path.join(m.app.root_path, m.app.static_folder,
                            url.split("/static/", 1)[-1].replace("/", os.sep))
        try:
            real = Image.open(path).size
        except OSError:
            real = None
        s.check(f"{img.get('id')} is {size}px", real == (size, size), detail=f"{url} is {real}")

    s.section("Every handler the manifest names is registered")
    js = anon.get("/outlook-addin/launchevent.js").get_data(as_text=True)
    named = {ev.get("FunctionName") for evs in (desk_events, mob_events) for ev in evs.values()}
    for fn in sorted(named):
        s.check(f"{fn}", f'Office.actions.associate("{fn}", {fn})' in js,
                detail="an event handler Outlook cannot find blocks every send under SoftBlock")
    s.check("the send check fails open", js.count("allowEvent: true") >= 4)

    s.section("What the add-in page says works where, is what the manifest does")
    table = {row["feature"]: row for row in m.ADDIN_SUPPORT}
    s.check("the panel on a phone: yes, because there is a phone read surface",
            table["The guest panel beside a message"]["phone"] == "yes"
            and "MobileMessageReadCommandSurface" in mob_types)
    s.check("drafting while writing on a phone: no, and says what covers it",
            table["Draft with AI while writing"]["phone"] == "no"
            and "Draft a reply there" in table["Draft with AI while writing"]["fallback"])
    s.check("the automatic send check on a phone: no, with the Sent Items scan named",
            table["Automatic check on Send"]["phone"] == "no"
            and "Sent Items" in table["Automatic check on Send"]["fallback"]
            and "OnMessageSend" not in mob_events)
    s.check("the note in To on a phone: yes, because the phone runs that event",
            table["A note about the guest when they are put in To"]["phone"] == "yes"
            and "OnMessageRecipientsChanged" in mob_events)
    s.check("every 'no' says what covers it",
            all(row["fallback"] for row in m.ADDIN_SUPPORT
                if "no" in (row["desktop"], row["web"], row["phone"])))
    source = open(m.__file__, encoding="utf-8").read()
    s.check("and the old claim that it all works on mobile is gone",
            "Outlook on the web, and Outlook on mobile" not in source)

    s.section("The panels")
    for path in ("/outlook-addin/taskpane", "/outlook-addin/compose"):
        page = anon.get(path).get_data(as_text=True)
        s.check(f"{path} loads Microsoft's sign-in library pinned to its fingerprint",
                f'src="{m.MSAL_BROWSER_URL}" integrity="{m.MSAL_BROWSER_SRI}"' in page)
        s.check(f"{path} fits a phone", 'name="viewport"' in page)
    for name in ("outlook_addin_taskpane.html", "outlook_addin_compose.html",
                 "outlook_addin_common.js", "outlook_addin_launchevent.js"):
        text = open(os.path.join(m.app.root_path, m.app.template_folder, name),
                    encoding="utf-8").read()
        s.check(f"{name} never puts the app's answers in as HTML",
                "innerHTML" not in text and "insertAdjacentHTML" not in text,
                detail="subjects and previews are other people's words")
    common = anon.get("/outlook-addin/common.js").get_data(as_text=True)
    s.check("with sign-in off, the panels are told so", '"sso_client_id": ""' in common)
    saved = (m.ADDIN_CLIENT_ID, m.ADDIN_TENANT_ID)
    m.ADDIN_CLIENT_ID, m.ADDIN_TENANT_ID = "aaaaaaaa-0000-0000-0000-000000000001", "tenant-1"
    try:
        common = anon.get("/outlook-addin/common.js").get_data(as_text=True)
        s.check("with it on, they ask for this app's own permission",
                '"sso_scope": "api://aaaaaaaa-0000-0000-0000-000000000001/access_as_user"' in common)
    finally:
        m.ADDIN_CLIENT_ID, m.ADDIN_TENANT_ID = saved

    s.section("The add-in page")
    page = oc.get("/admin/outlook-addin").get_data(as_text=True)
    s.check("it says what works where", "What works where" in page
            and "Microsoft does not run send checks on phones" in page)
    s.check("how to put it in everybody's Outlook", "Integrated apps" in page)
    s.check("and the version Outlook will see", m.OUTLOOK_ADDIN_VERSION in page)
    s.check("it is the owner's", ec.get("/admin/outlook-addin").status_code in (302, 403))

    s.section("The note when a guest is put in To")
    today = m.house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.execute("""INSERT INTO guests (name, email, vip, caution, caution_level, created_at)
                    VALUES (?, 'zzoa.guest@example.invalid', 1, 'did not pay', 'refuse', ?)""",
                 (f"{TAG} Paul", now))
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, 'zzoa.guest@example.invalid', ?, ?, 2, 'confirmed', ?)""",
        (room["id"], f"{TAG}S1", f"{TAG}t", f"{TAG} Paul", (today + timedelta(days=9)).isoformat(),
         (today + timedelta(days=11)).isoformat(), now))
    key, _ = m.addin_issue_session(conn, emp["id"], "test", label=f"{TAG} staff")
    conn.commit()
    auth = {"Authorization": f"Bearer {key}"}
    r = anon.post("/api/addin/recipient-note",
                  data={"recipients": "reservations@chateaugudanes.com,ZZOA.Guest@example.invalid"},
                  headers=auth)
    note = (r.get_json() or {}).get("note") or ""
    s.check("it names the guest and what not to do",
            f"{TAG} Paul" in note and "Do not accept a booking" in note, detail=note)
    s.check("that they are a VIP, and when they come", "VIP" in note and "stay" in note)
    s.check("short enough for Outlook's bar", 0 < len(note) <= 150, detail=str(len(note)))
    r = anon.post("/api/addin/recipient-note", data={"recipients": "nobody@example.invalid"},
                  headers=auth)
    s.check("nobody known, no note", (r.get_json() or {}).get("note") is None)
    s.check("and without a key there is nothing there",
            anon.post("/api/addin/recipient-note", data={"recipients": "x@example.invalid"}).status_code == 404)

    s.section("Check before sending")
    r = anon.post("/api/check-send-conflict", headers=auth, data={
        "recipient_email": "zzoa.guest@example.invalid", "subject": "Hello",
        "body": "Looking forward to seeing you."})
    got = r.get_json() or {}
    s.check("a reply with nothing to check says so",
            got.get("conflict") is False and "Nothing in this reply" in got.get("checked", ""))
    room_row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    a, b = today + timedelta(days=70), today + timedelta(days=72)
    real = m.compute_room_total(conn, room_row, a, b)
    body = (f"The {room_row['name']} from {a.strftime('%d/%m/%Y')} to {b.strftime('%d/%m/%Y')} "
            f"is €{real + 300:.2f}.")
    r = anon.post("/api/check-send-conflict", headers=auth, data={
        "recipient_email": "zzoa.guest@example.invalid", "subject": "Your quote", "body": body})
    got = r.get_json() or {}
    s.check("a wrong price is caught", got.get("conflict") is True and got.get("note"),
            detail=str(got))
    if real:
        # Both ways people write a price: with the thousands separated and
        # without. "€1998.00" was read as €199 and stopped on Send.
        for spelled in (f"€{real:,.2f}", f"€{real:.2f}", f"{real:.0f} euros"):
            body_ok = (f"The {room_row['name']} from {a.strftime('%d/%m/%Y')} to "
                       f"{b.strftime('%d/%m/%Y')} is {spelled}.")
            r = anon.post("/api/check-send-conflict", headers=auth, data={
                "recipient_email": "zzoa.guest@example.invalid", "subject": "Your quote",
                "body": body_ok})
            got = r.get_json() or {}
            s.check(f"the right price passes, written {spelled!r}",
                    got.get("conflict") is False and "Checked against" in got.get("checked", ""),
                    detail=str(got))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
