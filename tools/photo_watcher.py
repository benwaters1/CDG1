"""Forward photographs from the camera's drop folder up to the château.

WHY THIS EXISTS AT ALL. The GH5 can send over Wi-Fi to exactly one useful
destination: a shared folder on a computer on the same network. Panasonic's
cloud and web-service options went when LUMIX CLUB shut down in 2022, and the
only other choice needs a phone in the loop. The app, meanwhile, runs on
Railway. The camera cannot reach it and never will, so something on the house
machine has to stand between them. This is that something.

Run it on the machine the camera sends to:

    python photo_watcher.py --folder "C:/LumixDrop" --url https://... --token ...

or put those in photo_watcher.ini beside this file and just run it.

WHAT IT IS CAREFUL ABOUT, because every one of these has a quiet failure:

  IT WAITS FOR THE FILE TO FINISH ARRIVING. A folder watcher's classic bug is
  reading a file the camera is still writing: you get half a JPEG, the server
  either refuses it or stores a picture with a grey band across the bottom,
  and because the name never changes nothing ever retries it. So a file is
  only sent once its size has stopped changing between two passes.

  IT REMEMBERS WHAT IT HAS SENT, by content rather than by name. Cameras reuse
  filenames -- P1030412.JPG comes round again every ten thousand frames, and
  formatting the card restarts the count -- so a list of names would skip a
  new photograph that happened to be called something familiar. The server
  deduplicates on content too, so this is belt and braces: this one saves the
  bandwidth, that one is the guarantee.

  IT NEVER DELETES ANYTHING. The folder is the camera's, not ours. Tidying it
  is a person's decision, and a watcher that empties a folder it does not own
  is one bad path away from deleting a card's worth of work.

  AND IT KEEPS GOING. The house connection drops, Railway restarts, the laptop
  sleeps. Anything that fails is simply not recorded as sent and is picked up
  on the next pass, so the recovery for almost everything is to wait.

Stdlib only, like the rest of this repo -- no requests, no watchdog. It has to
run on whatever Python is on that machine without anybody installing things.
"""
import argparse
import configparser
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_NAME = ".photo_watcher_sent.json"
# What a camera writes, and what a phone does. Deliberately not .rw2: the app
# cannot open a RAW file and would refuse it, so sending them is a round trip
# to a polite error. HEIC is in because the app converts it to JPEG now --
# an iPhone photograph dropped in the same folder would otherwise sit there
# unsent and nobody would know why.
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
SETTLE_SECONDS = 3


def log(message):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return set(json.load(fh).get("sent", []))
    except (OSError, ValueError):
        # A corrupt or missing record means everything looks new. That is the
        # safe way round: the server refuses the duplicates and says so.
        return set()


def save_state(path, sent):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"sent": sorted(sent)}, fh)
        os.replace(tmp, path)
    except OSError as e:
        log("could not write the record of what has been sent (%s)" % e)


def digest_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def settled(path, seen_sizes):
    """True once this file has stopped growing.

    Two passes with the same size. A camera writing a ten megabyte frame over
    Wi-Fi takes a few seconds, and a half-written JPEG is the bug this avoids:
    it stores as a picture with a grey band across the bottom, and since the
    name never changes afterwards, nothing ever tries again.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    was = seen_sizes.get(path)
    seen_sizes[path] = size
    return was is not None and was == size and size > 0


def post_photograph(url, token, path):
    """Send one file. (ok, what_the_server_said)."""
    name = os.path.basename(path)
    with open(path, "rb") as fh:
        payload = fh.read()
    boundary = "----gudanes%s" % hashlib.sha256(payload[:64] + name.encode()).hexdigest()[:16]
    mime = mimetypes.guess_type(name)[0] or "image/jpeg"
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="source"\r\n\r\ncamera\r\n').encode(),
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="photo"; filename="%s"\r\n'
         % name).encode(),
        ("Content-Type: %s\r\n\r\n" % mime).encode(),
        payload,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary,
                 "X-Gudanes-Token": token})
    try:
        # No way to turn certificate checking off, deliberately. The token in
        # that header IS the credential, and an unverified connection hands it
        # to anybody sitting in the middle. A flag for it would live in the
        # ini file on the house machine and be forgotten there. Testing
        # locally wants http://localhost, which needs no certificate at all.
        with urllib.request.urlopen(request, timeout=120) as resp:
            answer = json.loads(resp.read().decode("utf-8") or "{}")
        return bool(answer.get("ok")), answer.get("status") or "sent"
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read().decode("utf-8")).get("error") or str(e)
        except Exception:
            said = str(e)
        # 401 is worth saying loudly: it will never fix itself by waiting.
        return False, ("the token was refused — check it in the château admin"
                       if e.code == 401 else said)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, "could not reach the château (%s)" % e


def one_pass(folder, url, token, sent, seen_sizes):
    """Look once. Returns how many were newly accepted."""
    try:
        names = sorted(os.listdir(folder))
    except OSError as e:
        log("cannot read %s (%s)" % (folder, e))
        return 0
    accepted = 0
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in PHOTO_EXTENSIONS:
            continue
        if not settled(path, seen_sizes):
            continue
        try:
            digest = digest_of(path)
        except OSError:
            continue
        if digest in sent:
            continue
        ok, said = post_photograph(url, token, path)
        if ok:
            # Recorded whether it was stored or already there: both mean the
            # château has it, and that is the only question this file answers.
            sent.add(digest)
            accepted += 1
            log("%s — %s" % (name, said))
        else:
            log("%s — not sent: %s" % (name, said))
    return accepted


def settings_from(argv):
    ini = configparser.ConfigParser()
    ini.read(os.path.join(HERE, "photo_watcher.ini"))
    fallback = ini["watcher"] if ini.has_section("watcher") else {}
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--folder", default=fallback.get("folder"),
                   help="the folder the camera sends to")
    p.add_argument("--url", default=fallback.get("url"),
                   help="https://<the château>/api/photographs")
    p.add_argument("--token", default=fallback.get("token"),
                   help="from the château admin; keep it out of chat and email")
    p.add_argument("--every", type=int, default=int(fallback.get("every", 20)),
                   help="seconds between looks (default 20)")
    p.add_argument("--once", action="store_true",
                   help="one pass and stop, for testing the setup")
    return p.parse_args(argv)


def main(argv=None):
    args = settings_from(argv if argv is not None else sys.argv[1:])
    missing = [n for n in ("folder", "url", "token") if not getattr(args, n)]
    if missing:
        print("Nothing to go on: %s not set. Pass them, or put them in "
              "photo_watcher.ini beside this script." % ", ".join(missing))
        return 2
    if not os.path.isdir(args.folder):
        print("There is no folder at %s. Share a folder on this machine and "
              "point the camera at it first." % args.folder)
        return 2

    state_path = os.path.join(args.folder, STATE_NAME)
    sent = load_state(state_path)
    seen_sizes = {}
    log("watching %s — %d photograph(s) already sent" % (args.folder, len(sent)))
    if args.once:
        one_pass(args.folder, args.url, args.token, sent, seen_sizes)
        save_state(state_path, sent)
        return 0
    try:
        while True:
            before = len(sent)
            one_pass(args.folder, args.url, args.token, sent, seen_sizes)
            if len(sent) != before:
                save_state(state_path, sent)
            time.sleep(max(5, args.every))
    except KeyboardInterrupt:
        save_state(state_path, sent)
        log("stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
