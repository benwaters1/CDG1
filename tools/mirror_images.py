"""Take a copy of every photograph the site shows and does not own.

    python tools/mirror_images.py            # fetch whatever is missing
    python tools/mirror_images.py --list     # say what is missing, fetch nothing
    python tools/mirror_images.py --retry    # try the ones that failed before
    python tools/mirror_images.py --limit 20 # stop after twenty

Ninety-three photographs across forty templates, including the logo on every
page, are loaded from a Squarespace account the chateau no longer publishes
from. Nothing about that is broken today. The day it breaks, the public site
loses every picture and its masthead at once, and the recovery is to find
ninety-three photographs again -- from an account that by then has lapsed.

This downloads each one to the data volume and records it. The app then serves
its own copy: the templates are not touched, so a redesign cannot undo it.

The admin page at /admin/site-images does the same thing in twenty-second
bites, which is what a browser can wait for. This is the version for doing the
whole lot in one go on the server.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as m                                       # noqa: E402


def human(n):
    return "%.1f MB" % (n / 1048576.0) if n >= 1048576 else "%d KB" % (n / 1024)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--list", action="store_true",
                    help="say what is missing and stop")
    ap.add_argument("--retry", action="store_true",
                    help="try again on the ones that failed")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    m.init_db()
    conn = m.get_db()
    cover = m.mirror_coverage(conn)

    print("%d photographs on the site, %d held here (%d%%)"
          % (len(cover["wanted"]), cover["held"], cover["percent"]))
    print("copies go to %s" % m.MIRROR_DIR)
    if cover["orphans"]:
        # Kept, not deleted -- see mirror_coverage. Said out loud so the
        # disk figure is explainable rather than mysterious.
        print("%d more held that no page asks for any more" % len(cover["orphans"]))

    todo = list(cover["missing"])
    if args.retry:
        # A failure is already in `missing` -- the row exists with no file. The
        # flag only clears the recorded reason, so a second failure is reported
        # as a fresh one rather than as the old one still standing.
        for row in cover["errors"]:
            m.record_mirror(conn, row["source_url"], error="")
        conn.commit()
    if args.limit:
        todo = todo[:args.limit]

    if not todo:
        print("\nNothing to fetch.")
        conn.close()
        return 0

    if args.list:
        print()
        for u in todo:
            print("  " + u)
        conn.close()
        return 0

    print("\nfetching %d...\n" % len(todo))
    done = failed = total_bytes = 0
    started = time.monotonic()
    for i, url in enumerate(todo, 1):
        short = url.rsplit("/", 1)[-1][:58]
        try:
            filename, size, ctype = m.fetch_one_image(url, timeout=args.timeout)
            m.record_mirror(conn, url, filename, size, ctype)
            done += 1
            total_bytes += size
            print("  %3d/%d  %-60s %s" % (i, len(todo), short, human(size)))
        except Exception as exc:                       # noqa: BLE001
            m.record_mirror(conn, url, error=str(exc)[:200])
            failed += 1
            print("  %3d/%d  %-60s FAILED: %s" % (i, len(todo), short, exc))
        conn.commit()

    left = len(m.mirror_coverage(conn)["missing"])
    conn.close()

    print("\n%d copied (%s) in %ds, %d failed, %d still missing"
          % (done, human(total_bytes), time.monotonic() - started, failed, left))
    if not left:
        print("The public site would look the same tomorrow if that account "
              "were closed tonight.")
    else:
        print("Run it again for the rest, or --retry to re-attempt failures.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
