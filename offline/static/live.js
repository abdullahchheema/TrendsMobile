// Keeps every screen current without a manual refresh. Every few seconds it asks the server for a fingerprint of the data and the
// order badge counts; when the fingerprint changes the page is reloaded in place (same scroll position), unless the person is in the
// middle of typing or the screen is the POS or a data-entry form.
(function () {
  var INTERVAL = 8000, last = null, busy = false, typing = false;
  var SKIP = /^\/(pos|login|logout|entry|buy|accessories\/buy|exchange)(\/|$)/;
  document.addEventListener('input', function () { typing = true; }, true);
  document.addEventListener('turbo:load', function () { typing = false; });
  function setBadges(data) {
    [['online', data.online], ['shop', data.shop]].forEach(function (pair) {
      if (pair[1] === undefined) return;
      document.querySelectorAll('[data-live-badge="' + pair[0] + '"]').forEach(function (el) {
        var was = el.textContent.trim();
        el.textContent = pair[1] || '';
        el.hidden = !pair[1];
        if (String(pair[1]) !== was && pair[1]) { el.classList.add('bump'); setTimeout(function () { el.classList.remove('bump'); }, 400); }
      });
    });
  }
  function safeToReload() {
    if (typing || SKIP.test(location.pathname)) return false;
    var a = document.activeElement;
    if (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return false;
    return true;
  }
  function poll() {
    if (document.hidden || busy) return;
    busy = true;
    fetch('/online/live/', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        setBadges(data);
        if (last !== null && data.token !== last && safeToReload() && window.Turbo) window.Turbo.visit(location.href, { action: 'replace' });
        last = data.token;
      })
      .catch(function () { /* offline or the server is restarting: try again on the next tick */ })
      .then(function () { busy = false; });
  }
  setInterval(poll, INTERVAL);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
  poll();
})();
