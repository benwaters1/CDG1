"""Collect every photograph the Squarespace account still holds, and sort it.

    python tools/harvest_photos.py              # find them, fetch them, sort them
    python tools/harvest_photos.py --list       # say what it found, fetch nothing
    python tools/harvest_photos.py --size 2500  # bigger originals (slower, larger)
    python tools/harvest_photos.py --limit 40   # stop after forty, for a first look

WHY THIS IS SEPARATE FROM tools/mirror_images.py

mirror_images.py takes a copy of the photographs the site ALREADY SHOWS, so the
house stops depending on an account it no longer publishes from. That is a
rescue, and it fetches exactly the thirty-seven URLs written into the templates.

This does the opposite job: it goes looking for everything ELSE the account is
still holding — the pictures nobody has chosen yet. Twelve years of a restoration
were photographed and most of it has never been on this site. You cannot choose
between photographs you cannot see, so this brings them all down and lays them
out to be looked at.

WHAT IT WRITES, and why in that shape

    photo_harvest/full/      the picture itself, at --size (1500px by default)
    photo_harvest/thumbs/    the same picture at 300px
    photo_harvest/duplicates/ the second and later copies of an identical file
    photo_harvest/manifest.csv  every one, with where it came from

The thumbnails are not decoration. Sorting several hundred photographs means
looking at every one of them, and a 300px copy is the difference between that
being possible and not. Squarespace renders any size on demand, so both come
straight from the CDN and nothing here needs Pillow.

Duplicates are MOVED rather than deleted. The same photograph is often uploaded
twice under different names, and telling those apart by eye is exactly the job
you would rather not do twice — but a script that deletes somebody's only copy
of something is a script nobody runs a second time. They are set aside, counted,
and left for a person to empty.

Nothing here is destructive and nothing touches a template. Choosing which
photograph appears on the site is a separate act, done at /admin/site-images
against the fifteen named slots, so that a design handover cannot undo it.
"""
import argparse
import csv
import hashlib
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "photo_harvest")
FULL, THUMBS, DUPES = (os.path.join(OUT, n) for n in ("full", "thumbs", "duplicates"))

SITE = "https://chateaugudanes.com"
CDN = re.compile(r"https://images\.squarespace-cdn\.com/content/[^\s\"'()<>\\]+")
HREF = re.compile(r'href="(/[^"#?]*)"')

# Squarespace answers a bare urllib with 403. This is the same request a browser
# makes; nothing here logs in or reaches anything the public cannot already see.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
}

# Enough of the house's vocabulary to put a picture in the right pile. Read from
# the FILENAME, which is what the photographer called it and the one label that
# travels with the file. Anything unrecognised goes to 'unsorted' rather than
# being guessed at — a wrong folder is worse than an honest one.
FOLDERS = [
    # SEP is "+ or space or _ or -", because safe_name() turns the CDN's plus
    # signs into spaces and a pattern written against the URL then matches
    # nothing. That cost "Classic Double" its folder on the first run.
    ("bedrooms",    r"chambre|bedroom|classic.?double|ex.?chef|suite"),
    ("kitchen",     r"kitchen|cuisine|cooking"),
    ("dining",      r"dining|food|table|dinner|repas"),
    ("bathrooms",   r"bathroom|salle.?de.?bain|\bbath\b"),
    ("gardens",     r"garden|jardin|parkland|grounds|orchard|pool|tennis"),
    ("exterior",    r"facade|gudanes|gates|exterior|drone|dji|aerial|night|autumn|spring"),
    ("restoration", r"restoration|before|after|day_1|plaster|fresco|scaffold"),
    ("winter",      r"winter|snow|neige"),
    ("people",      r"guest|staff|team|portrait|karina|craig|fluffy"),
    ("workshops",   r"atelier|workshop|brocante|market"),
]


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def discover_pages():
    """Every public page of the Squarespace site, from its own sitemap.

    The sitemap is the site's own list, so it beats crawling: it includes pages
    nothing links to any more, which is exactly where the forgotten photographs
    tend to be.
    """
    pages = set()
    try:
        xml = get(SITE + "/sitemap.xml").decode("utf-8", "replace")
        pages |= set(re.findall(r"<loc>([^<]+)</loc>", xml))
    except Exception as exc:
        print("  sitemap unavailable (%s) — falling back to a crawl" % exc)
    if not pages:
        pages.add(SITE + "/")
        try:
            home = get(SITE + "/").decode("utf-8", "replace")
            for path in set(HREF.findall(home)):
                pages.add(SITE + path)
        except Exception as exc:
            print("  could not reach the site at all: %s" % exc)
    return sorted(pages)


def discover_images(pages, verbose=True):
    """Every distinct photograph, keyed by its CDN path without the size."""
    found = {}
    for i, page in enumerate(pages, 1):
        try:
            html = get(page).decode("utf-8", "replace")
        except Exception as exc:
            if verbose:
                print("  [%d/%d] %-58s  unreachable (%s)"
                      % (i, len(pages), page[-58:], type(exc).__name__))
            continue
        hits = {u.split("?")[0] for u in CDN.findall(html)}
        for u in hits:
            found.setdefault(u, page)
        if verbose:
            print("  [%d/%d] %-58s  %d picture(s)" % (i, len(pages), page[-58:], len(hits)))
        time.sleep(0.3)          # the account is somebody's, not ours to hammer
    return found


def seed_from_templates():
    """The thirty-seven already written into this repo's templates."""
    out = {}
    tpl = os.path.join(ROOT, "templates")
    for name in sorted(os.listdir(tpl)):
        if not name.endswith(".html"):
            continue
        with open(os.path.join(tpl, name), encoding="utf-8", errors="replace") as fh:
            for u in CDN.findall(fh.read()):
                out.setdefault(u.split("?")[0], "template:" + name)
    return out


def safe_name(url):
    """A filename that keeps the photographer's own words and stays unique.

    The last path segment is the name somebody typed; the segment before it is
    the CDN's id. Two different photographs are quite often both called
    'IMG_2988.jpg', so the id goes on the front — otherwise the second one
    silently overwrites the first and the loss looks like a duplicate.
    """
    parts = [p for p in url.split("/") if p]
    stem = urllib.parse.unquote(parts[-1]).replace("+", " ")
    ident = parts[-2][:8] if len(parts) > 1 else "x"
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "_", stem).strip()
    return "%s__%s" % (ident, stem or "photo.jpg")


def folder_for(name):
    low = name.lower()
    for folder, pattern in FOLDERS:
        if re.search(pattern, low):
            return folder
    return "unsorted"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="find them, fetch nothing")
    ap.add_argument("--size", type=int, default=1500, help="pixel width of the copy kept")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many")
    ap.add_argument("--sorted", action="store_true",
                    help="split into bedrooms/kitchen/gardens/... subfolders")
    args = ap.parse_args()

    print("Looking for the pages...")
    pages = discover_pages()
    print("  %d page(s)\n" % len(pages))

    print("Looking for photographs...")
    found = discover_images(pages)
    seeded = seed_from_templates()
    for u, where in seeded.items():
        found.setdefault(u, where)

    print("\n%d distinct photograph(s); %d of them already used by the site"
          % (len(found), len(seeded)))
    if args.list:
        for u in sorted(found):
            print("  %-9s %s" % (folder_for(safe_name(u)), safe_name(u)))
        return 0

    for d in (FULL, THUMBS, DUPES):
        os.makedirs(d, exist_ok=True)

    seen_hash, rows, failed = {}, [], []
    items = sorted(found.items())
    if args.limit:
        items = items[:args.limit]

    for i, (url, where) in enumerate(items, 1):
        name = safe_name(url)
        folder = folder_for(name)
        # One folder by default. Ten folders is a filing system, and a
        # filing system is something to argue with before you have even
        # seen the photographs. --sorted splits them if that is wanted.
        dest_dir = os.path.join(FULL, folder) if args.sorted else FULL
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        thumb = os.path.join(THUMBS, name)

        if os.path.exists(dest) and os.path.exists(thumb):
            print("  [%d/%d] have  %s" % (i, len(items), name[:56]))
            continue
        try:
            big = get("%s?format=%dw" % (url, args.size))
            small = get("%s?format=300w" % url)
        except Exception as exc:
            print("  [%d/%d] FAIL  %s (%s)" % (i, len(items), name[:46], exc))
            failed.append((name, url, str(exc)))
            continue

        digest = hashlib.sha256(small).hexdigest()
        if digest in seen_hash:
            os.makedirs(DUPES, exist_ok=True)
            with open(os.path.join(DUPES, name), "wb") as fh:
                fh.write(big)
            print("  [%d/%d] dupe  %s  (same picture as %s)"
                  % (i, len(items), name[:40], seen_hash[digest][:34]))
            rows.append((name, folder, url, where, len(big), digest, seen_hash[digest]))
            continue

        seen_hash[digest] = name
        with open(dest, "wb") as fh:
            fh.write(big)
        with open(thumb, "wb") as fh:
            fh.write(small)
        rows.append((name, folder, url, where, len(big), digest, ""))
        print("  [%d/%d] %-9s %s  (%d KB)" % (i, len(items), folder, name[:44], len(big) // 1024))
        time.sleep(0.2)

    with open(os.path.join(OUT, "manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "folder", "source_url", "found_on", "bytes", "sha256", "duplicate_of"])
        w.writerows(rows)

    kept = sum(1 for r in rows if not r[6])
    dupes = sum(1 for r in rows if r[6])
    print("\n" + "=" * 62)
    where = "photo_harvest/full/<folder>/" if args.sorted else "photo_harvest/full/"
    print("  kept        %d photograph(s)   -> %s" % (kept, where))
    print("  duplicates  %d                 -> photo_harvest/duplicates/" % dupes)
    print("  thumbnails  %d                 -> photo_harvest/thumbs/" % kept)
    if failed:
        print("  failed      %d (run again; --size 1000 if they are large)" % len(failed))
    print("  manifest    photo_harvest/manifest.csv")
    print("=" * 62)
    print("\nNothing was deleted and no template was touched.")
    print("Tell Claude it has finished and he will go through the thumbnails.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
