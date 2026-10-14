/*
 * The Gudanes add-in's event handlers. No window: Outlook runs these on its
 * own when something happens to a message being written.
 *
 *   OnNewMessageCompose, OnMessageRecipientsChanged -- a one-line note about
 *     the guest at the top of the message, and behind it, making sure this
 *     Outlook is signed in to the add-in before anybody presses Send.
 *     Windows, Mac, the web, iPhone and Android.
 *   OnMessageSend -- the check: the prices and dates in the email against
 *     the booking system. Windows, Mac and the web; Microsoft does not run
 *     send checks on phones.
 *
 * WHAT THE CHECK ON SEND PROMISES.
 *   - An answer inside SEND_BUDGET_MS, counted from its own first line.
 *     Outlook says "taking longer than expected" at five seconds, and those
 *     five include starting the add-in.
 *   - Mail only to the house's own addresses goes at once, without asking
 *     anybody anything.
 *   - It signs itself in. It used to check only if a panel had once been
 *     opened in that Outlook and left a key behind, so the people who most
 *     needed checking -- who never opened the panel -- were never checked.
 *     An @chateaugudanes.com account nobody has linked to a dashboard login
 *     gets a key for the check alone (kept apart, under CHECK_KEY).
 *   - A real problem stops the email and says what is wrong and what is
 *     right.
 *   - When it cannot check, it says so and the person decides. The manifest
 *     is PromptUser, so Send Anyway is always on Outlook's dialog, even if
 *     this file never loads at all. Every time it could not check is
 *     reported, and shows on the owner's home until it works again.
 *
 * PLAIN JAVASCRIPT, on purpose. Classic Outlook on Windows before Version
 * 2403 runs this file in a runtime that times out on async/await and will
 * not load a file with the ?: operator in it at all (Microsoft's own
 * troubleshooting page). test_send_check holds the file to that. The sign-in
 * library is held as a string, MSAL_SOURCE, and started only when it is
 * needed -- so a runtime too old to read it still runs everything else.
 */
var CONFIG = {{ config|tojson }};
var MSAL_SOURCE = {{ msal_source }};

var SEND_BUDGET_MS = CONFIG.send_budget_ms || 3000;
var COMPOSE_BUDGET_MS = 10000;
var NOTE_KEY = "gudanesGuest";
var SESSION_KEY = "gudanesSession";
var EXPIRES_KEY = "gudanesSessionExpires";
var TOKEN_KEY = "gudanesToken";
var CHECK_KEY = "gudanesCheckKey";
var CHECK_EXPIRES_KEY = "gudanesCheckKeyExpires";
// The key lasts a month. With three days or less left, the next email opened
// renews it, so the check on Send finds a good one waiting.
var RENEW_MS = 3 * 24 * 3600 * 1000;
// Outlook will not show a longer message on its Send dialog.
var MESSAGE_LIMIT = 500;
// Enough of the email for what the person wrote; the conversation quoted
// underneath is cut off on the server anyway, and a smaller request is a
// faster one.
var BODY_LIMIT = 20000;

// THE LINE THAT WAS MISSING. In a browser -- Outlook on the web, the new
// Outlook for Windows, Outlook for Mac -- office.js does not start until the
// page has called Office.onReady() or set Office.initialize, and it waits
// twenty seconds for that. This file did neither. So office.js never
// started, no handler here ever ran, Outlook waited on Send, and SoftBlock
// left nobody a way out: every email stopped, internal ones included. Classic
// Outlook on Windows runs this file without a page and skips onReady.
try { Office.onReady(function () {}); } catch (e) { /* the JavaScript-only runtime */ }


// ---- small things ----------------------------------------------------------

function now() { return new Date().getTime(); }

function words(code) {
  return (CONFIG.failure_words && CONFIG.failure_words[code]) || "the check could not run";
}

function platform() {
  try {
    var p = Office.context.platform;
    if (!p && Office.context.diagnostics) { p = Office.context.diagnostics.platform; }
    return String(p || "");
  } catch (e) { return ""; }
}

function profileAddress() {
  try { return String(Office.context.mailbox.userProfile.emailAddress || "").toLowerCase(); }
  catch (e) { return ""; }
}

function supports(set, version) {
  try { return !!Office.context.requirements.isSetSupported(set, version); }
  catch (e) { return false; }
}

function contains(list, value) {
  for (var i = 0; i < (list || []).length; i++) {
    if (list[i] === value) { return true; }
  }
  return false;
}

function isHouse(address) {
  var a = String(address || "").toLowerCase();
  var at = a.lastIndexOf("@");
  if (contains(CONFIG.house_addresses, a)) { return true; }
  return at > 0 && contains(CONFIG.house_domains, a.slice(at + 1));
}

function clip(text) {
  text = String(text || "");
  if (text.length <= MESSAGE_LIMIT) { return text; }
  return text.slice(0, MESSAGE_LIMIT - 1) + "…";
}

function encode(params) {
  var parts = [];
  for (var k in params) {
    if (Object.prototype.hasOwnProperty.call(params, k)) {
      var v = params[k];
      if (v === null || v === undefined) { v = ""; }
      parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v)));
    }
  }
  return parts.join("&");
}

// Belt and braces for the manifest's PromptUser. Where Outlook can be told at
// run time (Mailbox 1.14), every stop this handler makes says so itself, so
// Send Anyway is on the dialog even while Microsoft 365 still serves an older
// manifest -- as it does between a deploy and somebody pressing Update.
function promptUser(options) {
  try {
    if (supports("Mailbox", "1.14") && Office.MailboxEnums.SendModeOverride) {
      options.sendModeOverride = Office.MailboxEnums.SendModeOverride.PromptUser;
    }
  } catch (e) { /* an Outlook without it reads the manifest's */ }
  return options;
}

// Once only: Outlook takes the first answer, and a second can throw. An
// option an older Outlook does not know is dropped before giving up on it,
// and the last resort lets the email go rather than leave Outlook waiting.
function complete(event, options) {
  try { event.completed(options); return; } catch (e) { /* try plainer */ }
  if (options) {
    try { event.completed({ allowEvent: options.allowEvent, errorMessage: options.errorMessage }); return; }
    catch (e2) { /* and plainer still */ }
  }
  try { event.completed({ allowEvent: true }); } catch (e3) { /* nothing left to try */ }
}


// ---- asking the app --------------------------------------------------------

// POST, with a deadline. done(error, status, data) is called exactly once:
// with the answer, or with an error that says whether it was the deadline.
function post(url, params, who, timeoutMs, done) {
  var settled = false;
  var controller = null;
  var timer = null;
  function finish(error, status, data) {
    if (settled) { return; }
    settled = true;
    if (timer) { clearTimeout(timer); }
    done(error, status, data);
  }
  var headers = { "Content-Type": "application/x-www-form-urlencoded" };
  if (who && who.bearer) { headers.Authorization = "Bearer " + who.bearer; }
  else if (who && who.session) { headers.Authorization = "Bearer " + who.session; }
  else if (who && who.token) { params.token = who.token; }
  var options = { method: "POST", headers: headers, body: encode(params) };
  try {
    if (typeof AbortController !== "undefined") {
      controller = new AbortController();
      options.signal = controller.signal;
    }
  } catch (e) { controller = null; }
  timer = setTimeout(function () {
    if (controller) { try { controller.abort(); } catch (e) { /* already gone */ } }
    var late = new Error("no answer in time");
    late.timedOut = true;
    finish(late);
  }, Math.max(1, timeoutMs));
  try {
    fetch(url, options).then(function (r) {
      return r.text().then(function (text) {
        var data = null;
        try { if (text) { data = JSON.parse(text); } } catch (e) { data = null; }
        finish(null, r.status, data);
      });
    })["catch"](function (e) { finish(e || new Error("the request failed")); });
  } catch (e) {
    finish(e);
  }
}

// What could not be checked, and why, so it reaches the owner's home. Never
// waited for: it must not cost the person a moment, and keepalive lets it
// outlive the handler that sent it.
function reportFailure(reason, detail, elapsed, who) {
  try {
    var params = { reason: reason, detail: String(detail || "").slice(0, 120),
                   platform: platform(), mailbox: profileAddress(),
                   client_ms: String(elapsed), version: CONFIG.version };
    var headers = { "Content-Type": "application/x-www-form-urlencoded" };
    if (who && who.session) { headers.Authorization = "Bearer " + who.session; }
    else if (who && who.token) { params.token = who.token; }
    fetch(CONFIG.failed_url, { method: "POST", headers: headers, body: encode(params),
                               keepalive: true })["catch"](function () { /* told nobody; nothing else to do */ });
  } catch (e) { /* no fetch at all */ }
}


// ---- who is asking ---------------------------------------------------------

function roaming() { return Office.context.roamingSettings; }

// The key in this Outlook's roaming settings: a person's own while it is in
// date; else a check-only key (an account of the house's nobody has linked to
// a person yet, which the check on Send accepts and the panel does not); else
// the house's shared key; else null.
function heldKey() {
  try {
    var rs = roaming();
    var session = rs.get(SESSION_KEY) || "";
    var expires = Date.parse(rs.get(EXPIRES_KEY) || "") || 0;
    if (session && expires > now()) { return { session: session, expires: expires }; }
    var checker = rs.get(CHECK_KEY) || "";
    var checkUntil = Date.parse(rs.get(CHECK_EXPIRES_KEY) || "") || 0;
    if (checker && checkUntil > now()) {
      return { session: checker, expires: checkUntil, checkOnly: true };
    }
    var token = rs.get(TOKEN_KEY) || "";
    if (token) { return { token: token }; }
  } catch (e) { /* no roaming settings here */ }
  return null;
}

// A check-only key is kept under names of its own. The panel reads the
// person's key; given this one it would be refused, forget it, and the two
// would take turns undoing each other.
function keepKey(session, expiresAt, checkOnly, done) {
  try {
    var rs = roaming();
    if (checkOnly) {
      rs.set(CHECK_KEY, session);
      rs.set(CHECK_EXPIRES_KEY, expiresAt);
    } else {
      rs.set(SESSION_KEY, session);
      rs.set(EXPIRES_KEY, expiresAt);
    }
    rs.saveAsync(function () { if (done) { done(); } });
  } catch (e) { if (done) { done(); } }
}

function forgetKey(who) {
  try {
    var rs = roaming();
    if (who && who.checkOnly) {
      rs.remove(CHECK_KEY);
      rs.remove(CHECK_EXPIRES_KEY);
    } else {
      rs.remove(SESSION_KEY);
      rs.remove(EXPIRES_KEY);
    }
    rs.saveAsync(function () { /* nothing waits on it */ });
  } catch (e) { /* nothing to forget */ }
}

var msalApp = null;
var signInDetail = "";

function canSignIn() {
  return !!(CONFIG.sso_client_id && MSAL_SOURCE && supports("NestedAppAuth", "1.1"));
}

function globalRoot() {
  if (typeof globalThis !== "undefined") { return globalThis; }
  if (typeof self !== "undefined") { return self; }
  if (typeof window !== "undefined") { return window; }
  return {};
}

// Microsoft's library, started from its string the first time it is needed.
function msalLibrary() {
  var root = globalRoot();
  if (!root.msal) {
    try { (new Function(MSAL_SOURCE)).call(root); }
    catch (e) { signInDetail = "library " + ((e && e.name) || "failed"); return null; }
  }
  return root.msal || null;
}

function storageKind() {
  try {
    localStorage.setItem("gudanesProbe", "1");
    localStorage.removeItem("gudanesProbe");
    return "localStorage";
  } catch (e) { return "memoryStorage"; }
}

// A token from Microsoft 365 for this app, without a window: Outlook already
// knows who is signed in, and nested app authentication asks it. Anything
// that would need the person to answer something fails here instead, and
// the panel -- which can show a window -- is where that gets answered.
function microsoftToken(done) {
  try {
    if (!msalApp) {
      var lib = msalLibrary();
      if (!lib || !lib.createNestablePublicClientApplication) {
        done(new Error(signInDetail || "no sign-in library"));
        return;
      }
      msalApp = lib.createNestablePublicClientApplication({
        auth: { clientId: CONFIG.sso_client_id,
                authority: "https://login.microsoftonline.com/" + CONFIG.sso_tenant },
        cache: { cacheLocation: storageKind() }
      });
    }
    var started = msalApp;
    started.then(function (app) {
      return app.acquireTokenSilent({ scopes: [CONFIG.sso_scope] });
    }, function (e) {
      msalApp = null;
      throw e;
    }).then(function (result) {
      done(null, result.accessToken);
    }, function (e) {
      signInDetail = String((e && (e.errorCode || e.name)) || "refused");
      done(e || new Error("refused"));
    });
  } catch (e) {
    signInDetail = String((e && e.name) || "failed");
    done(e);
  }
}

// Microsoft's token exchanged for the add-in's own key, kept in roaming
// settings for every later email. done(error, who).
//
// purpose=check: somebody linked to a person in the dashboard gets their own
// key as before; an account of the house's nobody has linked yet gets a key
// for the check on Send and nothing else. The panel never asks this way.
function signIn(budgetMs, done) {
  var settled = false;
  var started = now();
  function finish(error, who) {
    if (settled) { return; }
    settled = true;
    done(error, who);
  }
  var timer = setTimeout(function () { finish(new Error("sign-in took too long")); }, Math.max(1, budgetMs));
  microsoftToken(function (error, token) {
    if (settled) { return; }
    if (error || !token) { clearTimeout(timer); finish(error || new Error("no token")); return; }
    post(CONFIG.session_url, { platform: platform(), purpose: "check" }, { bearer: token },
         Math.max(1, budgetMs - (now() - started)), function (err, status, data) {
      clearTimeout(timer);
      if (!err && status === 200 && data && data.session) {
        var checkOnly = data.level === "check";
        keepKey(data.session, data.expires_at, checkOnly, null);
        finish(null, { session: data.session, expires: Date.parse(data.expires_at || "") || 0,
                       checkOnly: checkOnly });
        return;
      }
      var refused = new Error("sign-in answered " + status);
      if (!err) { signInDetail = "the app answered " + status; }
      // Microsoft said who it is, and the app will not take them: not one of
      // the house's own accounts. Signing in again changes nothing.
      if (!err && status === 403) { refused.notLinked = true; }
      finish(err || refused);
    });
  });
}

// Somebody to ask as, inside budgetMs. done(who, failure code).
function withIdentity(budgetMs, done) {
  var held = heldKey();
  if (held) { done(held, ""); return; }
  if (!canSignIn()) { done(null, "no_identity"); return; }
  if (budgetMs < 600) { done(null, "no_identity"); return; }
  signIn(budgetMs, function (error, who) {
    if (who) { done(who, ""); return; }
    if (error && error.notLinked) { done(null, "not_linked"); return; }
    done(null, "sign_in_failed");
  });
}


// ---- reading the email -----------------------------------------------------

// To, Cc, Bcc, the subject and the text, all asked for at once. A part
// Outlook would not give is null.
function readItem(item, done) {
  var parts = {};
  var waiting = 1;
  function settle() {
    waiting -= 1;
    if (waiting === 0) { done(parts); }
  }
  function ask(name, getter) {
    waiting += 1;
    try {
      getter(function (result) {
        if (result && result.status === Office.AsyncResultStatus.Succeeded) { parts[name] = result.value; }
        else { parts[name] = null; }
        settle();
      });
    } catch (e) {
      parts[name] = null;
      settle();
    }
  }
  ask("to", function (cb) { item.to.getAsync(cb); });
  ask("cc", function (cb) { item.cc.getAsync(cb); });
  if (item.bcc) { ask("bcc", function (cb) { item.bcc.getAsync(cb); }); }
  ask("subject", function (cb) { item.subject.getAsync(cb); });
  ask("body", function (cb) { item.body.getAsync("text", cb); });
  settle();
}

// Every address the email is going to that is not the house's own.
function outsiders(parts) {
  var out = [];
  var lists = [parts.to, parts.cc, parts.bcc];
  for (var i = 0; i < lists.length; i++) {
    for (var j = 0; j < (lists[i] || []).length; j++) {
      var a = lists[i][j] && lists[i][j].emailAddress;
      if (a && !isHouse(a)) { out.push(String(a).toLowerCase()); }
    }
  }
  return out;
}

// Whether Outlook refused any of the address lines: then "nobody outside"
// is not known, and the email is checked rather than waved through.
function recipientsUnread(parts) {
  return parts.to === null || parts.cc === null || parts.bcc === null;
}


// ---- the check on Send -----------------------------------------------------

function onMessageSendHandler(event) {
  var started = now();
  var finished = false;
  var who = null;
  var timer = null;

  function elapsed() { return now() - started; }
  function left() { return SEND_BUDGET_MS - elapsed(); }

  function finish(options) {
    if (finished) { return; }
    finished = true;
    if (timer) { clearTimeout(timer); }
    complete(event, options);
  }

  // Could not check: say so, say why, and leave the person the choice.
  function cannotCheck(code, detail, report) {
    if (finished) { return; }
    if (report !== false) { reportFailure(code, detail || signInDetail, elapsed(), who); }
    var signingIn = code === "no_identity" || code === "signed_out" || code === "sign_in_failed";
    var options = { allowEvent: false };
    if (signingIn && supports("Mailbox", "1.14")) {
      options.errorMessage = clip("Gudanes could not check this email: " + words(code) + ". " +
        "Choose Sign in to open the Gudanes panel, which signs you in once for every email after. " +
        "Or check its prices and dates yourself and choose Send Anyway.");
      options.cancelLabel = "Sign in";
      options.commandId = "msgComposeOpenPaneButton";
    } else if (signingIn) {
      options.errorMessage = clip("Gudanes could not check this email: " + words(code) + ". " +
        "Open the Gudanes panel once to sign in. For this email, check its prices and dates " +
        "yourself and choose Send Anyway.");
    } else {
      options.errorMessage = clip("Gudanes could not check this email against the booking system: " +
        words(code) + ". Check its prices and dates yourself, then choose Send Anyway.");
    }
    finish(promptUser(options));
  }

  timer = setTimeout(function () { cannotCheck("timeout", "after " + SEND_BUDGET_MS + "ms"); },
                     SEND_BUDGET_MS);

  function check(identity, outside, parts, retried) {
    var params = { recipients: outside.join(","), subject: parts.subject || "",
                   body: String(parts.body || "").slice(0, BODY_LIMIT), source: "send",
                   mailbox: profileAddress(), platform: platform(), waited_ms: String(elapsed()) };
    post(CONFIG.check_url, params, identity, left() - 100, function (error, status, data) {
      if (finished) { return; }
      if (error) {
        if (error.timedOut) { cannotCheck("timeout", "the app took longer than the time left"); }
        else { cannotCheck("unreachable", String((error && error.name) || "network")); }
        return;
      }
      if (status === 200 && data) {
        if (data.conflict) {
          finish(promptUser({ allowEvent: false,
                   errorMessage: clip((data.note || "This email may not match the booking system.") +
                                      " Don't Send to correct it, or Send Anyway if it is right.") }));
        } else {
          finish({ allowEvent: true });
        }
        return;
      }
      var tell = !(data && data.recorded);
      if (status === 404 && identity.session && !retried) {
        // Signed out on the add-in page, or run out: forgotten, and signed
        // in afresh once if there is time.
        forgetKey(identity);
        if (canSignIn() && left() > 1200) {
          signIn(left() - 400, function (err, fresh) {
            if (finished) { return; }
            if (!fresh) { cannotCheck("signed_out"); return; }
            who = fresh;
            check(fresh, outside, parts, true);
          });
          return;
        }
        cannotCheck("signed_out");
        return;
      }
      if (status === 404) { cannotCheck("signed_out"); return; }
      if (status === 429) { cannotCheck("rate_limited", "", tell); return; }
      cannotCheck("server_error", "status " + status, tell);
    });
  }

  try {
    var item = Office.context.mailbox.item;
    readItem(item, function (parts) {
      if (finished) { return; }
      var outside = outsiders(parts);
      // Only the house: nothing a guest will read, nothing to check.
      if (!outside.length && !recipientsUnread(parts)) { finish({ allowEvent: true }); return; }
      if (parts.subject === null && parts.body === null) { cannotCheck("unreadable"); return; }
      withIdentity(left() - 700, function (identity, failure) {
        if (finished) { return; }
        if (!identity) { cannotCheck(failure); return; }
        who = identity;
        check(identity, outside, parts, false);
      });
    });
  } catch (e) {
    cannotCheck("script_error", String((e && e.name) || "error"));
  }
}


// ---- while writing ---------------------------------------------------------

// Shown at the top of the message the moment a guest is in To: what the
// person writing must not miss.
function showNote(item, text, done) {
  if (!text) {
    try { item.notificationMessages.removeAsync(NOTE_KEY, function () { done(); }); }
    catch (e) { done(); }
    return;
  }
  var message = { type: Office.MailboxEnums.ItemNotificationMessageType.InformationalMessage,
                  message: text, icon: "Icon.16x16", persistent: false };
  try {
    item.notificationMessages.replaceAsync(NOTE_KEY, message, function (result) {
      if (result.status === Office.AsyncResultStatus.Succeeded) { done(); return; }
      // Outlook on a phone has no icon list to name one from.
      message.icon = "none";
      item.notificationMessages.replaceAsync(NOTE_KEY, message, function () { done(); });
    });
  } catch (e) { done(); }
}

// A key that will still be good on Send: signed in now if there is none, or
// renewed if it has three days or less left. done(who), which may be null.
function readyKey(budgetMs, done) {
  var held = heldKey();
  var fresh = held && held.session && held.expires - now() > RENEW_MS;
  if (fresh || !canSignIn()) { done(held); return; }
  signIn(budgetMs, function (error, who) { done(who || held); });
}

function whileWriting(event) {
  var finished = false;
  var timer = null;
  function finish() {
    if (finished) { return; }
    finished = true;
    if (timer) { clearTimeout(timer); }
    try { event.completed(); } catch (e) { /* nothing more to do */ }
  }
  timer = setTimeout(finish, COMPOSE_BUDGET_MS);
  try {
    var item = Office.context.mailbox.item;
    readyKey(COMPOSE_BUDGET_MS - 3000, function (who) {
      if (finished) { return; }
      if (!who) { finish(); return; }
      readItem(item, function (parts) {
        if (finished) { return; }
        var outside = outsiders(parts);
        if (!outside.length) { showNote(item, "", finish); return; }
        post(CONFIG.note_url, { recipients: outside.join(",") }, who, 6000, function (error, status, data) {
          if (finished) { return; }
          if (!error && status === 404 && who.session) { forgetKey(who); }
          if (!error && status === 200 && data) { showNote(item, data.note, finish); return; }
          finish();
        });
      });
    });
  } catch (e) {
    finish();
  }
}

function onNewMessageComposeHandler(event) { whileWriting(event); }
function onMessageRecipientsChangedHandler(event) { whileWriting(event); }

// Required so Outlook can find each handler by the name the manifest gives.
// See "Activate add-ins with events" in Microsoft's documentation.
Office.actions.associate("onMessageSendHandler", onMessageSendHandler);
Office.actions.associate("onNewMessageComposeHandler", onNewMessageComposeHandler);
Office.actions.associate("onMessageRecipientsChangedHandler", onMessageRecipientsChangedHandler);
