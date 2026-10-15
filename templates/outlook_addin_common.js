/*
 * The Gudanes add-in: who is asking, and the calls to the app. Shared by the
 * read panel and the compose panel so the two cannot disagree about either.
 *
 * SIGNING IN. The panel asks Microsoft 365 for a token for this app through
 * nested app authentication (MSAL.js, loaded pinned and fingerprinted by the
 * page). The app checks it and answers with a key of the add-in's own, for
 * this person on this device, kept in their Outlook's roaming settings so the
 * send check -- which cannot sign anybody in -- has it too. The key lasts a
 * month and is renewed silently whenever a panel opens with three days or
 * less left.
 *
 * If this Outlook cannot sign in that way (an old build, or sign-in not set
 * up yet), the panel asks for the house's shared key instead, which the app
 * treats as staff and which the owner can switch off.
 *
 * Nothing the app sends back is ever put into the page as HTML: subjects and
 * previews of emails are other people's words, so everything goes in as text.
 */
var GUDANES = {{ config|tojson }};

var Gudanes = (function () {
  var SESSION_KEY = "gudanesSession";
  var EXPIRES_KEY = "gudanesSessionExpires";
  var TOKEN_KEY = "gudanesToken";
  var RENEW_MS = 3 * 24 * 3600 * 1000;
  var msalApp = null;
  var problem = "";

  function roaming() { return Office.context.roamingSettings; }

  function saveRoaming() {
    return new Promise(function (done) { roaming().saveAsync(function () { done(); }); });
  }

  function platform() {
    try {
      return String(Office.context.platform || (Office.context.diagnostics || {}).platform || "");
    } catch (e) { return ""; }
  }

  function onPhone() {
    var p = platform().toLowerCase();
    return p.indexOf("ios") >= 0 || p.indexOf("android") >= 0;
  }

  function canSignInHere() {
    try {
      return !!(GUDANES.sso_client_id && window.msal &&
                Office.context.requirements.isSetSupported("NestedAppAuth", "1.1"));
    } catch (e) { return false; }
  }

  async function microsoftToken() {
    if (!msalApp) {
      msalApp = await msal.createNestablePublicClientApplication({
        auth: {
          clientId: GUDANES.sso_client_id,
          authority: "https://login.microsoftonline.com/" + GUDANES.sso_tenant
        },
        cache: { cacheLocation: "localStorage" }
      });
    }
    var request = { scopes: [GUDANES.sso_scope] };
    try {
      return (await msalApp.acquireTokenSilent(request)).accessToken;
    } catch (silent) {
      // Consent not given yet, or Microsoft wants the person to sign in again:
      // the one case where a window is worth putting in front of them.
      if (silent instanceof msal.InteractionRequiredAuthError) {
        return (await msalApp.acquireTokenPopup(request)).accessToken;
      }
      throw silent;
    }
  }

  function held() {
    return {
      session: roaming().get(SESSION_KEY) || "",
      expires: roaming().get(EXPIRES_KEY) || "",
      token: roaming().get(TOKEN_KEY) || ""
    };
  }

  // Who this panel is asking as: {session} for a signed-in person, {token}
  // for the shared key, or null with `problem` saying why.
  async function signIn(force) {
    var h = held();
    var expires = Date.parse(h.expires || "") || 0;
    if (h.session && !force && expires - Date.now() > RENEW_MS) {
      return { session: h.session };
    }
    if (canSignInHere()) {
      try {
        var token = await microsoftToken();
        var r = await fetch(GUDANES.session_url, {
          method: "POST",
          headers: { Authorization: "Bearer " + token },
          body: new URLSearchParams({ platform: platform() })
        });
        var data = await r.json().catch(function () { return {}; });
        if (r.ok && data.session) {
          roaming().set(SESSION_KEY, data.session);
          roaming().set(EXPIRES_KEY, data.expires_at);
          await saveRoaming();
          problem = "";
          return { session: data.session, name: data.name, level: data.level };
        }
        problem = data.error || ("Signing in was refused (" + r.status + ").");
      } catch (e) {
        problem = "Microsoft 365 sign-in did not work in this Outlook: " +
                  ((e && (e.errorMessage || e.message)) || String(e));
      }
    } else if (GUDANES.sso_client_id) {
      problem = "This Outlook is too old to sign in to the add-in by itself.";
    } else {
      problem = "Signing in with Microsoft 365 is not switched on for the add-in yet.";
    }
    if (h.session && !force && expires > Date.now()) { return { session: h.session }; }
    if (h.token) { return { token: h.token }; }
    return null;
  }

  async function forgetSession() {
    roaming().remove(SESSION_KEY);
    roaming().remove(EXPIRES_KEY);
    await saveRoaming();
  }

  async function useSharedKey(key) {
    roaming().set(TOKEN_KEY, (key || "").trim());
    await saveRoaming();
  }

  async function forgetSharedKey() {
    roaming().remove(TOKEN_KEY);
    await saveRoaming();
  }

  function fail(message, extra) {
    var e = new Error(message);
    for (var k in (extra || {})) { e[k] = extra[k]; }
    return e;
  }

  // POST to the app as whoever this panel is. A 404 on a person's key means
  // it was signed out or has run out: forgotten, and signed in afresh once.
  async function call(url, params, retried) {
    var who = await signIn(false);
    if (!who) { throw fail(problem || "Not signed in.", { needsSetup: true }); }
    var body = new URLSearchParams(params || {});
    var headers = {};
    if (who.session) { headers.Authorization = "Bearer " + who.session; }
    else { body.set("token", who.token); }
    var r = await fetch(url, { method: "POST", headers: headers, body: body });
    if (r.status === 404 && who.session && !retried) {
      await forgetSession();
      return call(url, params, true);
    }
    if (r.status === 404) {
      throw fail(who.token ? "The shared key was not accepted — it may have been changed, " +
                             "or switched off now that people sign in with Microsoft 365."
                           : (problem || "Not signed in."), { needsSetup: true });
    }
    var data = await r.json().catch(function () { return {}; });
    if (!r.ok) { throw fail(data.error || ("The app answered " + r.status + "."), { status: r.status, data: data }); }
    return data;
  }

  // ---- reading the open item ----------------------------------------------

  function asyncValue(getter) {
    return new Promise(function (done) {
      try {
        getter(function (res) {
          done(res && res.status === Office.AsyncResultStatus.Succeeded ? res.value : null);
        });
      } catch (e) { done(null); }
    });
  }

  function domainOf(address) {
    var at = (address || "").lastIndexOf("@");
    return at < 0 ? "" : address.slice(at + 1).toLowerCase();
  }

  // The house's own domain is the signed-in person's. Anybody else in a
  // message is the guest -- whichever side of it they are on.
  function houseDomain() {
    try { return domainOf(Office.context.mailbox.userProfile.emailAddress); } catch (e) { return ""; }
  }

  function firstOutsider(list) {
    var house = houseDomain();
    for (var i = 0; i < (list || []).length; i++) {
      var a = list[i] && (list[i].emailAddress || "");
      if (a && domainOf(a) !== house) { return { email: a.toLowerCase(), name: list[i].displayName || "" }; }
    }
    return null;
  }

  function firstInsider(list) {
    var house = houseDomain();
    for (var i = 0; i < (list || []).length; i++) {
      var a = list[i] && (list[i].emailAddress || "");
      if (a && domainOf(a) === house) { return a.toLowerCase(); }
    }
    return "";
  }

  // In read mode: the guest, the inbox it reached, the subject and the thread.
  function readContext() {
    var item = Office.context.mailbox.item;
    var from = item.from || item.sender || {};
    var recipients = [].concat(item.to || [], item.cc || []);
    var guest = firstOutsider([from]) || firstOutsider(recipients);
    return {
      guest: guest, subject: item.subject || "", conversationId: item.conversationId || "",
      mailbox: firstInsider(recipients) || firstInsider([from]) ||
               ((Office.context.mailbox.userProfile || {}).emailAddress || "")
    };
  }

  async function readBodyText() {
    return (await asyncValue(function (cb) { Office.context.mailbox.item.body.getAsync("text", cb); })) || "";
  }

  // In compose mode, everything is asked for and answered later.
  async function composeContext() {
    var item = Office.context.mailbox.item;
    var to = (await asyncValue(function (cb) { item.to.getAsync(cb); })) || [];
    var cc = (await asyncValue(function (cb) { item.cc.getAsync(cb); })) || [];
    var subject = (await asyncValue(function (cb) { item.subject.getAsync(cb); })) || "";
    var body = (await asyncValue(function (cb) { item.body.getAsync("text", cb); })) || "";
    var from = null;
    if (item.from && item.from.getAsync) {
      from = await asyncValue(function (cb) { item.from.getAsync(cb); });
    }
    return {
      guest: firstOutsider(to) || firstOutsider(cc), subject: subject, body: body,
      conversationId: item.conversationId || "",
      mailbox: ((from && from.emailAddress) ||
                (Office.context.mailbox.userProfile || {}).emailAddress || "").toLowerCase()
    };
  }

  // ---- drawing -------------------------------------------------------------

  // A DOM element from a tag, attributes and children. Strings become text
  // nodes, never markup.
  function h(tag, attrs) {
    var node = document.createElement(tag);
    for (var k in (attrs || {})) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) { continue; }
      if (k === "class") { node.className = v; }
      else if (k.slice(0, 2) === "on") { node.addEventListener(k.slice(2), v); }
      else { node.setAttribute(k, v === true ? "" : v); }
    }
    for (var i = 2; i < arguments.length; i++) { append(node, arguments[i]); }
    return node;
  }

  function append(node, child) {
    if (child === null || child === undefined || child === false) { return; }
    if (Array.isArray(child)) { child.forEach(function (c) { append(node, c); }); return; }
    node.appendChild(typeof child === "object" ? child : document.createTextNode(String(child)));
  }

  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } return node; }

  function day(iso) {
    if (!iso) { return ""; }
    var d = new Date(iso.length <= 10 ? iso + "T12:00:00" : iso);
    if (isNaN(d)) { return iso; }
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }

  function euros(n) {
    if (n === null || n === undefined || n === "") { return ""; }
    return Number(n).toLocaleString("en-GB", { style: "currency", currency: "EUR" });
  }

  function copy(text, fallbackTextarea) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () {
        return legacyCopy(fallbackTextarea);
      });
    }
    return Promise.resolve(legacyCopy(fallbackTextarea));
  }

  function legacyCopy(textarea) {
    if (!textarea) { return false; }
    textarea.focus();
    textarea.select();
    try { return document.execCommand("copy"); } catch (e) { return false; }
  }

  function openLink(url) {
    if (!url) { return; }
    try { Office.context.ui.openBrowserWindow(url); }
    catch (e) { window.open(url, "_blank", "noopener"); }
  }

  return {
    signIn: signIn, call: call, forgetSession: forgetSession, useSharedKey: useSharedKey,
    forgetSharedKey: forgetSharedKey, problem: function () { return problem; },
    onPhone: onPhone, readContext: readContext, readBodyText: readBodyText,
    composeContext: composeContext, h: h, clear: clear, day: day, euros: euros,
    copy: copy, openLink: openLink, held: held, canSignInHere: canSignInHere
  };
})();
