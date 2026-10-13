"""A stand-in for Microsoft Graph, for the suites that read mailboxes and
write the house calendar.

Nothing in the suite may reach Microsoft (the harness clears the credentials
at import). This plays Microsoft's part instead: it answers the handful of
Graph calls the app makes -- a mailbox search, a message's text, a sent
folder, a calendar, its events, its permissions, the category list -- from
dictionaries a test fills in, records every call so a test can say what was
asked, and can be told to refuse, so the failure branches run too.

It replaces graph_get and graph_send (the only two functions that talk to
Graph) and the two token functions, and puts every one of them back on exit.
"""
import re
import threading
from urllib.parse import unquote

import _harness

m = _harness.m

KEY_PROP = None   # set from the app on first use


class FakeGraph:
    def __init__(self, mailboxes=(), calendar_mailbox=None):
        self.mailboxes = [mb.lower() for mb in mailboxes]
        self.calendar_mailbox = calendar_mailbox
        self.calls = []
        self.messages = {}          # mailbox -> [message dict as Graph returns it]
        self.bodies = {}            # message id -> text
        self.sent = {}              # mailbox -> [sent message dicts]
        self.refuse_mailboxes = {}  # mailbox -> status code to answer with
        self.calendars = {}         # calendar id -> {"name", "events": {id: body}}
        self.categories = []
        self.permissions = {}       # calendar id -> [permission dicts]
        self.refuse_subjects = set()   # substrings of a subject whose writes are refused
        self.refuse_categories = False
        self._next = 0
        self._lock = threading.Lock()
        self._saved = []

    # -- bookkeeping ------------------------------------------------------
    def _id(self, prefix):
        with self._lock:
            self._next += 1
            return f"{prefix}{self._next:04d}"

    def _fail(self, status, code="ErrorItemNotFound", message="not found"):
        m._GRAPH_ANSWER.error = f"Microsoft answered {status} ({code}: {message})"
        return None

    def writes(self, method=None):
        return [c for c in self.calls if c[0] != "GET" and (method is None or c[0] == method)]

    def event_bodies(self):
        return [ev for cal in self.calendars.values() for ev in cal["events"].values()]

    # -- graph_get ----------------------------------------------------------
    def get(self, path, token, params=None, headers=None):
        params = dict(params or {})
        with self._lock:
            self.calls.append(("GET", path, params, dict(headers or {})))
        m._GRAPH_ANSWER.error = None
        hit = re.match(r"^/users/([^/]+)/(.*)$", path)
        if not hit:
            return self._fail(400, "BadRequest", "unknown path")
        mailbox, rest = unquote(hit.group(1)).lower(), hit.group(2)
        if mailbox in self.refuse_mailboxes:
            status = self.refuse_mailboxes[mailbox]
            return self._fail(status, "ErrorAccessDenied", "Access to OData is disabled: "
                              "RAOP")
        if rest == "messages" and "$search" in params:
            wanted = set(re.findall(r"participants:([^\s\"]+)", params["$search"]))
            out = []
            for msg in self.messages.get(mailbox, []):
                people = {((msg.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()}
                for r in (msg.get("toRecipients") or []) + (msg.get("ccRecipients") or []):
                    people.add(((r.get("emailAddress") or {}).get("address") or "").lower())
                if people & wanted:
                    out.append(msg)
            return {"value": out[: int(params.get("$top", "50"))]}
        hit = re.match(r"^messages/([^/]+)$", rest)
        if hit:
            text = self.bodies.get(hit.group(1))
            if text is None:
                return self._fail(404)
            return {"uniqueBody": {"contentType": "text", "content": text},
                    "body": {"contentType": "text", "content": text}}
        if rest == "mailFolders/sentitems/messages":
            return {"value": list(self.sent.get(mailbox, []))}
        if rest == "calendars":
            return {"value": [{"id": cid, "name": c["name"]} for cid, c in self.calendars.items()]}
        hit = re.match(r"^calendars/([^/]+)$", rest)
        if hit:
            if hit.group(1) not in self.calendars:
                return self._fail(404)
            return {"id": hit.group(1)}
        hit = re.match(r"^calendars/([^/]+)/events$", rest)
        if hit:
            cal = self.calendars.get(hit.group(1))
            if cal is None:
                return self._fail(404)
            value = []
            for eid, body in cal["events"].items():
                props = body.get("singleValueExtendedProperties") or []
                value.append({"id": eid, "subject": body["subject"],
                              "start": body["start"], "end": body["end"],
                              "isAllDay": body.get("isAllDay"),
                              "singleValueExtendedProperties": props})
            return {"value": value}
        hit = re.match(r"^calendars/([^/]+)/calendarPermissions$", rest)
        if hit:
            return {"value": list(self.permissions.get(hit.group(1), []))}
        if rest == "outlook/masterCategories":
            if self.refuse_categories:
                return self._fail(403, "ErrorAccessDenied", "MailboxSettings.ReadWrite missing")
            return {"value": [{"displayName": c} for c in self.categories]}
        return self._fail(400, "BadRequest", f"the double does not know {rest}")

    # -- graph_send ---------------------------------------------------------
    def send(self, method, path, token, body=None, headers=None):
        with self._lock:
            self.calls.append((method, path, body, dict(headers or {})))
        hit = re.match(r"^/users/([^/]+)/(.*)$", path)
        rest = hit.group(2) if hit else ""
        subject = (body or {}).get("subject") or ""
        if any(bad in subject for bad in self.refuse_subjects):
            return 400, None, "Microsoft answered 400 (ErrorInvalidRequest: refused by the double)"
        if method == "POST" and rest == "calendars":
            cid = self._id("cal")
            self.calendars[cid] = {"name": body["name"], "events": {}}
            self.permissions[cid] = [{"id": "orgperm", "role": "freeBusyRead",
                                      "emailAddress": {"name": "My Organization"}}]
            return 201, {"id": cid, "name": body["name"]}, None
        hit2 = re.match(r"^calendars/([^/]+)/events$", rest)
        if method == "POST" and hit2:
            cal = self.calendars.get(hit2.group(1))
            if cal is None:
                return 404, None, "Microsoft answered 404 (ErrorItemNotFound: no calendar)"
            eid = self._id("ev")
            cal["events"][eid] = dict(body)
            return 201, {"id": eid}, None
        hit2 = re.match(r"^events/([^/]+)$", rest)
        if hit2 and method in ("PATCH", "DELETE"):
            for cal in self.calendars.values():
                if hit2.group(1) in cal["events"]:
                    if method == "DELETE":
                        del cal["events"][hit2.group(1)]
                        return 204, None, None
                    cal["events"][hit2.group(1)].update(body)
                    return 200, {"id": hit2.group(1)}, None
            return 404, None, "Microsoft answered 404 (ErrorItemNotFound: gone)"
        if method == "POST" and rest == "outlook/masterCategories":
            self.categories.append(body["displayName"])
            return 201, body, None
        hit2 = re.match(r"^calendars/([^/]+)/calendarPermissions/([^/]+)$", rest)
        if method == "PATCH" and hit2:
            for p in self.permissions.get(hit2.group(1), []):
                if p["id"] == hit2.group(2):
                    p.update(body)
                    return 200, p, None
            return 404, None, "Microsoft answered 404"
        return 400, None, f"Microsoft answered 400 (the double does not know {method} {rest})"

    # -- installing ---------------------------------------------------------
    def _swap(self, owner, name, value):
        self._saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, value)

    def __enter__(self):
        self._swap(m, "graph_get", self.get)
        self._swap(m, "graph_send", self.send)
        self._swap(m, "get_graph_token", lambda: "fake-mail-token")
        self._swap(m, "get_calendar_graph_token", lambda: "fake-calendar-token")
        self._swap(m, "graph_enabled", lambda: bool(self.mailboxes))
        self._swap(m, "MS_GRAPH_MAILBOXES", list(self.mailboxes))
        if self.calendar_mailbox:
            self._swap(m, "house_calendar_configured", lambda: True)
            self._swap(m, "HOUSE_CALENDAR_MAILBOX", self.calendar_mailbox)
        m.email_cache_forget()
        return self

    def __exit__(self, *exc):
        while self._saved:
            owner, name, value = self._saved.pop()
            setattr(owner, name, value)
        m.email_cache_forget()
        return False


def message(mid, *, frm, to, subject, when, preview="", conversation="conv-1",
            internet_id=None, draft=False):
    """One message as Graph's list call returns it."""
    return {
        "id": mid, "internetMessageId": internet_id or f"<{mid}@example.invalid>",
        "conversationId": conversation, "subject": subject,
        "from": {"emailAddress": {"address": frm, "name": frm.split("@")[0].title()}},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        "ccRecipients": [], "receivedDateTime": when, "sentDateTime": when,
        "bodyPreview": preview or subject, "webLink": f"https://outlook.office.com/mail/{mid}",
        "isDraft": draft,
    }
