# -*- coding: utf-8 -*-
"""Point this at the camera card and walk away.

    python tools/ingest_media.py E:\\DCIM --url https://... --key <token>
    python tools/ingest_media.py E:\\DCIM --watch        # keep looking

The camera roll page takes an upload, which is fine for a handful off a
phone. It is not fine for a card with four hundred frames on it that gets
plugged in every week, and it is that repetition — not the first import —
that decides whether any of this actually gets used.

WHAT THIS DOES NOT DO, deliberately:

  It does not decide anything. It posts files and stops. Everything about
  what is good, what has somebody in it and what goes on the public page
  happens server-side, once, where the rules live — so a change to those
  rules does not need this script updated on somebody's laptop.

  It does not delete from the card. Ever. The card is the negative. A script
  that tidies up after itself is a script that one day tidies up the only
  copy of something.

  It does not track what it has sent in a local file. The server knows, by
  the bytes, and asking it is one HTTP call. A local ledger goes wrong the
  first time you import from a second machine, and then goes wrong silently.

Stdlib only, like the rest of this repo — no requests.
"""
import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

PHOTO = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO = {".mp4", ".mov", ".m4v", ".webm"}
# Left out on purpose, and reported rather than skipped in silence: a
# photograph that vanishes without a word is how somebody discovers in March
# that October never arrived.
IGNORED = {".rw2", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".heic"}


def walk(root):
    """Every file worth offering, oldest first.

    Oldest first so a run that is interrupted — a card pulled, a laptop lid
    closed — has finished the beginning rather than a scatter through the
    middle, and the next run picks up where it stopped.
    """
    found, ignored = [], []
    for base, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in PHOTO or ext in VIDEO:
                found.append(path)
            elif ext in IGNORED:
                ignored.append(path)
    found.sort(key=lambda p: (os.path.getmtime(p), p))
    return found, ignored


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def post(url, key, paths):
    """One multipart POST, built by hand because this uses no third-party code."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for path in paths:
        kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
        body += ("--%s\r\n" % boundary).encode()
        body += ('Content-Disposition: form-data; name="media"; filename="%s"\r\n'
                 % os.path.basename(path)).encode()
        body += ("Content-Type: %s\r\n\r\n" % kind).encode()
        with open(path, "rb") as f:
            body += f.read()
        body += b"\r\n"
    body += ("--%s\r\n" % boundary).encode()
    body += b'Content-Disposition: form-data; name="source"\r\n\r\n'
    body += b"card\r\n"
    body += ("--%s--\r\n" % boundary).encode()

    req = urllib.request.Request(
        url.rstrip("/") + "/ingest/media", data=bytes(body), method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    req.add_header("X-Ingest-Key", key)
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.status


def already_there(url, key, hashes):
    """Ask the server which of these it has, so nothing is sent twice.

    Sending a 9MB frame to be told it is a duplicate costs the owner's
    upstream bandwidth in a French valley, every week, for the rest of the
    project. One small question first is worth it.
    """
    req = urllib.request.Request(
        url.rstrip("/") + "/ingest/known",
        data=json.dumps({"sha256": hashes}).encode(),
        method="POST", headers={"Content-Type": "application/json",
                                "X-Ingest-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return set(json.load(resp).get("known", []))
    except urllib.error.HTTPError as e:
        print("  ! the server would not answer which files it has (%s). "
              "Sending everything." % e.code)
        return set()


def run_once(root, url, key, batch):
    files, ignored = walk(root)
    if not files:
        print("Nothing to send from %s" % root)
        return 0
    print("%d file(s) on the card." % len(files))
    if ignored:
        print("  %d not sent — RAW and HEIC are not taken. Shoot RAW+JPEG:"
              % len(ignored))
        for p in ignored[:4]:
            print("      %s" % os.path.basename(p))

    by_hash = {}
    for path in files:
        by_hash.setdefault(digest(path), path)
    known = already_there(url, key, sorted(by_hash))
    fresh = [p for h, p in sorted(by_hash.items()) if h not in known]
    if not fresh:
        print("All of it is already there. Nothing sent.")
        return 0
    print("%d new. Sending in batches of %d." % (len(fresh), batch))

    sent = 0
    for i in range(0, len(fresh), batch):
        chunk = fresh[i:i + batch]
        try:
            post(url, key, chunk)
            sent += len(chunk)
            print("  sent %d/%d" % (sent, len(fresh)))
        except (urllib.error.URLError, OSError) as e:
            # Stop rather than carry on. A card half sent is fine — the next
            # run finds the rest by its hash. A card sent while the far end
            # is failing is a hundred identical errors and a lost reason.
            print("  ! stopped after %d: %s" % (sent, e))
            return sent
    return sent


def main(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", help="the card, or any folder of photographs")
    p.add_argument("--url", default=os.environ.get("GUDANES_URL",
                                                   "http://127.0.0.1:5000"))
    p.add_argument("--key", default=os.environ.get("GUDANES_INGEST_KEY", ""))
    p.add_argument("--batch", type=int, default=8,
                   help="files per request (default 8; video is large)")
    p.add_argument("--watch", action="store_true",
                   help="keep looking, every --every seconds")
    p.add_argument("--every", type=int, default=300)
    args = p.parse_args(argv[1:])

    if not os.path.isdir(args.folder):
        print("No such folder: %s" % args.folder)
        return 2
    if not args.key:
        print("No ingest key. Set GUDANES_INGEST_KEY or pass --key.")
        return 2

    if not args.watch:
        run_once(args.folder, args.url, args.key, args.batch)
        return 0
    print("Watching %s every %ds. Ctrl-C to stop." % (args.folder, args.every))
    while True:
        try:
            run_once(args.folder, args.url, args.key, args.batch)
        except KeyboardInterrupt:
            return 0
        except Exception as e:                       # keep watching
            print("  ! %s" % e)
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
