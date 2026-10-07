# -*- coding: utf-8 -*-
"""Send receipts the scanner saves, and never lose one.

THE WHOLE JOB. A document scanner on a desk at the château writes a file every
time somebody presses its button. This watches the folder it writes to, posts
each new file to the house's own app, and moves it into `sent` so it is not
sent twice. Nobody at the château opens anything: put the receipt down, press
the button, walk away.

RUN IT once and leave it running, or set it as a scheduled task that starts
with the PC. It takes nothing on the command line in the ordinary case:

    python tools/watch_receipts.py

It reads two things from the environment, or from a plain text file called
receipts.ini beside this script:

    GUDANES_SITE        https://...            the app's address
    GUDANES_INGEST_KEY  the key, as set on the deployment
    GUDANES_SCAN_DIR    the folder the scanner writes to

WHY IT MOVES RATHER THAN DELETES. A receipt is the only copy of a thing
somebody paid for. Sent files go to `sent/`, failures to `problem/`, and
nothing is ever removed -- a folder somebody can look in beats a log nobody
reads, and the house can see at a glance that the morning's receipts went.

AND WHY IT IS SAFE TO SEND TWICE. The app keys a scan on the bytes it
arrived as, so a run that dies between posting a file and moving it posts the
same file again and is told which row it already made. A receipt entered
twice is a supplier paid twice, and that is worth one column to prevent.

Stdlib only. This house has no third-party HTTP library anywhere and is not
acquiring one for a script that runs on somebody's desk.
"""
import mimetypes
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PICTURES = (".jpg", ".jpeg", ".png", ".webp", ".pdf", ".tif", ".tiff")
SETTLE_SECONDS = 3          # a scanner writing a file is not a finished file
SLEEP_SECONDS = 5


def settings():
    """Where to send, with what key, and what folder to watch."""
    found = {}
    ini = os.path.join(HERE, "receipts.ini")
    if os.path.exists(ini):
        with open(ini, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                found[key.strip().upper()] = value.strip()
    for key in ("GUDANES_SITE", "GUDANES_INGEST_KEY", "GUDANES_SCAN_DIR"):
        found[key] = (os.environ.get(key) or found.get(key) or "").strip()
    return found


def post_one(site, key, path):
    """One file to /ingest/receipt. (ok, message)."""
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        payload = fh.read()
    if not payload:
        return False, "the file is empty"

    boundary = uuid.uuid4().hex
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="file"; filename="%s"\r\n'
         % name.replace('"', "")).encode("utf-8"),
        ("Content-Type: %s\r\n\r\n" % ctype).encode(),
        payload,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    req = urllib.request.Request(
        site.rstrip("/") + "/ingest/receipt", data=body, method="POST",
        headers={"X-Ingest-Key": key,
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return True, resp.read().decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        # 404 means the app has no ingest key set, which is a thing to fix at
        # the deployment rather than here. Said plainly because the status
        # code on its own sends somebody looking in the wrong place.
        if e.code == 404:
            return False, ("the app is not accepting scans: no ingest key is "
                           "set on the site")
        if e.code == 403:
            return False, "the key in this script is not the key the site has"
        return False, "the site said %s: %s" % (e.code, detail)
    except Exception as e:
        return False, "could not reach the site (%s)" % type(e).__name__


def ready(path):
    """A file the scanner has finished writing."""
    try:
        if os.path.getsize(path) <= 0:
            return False
        return (time.time() - os.path.getmtime(path)) >= SETTLE_SECONDS
    except OSError:
        return False


def main():
    cfg = settings()
    site, key, watch = (cfg["GUDANES_SITE"], cfg["GUDANES_INGEST_KEY"],
                        cfg["GUDANES_SCAN_DIR"])
    missing = [n for n, v in (("the site address", site), ("the key", key),
                              ("the folder to watch", watch)) if not v]
    if missing:
        print("Not started: I do not know %s." % " or ".join(missing))
        print("Put them in %s, or set them as environment variables."
              % os.path.join(HERE, "receipts.ini"))
        return 2
    if not os.path.isdir(watch):
        print("Not started: there is no folder at %s" % watch)
        return 2

    sent = os.path.join(watch, "sent")
    bad = os.path.join(watch, "problem")
    os.makedirs(sent, exist_ok=True)
    os.makedirs(bad, exist_ok=True)

    print("Watching %s" % watch)
    print("Sending to %s" % site.rstrip("/"))
    print("Put a receipt under the scanner and press its button. "
          "Leave this window open.")
    while True:
        for name in sorted(os.listdir(watch)):
            path = os.path.join(watch, name)
            if os.path.isdir(path) or not name.lower().endswith(PICTURES):
                continue
            if not ready(path):
                continue
            ok, message = post_one(site, key, path)
            stamp = time.strftime("%H:%M")
            if ok:
                # Moved, never deleted: a receipt is the only copy of a thing
                # somebody paid for.
                target = os.path.join(sent, name)
                n = 1
                while os.path.exists(target):
                    stem, ext = os.path.splitext(name)
                    target = os.path.join(sent, "%s (%d)%s" % (stem, n, ext))
                    n += 1
                shutil.move(path, target)
                print("%s  sent %s" % (stamp, name))
            else:
                print("%s  COULD NOT SEND %s - %s" % (stamp, name, message))
                # Left where it is on a problem that may pass -- the network,
                # the site being asleep -- so the next loop tries again. Only
                # a file the site will never accept is set aside.
                if "not accepting" in message or "not the key" in message \
                        or "empty" in message:
                    shutil.move(path, os.path.join(bad, name))
                    print("          moved to the problem folder")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        print("\nStopped. Nothing was lost — anything not sent is still in "
              "the folder and will go when this is started again.")
