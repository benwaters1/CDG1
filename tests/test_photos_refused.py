"""Photographs the owner will not have on the site, kept off it.

THE PRAM. The marble fireplace with the old black pram in an empty room (it
was static/img/found/fireplace-and-pram.jpg) came back onto the site more than
once: a band across /events/photoshoots and a tile in the gallery's
restoration set. 23 Nov 2026, the owner: "this is not to be on the site, I've
said it heaps of times". The files are deleted, and this suite fails if the
photograph returns under any name.

HOW IT IS FOUND. Not by its name alone, since a photograph that comes back
usually comes back renamed or as a house copy of someone's address. Each
refused photograph is kept here as its print, the picture reduced to 16x16 in
grey (test_page_pictures' _print, the same measure that found the King Suite's
four-poster twice), and every image under static/img and every image any
public page draws, hidden or shown, is held against it. Two sizes of the pram
measured 0.1 apart; unrelated photographs measure tens.

To refuse another: add its print to REFUSED with the owner's words.
"""
import os
import re

from _harness import Suite
import _harness
from test_home_pictures import picture_key
from test_page_pictures import _print
from test_copy_unchanged import public_paths

m = _harness.m

REFUSED = {
    "the marble fireplace and the old pram (owner, 23 Nov 2026: not to be on the site)": (1.4995, [54, 60, 44, 76, 77, 52, 61, 31, 47, 31, 19, 101, 85, 23, 24, 39, 58, 61, 49, 84, 88, 78, 74, 40, 69, 65, 63, 208, 127, 30, 36, 34, 66, 75, 63, 91, 97, 98, 80, 54, 84, 94, 98, 205, 107, 32, 31, 35, 65, 74, 84, 99, 99, 101, 84, 65, 91, 96, 90, 201, 128, 37, 32, 34, 69, 41, 80, 109, 107, 105, 84, 76, 100, 100, 95, 212, 134, 42, 47, 53, 78, 35, 56, 104, 114, 108, 84, 69, 104, 102, 107, 211, 129, 46, 57, 59, 78, 53, 53, 66, 83, 73, 72, 62, 103, 101, 112, 197, 126, 49, 54, 58, 50, 51, 59, 51, 60, 62, 61, 67, 92, 92, 31, 117, 124, 48, 40, 37, 52, 78, 102, 86, 68, 70, 68, 56, 91, 93, 13, 40, 65, 41, 34, 33, 74, 85, 78, 56, 66, 66, 40, 52, 114, 107, 48, 44, 52, 47, 44, 41, 58, 81, 63, 61, 75, 65, 42, 65, 114, 129, 125, 96, 57, 32, 33, 30, 48, 76, 71, 57, 60, 45, 33, 66, 148, 175, 153, 137, 121, 83, 32, 9, 65, 71, 84, 59, 37, 18, 26, 89, 129, 84, 69, 80, 113, 141, 116, 61, 68, 65, 82, 51, 14, 42, 106, 117, 76, 57, 59, 85, 86, 104, 127, 109, 52, 61, 86, 62, 69, 122, 119, 84, 62, 55, 71, 103, 94, 84, 89, 93, 41, 55, 94, 108, 130, 115, 91, 69, 56, 55, 74, 107, 83, 67, 68, 81]),
}
NAMES = ("fireplace-and-pram", "old pram")


def _close(a, b):
    return abs(a[0] - b[0]) < 0.02 and sum(abs(x - y) for x, y in zip(a[1], b[1])) / 256 < 8


def _file_print(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return (im.width / im.height, list(im.convert("L").resize((16, 16)).getdata()))
    except Exception:
        return None


def run():
    s = Suite("Photographs the owner refused")
    root = m.app.root_path

    s.section("The measure")
    # Negative control, in the suite itself: a refused print is found against itself, and not against a photograph
    # it is not.
    for what, pr in REFUSED.items():
        s.check(f"the measure finds {what.split(' (')[0]} against itself", _close(pr, pr))
    other = _file_print(os.path.join(root, "static", "img", "found", "great-room.jpg"))
    s.check("and does not find it in another room as found",
            other is not None and not any(_close(pr, other) for pr in REFUSED.values()))

    s.section("Not in the house's files")
    hits = []
    for folder, _dirs, files in os.walk(os.path.join(root, "static", "img")):
        for name in files:
            if not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            path = os.path.join(folder, name)
            if any(n in name for n in NAMES):
                hits.append(os.path.relpath(path, root))
                continue
            pr = _file_print(path)
            if pr and any(_close(pr, r) for r in REFUSED.values()):
                hits.append(os.path.relpath(path, root))
    s.check("no copy of a refused photograph under static/img, by name or by what it shows", not hits,
            detail=", ".join(hits[:5]))

    s.section("Not on any page")
    client = m.app.test_client()
    shown, named = {}, []
    for path in public_paths(client):
        r = client.get(path)
        if r.status_code != 200:
            continue
        page = r.get_data(as_text=True)
        for tag in re.findall(r"<img\b[^>]*>", page):
            if any(n in tag for n in NAMES):
                named.append(path)
            src = re.search(r'\ssrc="([^"]*)"', tag)
            if src and src.group(1):
                shown.setdefault(picture_key(src.group(1)), path)
    s.check("no page names a refused photograph", not named, detail=", ".join(sorted(set(named))[:5]))
    found = [(k, p) for k, p in shown.items() if _print(k) and any(_close(_print(k), r) for r in REFUSED.values())]
    s.check("no page shows one under another name", not found, detail="; ".join(f"{k} on {p}" for k, p in found[:5]))
    return s


if __name__ == "__main__":
    print(run().report())
