"""The home page, 26 November 2026.

The owner: "whats suddenly happened its so dark and the logo is fucked, the
menu also pops up on the right i want it on the left, also i want manage
booking field under the dates there" -- "in the menu side bar".

  - THE LOGO. Since the site review of 25 Nov the pages send a smaller copy of
    each picture (/pic). The copy was made in RGB unless the file was RGBA,
    and the logo is greyscale with a clear background (LA), so its clear part
    came out black: the logo in a black box on every page. A picture with a
    clear background now keeps it, the copies made before are made again,
    and a PNG's sized address changes so a browser that kept the black one
    for thirty days asks for it afresh.
  - THE HERO, lighter: the navy wash over the misty film made the page read
    as dusk. The words carry their own shadow instead.
  - THE MENU opens from the left again.
  - MANAGE YOUR BOOKING, a field in the menu under its dates, that finds the
    booking.
"""
import io
import os
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, db, house_today
import _harness

m = _harness.m
TAG = "ZZHOME"


def _css():
    with open(os.path.join(m.app.root_path, "static", "gudanes.css"), encoding="utf-8") as fh:
        return fh.read()


def _last_rule(css, selector):
    """The body of the LAST rule written for exactly this selector."""
    found = [mm.group(1) for mm in re.finditer(re.escape(selector) + r"\s*\{([^}]*)\}", css)]
    return found[-1] if found else ""


def run():
    s = Suite("The home page, 26 November 2026")
    from PIL import Image
    img_dir = os.path.join(m.app.root_path, "static", "img")
    made = []
    try:
        _checks(s, Image, img_dir, made)
    finally:
        for p in made:
            try:
                os.remove(p)
            except OSError:
                pass
        conn = db()
        conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (f"%{TAG}%",))
        conn.execute("DELETE FROM submission_log WHERE ip_address LIKE '10.78.%'")
        conn.commit()
        conn.close()
    return s


def _checks(s, Image, img_dir, made):
    client = m.app.test_client()

    s.section("A picture with a clear background keeps it at every size")
    for mode, name in (("LA", f"{TAG.lower()}-la.png"), ("P", f"{TAG.lower()}-p.png"),
                       ("RGBA", f"{TAG.lower()}-rgba.png")):
        path = os.path.join(img_dir, name)
        if mode == "LA":
            im = Image.new("LA", (900, 300), (0, 0))
            for x in range(300, 600):
                for y in range(100, 200):
                    im.putpixel((x, y), (255, 255))
        elif mode == "P":
            im = Image.new("P", (900, 300), 0)
            im.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
            im.info["transparency"] = 0
        else:
            im = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
        im.save(path, transparency=0) if mode == "P" else im.save(path)
        made.append(path)
        r = client.get(f"/pic/480/{name}")
        got = Image.open(io.BytesIO(r.get_data())) if r.status_code == 200 else None
        corner = got.convert("RGBA").getpixel((2, 2)) if got else None
        s.check(f"{mode}: the copy is still clear in the corner, not black",
                got is not None and got.mode == "RGBA" and corner[3] == 0,
                detail=f"{r.status_code} {got and got.mode} {corner}")
    jpg = os.path.join(img_dir, f"{TAG.lower()}-photo.jpg")
    Image.new("RGB", (900, 300), (200, 150, 100)).save(jpg)
    made.append(jpg)
    got = Image.open(io.BytesIO(client.get(f"/pic/480/{TAG.lower()}-photo.jpg").get_data()))
    s.check("a photograph is still sent without an alpha channel it does not need", got.mode == "RGB")
    s.check("a PNG's sized address is new, so a browser that kept the black copy asks again",
            m.pic_query("~m/abc.png") == "?a" and m.pic_query("retreats/x.jpg") == "")
    with m.app.test_request_context("/"):
        page = client.get("/").get_data(as_text=True)
    pngs = re.findall(r'/pic/\d+/[^ "]+\.png[^ "]*', page)
    s.check("and every sized PNG on the home page carries it",
            pngs and all(p.endswith("?a") for p in pngs), detail=str(pngs[:3]))

    s.section("The hero, lighter, its words still read")
    css = _css()
    wash = _last_rule(css, ".g .g-hero__wash")
    s.check("the last word on the wash: a tenth of navy at the top, half at the foot",
            "rgba(14,28,48,.10) 0%" in wash and "rgba(14,28,48,.50) 100%" in wash, detail=wash)
    s.check("and the words carry their own shadow",
            "text-shadow" in _last_rule(css, ".g .g-hero__eyebrow, .g .g-hero__title, .g .g-hero__motto, "
                                             ".g .g-hero__fact,\n.g .g-hero .g-hist-tag--home a"))

    s.section("The menu opens from the left")
    # The rule that places it (the one with position: fixed), and nothing later
    # in the file setting a side or a slide on the drawer itself.
    placing = [mm.group(1) for mm in re.finditer(r"\n\.g-navwrap\{([^}]*)\}", css)
               if "position: fixed" in mm.group(1)]
    body = placing[-1] if placing else ""
    later = [mm.group(0)[:80] for mm in re.finditer(r"\.g-navwrap(?:\.is-open)?\s*\{[^}]*\}", css)
             if re.search(r"\bright\s*:\s*0|translateX\(100%\)", mm.group(0))]
    s.check("nothing in the stylesheet sends it back to the right", not later, detail=str(later))
    s.check("it sits on the left edge, off to the left while shut",
            "left: 0" in body and "translateX(-100%)" in body and "right: 0" not in body, detail=body)
    s.check("and its shadow falls to the right, over the page", "box-shadow: 20px 0" in body)

    s.section("Manage your booking, in the menu under its dates")
    form = re.search(r'<form class="g-qbook g-qmanage"[^>]*>(.*?)</form>', page, re.S)
    whole = form.group(0) if form else ""
    s.check("it is there, after the dates", form is not None
            and page.index('class="g-qbook"') < page.index('class="g-qbook g-qmanage"'))
    s.check("and asks the reference and the email, posting to the page that finds bookings",
            'action="/book/manage"' in whole and 'name="reference_code"' in whole
            and 'name="email"' in whole and 'name="csrf_token"' in whole)
    conn = db()
    room = _harness.ensure_room()
    day = house_today() + timedelta(days=210)
    token = f"tok{TAG}{_harness.secrets_token()}".lower()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, amount_paid,
               city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, 0, 0, ?)""",
        (room["id"], m.REFERENCE_PREFIXES["room"] + "H0ME26", token, f"Guest {TAG}",
         "home.zzhome@example.invalid", day.isoformat(), (day + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    r = m.app.test_client().post("/book/manage", data={
        "reference_code": m.REFERENCE_PREFIXES["room"] + "H0ME26",
        "email": "home.zzhome@example.invalid"}, environ_base={"REMOTE_ADDR": "10.78.0.1"})
    s.check("what it sends opens the booking", r.status_code in (302, 303)
            and r.headers["Location"].endswith(f"/book/manage/{token}"),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    s.check("on every public page, not only the home page",
            'class="g-qbook g-qmanage"' in client.get("/book").get_data(as_text=True))

    s.section("The foot of every page: the house's own drawing")
    # The owner, 26 Nov 2026, with the artwork: "this is for the bottom".
    foot = re.search(r'<div class="g-crest g-crest--foot">(.*?)</div>\s*<div class="g-wrap">',
                     client.get("/contact").get_data(as_text=True), re.S)
    foot = foot.group(1) if foot else ""
    s.check("the footer draws the new artwork, at its own size",
            "img/chateau-de-gudanes-crest.png" in foot and 'width="1200" height="841"' in foot
            and "chateau_engraving.png" not in foot, detail=foot[:200])
    s.check("still saying what the three dates were, and linking to the history",
            'href="/history"' in foot and all(w in foot for w in ("The fortress", "The restoration")))
    art = Image.open(os.path.join(img_dir, "chateau-de-gudanes-crest.png"))
    s.check("drawn on nothing, so it sits on the band rather than in a box",
            art.convert("RGBA").getpixel((2, 2))[3] == 0)
    band = _last_rule(css, ".g .g-footer .g-crest--foot")
    s.check("on a parchment band of its own, where its slate blue reads",
            "background: #F6F1E7" in band, detail=band)


if __name__ == "__main__":
    print(run().report())
