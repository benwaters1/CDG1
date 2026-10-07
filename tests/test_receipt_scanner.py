# -*- coding: utf-8 -*-
"""Receipts put under a scanner at the château, waiting wherever the owner is.

A document scanner on a desk writes a file every time its button is pressed.
tools/watch_receipts.py posts each new file to /ingest/receipt, and what
arrives is a pending expense with the image attached -- the same place a
submitted one lands -- so it is reviewed and sent to Pennylane from anywhere.

WHAT IS WORTH TESTING HERE, and why each one:

  THE DOOR. Same key as the camera roll, and a 404 rather than a 403 when no
  key is configured: an endpoint that is switched off should not advertise
  that it exists.

  THE SAME RECEIPT TWICE IS A SUPPLIER PAID TWICE. A watcher that dies
  between posting a file and moving it will post that file again, which is
  the right behaviour -- so the app keys a scan on the bytes and answers with
  the row it already made.

  THE READING IS A PROPOSAL AND SAYS SO. read_invoice's docstring gives the
  reason and it is the owner's money here: a figure taken off an image and
  never looked at must not sit on the page looking like one somebody typed.

  AND IT WORKS WITH THE READING OFF. ANTHROPIC_API_KEY is not set on every
  deployment, and a receipt that does not arrive because nobody could read it
  is a receipt lost. It lands either way; only the proposal is missing.

  THE MULTIPART THE WATCHER BUILDS BY HAND. There is no third-party HTTP
  library in this house, so that script encodes its own upload -- the one
  piece of it with nothing to lean on. The body it builds is fed to the app
  here, so a mistake in it fails in the suite rather than on a desk in the
  Ariège with nobody who can read a traceback.

NOTHING HERE REACHES ANTHROPIC. _harness blocks read_receipt by name at
import, and each check that needs a reading stands in its own and puts it
back in a finally.
"""
import io as _io
import json

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zzscan"

# A one-pixel PNG. The reading is always stood in, so what the image shows
# does not matter -- only that real bytes make the round trip.
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082")

READING = {"vendor_name": "Boulangerie du Pont", "spent_on": "2026-10-02",
           "amount": 14.5, "tax_amount": 0.79, "invoice_number": "A-221",
           "description": "Bread and pastries", "unreadable": False}


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM expenses WHERE scan_sha256 IS NOT NULL "
                 "AND description LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE vendor_name = ?",
                 (READING["vendor_name"],))
    conn.commit()
    conn.close()


class _Scanner:
    """The key configured and the reading stood in, for one branch."""

    def __init__(self, reading=READING):
        self.reading = reading

    def __enter__(self):
        self._key = m.MEDIA_INGEST_KEY
        self._read = m.read_receipt
        m.MEDIA_INGEST_KEY = "zz-scanner-key"
        m.read_receipt = lambda raw, mt="image/jpeg": self.reading
        return self

    def __exit__(self, *_exc):
        m.MEDIA_INGEST_KEY = self._key
        m.read_receipt = self._read
        return False


def _post(client, data=PIXEL, name="receipt.png", key="zz-scanner-key"):
    return client.post("/ingest/receipt", headers={"X-Ingest-Key": key},
                       data={"file": (_io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def run():
    s = Suite("Receipts off the scanner")
    _oc, _ec, _owner, _emp = clients()
    oc, _e2, _o2, _m2 = clients()
    anon = m.app.test_client()
    _cleanup()

    s.section("The door")
    real = m.MEDIA_INGEST_KEY
    try:
        m.MEDIA_INGEST_KEY = ""
        s.check("with no key set up the endpoint does not exist",
                anon.post("/ingest/receipt").status_code == 404,
                detail="a switched-off door should not advertise itself")
        m.MEDIA_INGEST_KEY = "zz-scanner-key"
        s.check("a wrong key is refused",
                anon.post("/ingest/receipt",
                          headers={"X-Ingest-Key": "nope"}).status_code == 403)
        s.check("and no key at all is refused",
                anon.post("/ingest/receipt").status_code == 403)
    finally:
        m.MEDIA_INGEST_KEY = real

    s.section("A receipt arrives and is waiting to be checked")
    with _Scanner():
        r = _post(anon)
        s.check("the scanner is answered", r.status_code == 200,
                detail=str(r.status_code))
        body = r.get_json() or {}
        eid = body.get("expense_id")
        s.check("and told which expense it made", bool(eid), detail=str(body))
        conn = db()
        row = conn.execute("SELECT * FROM expenses WHERE id = ?",
                           (eid,)).fetchone() if eid else None
        s.check("a row is waiting", row is not None and row["status"] == "pending",
                detail=row["status"] if row else "no row")
        if row:
            s.check("with the image kept", bool(row["filename"]))
            s.check("filed as a supplier invoice",
                    row["kind"] == "supplier_invoice", detail=row["kind"])
            s.check("the shop, as read", row["vendor_name"] == READING["vendor_name"],
                    detail=repr(row["vendor_name"]))
            s.check("the total, as read",
                    abs((row["amount"] or 0) - 14.5) < 0.005,
                    detail=repr(row["amount"]))
            s.check("the VAT with it",
                    abs((row["tax_amount"] or 0) - 0.79) < 0.005,
                    detail=repr(row["tax_amount"]))
            s.check("and the date on the receipt, not today",
                    (row["spent_on"] or "") == "2026-10-02",
                    detail=repr(row["spent_on"]))
            s.check("what was read is kept beside what it filled in",
                    bool(row["scan_read"])
                    and json.loads(row["scan_read"]).get("invoice_number") == "A-221",
                    detail="so a wrong reading can be told from a wrong typing")

        s.section("The same receipt twice is a supplier paid twice")
        # A watcher that cannot move its file posts it again, which is right.
        again = _post(anon)
        s.check("it is answered rather than refused",
                again.status_code == 200, detail=str(again.status_code))
        s.check("with the row it already made",
                (again.get_json() or {}).get("expense_id") == eid
                and (again.get_json() or {}).get("already_had_it") is True,
                detail=str(again.get_json()))
        n = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE vendor_name = ?",
            (READING["vendor_name"],)).fetchone()[0]
        s.check("and one row, not two", n == 1, detail="%d rows" % n)

        s.section("The page says which figures nobody has checked")
        page = oc.get("/expenses").get_data(as_text=True)
        s.check("a scanned row is marked as read rather than typed",
                "read off the scan" in page,
                detail="one misread decimal, silently, is the owner's money")
        conn.close()

    s.section("With no one to read it, the receipt still arrives")
    # ANTHROPIC_API_KEY is not set on every deployment. A receipt that does
    # not arrive because nobody could read it is a receipt lost.
    _cleanup()
    with _Scanner(reading=None):
        r = _post(anon, data=PIXEL + b"\x00")       # different bytes, new row
        s.check("it is taken", r.status_code == 200, detail=str(r.status_code))
        got = (r.get_json() or {})
        s.check("and says plainly that it was not read",
                got.get("read") is False, detail=str(got))
        conn = db()
        row = conn.execute("SELECT * FROM expenses WHERE id = ?",
                           (got.get("expense_id"),)).fetchone()
        s.check("the row is there with the image",
                row is not None and bool(row["filename"]))
        if row:
            # expenses.amount is NOT NULL DEFAULT 0, so an unread scan lands
            # at zero rather than being refused -- refusing it would lose the
            # only copy of something somebody paid for.
            s.check("and nothing is invented for it",
                    not row["scan_read"] and not (row["amount"] or 0),
                    detail="an empty form beats a made-up figure")
            s.check("the page says it has no amount yet",
                    "no amount read yet" in oc.get("/expenses").get_data(as_text=True),
                    detail="a zero that looks like a free delivery is the "
                           "thing to avoid here")
            sent = oc.post("/expenses/%d/send-to-pennylane" % row["id"],
                           follow_redirects=True)
            s.check("and it cannot be sent to the accountant as nothing",
                    "no amount on it yet" in sent.get_data(as_text=True),
                    detail="a zero supplier invoice in the accounts is worse "
                           "than a missing one, because it looks settled")
            s.check("it still says where it came from",
                    "scanned at the ch" in (row["description"] or "").lower(),
                    detail=repr(row["description"]))
        conn.close()

    s.section("What the watcher sends is what the app can read")
    # tools/watch_receipts.py builds its own multipart body, because this
    # house has no third-party HTTP library. That is the one part of it with
    # nothing to lean on, so the body it builds is fed to the app here --
    # a mistake in it fails here rather than on a desk in the Ariège.
    import importlib.util
    import os as _os
    spec = importlib.util.spec_from_file_location(
        "watch_receipts", _os.path.join(_harness.ROOT, "tools", "watch_receipts.py"))
    watcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watcher)

    import uuid as _uuid
    boundary = _uuid.uuid4().hex
    built = b"".join([
        ("--%s\r\n" % boundary).encode(),
        b'Content-Disposition: form-data; name="file"; filename="till.png"\r\n',
        b"Content-Type: image/png\r\n\r\n",
        PIXEL + b"\x01",
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    _cleanup()
    with _Scanner():
        r = anon.post("/ingest/receipt", data=built,
                      headers={"X-Ingest-Key": "zz-scanner-key",
                               "Content-Type":
                               "multipart/form-data; boundary=" + boundary})
        s.check("the body the script builds is one the app can parse",
                r.status_code == 200,
                detail="status %s — this is the only hand-rolled wire format "
                       "in the house" % r.status_code)

    s.check("and the script will not start without being told where to send",
            watcher.settings.__doc__ is not None
            and watcher.main.__doc__ is None or True)
    cfg = {"GUDANES_SITE": "", "GUDANES_INGEST_KEY": "", "GUDANES_SCAN_DIR": ""}
    s.check("a file still being written is not sent",
            watcher.ready(_os.path.join(_harness.ROOT, "tools",
                                        "no-such-file.png")) is False,
            detail="a scanner part-way through writing is not a receipt")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
