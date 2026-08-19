// The château's internet is unreliable, and this app is installed to a phone
// home screen in standalone mode — no address bar, no back button. A failed
// navigation there is not a browser error page somebody can retry from; it is
// a dead screen with no way out. This exists mostly to make sure that never
// happens.
//
// What is deliberately NOT cached: pages and data. Tasks, who is on shift and
// guest details change constantly and are used by several people at once, so
// serving a stale copy would have somebody act on yesterday's information —
// worse than telling them plainly they are offline. Only the shell is cached:
// the stylesheets, the icons, and the offline page itself.
//
// Bump CACHE when the precached list changes; activate deletes every other
// cache, so an old shell cannot linger after a deploy.
var CACHE = 'gudanes-shell-v1';
var SHELL = [
  '/static/offline.html',
  '/static/style.css',
  '/static/gudanes.css',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

self.addEventListener('install', function(event) {
  event.waitUntil(
    // Added one at a time rather than with addAll, which fails the whole
    // install if any single file 404s — that would leave no offline page at
    // all. A missing icon should cost an icon, not the entire safety net.
    caches.open(CACHE).then(function(cache) {
      return Promise.all(SHELL.map(function(url) {
        return cache.add(url).catch(function() {});
      }));
    }).then(function() { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(names.map(function(name) {
        return name === CACHE ? null : caches.delete(name);
      }));
    }).then(function() { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function(event) {
  var request = event.request;

  // Anything that changes something — clocking in, completing a task, posting
  // to chat — must never be served from a cache or quietly swallowed. Let it
  // fail honestly, so the page can say so.
  if (request.method !== 'GET') return;

  // A page somebody is navigating to. Network first always, because the data
  // has to be current; the offline page appears only when the network really
  // is not there.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(function() {
        return caches.match('/static/offline.html').then(function(page) {
          return page || new Response(
            'You are offline.',
            {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}});
        });
      })
    );
    return;
  }

  // The shell. Network first so a deployed change is picked up straight away,
  // falling back to the cached copy — this is what stops the offline page
  // rendering unstyled, which reads as "the app is broken" rather than "the
  // connection is".
  var url = new URL(request.url);
  if (url.origin === self.location.origin && url.pathname.indexOf('/static/') === 0) {
    event.respondWith(
      fetch(request).then(function(response) {
        if (response && response.ok) {
          var copy = response.clone();
          caches.open(CACHE).then(function(cache) { cache.put(request, copy); });
        }
        return response;
      }).catch(function() {
        return caches.match(request);
      })
    );
  }
});

self.addEventListener('push', function(event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  var title = data.title || 'Château de Gudanes';
  var options = {
    body: data.body || '',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    data: { link: data.link || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var link = (event.notification.data && event.notification.data.link) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clients) {
      for (var i = 0; i < clients.length; i++) {
        if (clients[i].url.indexOf(link) !== -1 && 'focus' in clients[i]) return clients[i].focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(link);
    })
  );
});
