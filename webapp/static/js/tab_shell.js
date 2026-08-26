/* In-page tab shell.
 *
 * Turns the single main content area into a set of tabs, each hosting a tool in
 * its own iframe (/?tab=<id>&embed=1). Iframes stay mounted while you switch
 * tabs, so each tool keeps its state. All tabs share the one bridged customer
 * (the session cookie); per-tab customer context is a later phase.
 *
 * Exposes window.__tabShell for bridge_status.js:
 *   .getActiveTabId()  -> id of the visible tab (or null)
 *   .reloadAll()       -> reload every open tool iframe (used when the bridge
 *                         state flips to 'bridged' so gate screens clear)
 */
(function () {
  const STORAGE_KEY = 'rcau_open_tabs_v1';

  const bar = document.getElementById('tab-bar');
  const panels = document.getElementById('tab-panels');
  const empty = document.getElementById('tab-empty');
  const newBtn = document.getElementById('tab-new-btn');
  if (!bar || !panels) return; // not on the dashboard

  // id -> display name, harvested from the sidebar nav links.
  const toolNames = {};
  document.querySelectorAll('nav a[href*="tab="]').forEach((a) => {
    try {
      const id = new URL(a.href, location.origin).searchParams.get('tab');
      const name = (a.textContent || '').trim();
      if (id && name) toolNames[id] = name;
    } catch (_) {}
  });
  const nameFor = (id) => toolNames[id] || id;

  let tabs = [];       // [{ id }]
  let activeId = null;

  function persist() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ tabs, activeId }));
    } catch (_) {}
  }

  function restore() {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return false;
      const data = JSON.parse(raw);
      if (!data || !Array.isArray(data.tabs) || !data.tabs.length) return false;
      tabs = data.tabs.filter((t) => t && t.id);
      activeId = data.activeId && tabs.some((t) => t.id === data.activeId)
        ? data.activeId : (tabs[0] && tabs[0].id) || null;
      return tabs.length > 0;
    } catch (_) {
      return false;
    }
  }

  function panelId(id) { return 'tab-panel-' + id; }

  function ensureIframe(id) {
    let frame = document.getElementById(panelId(id));
    if (!frame) {
      frame = document.createElement('iframe');
      frame.id = panelId(id);
      frame.src = '/?tab=' + encodeURIComponent(id) + '&embed=1';
      frame.className = 'absolute inset-0 w-full h-full border-0';
      frame.setAttribute('title', nameFor(id));
      panels.appendChild(frame);
    }
    return frame;
  }

  function render() {
    // Tab buttons (rebuilt each time; the "+ New tab" button is left in place).
    bar.querySelectorAll('[data-tab-btn]').forEach((n) => n.remove());
    tabs.forEach((t) => {
      const active = t.id === activeId;
      const btn = document.createElement('div');
      btn.setAttribute('data-tab-btn', t.id);
      btn.className =
        'flex-shrink-0 group flex items-center gap-2 mb-2 pl-4 pr-2 py-2 rounded-lg text-sm font-bold cursor-pointer transition ' +
        (active
          ? 'bg-white dark:bg-slate-700 text-blue-600 dark:text-blue-400 shadow-sm'
          : 'text-slate-500 dark:text-slate-400 hover:bg-white/60 dark:hover:bg-slate-700/50');

      const label = document.createElement('span');
      label.className = 'truncate max-w-[14rem]';
      label.textContent = nameFor(t.id);
      label.addEventListener('click', () => activate(t.id));
      btn.appendChild(label);

      const close = document.createElement('button');
      close.className =
        'flex items-center justify-center w-5 h-5 rounded-md text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-600 hover:text-rose-500 transition';
      close.setAttribute('aria-label', 'Close ' + nameFor(t.id));
      close.textContent = '×';
      close.addEventListener('click', (e) => { e.stopPropagation(); closeTab(t.id); });
      btn.appendChild(close);

      bar.insertBefore(btn, newBtn);
    });

    // Iframe visibility.
    tabs.forEach((t) => {
      const frame = ensureIframe(t.id);
      frame.classList.toggle('hidden', t.id !== activeId);
    });
    // Drop iframes for closed tabs.
    panels.querySelectorAll('iframe').forEach((frame) => {
      const id = frame.id.replace('tab-panel-', '');
      if (!tabs.some((t) => t.id === id)) frame.remove();
    });

    const hasTabs = tabs.length > 0;
    panels.classList.toggle('hidden', !hasTabs);
    if (empty) empty.classList.toggle('hidden', hasTabs);

    persist();
  }

  function activate(id) {
    activeId = id;
    render();
  }

  function openTab(id, focus = true) {
    if (!id) return;
    if (!tabs.some((t) => t.id === id)) tabs.push({ id });
    if (focus) activeId = id;
    render();
  }

  function closeTab(id) {
    const idx = tabs.findIndex((t) => t.id === id);
    if (idx === -1) return;
    tabs.splice(idx, 1);
    if (activeId === id) {
      const next = tabs[idx] || tabs[idx - 1] || null;
      activeId = next ? next.id : null;
    }
    render();
  }

  // --- "+ New tab" picker -------------------------------------------------
  let picker = null;
  function closePicker() {
    if (picker) { picker.remove(); picker = null; document.removeEventListener('click', onDocClick, true); }
  }
  function onDocClick(e) {
    if (picker && !picker.contains(e.target) && e.target !== newBtn) closePicker();
  }
  function openPicker() {
    if (picker) { closePicker(); return; }
    picker = document.createElement('div');
    picker.className =
      'absolute z-40 mt-1 max-h-80 w-64 overflow-y-auto rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-xl p-1';
    const rect = newBtn.getBoundingClientRect();
    const host = document.getElementById('tab-shell');
    const hostRect = host.getBoundingClientRect();
    picker.style.left = (rect.left - hostRect.left) + 'px';
    picker.style.top = (rect.bottom - hostRect.top + 4) + 'px';
    host.appendChild(picker);

    Object.keys(toolNames)
      .sort((a, b) => toolNames[a].localeCompare(toolNames[b]))
      .forEach((id) => {
        const item = document.createElement('button');
        item.className =
          'block w-full text-left px-3 py-2 rounded-lg text-sm font-semibold text-slate-700 dark:text-slate-200 hover:bg-blue-50 dark:hover:bg-blue-900/30 transition';
        item.textContent = toolNames[id];
        item.addEventListener('click', () => { closePicker(); openTab(id); });
        picker.appendChild(item);
      });

    setTimeout(() => document.addEventListener('click', onDocClick, true), 0);
  }
  if (newBtn) newBtn.addEventListener('click', (e) => { e.stopPropagation(); openPicker(); });

  // --- Sidebar links open tabs instead of navigating ----------------------
  document.querySelectorAll('nav a[href*="tab="]').forEach((a) => {
    a.addEventListener('click', (e) => {
      let id;
      try { id = new URL(a.href, location.origin).searchParams.get('tab'); } catch (_) { return; }
      if (!id) return;
      e.preventDefault();
      openTab(id);
    });
  });

  // --- Public hooks for bridge_status.js ----------------------------------
  window.__tabShell = {
    getActiveTabId: () => activeId,
    reloadAll: () => {
      panels.querySelectorAll('iframe').forEach((f) => {
        try { f.contentWindow.location.reload(); } catch (_) { f.src = f.src; }
      });
    },
  };

  // --- Boot ----------------------------------------------------------------
  if (!restore()) {
    const initial = window.__INITIAL_TAB;
    if (initial) openTab(initial);
    else render();
  } else {
    render();
  }
})();
