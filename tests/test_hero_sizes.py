"""The photograph at the top of a page goes out at the size it is shown (9 Nov 2026).

Measured on the live site at phone width: every photograph further down a page
came through pic() at its size, and the hero came whole -- eight of them were
5.7MB between them, 2.0MB on /facilities and 1.4MB on /contact, for a picture
375px wide. size_the_heroes (app.py) gives every g-hero__img the house holds the
file for the srcset pic() would have given it, at the response, so a template
that arrives whole from the design side is covered the day it lands.

Held here:
  - the house's own files: sized, every width a resizer width or the file's
    own, none wider than the file, sizes 100vw, the src exactly as written
  - the smallest of those answers, as a WebP, and is smaller than the file
  - a photograph the house holds a downloaded copy of: sized from the copy,
    and the photo swap still swaps its src; read after the swap as well
  - left alone: an address the house holds nothing for, a picture the owner
    swapped in the admin, a tag that already carries a srcset
  - every page in the sitemap: no hero the house holds goes out unsized
"""
import io
import os
import re
import secrets

from _harness import Suite, db
import _harness

m = _harness.m

HERO = re.compile(r'<img\b[^>]*\bclass="[^"]*\bg-hero__img\b[^"]*"[^>]*>')
OWN_FILES = ("/workshops", "/history", "/facilities")


def _hero(page):
    tag = HERO.search(page)
    return tag.group(0) if tag else ""


def _attr(tag, name):
    found = re.search(r'\s' + name + r'="([^"]*)"', tag)
    return found.group(1) if found else None


def _widths(srcset):
    return [(u, int(w)) for u, w in re.findall(r"(\S+) (\d+)w", srcset or "")]


def _jpeg(width, height):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(out, "JPEG", quality=90)
    return out.getvalue()


def _drop(path):
    """Remove a file this suite wrote, and say whether it went (see test_photo_mirror's _drop_stub)."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def public_paths(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    paths = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", body):
        path = re.sub(r"^https?://[^/]+", "", loc.strip()) or "/"
        if path not in paths:
            paths.append(path)
    return paths


def run():
    s = Suite("Hero photographs at their size")
    client = m.app.test_client()

    s.section("The house's own files")
    for path in OWN_FILES:
        r = client.get(path)
        tag = _hero(r.get_data(as_text=True))
        if not s.check(f"{path} has its hero", r.status_code == 200 and bool(tag), detail=str(r.status_code)):
            continue
        src = _attr(tag, "src")
        rel = src[len("/static/img/"):] if src and src.startswith("/static/img/") else None
        size = m.pic_size(src) if rel else None
        widths = _widths(_attr(tag, "srcset"))
        s.check(f"{path}: the hero offers its smaller sizes", len(widths) >= 2, detail=tag[:200])
        s.check(f"{path}: every one is this file, through the resizer",
                widths and all(u == f"/pic/{w}/{rel}" for u, w in widths), detail=str(widths[:3]))
        s.check(f"{path}: every width is a resizer width or the file's own, none wider than the file",
                size and widths and all((w in m.PIC_WIDTHS or w == size[0]) and w <= size[0] for _u, w in widths)
                and widths[-1][1] == size[0], detail=f"{size} {[w for _u, w in widths]}")
        s.check(f"{path}: asked for at the width of the screen", _attr(tag, "sizes") == "100vw")
        s.check(f"{path}: the src is still the file as written, for a browser that reads no srcset",
                src and src.startswith("/static/img/") and "/pic/" not in src, detail=src)
        if path == "/facilities" and widths:
            small = client.get(widths[0][0])
            whole = os.path.getsize(m._pic_file(src))
            s.check("the smallest answers, as a WebP", small.status_code == 200
                    and small.mimetype == "image/webp", detail=f"{small.status_code} {small.mimetype}")
            s.check("and is a fraction of the file a phone was sent before",
                    0 < len(small.get_data()) < whole / 4, detail=f"{len(small.get_data())} of {whole}")

    s.section("A photograph the house holds a downloaded copy of")
    pages = {p: client.get(p).get_data(as_text=True) for p in ("/events", "/restoration", "/whats-on", "/contact", "/book")}
    remote = [(p, _attr(_hero(h), "src")) for p, h in pages.items()]
    remote = [(p, html_src) for p, html_src in remote
              if html_src and html_src.startswith("https://") and m._pic_held(m.html_unescape(html_src)) is None]
    if not s.check("a page whose hero the house holds nothing for yet, to give it a copy", bool(remote),
                   detail=str(list(pages))):
        return s
    path, html_src = remote[0]
    url = m.html_unescape(html_src)
    name = m.mirror_name(url) + ".jpg"
    file = os.path.join(m.MIRROR_DIR, name)
    os.makedirs(m.MIRROR_DIR, exist_ok=True)
    conn = db()
    try:
        with io.open(file, "wb") as fh:
            fh.write(_jpeg(2000, 1250))
        conn.execute("INSERT OR REPLACE INTO mirrored_images (source_url, filename, bytes, content_type, fetched_at) "
                     "VALUES (?, ?, ?, 'image/jpeg', ?)", (url, name, os.path.getsize(file), _harness.datetime_now()))
        conn.commit()
        m.mirror_cache_clear()
        tag = _hero(client.get(path).get_data(as_text=True))
        widths = _widths(_attr(tag, "srcset"))
        s.check(f"{path}: sized from the copy", widths and all(u == f"/pic/{w}/~m/{name}" for u, w in widths)
                and [w for _u, w in widths] == [480, 800, 1200, 1600, 2000], detail=tag[:240])
        s.check(f"{path}: and the photo swap still sends the copy as its src",
                _attr(tag, "src") == f"/mirrored-photo/{name}", detail=_attr(tag, "src"))
        after = m.size_hero(f'<img class="g-hero__img" src="/mirrored-photo/{name}" alt="">')
        s.check("read after the swap as well, so the order of the two does not matter",
                f"/pic/800/~m/{name} 800w" in after and after.endswith(' sizes="100vw">'), detail=after)
        small = client.get(widths[0][0]) if widths else None
        s.check("its smallest size answers", small is not None and small.status_code == 200
                and small.mimetype == "image/webp", detail=str(small and small.status_code))

        s.section("A picture the owner swapped in the admin is left alone")
        token = secrets.token_hex(8)
        upload = "hero-swap-" + token + ".jpg"
        with io.open(os.path.join(m.UPLOAD_DIR, upload), "wb") as fh:
            fh.write(_jpeg(1600, 1000))
        conn.execute("INSERT OR REPLACE INTO site_photo_overrides (source_url, filename, token, set_at) VALUES (?, ?, ?, ?)",
                     (url, upload, token, _harness.datetime_now()))
        conn.commit()
        m.forget_site_overrides()
        try:
            tag = _hero(client.get(path).get_data(as_text=True))
            s.check(f"{path}: the owner's picture is the one sent", _attr(tag, "src") == f"/site-photo/{token}.jpg",
                    detail=_attr(tag, "src"))
            s.check("and nothing of the copy it replaced is offered", "srcset" not in tag and "~m/" not in tag,
                    detail=tag[:240])
        finally:
            conn.execute("DELETE FROM site_photo_overrides WHERE source_url = ?", (url,))
            conn.commit()
            m.forget_site_overrides()
            s.check("the owner's test picture is cleared away", _drop(os.path.join(m.UPLOAD_DIR, upload)))
    finally:
        conn.execute("DELETE FROM mirrored_images WHERE source_url = ?", (url,))
        conn.commit()
        conn.close()
        m.mirror_cache_clear()
        s.check("the test copy is cleared away", _drop(file))

    s.section("Anything else is left exactly as it was")
    for tag in ('<img class="g-hero__img" src="https://example.org/elsewhere.jpg" alt="">',
                '<img class="g-hero__img" src="/static/img/no-such-photograph.jpg" alt="">',
                '<img class="g-hero__img" src="/static/img/retreats/guests-toasting.jpg" srcset="/a.jpg 1w" alt="">'):
        s.check("unchanged: " + _attr(tag, "src"), m.size_hero(tag) == tag, detail=m.size_hero(tag))

    s.section("Every page in the sitemap")
    was_live = m.SITE_IS_LIVE
    try:
        m.SITE_IS_LIVE = True
        paths = public_paths(client)
        s.check("the sitemap lists the public pages", len(paths) >= 15, detail=str(len(paths)))
        heroes, bare = 0, []
        for path in paths:
            for tag in HERO.findall(client.get(path).get_data(as_text=True)):
                src = _attr(tag, "src") or ""
                if m._hero_pic_name(src) is None:
                    continue
                heroes += 1
                if not _widths(_attr(tag, "srcset")):
                    bare.append(f"{path} {src[:60]}")
        s.check("there are heroes the house holds to look at", heroes >= 3, detail=str(heroes))
        s.check("none of them goes out at full size", not bare, detail="; ".join(bare[:4]))
    finally:
        m.SITE_IS_LIVE = was_live
    return s


if __name__ == "__main__":
    print(run().report())
