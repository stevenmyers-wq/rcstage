/* Live customer-bridge status heartbeat.
 *
 * Polls /api/sm_auth/status and drives the outer #bridge-status-bar so the
 * bridged customer — and, crucially, an EXPIRED bridge — is always visible
 * rather than surfacing only as a failed action inside a tool. The server tries
 * a silent re-mint before reporting 'expired', so this only nags the user when
 * the bridge genuinely cannot be recovered without re-auth.
 *
 * Per product decision: on expiry we show a red banner + Reconnect button. We
 * do NOT auto-reload the page (that could interrupt in-flight work).
 */
(function () {
  const POLL_MS = 60000;

  const bar = document.getElementById('bridge-status-bar');
  const dot = document.getElementById('bridge-dot');
  const label = document.getElementById('bridge-label');
  const reconnect = document.getElementById('bridge-reconnect');
  const exit = document.getElementById('bridge-exit');
  if (!bar) return;

  let lastState = null;
  let lastReload = 0;

  function activeTab() {
    try { return (window.__tabShell && window.__tabShell.getActiveTabId()) || ''; }
    catch (_) { return ''; }
  }

  function show(el, on) { if (el) el.classList.toggle('hidden', !on); }

  function apply(data) {
    const state = (data && data.state) || 'none';
    const tab = activeTab();

    if (state === 'none') {
      show(bar, false);
    } else if (state === 'expired') {
      show(bar, true);
      bar.classList.add('flex');
      dot.className = 'w-3 h-3 rounded-full bg-rose-500 flex-shrink-0 animate-pulse';
      label.textContent = 'Bridge expired — reconnect to continue';
      label.className = 'font-bold text-rose-600 dark:text-rose-400 truncate';
      if (reconnect) reconnect.href = '/api/sm_auth/login?tab=' + encodeURIComponent(tab);
      show(reconnect, true);
      show(exit, false);
    } else { // bridged
      show(bar, true);
      bar.classList.add('flex');
      dot.className = 'w-3 h-3 rounded-full bg-emerald-500 flex-shrink-0';
      const name = (data && data.target_name) || 'Customer';
      const id = (data && data.target_id) || '';
      label.innerHTML = 'Bridged → <span class="text-blue-700 dark:text-blue-300">' +
        escapeHtml(String(name)) + '</span>' +
        (id ? ' <span class="text-xs font-mono font-medium opacity-60 ml-1">(' + escapeHtml(String(id)) + ')</span>' : '');
      label.className = 'font-bold text-slate-800 dark:text-slate-100 truncate';
      if (exit) exit.href = '/api/sm_auth/logout?tab=' + encodeURIComponent(tab);
      show(reconnect, false);
      show(exit, true);
    }

    // When the bridge (re)connects after being absent/expired, refresh open
    // tool iframes so any gate screens fall away and tools see the new token.
    // Skip if a bridge-changed message already triggered a reload moments ago.
    if (state === 'bridged' && lastState && lastState !== 'bridged'
        && (Date.now() - lastReload) > 3000) {
      reloadTabs();
    }
    lastState = state;
  }

  function reloadTabs() {
    lastReload = Date.now();
    if (window.__tabShell && window.__tabShell.reloadAll) window.__tabShell.reloadAll();
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  async function poll() {
    try {
      const res = await fetch('/api/sm_auth/status', { headers: { 'Accept': 'application/json' } });
      if (res.ok) apply(await res.json());
    } catch (_) { /* best-effort; keep last shown state */ }
  }

  // A tool iframe reports it just (re)bridged to a customer. Reload open tabs
  // onto the new account and refresh the pill immediately, rather than waiting
  // for the next poll — this is what makes the pill reappear after an
  // exit-bridge / re-bridge cycle done inside a tab.
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin) return;
    if (!e.data || e.data.type !== 'rcau-bridge-changed') return;
    reloadTabs();
    poll();
  });

  poll();
  setInterval(poll, POLL_MS);
  window.addEventListener('focus', poll);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
})();
