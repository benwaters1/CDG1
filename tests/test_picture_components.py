"""The picture components and their specimen page (image layout brief, 27 Oct 2026).

Twelve ways a photograph is set on a public page, built once in
templates/_pictures.html and shown together on /design for the owner to judge.
What is held here is what the brief asks of every one of them, and what would
fail quietly if it went:

  - /design is hidden: noindex, refused in robots.txt, out of the sitemap, and
    linked from no page. A specimen page in a search result is a page of
    sample photographs with the house's name on it.
  - pic() draws every image: width and height set (nothing jumps as it loads),
    lazy unless it is the hero, a WebP srcset where the house holds the file,
    and a Squarespace address left exactly as written so the photo mirror can
    still swap it.
  - /pic answers with a WebP at the widths pic() asks for and no others, never
    wider than the file, and never anything outside static/img.
  - Never more than four together: the album takes three, the mosaic four.
  - The sliders are keyboard-driven and never move on their own.
  - Nothing moves for reduced motion.
  - Captions stay hidden on the real pages (owner, 15 Oct); only /design shows
    them, so the labels can be judged.
"""
import io
import os
import re

from _harness import Suite, ROOT
import _harness

m = _harness.m

TPL = os.path.join(ROOT, "templates")
CSS = os.path.join(ROOT, "static", "gudanes-pictures.css")
NOINDEX = re.compile(r'<meta[^>]+name=["\']robots["\'][^>]+noindex', re.I)
COMPONENTS = {
    "A cadre": "g-cadre", "B passe-partout": "g-pp", "C album": "g-album",
    "D carrousel": "g-car", "E mosaique": "g-mos", "F avant/apres": "g-aa",
    "G bande": "g-bande", "H split": "g-sp", "I plein cadre": "g-plein",
    "J medaillon": "g-med", "K carnet": "g-carnet", "L carte postale": "g-cp",
    "see all": "g-seeall", "wax seal": "g-seal", "divider": "g-div",
}
SAMPLE = "/static/img/retreats/vin-chaud.jpg"
SQUARESPACE = ("https://images.squarespace-cdn.com/content/v1/6a55fc287fb69872c3cc49bd/"
               "a92dff93-ba4c-438a-8a7f-88a1f47fa97e/Screenshot+2026-08-14+at+11.53.43+pm.png?format=1000w")


def _imgs(html):
    return re.findall(r"<img\b[^>]*>", html)


def _attr(tag, name):
    hit = re.search(r'\s' + name + r'="([^"]*)"', tag)
    return hit.group(1) if hit else None


def _inside(path, folder):
    """Whether path is in folder. Different drives are simply not inside."""
    path, folder = os.path.abspath(path), os.path.abspath(folder)
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:
        return False


def _render(source):
    with m.app.test_request_context("/design"):
        return m.app.jinja_env.from_string(source).render()


def run():
    s = Suite("Picture components")
    anon = m.app.test_client()
    was_live = m.SITE_IS_LIVE

    s.section("/design, and that it is hidden")
    try:
        m.SITE_IS_LIVE = True
        r = anon.get("/design")
        page = r.get_data(as_text=True)
        s.check("/design loads", r.status_code == 200, detail=str(r.status_code))
        s.check("it says noindex, live as well as staging", bool(NOINDEX.search(page)),
                detail="a page of sample photographs in the house's search results")
        robots = anon.get("/robots.txt").get_data(as_text=True)
        s.check("robots.txt refuses it", "Disallow: /design" in robots, detail=robots[:200])
        sitemap = anon.get("/sitemap.xml").get_data(as_text=True)
        s.check("the sitemap does not offer it", "/design<" not in sitemap)
    finally:
        m.SITE_IS_LIVE = was_live
    linked = []
    for name in sorted(os.listdir(TPL)):
        if not name.endswith(".html") or name == "design.html":
            continue
        body = io.open(os.path.join(TPL, name), encoding="utf-8").read()
        if re.search(r"""(href=["']/design["'#?]|url_for\(\s*['"]design_page['"])""", body):
            linked.append(name)
    s.check("no other page links to it", not linked, detail=", ".join(linked))
    for label, cls in COMPONENTS.items():
        s.check(f"it shows {label}", f'class="{cls} ' in page or f'class="{cls}"' in page
                or f'class="{cls}--' in page, detail=cls)

    s.section("every image in the components")
    # From the first component to the lightbox: the masthead and the footer
    # are public_base's own and arrive whole from the design side.
    body = page[page.find('id="cadre"'):page.find('class="g-lb"')]
    s.check("the components are there to read", len(_imgs(body)) > 40, detail=str(len(_imgs(body))))
    for tag in _imgs(body):
        if "data-lb-img" in tag:          # the lightbox's empty stage, filled on open
            continue
        src = _attr(tag, "src") or ""
        short = src.rsplit("/", 1)[-1][:40]
        ok = (_attr(tag, "alt") is not None and _attr(tag, "width") and _attr(tag, "height")
              and (_attr(tag, "loading") == "lazy" or _attr(tag, "fetchpriority") == "high"))
        s.check(f"{short}: alt, width, height, lazy", bool(ok), detail=tag[:160])

    s.section("pic(): how every component draws an image")
    w, h = m.pic_size(SAMPLE)
    tag = str(m.pic(SAMPLE, "Vin chaud on a silver tray", sizes="50vw"))
    s.check("the file's own width and height", f'width="{w}" height="{h}"' in tag, detail=tag)
    s.check("lazy, and decoded off the main thread",
            'loading="lazy"' in tag and 'decoding="async"' in tag, detail=tag)
    want = [x for x in m.PIC_WIDTHS if x < w] + [w]
    got = [int(x) for x in re.findall(r"/pic/(\d+)/retreats/vin-chaud\.jpg \1w", tag)]
    s.check("a WebP srcset at every width below the file's own, and the file's own",
            got == want, detail=f"{got} != {want}")
    s.check("the sizes it was given", 'sizes="50vw"' in tag, detail=tag)
    s.check("the JPEG kept as src, for anything that cannot take the srcset",
            f'src="{SAMPLE}"' in tag, detail=tag)
    hero = str(m.pic(SAMPLE, "x", hero=True))
    s.check("the hero is asked for first and never lazy",
            'fetchpriority="high"' in hero and "loading=" not in hero, detail=hero)
    remote = str(m.pic(SQUARESPACE, "Guests walking through the grounds", w=1000, h=667))
    s.check("a Squarespace photograph keeps its address exactly, for the mirror to swap",
            f'src="{SQUARESPACE}"' in remote and "srcset" not in remote, detail=remote[:200])
    s.check("and still carries the width and height it was given",
            'width="1000" height="667"' in remote, detail=remote[:200])
    s.check("a file that is not there is not offered at sizes that would 404",
            "srcset" not in str(m.pic("/static/img/retreats/no-such-photo.jpg", "x")))
    s.check("an address that climbs out of static/img is not a house file",
            m.pic_size("/static/img/../../app.py") is None
            and m.pic_size("/static/img/retreats/../../../app.py") is None)
    s.check("the alt is escaped", "&lt;b&gt;" in str(m.pic(SAMPLE, "<b>")))

    s.section("/pic: the WebP at the width asked for")
    r = anon.get(f"/pic/480{SAMPLE[len('/static/img'):]}")
    s.check("answers a listed width", r.status_code == 200, detail=str(r.status_code))
    s.check("as a WebP", r.mimetype == "image/webp", detail=r.mimetype)
    body = r.get_data()
    try:
        from PIL import Image
        with Image.open(io.BytesIO(body)) as im:
            fmt, size = im.format, im.size
    except Exception as exc:
        fmt, size = str(exc), (0, 0)
    s.check("that really is a WebP 480 wide, in proportion",
            fmt == "WEBP" and size[0] == 480 and abs(size[1] - round(h * 480 / w)) <= 1,
            detail=f"{fmt} {size}")
    again = anon.get(f"/pic/480{SAMPLE[len('/static/img'):]}").get_data()
    s.check("made once and kept: the second answer is the same file", again == body)
    s.check("kept on the volume, beside the mirrored photographs, not in the repo",
            _inside(m.PIC_DIR, m.MIRROR_DIR) and not _inside(m.PIC_DIR, ROOT),
            detail=m.PIC_DIR)
    s.check("cached by browsers for a month", "max-age=2592000" in r.headers.get("Cache-Control", ""))
    s.check("the file's own width is answered too",
            anon.get(f"/pic/{w}{SAMPLE[len('/static/img'):]}").status_code == 200)
    s.check("a width nobody asks for is refused",
            anon.get(f"/pic/481{SAMPLE[len('/static/img'):]}").status_code == 404)
    s.check("never wider than the file (it is %d)" % w, w < 2400 and anon.get(
        f"/pic/2400{SAMPLE[len('/static/img'):]}").status_code == 404)
    for bad in ("/pic/800/../app.py", "/pic/800/retreats/../../../app.py",
                "/pic/800/retreats/no-such-photo.jpg", "/pic/800/retreats/SOURCES.txt"):
        s.check(f"refuses {bad}", anon.get(bad).status_code == 404)

    s.section("never more than four together")
    six = "[" + ",".join(f"['{SAMPLE}', 'p{i}']" for i in range(6)) + "]"
    head = "{% from '_pictures.html' import album, mosaique, carrousel %}"
    out = _render(head + "{{ album(" + six + ") }}")
    s.check("the album takes three", len(_imgs(out)) == 3, detail=str(len(_imgs(out))))
    out = _render(head + "{{ mosaique(" + six + ") }}")
    s.check("the mosaic takes four", len(_imgs(out)) == 4, detail=str(len(_imgs(out))))

    s.section("the sliders")
    out = _render(head + "{{ carrousel(" + six + ", 'Six') }}")
    s.check("the track takes the keyboard", 'data-car-track tabindex="0"' in out)
    s.check("both arrows say what they do",
            out.count('aria-label="Previous photograph"') == 1
            and out.count('aria-label="Next photograph"') == 1)
    s.check("the counter is read out as it changes", 'aria-live="polite">1 / 6<' in out)
    src = io.open(os.path.join(TPL, "_pictures.html"), encoding="utf-8").read()
    script = src[src.index("macro pictures_script"):]
    s.check("nothing in the script moves a slider on a timer",
            "setInterval" not in script and "autoplay" not in script.lower())
    s.check("the arrow keys move it", "ArrowLeft" in script and "ArrowRight" in script)

    s.section("reduced motion")
    css = io.open(CSS, encoding="utf-8").read()
    calm = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\n\}", css, re.S)
    calm = calm.group(1) if calm else ""
    for what in (".g-bande__row", ".g-zoom", ".g-plein .g-plein__img", ".g-rv"):
        s.check(f"{what} is still for reduced motion", what in calm, detail="missing from the block")
    s.check("the block stops animation, not only transition",
            "animation: none" in calm and "transition: none" in calm)
    s.check("the strip is not set drifting at all for reduced motion",
            "prefers-reduced-motion: reduce" in script and "if(!still){" in script)
    s.check("photographs are only ever hidden once the script runs",
            ".g-rv-on" in css and not re.search(r"^\.g \.g-rv\{[^}]*opacity:\s*0", css, re.M))

    s.section("captions stay hidden on the site")
    s.check("hidden by default", re.search(r"\.g \.g-pc__cap\{\s*display:\s*none", css) is not None)
    shown = re.findall(r"([^{}]*)\{[^}]*\.g-pc__cap[^}]*\}", css)
    on = [sel.strip() for sel in re.findall(r"([^{}\n]+)\{\s*display:\s*block\s*!important", css)
          if "g-pc__cap" in sel]
    s.check("and shown only inside .g-caps", on and all(".g.g-caps" in sel for sel in on), detail=str(on))
    sets = re.compile(r"""class=["'][^"']*\bg-caps\b|block bodyclass %\}[^{]*\bg-caps\b""")
    using = [n for n in os.listdir(TPL) if n.endswith(".html") and n != "design.html"
             and sets.search(io.open(os.path.join(TPL, n), encoding="utf-8").read())]
    s.check("the check can see it where it is set",
            bool(sets.search(io.open(os.path.join(TPL, "design.html"), encoding="utf-8").read())))
    s.check("which no page but /design sets", not using, detail=", ".join(using))

    s.section("the stylesheet reaches every public page")
    base = io.open(os.path.join(TPL, "public_base.html"), encoding="utf-8").read()
    s.check("public_base links gudanes-pictures.css", "gudanes-pictures.css" in base)
    s.check("after gudanes.css, so its rules win a tie",
            base.find("gudanes-pictures.css") > base.find("filename='gudanes.css'") > 0)
    return s


if __name__ == "__main__":
    print(run().report())
