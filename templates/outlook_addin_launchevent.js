/*
 * The Gudanes add-in's event handlers. No page, no window: Outlook runs these
 * on its own when something happens to a message being written.
 *
 *   OnNewMessageCompose, OnMessageRecipientsChanged -- a one-line note about
 *     the guest at the top of the message, as soon as they are in To. Runs on
 *     Outlook for Windows, Mac, the web, iPhone and Android.
 *   OnMessageSend -- the price and date check. Windows, Mac and the web only:
 *     Microsoft does not run send checks on phones.
 *
 * Written plainly (no async/await, no URLSearchParams): classic Outlook on
 * Windows runs this file in a JavaScript-only runtime, not a browser.
 *
 * NEVER IN THE WAY. Nothing here signs anybody in; it uses the key a panel
 * already left in this Outlook's roaming settings, and with none it quietly
 * does nothing. Every failure lets the message through: the send check is
 * SoftBlock in the manifest and treats an error, a timeout or a missing key
 * as "no conflict".
 */
var CONFIG = {{ config|tojson }};
var SEND_TIMEOUT_MS = 8000;
var NOTE_KEY = "gudanesGuest";

function credentials() {
  var rs = Office.context.roamingSettings;
  var session = rs.get("gudanesSession") || "";
  var expires = Date.parse(rs.get("gudanesSessionExpires") || "") || 0;
  if (session && expires > Date.now()) { return { session: session }; }
  var token = rs.get("gudanesToken") || "";
  return token ? { token: token } : null;
}

function encode(params) {
  var parts = [];
  for (var k in params) {
    if (Object.prototype.hasOwnProperty.call(params, k)) {
      parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(params[k] == null ? "" : params[k]));
    }
  }
  return parts.join("&");
}

function post(url, params, who) {
  var headers = { "Content-Type": "application/x-www-form-urlencoded" };
  if (who.session) { headers.Authorization = "Bearer " + who.session; }
  else { params.token = who.token; }
  return fetch(url, { method: "POST", headers: headers, body: encode(params) }).then(function (r) {
    if (!r.ok) { throw new Error("the app answered " + r.status); }
    return r.json();
  });
}

function addresses(list) {
  var out = [];
  for (var i = 0; i < (list || []).length; i++) {
    if (list[i] && list[i].emailAddress) { out.push(list[i].emailAddress.toLowerCase()); }
  }
  return out;
}

function showNote(item, text, done) {
  if (!text) {
    item.notificationMessages.removeAsync(NOTE_KEY, function () { done(); });
    return;
  }
  var message = { type: Office.MailboxEnums.ItemNotificationMessageType.InformationalMessage,
                  message: text, icon: "Icon.16x16", persistent: false };
  item.notificationMessages.replaceAsync(NOTE_KEY, message, function (result) {
    if (result.status === Office.AsyncResultStatus.Succeeded) { done(); return; }
    // Outlook on a phone has no icon list to name one from.
    message.icon = "none";
    item.notificationMessages.replaceAsync(NOTE_KEY, message, function () { done(); });
  });
}

function refreshGuestNote(event) {
  var finished = false;
  function done() { if (!finished) { finished = true; event.completed(); } }
  try {
    var who = credentials();
    var item = Office.context.mailbox.item;
    if (!who) { done(); return; }
    item.to.getAsync(function (toResult) {
      item.cc.getAsync(function (ccResult) {
        var list = [];
        if (toResult.status === Office.AsyncResultStatus.Succeeded) { list = list.concat(addresses(toResult.value)); }
        if (ccResult.status === Office.AsyncResultStatus.Succeeded) { list = list.concat(addresses(ccResult.value)); }
        if (!list.length) { showNote(item, "", done); return; }
        post(CONFIG.note_url, { recipients: list.join(",") }, who)
          .then(function (data) { showNote(item, data && data.note, done); })
          .catch(function () { done(); });
      });
    });
    setTimeout(done, SEND_TIMEOUT_MS);
  } catch (e) {
    done();
  }
}

function onNewMessageComposeHandler(event) { refreshGuestNote(event); }
function onMessageRecipientsChangedHandler(event) { refreshGuestNote(event); }

function onMessageSendHandler(event) {
  try {
    var item = Office.context.mailbox.item;
    var who = credentials();
    if (!who) {
      // Not signed in on this device yet: fail open, never block on a missing key.
      event.completed({ allowEvent: true });
      return;
    }
    item.to.getAsync(function (toResult) {
      var recipient = "";
      if (toResult.status === Office.AsyncResultStatus.Succeeded && toResult.value && toResult.value.length) {
        recipient = toResult.value[0].emailAddress || "";
      }
      item.subject.getAsync(function (subjectResult) {
        var subject = subjectResult.status === Office.AsyncResultStatus.Succeeded ? (subjectResult.value || "") : "";
        item.body.getAsync("text", function (bodyResult) {
          var body = bodyResult.status === Office.AsyncResultStatus.Succeeded ? (bodyResult.value || "") : "";
          checkConflict(who, recipient, subject, body, event);
        });
      });
    });
  } catch (e) {
    event.completed({ allowEvent: true });
  }
}

function checkConflict(who, recipient, subject, body, event) {
  var settled = false;
  var timer = setTimeout(function () {
    if (settled) { return; }
    settled = true;
    event.completed({ allowEvent: true }); // fail open on timeout
  }, SEND_TIMEOUT_MS);

  post(CONFIG.check_url, { recipient_email: recipient, subject: subject, body: body }, who)
    .then(function (data) {
      if (settled) { return; }
      settled = true;
      clearTimeout(timer);
      if (data && data.conflict) {
        event.completed({
          allowEvent: false,
          errorMessage: data.note || "This message may not match current pricing or availability. Please double-check before sending."
        });
      } else {
        event.completed({ allowEvent: true });
      }
    })
    .catch(function () {
      if (settled) { return; }
      settled = true;
      clearTimeout(timer);
      event.completed({ allowEvent: true }); // fail open on any network or parse error
    });
}

// Required so Outlook can find each handler. Without it, SoftBlock treats the
// add-in as broken on Send and blocks every message -- the opposite of the
// point. See "Activate add-ins with events" in Microsoft's documentation.
Office.actions.associate("onMessageSendHandler", onMessageSendHandler);
Office.actions.associate("onNewMessageComposeHandler", onNewMessageComposeHandler);
Office.actions.associate("onMessageRecipientsChangedHandler", onMessageRecipientsChangedHandler);
