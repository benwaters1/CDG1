"""The route map on Contact: where the house is, and the roads to it.

The owner, 19 October 2026: "put a map onto the contact page too of where we
are, design it nicely and show the routes." The design side had taken the
drive-time map off Contact on 1 October for carrying unapproved facts, and the
house has a rule about maps that cost it a guest once: a search by name sent
people to Tokyo, so anything that navigates goes to the pin set at the gates,
or nowhere (test_where_the_gates_are).

So the map is DRAWN (templates/_route_map.html, by tools/draw_route_map.py):
no embedded frame loading a third party, no coordinate typed into a page, and
the only figures on it are the ones Contact already stated. Checked here:

  - Contact draws it, with the four roads and the train, each in the legend
    with the time the page already gave;
  - with no pin: no navigation at all, and the telephone instruction stays;
  - with a pin: Google, Apple and four sets of directions, every one ending
    at the pin;
  - nothing on it is fetched from anywhere else, and no coordinate is in it;
  - the drawing in the template is exactly what the tool draws, so nobody
    hand-edits the path data and drifts from the source;
  - a handover that drops the map from contact.html, or its rules from the
    stylesheet, goes red here, and tools/repair_handover.py puts both back.
"""
import importlib.util
import io
import os
import re
import shutil
import subprocess
import tempfile

from _harness import Suite, db
import _harness

m = _harness.m
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAIR = re.compile(r"4[12]\.\d{3,}\s*[,/]\s*1\.\d{3,}|4[12]\.\d{4,}|(?<![\d.])1\.6\d{3,}")
ROUTES = ("toulouse", "carcassonne", "andorra", "barcelona")
# What Contact said before the map came back, and still says beside each road.
STATED = ("An hour and three quarters", "About an hour and forty minutes",
          "About an hour and a half", "About three hours",
          "three minutes from the gates by car")


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pin(lat, lng):
    conn = db()
    for key, value in (("house_lat", lat), ("house_lng", lng)):
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        if value is not None:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def _saved_pin():
    conn = db()
    got = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM app_settings WHERE key IN ('house_lat', 'house_lng')")}
    conn.close()
    return got.get("house_lat"), got.get("house_lng")


def _map(html):
    at = html.find('class="g-rmap"')
    end = html.find("</script>", at)
    return html[at:end] if at != -1 else ""


def run():
    s = Suite("The route map on Contact")
    before = _saved_pin()
    try:
        _run(s)
    finally:
        _pin(*before)
    return s


def _run(s):
    anon = m.app.test_client()

    s.section("Contact draws it")
    _pin(None, None)
    r = anon.get("/contact")
    page = r.get_data(as_text=True)
    drawn = _map(page)
    s.check("the page opens", r.status_code == 200, r, detail=str(r.status_code))
    s.check("with the map in it", 'class="g-rmap__svg"' in drawn)
    s.check("under Getting Here", page.find("Getting Here") < page.find('class="g-rmap"'))
    s.check("the drawing says what it shows, for a screen reader",
            "<title" in drawn and "<desc" in drawn and "Les Cabannes" in drawn)
    for key in ROUTES:
        s.check(f"the road from {key.capitalize()} is drawn and in the legend",
                f'g-rmap__r--{key}' in drawn and f'data-route="{key}"' in drawn
                and f"g-rmap__item--{key}" in drawn)
    s.check("and so is the railway", "g-rmap__rail" in drawn and "By train" in drawn)
    missing = [t for t in STATED if t not in drawn]
    s.check("each road carries the time Contact already gave it", not missing,
            detail=str(missing))
    s.check("pressing a road brings it forward (the legend's buttons say which)",
            drawn.count('aria-pressed="false"') == len(ROUTES)
            and ".g-rmap[data-show=\"toulouse\"] .g-rmap__r--toulouse" in _read("static/gudanes.css"))

    s.section("With no pin, nothing navigates")
    s.check("no directions", "maps/dir" not in drawn and "Directions from" not in drawn)
    s.check("no Google or Apple button", "maps.apple.com" not in page and "query=42" not in page)
    s.check("and no claim that a pin is the gates", "The pin is the gates" not in page)
    s.check("the way in is still said in words",
            re.search(r"(?i)telephone (?:when you leave|from) Les Cabannes", page) is not None)

    s.section("With the pin set, everything goes to it")
    _pin("42.8083", "1.6528")
    page = anon.get("/contact").get_data(as_text=True)
    drawn = _map(page)
    dirs = re.findall(r'href="([^"]*maps/dir[^"]*)"', drawn)
    s.check("four sets of directions, one per road", len(dirs) == len(ROUTES), detail=str(dirs))
    s.check("every one ends at the pin",
            dirs and all("destination=42.8083,1.6528" in d.replace("&amp;", "&") for d in dirs),
            detail=str(dirs[:1]))
    s.check("and starts where its road starts",
            any("origin=Toulouse-Blagnac" in d for d in dirs)
            and any("origin=Barcelona" in d for d in dirs), detail=str(dirs))
    with m.app.test_request_context("/"):
        links = m.house_map_links()
    s.check("the Google and Apple buttons are house_map_links, the one builder",
            links["google"].replace("&", "&amp;") in drawn
            and links["apple"].replace("&", "&amp;") in drawn)
    s.check("nothing searches by name", "query=Ch" not in drawn and "maps?q=" not in drawn)

    s.section("Drawn here, fetched from nowhere")
    s.check("no frame, no embed", "<iframe" not in drawn and "output=embed" not in drawn)
    svg = drawn[drawn.find("<svg"):drawn.find("</svg>")]
    s.check("nothing in the drawing loads from another address",
            "http" not in svg and "<image" not in svg, detail=re.findall(r"https?://\S+", svg)[:2])
    tpl = re.sub(r"\{#.*?#\}", " ", _read("templates/_route_map.html"), flags=re.S)
    s.check("and no coordinate is written into it", not PAIR.search(tpl),
            detail=str(PAIR.findall(tpl)[:3]))

    s.section("The drawing is the tool's, not a hand edit")
    tool = _load("draw_route_map_t", "tools/draw_route_map.py")
    src = _read("templates/_route_map.html")
    block = src.split(tool.BEGIN, 1)[1].split(tool.END, 1)[0].strip()
    s.check("the template holds exactly what tools/draw_route_map.py draws",
            block == tool.draw().strip(),
            detail="run python tools/draw_route_map.py, and change the drawing there")

    s.section("A handover that drops it is caught and repaired")
    s.check("contact.html calls it", "route_map()" in _read("templates/contact.html"))
    s.check("the stylesheet has its rules", ".g-rmap{" in _read("static/gudanes.css"))
    _repair_check(s)


def _repair_check(s):
    tmp = tempfile.mkdtemp(prefix="rmaprepair-")
    try:
        os.makedirs(os.path.join(tmp, "templates"))
        os.makedirs(os.path.join(tmp, "static"))
        shutil.copy(os.path.join(ROOT, "templates", "contact.html"),
                    os.path.join(tmp, "templates", "contact.html"))
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

        edit("templates/contact.html", lambda t: re.sub(
            r"\{% from '_route_map.html' import route_map with context %\}\n\s*\{\{ route_map\(\) \}\}\n", "", t))

        def drop_rules(t):
            at = t.index(".g-rmap{")
            start = t.rindex("/* ====", 0, at)
            end = t.find("\n/* ====", at)
            return t[:start] + (t[end:] if end != -1 else "")
        edit("static/gudanes.css", drop_rules)

        with io.open(os.path.join(tmp, "templates", "contact.html"), encoding="utf-8") as f:
            s.check("(the doctored copy has really lost the map)", "route_map()" not in f.read())
        tool = _load("repair_handover_rm", "tools/repair_handover.py")
        tool.ROOT = tmp
        tool.TEMPLATE_DIR = os.path.join(tmp, "templates")
        n = tool.repair_route_map()
        with io.open(os.path.join(tmp, "templates", "contact.html"), encoding="utf-8") as f:
            contact = f.read()
        heading = contact.find("Getting Here</h2>")
        s.check("the map is put back under Getting Here",
                "route_map()" in contact and heading != -1
                and heading < contact.find("route_map()") < contact.find("By car"),
                detail=f"{n} repairs")
        with io.open(os.path.join(tmp, "static", "gudanes.css"), encoding="utf-8") as f:
            s.check("its rules come back from git", ".g-rmap{" in f.read())
        s.check("and a second run changes nothing", tool.repair_route_map() == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print(run().report())
