// WHAT THE PHONE DID WHILE IT HAD NO SIGNAL.
//
// The château is a valley behind a metre of stone and the signal dies room by
// room. A housekeeper on the third floor taps a task off, the POST never
// leaves the handset, and the page says "check your connection and try again"
// — which is true and useless. They are standing in the room, the work is
// done, and there is no connection to check.
//
// So the tap is kept here and sent when the signal comes back. Three rules
// carry the whole thing:
//
//   ONE LIST, ON THE SERVER. Which actions may be held is decided in
//   OFFLINE_ACTIONS in app.py and rendered into the page. A copy of that list
//   here would be a second list, and two lists that have to agree are two
//   lists that stop agreeing.
//
//   EVERY HELD ACTION CARRIES A KEY. A queue retries by definition — the
//   phone cannot tell "the server never heard me" from "it heard me and the
//   reply was lost". The key is what makes the second one harmless: the
//   server files it the first time and hands back the same answer after.
//
//   IN ORDER, AND STOPPING ON THE FIRST FAILURE. Start-break then end-break
//   replayed the other way round is a break that never ended. If one will not
//   send, the ones behind it wait rather than jumping the queue.

(function () {
  'use strict';

  var KEY = 'gudanes.pending.v1';
  var el = null;      // the banner, built on first use
  var sending = false;

  function held() {
    try {
      return JSON.parse(window.localStorage.getItem(KEY) || '[]');
    } catch (e) {
      return [];
    }
  }

  function keep(list) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(list));
    } catch (e) {
      // A full or blocked store. Nothing to do but let the caller fail
      // honestly, which is what happens if this throws.
    }
  }

  // Random enough that two handsets cannot collide, and generated when the
  // person TAPS rather than when the send is attempted — the whole point is
  // that every attempt at one tap carries the same key.
  function newKey() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return 'k' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
  }

  function allowed() {
    var tag = document.getElementById('offline-actions');
    if (!tag) return {};
    try {
      return JSON.parse(tag.textContent || '{}');
    } catch (e) {
      return {};
    }
  }

  function banner() {
    if (el) return el;
    el = document.createElement('div');
    el.className = 'pending-bar';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.hidden = true;
    document.body.appendChild(el);
    return el;
  }

  // SAYS WHAT IS WAITING, not how many. "3 things waiting" is a number nobody
  // can act on; the list is what lets somebody see their own tap is in there
  // and stop wondering whether it took.
  function draw() {
    var list = held();
    var bar = banner();
    if (!list.length) {
      bar.hidden = true;
      bar.textContent = '';
      return;
    }
    var names = list.map(function (a) { return a.label; });
    var failed = list.filter(function (a) { return a.error; });
    bar.hidden = false;
    bar.textContent = '';
    var line = document.createElement('p');
    line.className = 'pending-bar__l';
    line.textContent = navigator.onLine
      ? 'Sending: ' + names.join(', ')
      : 'Kept until you have signal: ' + names.join(', ');
    bar.appendChild(line);
    if (failed.length) {
      var why = document.createElement('p');
      why.className = 'pending-bar__e';
      // NAMED, not counted. A queue that says "1 failed" is a queue somebody
      // clears without knowing what they threw away.
      why.textContent = failed[0].label + ' would not send: ' + failed[0].error
        + '. It is still here — tell whoever runs the house.';
      bar.appendChild(why);
    }
  }

  function send() {
    if (sending) return Promise.resolve();
    var list = held();
    if (!list.length || !navigator.onLine) { draw(); return Promise.resolve(); }
    sending = true;
    draw();

    function step() {
      var current = held();
      if (!current.length) { return Promise.resolve(); }
      var action = current[0];
      // The one at the front, on its own. Anything behind it waits: replaying
      // an end-break before its start-break is a break that never ended.
      return fetch(action.url, {
        method: 'POST',
        headers: {
          'X-Action-Key': action.key,
          'X-Action-Taken-At': action.at,
          'X-Requested-With': 'fetch'
        },
        credentials: 'same-origin'
      }).then(function (r) {
        if (r.status >= 500) throw new Error('the house could not be reached');
        if (!r.ok) {
          // A refusal is an ANSWER, not a network problem: the task was
          // deleted, or somebody else had already done it. Retrying for ever
          // would never clear, so it comes off the queue and is named.
          var rest = held().slice(1);
          keep(rest);
          action.error = 'it was refused (' + r.status + ')';
          rest.unshift(action);
          keep(rest);
          return Promise.resolve();
        }
        keep(held().slice(1));
        return step();
      }).catch(function () {
        // Still no signal. Left exactly where it is, in order, for next time.
        return Promise.resolve();
      });
    }

    return step().then(function () {
      sending = false;
      draw();
    }, function () {
      sending = false;
      draw();
    });
  }

  // The one call a page makes. Behaves like fetch on a good connection and
  // like a notebook on a bad one.
  //
  // Resolves with {held: true} rather than rejecting, because the action HAS
  // been taken — the person tapped, the work is done, and the only thing
  // outstanding is telling the house. The banner is what keeps that honest.
  function take(url, label) {
    var key = newKey();
    var at = new Date().toISOString();
    return fetch(url, {
      method: 'POST',
      headers: {
        'X-Action-Key': key,
        'X-Action-Taken-At': at,
        'X-Requested-With': 'fetch'
      },
      credentials: 'same-origin'
    }).then(function (r) {
      if (r.status >= 500) throw new Error('server');
      return r.json().then(function (data) {
        return { ok: r.ok, status: r.status, data: data, held: false };
      }, function () {
        return { ok: r.ok, status: r.status, data: {}, held: false };
      });
    }).catch(function () {
      var list = held();
      list.push({ key: key, url: url, label: label || 'Something you did', at: at });
      keep(list);
      draw();
      return { ok: true, status: 0, data: {}, held: true };
    });
  }

  window.gudanes = window.gudanes || {};
  window.gudanes.take = take;
  window.gudanes.pending = held;
  window.gudanes.flush = send;
  window.gudanes.allowed = allowed;

  window.addEventListener('online', send);
  document.addEventListener('DOMContentLoaded', function () { draw(); send(); });
  // A phone that was asleep in a pocket while the signal came back gets no
  // online event, so coming back to the app tries again.
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) send();
  });
}());
