/* Tabs on a staff page (27 Nov 2026: the stay occasions, and one occasion's
   "The stay" and "Chef & dinner").

   Markup: a [data-tabs] bar of links, each [data-tab="key"] with href="#hash";
   any number of elements carry [data-tab-panel="key"] (a tab can span two
   forms), the first of them id="hash". Without script every panel shows and
   the links jump to them.

   - The tab chosen is the address's hash, so it survives a reload, and a save:
     each form on the page is sent with the hash on its address, and a browser
     carries that fragment through the redirect that follows a save.
   - The page opens at the TOP, whatever tab its address names. A hash that
     names a panel is one the browser scrolls to as the page loads, which put
     the tabs under the header and the "Saved" or the refusal above them out of
     sight, on every visit (27 Nov 2026). So the hash is written in only once
     the page has loaded (writing it then scrolls nothing), and a page that
     arrives with a tab's hash goes back to the top.
   - A hash naming something INSIDE a panel (#dinner, #description, #retreat,
     as older links and redirects say) opens that panel and goes to it.
   - The server can open a tab itself (data-open, with data-why to say why):
     a save refused for something on the other tab opens that tab and says so.
   - A box the browser refuses (required, empty) on a hidden tab opens that tab
     first; otherwise the browser cannot show it and the Save button seems to
     do nothing at all. */
(function () {
  var loaded = document.readyState === 'complete', pending = null, toTop = false;
  function writeHash(hash) {
    if (!history.replaceState) return;
    if (!loaded) { pending = hash; return; }
    history.replaceState(null, '', location.pathname + location.search + '#' + hash);
  }
  window.addEventListener('load', function () {
    loaded = true;
    if (pending !== null) writeHash(pending);
    /* After the browser's own scroll to the hash, which happens as the page finishes loading. */
    if (toTop) setTimeout(function () { window.scrollTo(0, 0); }, 0);
  });

  document.querySelectorAll('[data-tabs]').forEach(function (bar) {
    var tabs = Array.prototype.slice.call(bar.querySelectorAll('[data-tab]'));
    if (!tabs.length) return;
    var keys = tabs.map(function (t) { return t.getAttribute('data-tab'); });
    var note = bar.getAttribute('data-note') ? document.getElementById(bar.getAttribute('data-note')) : null;
    function hashOf(key) { var t = tabs[keys.indexOf(key)]; return t ? t.getAttribute('href').slice(1) : ''; }
    function panels(key) { return Array.prototype.slice.call(document.querySelectorAll('[data-tab-panel="' + key + '"]')); }
    function label(key) { var t = tabs[keys.indexOf(key)]; return t ? (t.getAttribute('data-label') || t.textContent).trim() : ''; }
    function say(words) { if (!note) return; note.textContent = words || ''; note.hidden = !words; }

    function show(key, focus, keepHash) {
      if (keys.indexOf(key) < 0) key = keys[0];
      tabs.forEach(function (t, i) {
        var on = keys[i] === key;
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;
        if (on && focus) t.focus();
      });
      keys.forEach(function (k) { panels(k).forEach(function (p) { p.hidden = k !== key; }); });
      /* The server hears which tab was in front (a hidden tab_shown), to say "opened for you" only when it moves. */
      document.querySelectorAll('[data-tab-shown]').forEach(function (i) { i.value = key; });
      if (!keepHash) writeHash(hashOf(key));
      return key;
    }

    /* Which tab a hash means: a tab's own, or the panel holding the element it names. */
    function resolve(hash) {
      hash = (hash || '').replace(/^#/, '');
      if (!hash) return null;
      for (var i = 0; i < tabs.length; i++) if (tabs[i].getAttribute('href') === '#' + hash) return { key: keys[i] };
      var el = document.getElementById(decodeURIComponent(hash));
      var p = el && el.closest('[data-tab-panel]');
      return p && keys.indexOf(p.getAttribute('data-tab-panel')) >= 0 ? { key: p.getAttribute('data-tab-panel'), el: el } : null;
    }

    tabs.forEach(function (t, i) {
      t.addEventListener('click', function (e) { e.preventDefault(); say(''); show(keys[i]); });
      t.addEventListener('keydown', function (e) {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        e.preventDefault();
        show(keys[(i + (e.key === 'ArrowRight' ? 1 : keys.length - 1)) % keys.length], true);
      });
    });

    var forced = bar.getAttribute('data-open');
    var start = resolve(location.hash);
    if (start && !start.el) {
      /* Arrived with a tab's own hash: the browser has scrolled, or will, to the panel. Back to the top. */
      toTop = true;
      window.scrollTo(0, 0);
    }
    if (forced && keys.indexOf(forced) >= 0) {
      show(forced);
      tabs[keys.indexOf(forced)].classList.add('page-tab--problem');
      say(bar.getAttribute('data-why') || '');
    } else {
      show(start ? start.key : keys[0], false, !!(start && start.el));
      if (start && start.el) start.el.scrollIntoView();
    }
    window.addEventListener('hashchange', function () {
      var to = resolve(location.hash);
      if (!to) return;
      show(to.key, false, !!to.el);
      if (to.el) to.el.scrollIntoView();
    });

    /* A save keeps the tab: the form goes with the hash on its address. */
    document.querySelectorAll('form').forEach(function (form) {
      form.addEventListener('submit', function () {
        var action = form.getAttribute('action') || location.pathname + location.search;
        var hash = location.hash || (pending !== null ? '#' + pending : '');
        if (action.indexOf('#') < 0 && hash) form.setAttribute('action', action + hash);
      });
      /* A box the browser refuses on a hidden tab: open that tab, so it can be shown. */
      form.addEventListener('invalid', function (e) {
        var p = e.target.closest && e.target.closest('[data-tab-panel]');
        if (!p || !p.hidden) return;
        var key = show(p.getAttribute('data-tab-panel'));
        var lab = e.target.labels && e.target.labels[0] ? e.target.labels[0].textContent.trim() : '';
        say((lab ? '“' + lab + '”' : 'A box') + ' on ' + label(key) + ' needs filling in, so it is open.');
      }, true);
    });
  });
})();
