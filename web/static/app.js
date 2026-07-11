/* ── State ───────────────────────────────────────────────────────────────── */
const S = {
  inventory: [],
  filter:    'all',
  search:    '',
  sort:      { col: 'item_id', dir: 'asc' },
  imgCache:  {},
  charts:    {},
  _velGroup: 'set',
  user:      null,
  plan:      'free',
};
let _selectedPhotos = [];
let _currentItems   = [];
let _renderedCount  = 0;
let _listingsFilter = 'all';
const BATCH_SIZE    = 30;

/* ── Chart theme (read CSS variables once at startup) ────────────────────── */
const _cs = getComputedStyle(document.documentElement);
const CHART_THEME = {
  grid:    _cs.getPropertyValue('--border').trim()   || '#2e2e3e',
  tick:    _cs.getPropertyValue('--text-muted').trim() || '#888899',
  legend:  _cs.getPropertyValue('--text').trim()     || '#e8e8f0',
  success: _cs.getPropertyValue('--success').trim()  || '#4caf7d',
  danger:  _cs.getPropertyValue('--danger').trim()   || '#ff6b6b',
  warning: _cs.getPropertyValue('--warning').trim()  || '#ffa94d',
  accent:  _cs.getPropertyValue('--accent').trim()   || '#6c63ff',
};

/* ── Tier / Plan Features ──────────────────────────────────────────────────── */
const PLAN_FEATURES = {
  unlimited_items:      ['gym_leader', 'champion'],
  ebay_listing:         ['gym_leader', 'champion'],
  ai_descriptions:      ['gym_leader', 'champion'],
  ai_descriptions_managed: ['champion'],
  price_history:        ['gym_leader', 'champion'],
  export_accounting:    ['gym_leader', 'champion'],
};

function canAccess(feature) {
  return PLAN_FEATURES[feature]?.includes(S.plan) ?? false;
}

function isPro() { return S.plan === 'gym_leader' || S.plan === 'champion'; }
function isChampion() { return S.plan === 'champion'; }

/* ── Safe Chart Creation ───────────────────────────────────────────────────── */
function safeCreateChart(canvasId, config) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) {
    console.warn(`[chart] Canvas #${canvasId} not found — skipping`);
    return null;
  }
  // Destroy existing chart on this canvas if any
  const existing = Chart.getChart(canvas);
  if (existing) {
    try { existing.destroy(); } catch {}
  }

  try {
    return new Chart(canvas, config);
  } catch (e) {
    console.error(`[chart] Failed to create chart #${canvasId}:`, e);
    return null;
  }
}

/* ── API ─────────────────────────────────────────────────────────────────── */
const api = {
  async get(path) {
    const r = await fetch('/api' + path);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async post(path, body) {
    const r = await fetch('/api' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async postForm(path, formData) {
    const r = await fetch('/api' + path, { method: 'POST', body: formData });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async patch(path, body) {
    const r = await fetch('/api' + path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async del(path) {
    const r = await fetch('/api' + path, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
};

/* ── Toast ───────────────────────────────────────────────────────────────── */
const TOAST_ICONS = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };

function toast(msg, type = 'info', duration = 4000) {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `
    <span class="toast-icon">${TOAST_ICONS[type] || 'ℹ️'}</span>
    <span class="toast-msg">${msg}</span>
    <button class="toast-close">✕</button>`;
  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));

  const dismiss = () => {
    clearTimeout(timerId);
    el.style.transition = 'transform 0.25s ease, opacity 0.25s ease';
    el.style.transform  = 'translateX(calc(100% + 24px))';
    el.style.opacity    = '0';
    setTimeout(() => el.remove(), 260);
  };
  el.querySelector('.toast-close').addEventListener('click', dismiss);
  const timerId = setTimeout(dismiss, duration);
}

/* ── Custom confirm dialog ───────────────────────────────────────────────── */
function confirmDialog(title, message) {
  return new Promise(resolve => {
    const ov = document.createElement('div');
    ov.className = 'dialog-overlay';
    ov.innerHTML = `
      <div class="dialog">
        <h3>${title}</h3>
        <p>${message}</p>
        <div class="dialog-actions">
          <button class="btn btn-ghost" id="dlg-cancel">Cancel</button>
          <button class="btn btn-danger" id="dlg-ok">Confirm</button>
        </div>
      </div>`;
    document.body.appendChild(ov);
    requestAnimationFrame(() => ov.classList.add('visible'));
    const close = (val) => {
      ov.classList.remove('visible');
      setTimeout(() => { ov.remove(); resolve(val); }, 200);
    };
    ov.querySelector('#dlg-cancel').onclick = () => close(false);
    ov.querySelector('#dlg-ok').onclick     = () => close(true);
  });
}

/* ── Modal helpers ───────────────────────────────────────────────────────── */
function openModal(id)  { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) {
  if (id) {
    document.getElementById(id)?.classList.add('hidden');
  } else {
    const ov = document.getElementById('modal-overlay');
    if (ov) {
      ov.classList.remove('visible');
      setTimeout(() => { ov.style.display = 'none'; ov.innerHTML = ''; }, 200);
    }
  }
}

function showModal(html) {
  let ov = document.getElementById('modal-overlay');
  if (!ov) {
    ov = document.createElement('div');
    ov.id = 'modal-overlay';
    ov.className = 'modal-overlay hidden';
    ov.addEventListener('click', e => { if (e.target === ov) closeModal(); });
    document.body.appendChild(ov);
  }
  ov.innerHTML = `<div class="modal-box">${html}</div>`;
  ov.classList.remove('hidden');
  ov.style.display = 'flex';
  requestAnimationFrame(() => ov.classList.add('visible'));
}

/* ── Drawer ──────────────────────────────────────────────────────────────── */
function closeDrawer() {
  document.getElementById('listing-drawer').classList.remove('open');
  document.getElementById('drawer-overlay').classList.remove('visible');
}

function initMobileNav() {
  if (document.getElementById('mobile-topbar')) return;
  if (window.innerWidth > 768) return;

  const topbar = document.createElement('div');
  topbar.id = 'mobile-topbar';
  topbar.className = 'mobile-topbar';
  topbar.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px">
      <img src="/static/logo.png" style="width:28px;height:28px;border-radius:6px">
      <span style="font-weight:700;font-size:16px">PokeManager</span>
    </div>
    <button onclick="toggleMobileMenu()" style="background:none;border:none;color:var(--text);font-size:24px;cursor:pointer;padding:4px 8px;line-height:1">☰</button>
  `;
  document.body.insertBefore(topbar, document.body.firstChild);

  const overlay = document.createElement('div');
  overlay.id = 'mobile-menu-overlay';
  overlay.className = 'mobile-menu-overlay';
  overlay.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px">
      <span style="font-weight:700;font-size:18px">PokeManager</span>
      <button onclick="toggleMobileMenu()" style="background:none;border:none;color:var(--text);font-size:28px;cursor:pointer">✕</button>
    </div>
    <nav style="display:flex;flex-direction:column;gap:4px">
      <a onclick="navigate('/');toggleMobileMenu()" class="mobile-nav-link">📦 Inventory</a>
      <a onclick="navigate('/analytics');toggleMobileMenu()" class="mobile-nav-link">📊 Analytics</a>
      <a onclick="navigate('/listings');toggleMobileMenu()" class="mobile-nav-link">🏷️ Listings</a>
      <a onclick="navigate('/watchlist');toggleMobileMenu()" class="mobile-nav-link">👁️ Watchlist</a>
      <a onclick="navigate('/sales');toggleMobileMenu()" class="mobile-nav-link">💰 Sales</a>
      <a onclick="navigate('/calculator');toggleMobileMenu()" class="mobile-nav-link">🧮 Calculator</a>
      <a onclick="navigate('/upgrade');toggleMobileMenu()" class="mobile-nav-link">⭐ Upgrade</a>
      <a onclick="navigate('/settings');toggleMobileMenu()" class="mobile-nav-link">⚙️ Settings</a>
    </nav>
    <div style="margin-top:auto;padding-top:20px;border-top:1px solid var(--border)">
      <a onclick="confirmLogout();toggleMobileMenu()" class="mobile-nav-link" style="color:var(--accent2)">🚪 Sign Out</a>
    </div>
  `;
  overlay.addEventListener('click', function(e) {
    if (e.target === overlay) toggleMobileMenu();
  });
  document.body.appendChild(overlay);
}

function toggleMobileMenu() {
  const overlay = document.getElementById('mobile-menu-overlay');
  if (overlay) overlay.classList.toggle('open');
}

/* ── Loading / empty state helpers ──────────────────────────────────────── */
function showPageLoader(message = 'Loading…') {
  return `<div class="page-loader"><div class="spinner"></div><p class="text-muted">${message}</p></div>`;
}

function emptyState(icon, title, subtitle = '') {
  return `<div class="empty-state-full">
    <div class="empty-icon">${icon}</div>
    <h3>${title}</h3>
    ${subtitle ? `<p class="text-muted">${subtitle}</p>` : ''}
  </div>`;
}

/* ── Chart.js instance registry ─────────────────────────────────────────── */
function destroyChart(key) {
  if (S.charts[key]) {
    try { S.charts[key].destroy(); } catch {}
    delete S.charts[key];
  }
}

function destroyAllCharts() {
  Object.keys(S.charts).forEach(destroyChart);
}

/* ── Global Escape key handler ───────────────────────────────────────────── */
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const mobileMenu = document.getElementById('mobile-menu-overlay');
  if (mobileMenu?.classList.contains('open')) { toggleMobileMenu(); return; }
  const popover = document.getElementById('card-popover');
  if (popover) { popover.remove(); return; }
  const camModal = document.getElementById('camera-modal');
  if (camModal?.classList.contains('open')) { closeCamera(); return; }
  const drawer = document.getElementById('listing-drawer');
  if (drawer?.classList.contains('open')) { closeDrawer(); return; }
  const modal = document.getElementById('modal-overlay');
  if (modal && !modal.classList.contains('hidden') && getComputedStyle(modal).display !== 'none') {
    closeModal();
    return;
  }
});


/* ── Page transitions ────────────────────────────────────────────────────── */
function navigate(path) {
  history.pushState(null, '', path);
  routeWithTransition();
}

function routeWithTransition() {
  const app = document.getElementById('app');
  destroyAllCharts();
  app.classList.add('page-fade-out');
  setTimeout(() => {
    app.classList.remove('page-fade-out');
    routeCurrentPath();
    app.classList.add('page-fade-in');
    setTimeout(() => app.classList.remove('page-fade-in'), 300);
  }, 140);
}

function routeCurrentPath() {
  const path = window.location.pathname;
  highlightNav(path);
  (ROUTES[path] || ROUTES['/'])();
}

function highlightNav(path) {
  document.querySelectorAll('.nav-link').forEach(a => {
    a.classList.toggle('active', a.dataset.route === path);
  });
  document.querySelectorAll('.mobile-menu-link').forEach(a => {
    a.classList.toggle('active', a.href === window.location.pathname || a.getAttribute('href') === path);
  });
}

/* ── Counter animation ───────────────────────────────────────────────────── */
function animateCount(el, from, to, duration = 900, prefix = '', suffix = '') {
  const start  = performance.now();
  const isInt  = Number.isInteger(to) && !suffix.includes('%');
  const update = (now) => {
    const p     = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    const val   = from + (to - from) * eased;
    el.textContent = prefix + (isInt ? Math.round(val) : val.toFixed(suffix === '%' ? 1 : 2)) + suffix;
    if (p < 1) requestAnimationFrame(update);
  };
  requestAnimationFrame(update);
}

/* ── Image lazy-loading ──────────────────────────────────────────────────── */
const imageObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    const thumb = entry.target;
    if (thumb.dataset.loaded) return;
    thumb.dataset.loaded = '1';
    imageObserver.unobserve(thumb);
    const itemId = parseInt(thumb.dataset.itemId);
    if (S.imgCache[itemId] !== undefined) {
      applyThumb(thumb, S.imgCache[itemId], itemId);
      return;
    }
    fetch(`/api/inventory/${itemId}/image`)
      .then(r => r.json())
      .then(d => {
        S.imgCache[itemId] = d.image_url || null;
        applyThumb(thumb, d.image_url, itemId);
      })
      .catch(() => {
        S.imgCache[itemId] = null;
        thumb.innerHTML = '<span class="thumb-none">—</span>';
      });
  });
}, { rootMargin: '200px' });

function applyThumb(thumb, url, itemId) {
  if (url) {
    const img = document.createElement('img');
    img.className = 'card-thumb-img';
    img.alt = 'card';
    img.loading = 'lazy';
    img.src = url;
    img.style.cssText = 'width:100%;height:auto;max-height:100%;object-fit:contain;display:block';
    img.onerror = () => { thumb.innerHTML = '<span class="thumb-error">?</span>'; };
    img.onclick = (e) => {
      e.stopPropagation();
      const item = S.inventory.find(i => i.item_id === itemId);
      if (item) showCardPopover(itemId, url, item);
    };
    thumb.innerHTML = '';
    thumb.appendChild(img);
    thumb.classList.add('loaded');
  } else {
    thumb.innerHTML = '<div style="width:100%;height:100%;min-height:130px;background:var(--surface2);border-radius:4px;display:flex;align-items:center;justify-content:center;color:var(--text-muted);font-size:20px">🃏</div>';
    thumb.classList.add('loaded');
  }
}

function observeThumbs(container) {
  (container || document).querySelectorAll('.card-thumb[data-item-id]:not([data-loaded])').forEach(el => {
    imageObserver.observe(el);
  });
}

/* ── Card popover ────────────────────────────────────────────────────────── */
function showCardPopover(itemId, imgUrl, item) {
  document.getElementById('card-popover')?.remove();
  const ov = document.createElement('div');
  ov.id = 'card-popover';
  ov.className = 'card-popover-overlay';
  const pp = item.potential_profit;
  ov.innerHTML = `
    <div class="popover-inner">
      <button class="popover-close" onclick="document.getElementById('card-popover').remove()">✕</button>
      <img src="${imgUrl}" alt="${esc(item.card_name || '')}" class="popover-img"
           onerror="this.className='popover-img-placeholder'">
      <div class="popover-info">
        <h3>${esc(item.card_name || '—')}</h3>
        <p class="text-muted" style="font-size:0.82rem">${esc(item.condition || '')}${item.region ? ' · ' + esc(item.region) : ''}</p>
        <div class="popover-prices">
          <span>Market</span>    <strong>${fmt(item.live_price)}</strong>
          <span>Quick sell</span><strong>${fmt(item.quick_price)}</strong>
          <span>Bought</span>    <strong>${fmt(item.purchase_price)}</strong>
          <span>Pot. profit</span><strong class="${profitClass(pp)}">${fmt(pp)}</strong>
        </div>
        <div class="popover-history" style="margin:10px 0 6px">
          <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:4px">Price history (90 days)</div>
          <canvas id="popover-sparkline" width="200" height="44" style="border-radius:4px;background:rgba(0,0,0,0.2)"></canvas>
        </div>
        <div id="popover-competitors" style="margin:10px 0 0"></div>
        <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
          ${item.pc_url ? `<a href="${item.pc_url}" target="_blank" class="btn btn-ghost btn-sm">PriceCharting ↗</a>` : ''}
          ${item.ebay_listing_id ? `<a href="https://www.ebay.co.uk/itm/${item.ebay_listing_id}" target="_blank" class="btn btn-ghost btn-sm">eBay ↗</a>` : ''}
        </div>
      </div>
    </div>`;
  ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
  document.body.appendChild(ov);
  requestAnimationFrame(() => ov.classList.add('visible'));

  fetch(`/api/price-history/${itemId}`)
    .then(r => r.json())
    .then(data => {
      const canvas = document.getElementById('popover-sparkline');
      if (canvas && data.history?.length > 1) drawSparkline(canvas, data.history, 'stable');
    })
    .catch(() => {});

  fetch(`/api/pricing/competitors/${itemId}`)
    .then(r => r.json())
    .then(data => {
      const slot = document.getElementById('popover-competitors');
      if (!slot || data.error || !data.competitors) return;
      const c      = data.competitors;
      const count  = c.count ?? 0;
      const ourP   = data.our_price || data.market;
      slot.innerHTML = `
        <div class="popover-competitors">
          <div class="comp-header">eBay Competition (${count} listings)</div>
          <div class="comp-rows">
            ${c.lowest    != null ? `<div class="comp-row"><span>Lowest</span><strong>${fmt(c.lowest)}</strong></div>` : ''}
            ${c.median    != null ? `<div class="comp-row"><span>Median</span><strong>${fmt(c.median)}</strong></div>` : ''}
            ${c.quick_sell != null ? `<div class="comp-row highlight"><span>Quick sell</span><strong>${fmt(c.quick_sell)}</strong></div>` : ''}
            ${ourP         != null ? `<div class="comp-row"><span>Our price</span><strong>${fmt(ourP)}</strong></div>` : ''}
          </div>
          ${c.strategy_note ? `<p class="comp-strategy-note">${esc(c.strategy_note)}</p>` : ''}
        </div>`;
    })
    .catch(() => {});
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */
function fmt(n, prefix = '£') {
  if (n === null || n === undefined) return '—';
  return prefix + Number(n).toFixed(2);
}
function profitClass(n) {
  if (n === null || n === undefined) return 'profit-neu';
  return n >= 0 ? 'profit-pos' : 'profit-neg';
}
function esc(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#x27;');
}
function extractError(msg) {
  try { return JSON.parse(msg).detail || msg; } catch { return msg; }
}
function flashCard(itemId, type = 'success') {
  const card = document.querySelector(`.inv-card[data-id="${itemId}"]`);
  if (!card) return;
  card.classList.remove('flash-success', 'flash-error');
  void card.offsetWidth;
  card.classList.add(`flash-${type}`);
  setTimeout(() => card.classList.remove(`flash-${type}`), 1500);
}

/* ── Filter & sort helpers ───────────────────────────────────────────────── */
const FILTERS = [
  { key: 'all',        label: 'All' },
  { key: 'in_stock',   label: 'In Stock' },
  { key: 'sold',       label: 'Sold' },
  { key: 'ebay',       label: 'eBay Listed' },
  { key: 'not_listed', label: 'Not Listed' },
  { key: 'underwater', label: '⚠️ Underwater' },
  { key: 'low_eff',    label: '⚡ Low Eff.' },
];

function applyFiltersAndSort() {
  let items = [...S.inventory];
  switch (S.filter) {
    case 'in_stock':   items = items.filter(i => i.status === 'Inventory'); break;
    case 'sold':       items = items.filter(i => i.status === 'Sold'); break;
    case 'ebay':       items = items.filter(i => i.ebay_listed === 'Yes'); break;
    case 'not_listed': items = items.filter(i => i.status === 'Inventory' && i.ebay_listed !== 'Yes'); break;
    case 'underwater': items = items.filter(i => i.status === 'Inventory' && (i.live_price || 0) < (i.purchase_price || 0)); break;
    case 'low_eff':    items = items.filter(i => i.status === 'Inventory' && i.purchase_price > 0
                               && ((i.potential_profit ?? 0) / i.purchase_price) < 0.1); break;
  }
  if (S.search) {
    const q = S.search.toLowerCase();
    items = items.filter(i =>
      (i.card_name || '').toLowerCase().includes(q) ||
      (i.condition || '').toLowerCase().includes(q) ||
      (i.region    || '').toLowerCase().includes(q)
    );
  }
  const { col, dir } = S.sort;
  items.sort((a, b) => {
    let av = a[col], bv = b[col];
    if (av == null) av = dir === 'asc' ?  Infinity : -Infinity;
    if (bv == null) bv = dir === 'asc' ?  Infinity : -Infinity;
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    return av < bv ? (dir === 'asc' ? -1 : 1) : av > bv ? (dir === 'asc' ? 1 : -1) : 0;
  });
  return items;
}

function countFor(k) {
  const inv = S.inventory;
  switch (k) {
    case 'all':        return inv.length;
    case 'in_stock':   return inv.filter(i => i.status === 'Inventory').length;
    case 'sold':       return inv.filter(i => i.status === 'Sold').length;
    case 'ebay':       return inv.filter(i => i.ebay_listed === 'Yes').length;
    case 'not_listed': return inv.filter(i => i.status === 'Inventory' && i.ebay_listed !== 'Yes').length;
    case 'underwater': return inv.filter(i => i.status === 'Inventory' && (i.live_price || 0) < (i.purchase_price || 0)).length;
    case 'low_eff':    return inv.filter(i => i.status === 'Inventory' && i.purchase_price > 0
                              && ((i.potential_profit ?? 0) / i.purchase_price) < 0.1).length;
    default: return 0;
  }
}

/* ── Card skeleton ───────────────────────────────────────────────────────── */
function skeletonCards(count = 12) {
  const card = `
    <div class="inv-card" style="pointer-events:none">
      <div style="height:140px;overflow:hidden;position:relative;background:var(--surface2)">
        <div class="skel" style="position:absolute;inset:0;border-radius:0"></div>
      </div>
      <div class="inv-card-body">
        <div style="display:flex;justify-content:space-between;margin-bottom:8px">
          <div class="skel skel-id"></div><div class="skel skel-badge"></div>
        </div>
        <div class="skel skel-name" style="margin-bottom:6px"></div>
        <div class="skel" style="width:70px;height:12px;margin-bottom:14px"></div>
        <div class="inv-card-prices">
          <div class="skel skel-price"></div><div class="skel skel-price"></div>
          <div class="skel skel-price"></div><div class="skel skel-price"></div>
        </div>
      </div>
      <div class="inv-card-actions"><div class="skel skel-sm"></div></div>
    </div>`;
  return Array(count).fill(card).join('');
}

/* ── Inventory card ──────────────────────────────────────────────────────── */
function renderInventoryCard(item) {
  const liveP  = item.live_price     || 0;
  const buyP   = item.purchase_price || 0;
  const profit = liveP - buyP;
  const pClass = profit > 0 ? 'profit-pos' : profit < 0 ? 'profit-neg' : '';
  const roi = buyP > 0 ? ((profit / buyP) * 100).toFixed(1) : 0;
  const roiClass = roi > 0 ? 'profit-pos' : roi < 0 ? 'profit-neg' : '';
  const isUW   = item.status === 'Inventory' && liveP > 0 && liveP < buyP;
  const isLE   = item.status === 'Inventory' && buyP > 0 && ((item.potential_profit ?? 0) / buyP) < 0.1;
  const isListed = item.ebay_listed === 'Yes';
  const isSold   = item.status === 'Sold';

  const badges = [
    isListed  ? `<span class="badge badge-ebay">eBay</span>`   : '',
    isUW      ? `<span class="badge badge-danger">⚠️</span>`   : '',
    isLE && !isUW ? `<span class="badge badge-warn">⚡</span>` : '',
    isSold    ? `<span class="badge badge-sold">Sold</span>`   : '',
  ].filter(Boolean).join('');

  const canList = canAccess('ebay_listing');

  const mainActions = isSold
    ? `<button onclick="openPriceCheck(${item.item_id})" class="btn btn-ghost btn-sm" style="flex:1;min-width:60px;max-width:none;padding:8px 4px;font-size:12px;white-space:nowrap">💰 Check</button>`
    : `<button onclick="openPriceCheck(${item.item_id})" class="btn btn-ghost btn-sm" style="flex:1;min-width:60px;max-width:none;padding:8px 4px;font-size:12px;white-space:nowrap">💰 Check</button>
       <button onclick="refreshSinglePrice(${item.item_id})" class="btn btn-ghost btn-sm refresh-price-btn" style="flex:1;min-width:60px;max-width:none;padding:8px 4px;font-size:12px;white-space:nowrap">🔄 Refresh</button>
       <button onclick="openEditModal(${item.item_id})" class="btn btn-ghost btn-sm" style="flex:1;min-width:60px;max-width:none;padding:8px 4px;font-size:12px;white-space:nowrap">✏️ Edit</button>
       <button onclick="confirmRemove(${item.item_id})" class="btn btn-danger btn-sm" style="flex:1;min-width:60px;max-width:none;padding:8px 4px;font-size:12px;white-space:nowrap">🗑️ Delete</button>
       ${isListed
         ? ''
         : canList
         ? `<button onclick="openListingDrawer(${item.item_id})" class="btn btn-accent btn-sm" style="flex:1;min-width:60px;max-width:none;padding:8px 4px;font-size:12px;white-space:nowrap">📋 List</button>
            <button onclick="document.getElementById('already-listed-${item.item_id}').style.display='block'" class="btn btn-ghost btn-sm" style="flex:1;min-width:60px;max-width:none;padding:8px 4px;font-size:12px;white-space:nowrap">🔗 Already Listed</button>`
         : `<button onclick="showUpgradePrompt('ebay_listing')" class="btn btn-ghost btn-sm" style="flex:1;min-width:60px;max-width:none;padding:8px 4px;font-size:12px;white-space:nowrap">🔒 List</button>`
       }`;

  const sellAction = isSold
    ? `<div style="padding:0 8px 8px 8px;width:100%;box-sizing:border-box">
         <button onclick="openSoldAndDelistModal(${item.item_id})" class="btn btn-success" style="width:100%;padding:10px">💰 Sold</button>
       </div>`
    : canList || !isListed
    ? `<div style="padding:0 8px 8px 8px;width:100%;box-sizing:border-box">
         <button onclick="openSellModal(${item.item_id})" class="btn btn-success" style="width:100%;padding:10px">💰 Sell</button>
       </div>`
    : '';

  const selected = S.selection?.has(item.item_id);

  return `
    <div class="inv-card${isUW ? ' is-underwater' : ''}${selected ? ' is-selected' : ''}" data-id="${item.item_id}" style="background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;display:flex;flex-direction:column;margin-bottom:12px;width:100%;box-sizing:border-box;position:relative">
      <div style="position:absolute;top:6px;right:6px;z-index:10">
        <label style="display:flex;align-items:center;justify-content:center;width:24px;height:24px;background:var(--surface);border:2px solid var(--border);border-radius:6px;cursor:pointer;transition:all 0.2s">
          <input type="checkbox" class="bulk-cb inv-card-checkbox" data-id="${item.item_id}"
                 ${selected ? 'checked' : ''}
                 onchange="toggleSelectItem(${item.item_id})"
                 style="display:none">
          <span class="check-indicator" style="font-size:14px;color:var(--accent);font-weight:700">${selected ? '✓' : ''}</span>
        </label>
      </div>

      <div style="position:relative;padding-left:116px;min-height:150px">
        <div style="position:absolute;left:0;top:0;bottom:0;width:110px;padding:0">
          <div class="card-thumb" data-item-id="${item.item_id}" style="width:110px;height:100%;display:flex;align-items:center;justify-content:center"><div class="thumb-spinner"></div></div>
        </div>

        <div style="padding:8px 8px 8px 8px;padding-right:32px;min-width:0;overflow:hidden;display:flex;flex-direction:column;gap:6px;box-sizing:border-box;flex:1">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;overflow:hidden;max-width:100%">
            <span class="inv-card-id" style="color:var(--text-muted);font-size:11px;flex-shrink:0">#${item.item_id}</span>
            <span style="color:var(--text-muted);flex-shrink:0">·</span>
            <div class="inv-card-name" style="font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;font-size:13px" title="${esc(item.card_name || '')}">${esc(item.card_name || '—')}</div>
            <div class="inv-card-badges" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;overflow:hidden;max-width:100%;padding-right:30px;box-sizing:border-box">${badges}</div>
          </div>

          <div class="inv-card-cond" style="font-size:11px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%">${esc(item.condition || '—')} · ${esc(item.region || 'EN')}</div>

          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:12px;max-width:100%;box-sizing:border-box">
            <div><span style="color:var(--text-muted);font-size:10px;text-transform:uppercase">Bought</span><br><strong style="font-size:13px;font-weight:600">${fmt(buyP)}</strong></div>
            <div><span style="color:var(--text-muted);font-size:10px;text-transform:uppercase">${isSold ? 'Sold for' : 'Market'}</span><br><strong style="font-size:13px;font-weight:600;${isSold ? 'color:var(--success)' : ''}">${isSold ? fmt(item.sell_price) : fmt(item.live_price)}</strong></div>
            <div><span style="color:var(--text-muted);font-size:10px;text-transform:uppercase">Profit</span><br><strong style="font-size:13px;font-weight:600;${isSold ? 'color:var(--success)' : pClass}">${isSold ? (item.profit >= 0 ? '+' : '') + fmt(item.profit) : (profit >= 0 ? '+' : '') + fmt(profit)}</strong></div>
          </div>

          ${isSold && item.date_sold ? `
            <div style="display:flex;align-items:center;gap:12px;margin-top:4px;font-size:11px;padding:6px 0;border-top:1px solid var(--border);color:var(--text-muted);box-sizing:border-box">
              <span>Sold on ${new Date(item.date_sold).toLocaleDateString('en-GB')}</span>
            </div>
          ` : ''}

          <div style="display:flex;align-items:center;gap:12px;margin-top:${isSold && item.date_sold ? '0' : '4'}px;font-size:12px;flex-wrap:wrap;max-width:100%;box-sizing:border-box">
            <span style="color:var(--text-muted)">Quick <strong style="color:var(--accent);font-size:13px;font-weight:600">${fmt(item.quick_price)}</strong></span>
            <span style="color:var(--text-muted)">ROI <strong style="color:${roi > 0 ? '#10b981' : roi < 0 ? '#ef4444' : '#6b7280'};font-size:13px;font-weight:600">${roi > 0 ? '+' : ''}${roi}%</strong></span>
          </div>
        </div>
      </div>

      <div style="width:100%;height:65px;background:var(--surface2)">
        <canvas class="sparkline-canvas" style="width:100%;height:65px;display:block"></canvas>
      </div>

      <div style="display:flex;gap:4px;padding:8px;border-top:1px solid var(--border);flex-wrap:wrap;box-sizing:border-box;width:100%">${mainActions}</div>
      <div id="already-listed-${item.item_id}" style="display:none;padding:8px 12px;border-top:1px solid var(--border)">
        <div style="font-size:12px;color:var(--text-muted);margin-bottom:6px">Enter your eBay listing ID or URL:</div>
        <div style="display:flex;gap:8px">
          <input type="text" placeholder="336xxxxxxxxx or ebay.co.uk/itm/..."
                 style="flex:1;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--surface2);color:var(--text);font-size:13px"
                 id="listing-input-${item.item_id}">
          <button onclick="syncExistingListing(${item.item_id})" class="btn btn-accent btn-sm">Sync</button>
          <button onclick="document.getElementById('already-listed-${item.item_id}').style.display='none'" class="btn btn-ghost btn-sm">✕</button>
        </div>
      </div>
      ${sellAction}
    </div>`;
}

// CSS class for action buttons
// Added to style.css: .inv-card-btn styling

/* ── Batch rendering ─────────────────────────────────────────────────────── */
function renderInventoryBatch(items, append = false) {
  const grid = document.getElementById('inventory-grid');
  if (!grid) return;

  if (!append) {
    grid.innerHTML = items.length === 0
      ? `<div class="empty-state" style="grid-column:1/-1">No items match this filter.</div>`
      : '';
    _renderedCount = 0;
  }

  grid.querySelector('#load-sentinel')?.remove();
  if (items.length === 0) return;

  const batch = items.slice(_renderedCount, _renderedCount + BATCH_SIZE);
  const frag  = document.createDocumentFragment();
  batch.forEach(item => {
    const div = document.createElement('div');
    div.innerHTML = renderInventoryCard(item);
    frag.appendChild(div.firstElementChild);
  });
  grid.appendChild(frag);
  _renderedCount += batch.length;

  observeThumbs(grid);
  grid.querySelectorAll('.inv-card[data-id]:not([data-sparkline-loaded])').forEach(c => sparklineObserver.observe(c));
  if (_renderedCount < items.length) observeLoadMore(items);
}

function observeLoadMore(items) {
  const grid = document.getElementById('inventory-grid');
  if (!grid) return;
  const sentinel = document.createElement('div');
  sentinel.id = 'load-sentinel';
  sentinel.style.cssText = 'height:1px;grid-column:1/-1';
  grid.appendChild(sentinel);

  const obs = new IntersectionObserver(entries => {
    if (!entries[0].isIntersecting) return;
    obs.disconnect();
    sentinel.remove();
    renderInventoryBatch(items, true);
  }, { rootMargin: '200px' });

  obs.observe(sentinel);
}

/* ── Grid refresh ────────────────────────────────────────────────────────── */
function refreshInventoryGrid() {
  _currentItems  = applyFiltersAndSort();
  _renderedCount = 0;
  renderInventoryBatch(_currentItems, false);

  document.querySelectorAll('.filter-tab[data-filter]').forEach(btn => {
    btn.classList.toggle('active', S.filter === btn.dataset.filter);
    const count = btn.querySelector('.count');
    if (count) count.textContent = countFor(btn.dataset.filter);
  });

  const countEl = document.getElementById('row-count');
  if (countEl) countEl.textContent = `${_currentItems.length} items`;

  // Show tier limit banner for free users
  if (S.plan === 'free') {
    const bannerEl = document.getElementById('tier-limit-banner');
    if (bannerEl) {
      const inStock = S.inventory.filter(i => i.status === 'Inventory').length;
      if (inStock >= 40) {
        const remaining = 50 - inStock;
        bannerEl.innerHTML = `
          ${remaining <= 0
            ? `⚠️ You've reached the 50-item limit for free accounts.`
            : `⚠️ ${remaining} item slot${remaining === 1 ? '' : 's'} remaining on your free plan.`
          }
          <a href="#" onclick="navigate('/upgrade'); return false" style="color:var(--accent);margin-left:8px">
            Upgrade for unlimited →
          </a>
        `;
        bannerEl.style.display = 'block';
      } else {
        bannerEl.style.display = 'none';
      }
    }
  }

  updateBulkToolbar();
}

/* ── Inventory page ──────────────────────────────────────────────────────── */
async function renderInventory() {
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">Inventory</h1>
      <div style="display:flex;gap:8px">
        <button class="btn btn-ghost" id="refresh-all-prices-btn" onclick="refreshAllPrices()">🔄 Refresh All Prices</button>
        <button class="btn btn-ghost" onclick="openCsvImportModal()">📥 Import CSV</button>
        <button class="btn btn-accent" onclick="openAddItemModal()">+ Add Item</button>
      </div>
    </div>
    <div class="inventory-controls">
      <div class="toolbar">
        <div class="search-wrap">
          <span class="search-icon">🔍</span>
          <input class="search-box" id="search-input" type="text"
                 placeholder="Search cards…" value="${esc(S.search)}" />
        </div>
      </div>
      <div class="filter-tabs" id="filter-tabs">
        ${FILTERS.map(f => `
          <button class="filter-tab ${S.filter === f.key ? 'active' : ''}" data-filter="${f.key}">
            ${f.label} <span class="count">0</span>
          </button>`).join('')}
      </div>
      <div class="sort-controls">
        <button class="btn btn-sm btn-ghost" onclick="toggleSelectAll()" title="Select all">☑ All</button>
        <span class="sort-label">Sort by:</span>
        <select id="sort-field" class="form-input" style="width:auto;padding:5px 10px">
          <option value="item_id">ID</option>
          <option value="card_name">Name</option>
          <option value="live_price">Market Price</option>
          <option value="quick_price">Quick Price</option>
          <option value="potential_profit">Potential Profit</option>
          <option value="purchase_price">Bought</option>
          <option value="date_added">Date Added</option>
        </select>
        <button id="sort-dir-btn" class="btn btn-icon" title="Toggle direction">
          ${S.sort.dir === 'asc' ? '↑' : '↓'}
        </button>
      </div>
    </div>
    ${S.plan === 'free' ? `<div id="tier-limit-banner" class="tier-limit-banner" style="display:none"></div>` : ''}
    <div class="bulk-toolbar hidden" id="bulk-toolbar">
      <span class="bulk-count">0 selected</span>
      <button class="btn btn-sm btn-ghost" onclick="bulkUpdatePrices()">🔄 Update Prices</button>
      <button class="btn btn-sm btn-ghost" onclick="bulkExport()">📥 Export CSV</button>
      <button class="btn btn-sm btn-danger" onclick="bulkRemove()">🗑️ Remove</button>
      <button class="btn btn-sm btn-ghost" onclick="clearSelection()">✕ Clear</button>
    </div>
    <div id="inventory-grid" class="inventory-grid" style="margin-top:12px">${skeletonCards(12)}</div>
    <p id="row-count" class="text-muted" style="font-size:0.82rem;margin-top:12px">Loading…</p>`;

  document.getElementById('search-input').addEventListener('input', e => {
    S.search = e.target.value;
    refreshInventoryGrid();
  });

  const sortSel = document.getElementById('sort-field');
  sortSel.value = S.sort.col;
  sortSel.addEventListener('change', () => { S.sort.col = sortSel.value; refreshInventoryGrid(); });

  const dirBtn = document.getElementById('sort-dir-btn');
  dirBtn.addEventListener('click', () => {
    S.sort.dir = S.sort.dir === 'asc' ? 'desc' : 'asc';
    dirBtn.textContent = S.sort.dir === 'asc' ? '↑' : '↓';
    refreshInventoryGrid();
  });

  document.getElementById('filter-tabs').querySelectorAll('.filter-tab').forEach(btn => {
    btn.addEventListener('click', () => { S.filter = btn.dataset.filter; refreshInventoryGrid(); });
  });

  try {
    const data = await api.get('/inventory');
    S.inventory = data.items;
  } catch {
    document.getElementById('inventory-grid').innerHTML =
      `<div class="empty-state" style="grid-column:1/-1">Failed to load inventory.</div>`;
    document.getElementById('row-count').textContent = '';
    return;
  }

  if (S.inventory.length === 0 && !sessionStorage.getItem('onboarding_dismissed')) {
    renderOnboarding();
    return;
  }

  refreshInventoryGrid();
  checkDataHealth();
}

/* ── New-user onboarding ──────────────────────────────────────────────────── */
function renderOnboarding() {
  const app = document.getElementById('app');
  app.innerHTML = `
    <div class="onboarding-page">
      <div class="onboarding-hero">
        <div class="onboarding-icon">🃏</div>
        <h1>Welcome to PokeManager</h1>
        <p class="text-muted">Your Pokémon TCG inventory and reselling platform. Let's get you set up.</p>
      </div>

      <div class="onboarding-steps">
        <div class="onboarding-step" onclick="openAddItemModal()">
          <div class="step-number">1</div>
          <div class="step-content">
            <h3>Add your first card</h3>
            <p>Paste a PriceCharting URL and your purchase price — we'll track the market value automatically.</p>
            <button class="btn btn-accent" onclick="event.stopPropagation();openAddItemModal()">+ Add a Card</button>
          </div>
        </div>

        <div class="onboarding-step" onclick="navigate('/settings')">
          <div class="step-number">2</div>
          <div class="step-content">
            <h3>Connect your eBay account</h3>
            <p>Add your eBay API keys in Settings to enable auto-listing, price syncing, and sale detection.</p>
            <button class="btn btn-ghost" onclick="event.stopPropagation();navigate('/settings')">⚙️ Open Settings</button>
          </div>
        </div>

        <div class="onboarding-step" onclick="navigate('/listings')">
          <div class="step-number">3</div>
          <div class="step-content">
            <h3>List and track sales</h3>
            <p>Once cards are added, list them on eBay directly from the dashboard. Sales are detected automatically.</p>
            <button class="btn btn-ghost" onclick="event.stopPropagation();navigate('/listings')">📋 View Listings</button>
          </div>
        </div>
      </div>

      <div class="onboarding-demo">
        <h3>Already tracking cards elsewhere?</h3>
        <p class="text-muted">Import an existing inventory.xlsx spreadsheet all at once from Settings.</p>
        <div style="display:flex;gap:10px;margin-top:12px">
          <button class="btn btn-ghost" onclick="navigate('/settings')">📤 Import from Excel</button>
        </div>
      </div>

      <button class="btn btn-ghost onboarding-skip" onclick="dismissOnboarding()">
        Skip — I'll explore on my own
      </button>
    </div>
  `;
}

function dismissOnboarding() {
  sessionStorage.setItem('onboarding_dismissed', '1');
  renderInventory();
}

/* ── Data health banner ──────────────────────────────────────────────────── */
async function checkDataHealth() {
  if (sessionStorage.getItem('health_banner_dismissed')) return;
  const health = await api.get('/analytics/data-health').catch(() => null);
  if (!health || health.total === 0) return;

  document.getElementById('data-health-banner')?.remove();
  const banner = document.createElement('div');
  banner.id = 'data-health-banner';
  banner.className = 'data-health-banner';
  banner.innerHTML = `
    ⚠️ ${health.total} item(s) have price data issues —
    <a href="#" onclick="navigate('/analytics');return false">view details on Analytics</a>
    <button onclick="sessionStorage.setItem('health_banner_dismissed','1');this.closest('.data-health-banner').remove()">✕</button>`;
  document.querySelector('#app .page-header')?.after(banner);
}

/* ── Edit modal ──────────────────────────────────────────────────────────── */
let _editId = null;
function openEditModal(itemId) {
  const item = S.inventory.find(i => i.item_id === itemId);
  if (!item) return;
  _editId = itemId;
  showModal(`
    <h2 style="margin-bottom:16px">✏️ Edit Item #${itemId}</h2>
    <div class="form-section">
      <label class="form-label">Card Name</label>
      <input id="edit-name" class="form-input" value="${esc(item.card_name || '')}">
    </div>
    <div class="form-section">
      <label class="form-label">PriceCharting URL</label>
      <input id="edit-pc-url" class="form-input" type="url" value="${esc(item.pc_url || '')}">
    </div>
    <div class="form-section">
      <label class="form-label">Purchase Price (£)</label>
      <input id="edit-price" class="form-input" type="number" step="0.01" value="${item.purchase_price || ''}">
    </div>
    <div class="form-section">
      <label class="form-label">Condition</label>
      <select id="edit-condition" class="form-input">
        ${['Near mint or better','Lightly played','Moderately played','Heavily played','Damaged','Graded PSA 10','Graded PSA 9','Graded PSA 8','Graded PSA 7','Graded PSA 6','Graded PSA 5','Graded PSA 4','Graded PSA 3','Graded PSA 2','Graded PSA 1']
          .map(c => `<option${c === (item.condition||'') ? ' selected' : ''}>${c}</option>`).join('')}
      </select>
    </div>
    <div class="form-section">
      <label class="form-label">Region</label>
      <select id="edit-region" class="form-input">
        ${['','EN','JP','KR'].map(r => `<option value="${r}"${r === (item.region||'') ? ' selected' : ''}>${r||'EN (default)'}</option>`).join('')}
      </select>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="confirmEdit()">Save Changes</button>
    </div>`);
}

async function confirmEdit() {
  const item = S.inventory.find(i => i.item_id === _editId);
  if (!item) return;
  const btn = document.querySelector('#modal-overlay .btn-accent');
  const fields = {};
  const name      = document.getElementById('edit-name')?.value.trim();
  const pcUrl     = document.getElementById('edit-pc-url')?.value.trim();
  const price     = parseFloat(document.getElementById('edit-price')?.value);
  const condition = document.getElementById('edit-condition')?.value;
  const region    = document.getElementById('edit-region')?.value;

  if (name && name !== item.card_name)           fields.card_name = name;
  if (pcUrl && pcUrl !== item.pc_url)            fields.pc_url = pcUrl;
  if (!isNaN(price) && price !== item.purchase_price) fields.purchase_price = String(price);
  if (condition !== item.condition)              fields.condition = condition;
  if (region !== (item.region || ''))            fields.region = region;

  if (Object.keys(fields).length === 0) { closeModal(); return; }
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Saving…'; }

  try {
    await api.patch(`/inventory/${_editId}/fields`, { fields });
    Object.assign(item, {
      card_name: fields.card_name ?? item.card_name,
      pc_url: fields.pc_url ?? item.pc_url,
      purchase_price: fields.purchase_price != null ? parseFloat(fields.purchase_price) : item.purchase_price,
      condition: fields.condition ?? item.condition,
      region: fields.region ?? item.region,
    });
    closeModal();
    refreshInventoryGrid();
    flashCard(_editId, 'success');
    toast('Item updated', 'success');
  } catch (e) {
    toast('Save failed: ' + extractError(e.message), 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Save Changes'; }
  }
}

/* ── Sell modal ──────────────────────────────────────────────────────────── */
let _sellId = null;
function openSellModal(itemId) {
  const item = S.inventory.find(i => i.item_id === itemId);
  if (!item) return;
  _sellId = itemId;
  const market = parseFloat(item.live_price || 0);
  const quick  = parseFloat(item.quick_price || 0);
  const suggested = (quick || market * 0.95 || 0).toFixed(2);
  showModal(`
    <h2 style="margin-bottom:6px">💰 Record Sale</h2>
    <p class="text-muted" style="margin-bottom:16px">${esc(item.card_name || '')}</p>
    <div class="form-section">
      <label class="form-label">Sale Price (£)</label>
      <input type="number" id="sell-price-input" class="form-input"
             value="${suggested}" step="0.01" min="0.01" />
      <div class="price-hints">
        ${quick ? `<button class="pill-btn" onclick="document.getElementById('sell-price-input').value='${quick.toFixed(2)}'">Quick sell ${fmt(quick)}</button>` : ''}
        ${market ? `<button class="pill-btn" onclick="document.getElementById('sell-price-input').value='${market.toFixed(2)}'">Market ${fmt(market)}</button>` : ''}
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-success" onclick="confirmSell()">✅ Record Sale</button>
    </div>`);
  setTimeout(() => document.getElementById('sell-price-input')?.focus(), 80);
}

/* ── Sold & delist modal (for eBay listings) ───────────────────────────────── */
async function openSoldAndDelistModal(itemId) {
  const item = S.inventory.find(i => i.item_id === itemId);
  if (!item) return;

  const market = parseFloat(item.live_price || 0);
  const quick  = parseFloat(item.quick_price || 0);
  const listed = parseFloat(item.sell_price || 0);
  const suggested = listed || quick || market;

  showModal(`
    <h2 style="margin-bottom:6px">💰 Mark as Sold</h2>
    <p class="text-muted" style="margin-bottom:16px">${esc(item.card_name || '')}</p>

    <div class="form-section">
      <label class="form-label">Sale Price (£)</label>
      <input type="number" id="sold-delist-price" class="form-input"
             value="${suggested.toFixed(2)}" step="0.01" min="0.01">
      <div class="price-hints" style="margin-top:8px">
        ${listed  ? `<button class="pill-btn" onclick="document.getElementById('sold-delist-price').value='${listed.toFixed(2)}'">Listed ${fmt(listed)}</button>` : ''}
        ${quick   ? `<button class="pill-btn" onclick="document.getElementById('sold-delist-price').value='${quick.toFixed(2)}'">Quick ${fmt(quick)}</button>` : ''}
        ${market  ? `<button class="pill-btn" onclick="document.getElementById('sold-delist-price').value='${market.toFixed(2)}'">Market ${fmt(market)}</button>` : ''}
      </div>
    </div>

    <div style="background:rgba(108,99,255,0.06);border:1px solid rgba(108,99,255,0.2);border-radius:8px;padding:12px;margin:14px 0;font-size:13px">
      This will:
      <ul style="margin:6px 0 0 16px;line-height:1.8">
        <li>End the eBay listing (${item.ebay_listing_id || 'no listing ID'})</li>
        <li>Mark item as Sold in your inventory</li>
        <li>Record the profit (postage paid by buyer via Simple Delivery)</li>
      </ul>
    </div>

    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-success" onclick="confirmSoldAndDelist(${itemId})">
        ✅ Mark Sold & End Listing
      </button>
    </div>
  `);

  setTimeout(() => document.getElementById('sold-delist-price')?.focus(), 80);
}

async function confirmSoldAndDelist(itemId) {
  const price = parseFloat(document.getElementById('sold-delist-price')?.value);
  if (!price || price <= 0) { toast('Enter a valid price', 'error'); return; }

  const btn = document.querySelector('.btn-success');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Processing…'; }

  try {
    const resp = await api.post(`/listings/sold-and-delist/${itemId}`, { sell_price: price });

    if (resp.success) {
      const msg = resp.warning
        ? `✅ Sold at ${fmt(price)} — ${resp.warning}`
        : `✅ Sold at ${fmt(price)} · listing ended`;
      toast(msg, resp.warning ? 'warning' : 'success', 6000);

      closeModal();

      // Update local state
      const item = S.inventory.find(i => i.item_id === itemId);
      if (item) {
        item.status        = 'Sold';
        item.sell_price    = price;
        item.ebay_listed   = 'No';
        item.ebay_listing_id = '';
      }

      buildListingsPage();
      refreshInventoryGrid();
    } else {
      toast(`❌ ${resp.error}`, 'error');
      if (btn) { btn.disabled = false; btn.textContent = '✅ Mark Sold & End Listing'; }
    }
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
    if (btn) { btn.disabled = false; btn.textContent = '✅ Mark Sold & End Listing'; }
  }
}
async function confirmSell() {
  const price = parseFloat(document.getElementById('sell-price-input').value);
  if (!price || price <= 0) { toast('Enter a valid price', 'error'); return; }
  const btn = document.querySelector('#modal-overlay .btn-success');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Saving…'; }
  try {
    const res = await api.post(`/inventory/${_sellId}/sell`, { sell_price: price });
    if (res.success === false) {
      toast(res.error || 'Sell failed', 'error');
      if (btn) { btn.disabled = false; btn.textContent = '✅ Record Sale'; }
      return;
    }
    const item = S.inventory.find(i => i.item_id === _sellId);
    if (item) { item.status = 'Sold'; item.sell_price = price; }
    closeModal();
    const card = document.querySelector(`.inv-card[data-id="${_sellId}"]`);
    if (card) {
      card.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
      card.style.opacity = '0'; card.style.transform = 'scale(0.88)';
      setTimeout(() => { card.remove(); refreshInventoryGrid(); }, 320);
    } else { refreshInventoryGrid(); }
    toast(`Sold for ${fmt(price)} ✅`, 'success');
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
    if (btn) { btn.disabled = false; btn.textContent = '✅ Record Sale'; }
  }
}

/* ── Delete modal ────────────────────────────────────────────────────────── */
let _delId = null;
function openDelete(id, name) {
  _delId = id;
  document.getElementById('delete-card-name').textContent = name;
  openModal('modal-delete');
}
function confirmRemove(itemId) {
  const item = S.inventory.find(i => i.item_id === itemId);
  openDelete(itemId, item?.card_name || `Item #${itemId}`);
}
async function confirmDelete() {
  try {
    const res = await api.del(`/inventory/${_delId}`);
    if (res.success === false) {
      toast(`Remove failed: ${res.error}`, 'error');
      return;
    }
    S.inventory = S.inventory.filter(i => i.item_id !== _delId);
    closeModal('modal-delete');
    refreshInventoryGrid();
    toast('Item removed', 'success');
  } catch (e) {
    // Network error or connection dropped (e.g. server reload mid-request)
    toast('Connection lost — server may have restarted. Try again.', 'warning');
    console.error('[delete] Network error:', e);
  }
}

/* ── Sync existing eBay listing ──────────────────────────────────────────── */
async function syncExistingListing(itemId) {
  const input = document.getElementById('listing-input-' + itemId)?.value.trim();
  if (!input) { toast('Enter a listing ID or URL', 'warning'); return; }

  // Extract listing ID from URL or use directly
  let listingId = input.includes('ebay')
    ? input.match(/\/(\d{12,})/)?.[1] || input.match(/itm\/(\d+)/)?.[1]
    : input.replace(/\D/g, '');

  if (!listingId) { toast('Invalid listing ID or URL', 'error'); return; }

  try {
    const res = await fetch(`/api/inventory/${itemId}/set-listing`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ebay_listing_id: listingId })
    });
    if (res.ok) {
      toast('✅ Listing synced!', 'success');
      await loadInventory();
    } else {
      const err = await res.json().catch(() => ({ error: 'Failed to sync' }));
      toast(err.error || 'Failed to sync listing', 'error');
    }
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
  }
}

/* ── Add item modal ──────────────────────────────────────────────────────── */
function openAddItemModal() {
  _addRowCount = 1;
  showModal(`
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <h2 style="margin:0;font-size:16px">Add Cards</h2>
      <button class="btn btn-ghost btn-sm" onclick="addAnotherRow()">+ Add another</button>
    </div>
    <div id="add-rows-container">
      ${renderAddRow(1)}
    </div>
    <div class="modal-actions" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="submitBulkAdd()">Add Cards</button>
    </div>
  `);
  setTimeout(() => document.getElementById('pc-url-1')?.focus(), 80);
}

let _addRowCount = 1;

function renderAddRow(index) {
  return `
    <div class="add-row" id="add-row-${index}">
      ${index > 1 ? `
        <button onclick="removeAddRow(${index})" class="add-row-remove" title="Remove">✕</button>
      ` : ''}
      <div class="add-row-label">Card ${index}</div>
      <div class="add-row-url-price">
        <input type="url" id="pc-url-${index}" class="form-input add-row-url"
               placeholder="PriceCharting URL (optional)">
        <input type="number" id="price-${index}" class="form-input add-row-price"
               placeholder="£0.00" step="0.01" min="0">
      </div>
      <div class="add-row-dropdowns">
        <select id="condition-${index}" class="form-input">
          <option value="Near mint or better">Near mint or better</option>
          <option value="Lightly played">Lightly played</option>
          <option value="Moderately played">Moderately played</option>
          <option value="Heavily played">Heavily played</option>
          <option value="GetGraded 10">GetGraded 10</option>
          <option value="GetGraded 9.5">GetGraded 9.5</option>
          <option value="PSA 10">PSA 10</option>
          <option value="PSA 9">PSA 9</option>
          <option value="ACE 10">ACE 10</option>
          <option value="BGS 9.5">BGS 9.5</option>
        </select>
        <select id="source-${index}" class="form-input">
          <option value="">— Source —</option>
          <option value="Card Shop">Card Shop</option>
          <option value="eBay">eBay</option>
          <option value="Vinted">Vinted</option>
          <option value="Facebook Marketplace">Facebook Marketplace</option>
          <option value="Pack Opening">Pack Opening</option>
          <option value="Trade">Trade</option>
          <option value="Car Boot / Market">Car Boot / Market</option>
          <option value="Gift">Gift</option>
          <option value="Other">Other</option>
        </select>
      </div>
    </div>
  `;
}

function addAnotherRow() {
  _addRowCount++;
  const container = document.getElementById('add-rows-container');
  const div = document.createElement('div');
  div.innerHTML = renderAddRow(_addRowCount);
  container.appendChild(div.firstElementChild);
  document.getElementById(`pc-url-${_addRowCount}`)?.focus();
}

function removeAddRow(index) {
  document.getElementById(`add-row-${index}`)?.remove();
}

async function submitBulkAdd() {
  const btn = document.querySelector('.modal-actions .btn-accent');

  // Collect all rows
  const rows = document.querySelectorAll('.add-row');
  const cards = [];

  for (const row of rows) {
    const id    = row.id.replace('add-row-', '');
    const url   = document.getElementById(`pc-url-${id}`)?.value.trim();
    const price = parseFloat(document.getElementById(`price-${id}`)?.value);
    const cond  = document.getElementById(`condition-${id}`)?.value;
    const src   = document.getElementById(`source-${id}`)?.value.trim();

    if (!url && !price) continue; // skip empty rows
    if (!url) { toast(`Row ${id}: PriceCharting URL is required`, 'error'); return; }
    if (!price || price <= 0) { toast(`Row ${id}: Enter a valid price`, 'error'); return; }

    cards.push({ pc_url: url, purchase_price: price, condition: cond, source: src });
  }

  if (!cards.length) { toast('Add at least one card', 'error'); return; }

  btn.disabled = true;
  btn.textContent = `Adding ${cards.length} card${cards.length > 1 ? 's' : ''}…`;

  let added = 0;
  let failed = 0;
  const errors = [];

  for (const card of cards) {
    try {
      const resp = await api.post('/inventory/add', card);
      if (resp.success) {
        added++;
      } else {
        failed++;
        errors.push(resp.error || 'Unknown error');
      }
    } catch (e) {
      failed++;
      errors.push(extractError(e.message));
    }
  }

  closeModal();

  if (added > 0) {
    toast(`✅ Added ${added} card${added > 1 ? 's' : ''}${failed ? `, ${failed} failed` : ''}`, 'success', 5000);
    const data = await api.get('/inventory');
    S.inventory = data.items;
    refreshInventoryGrid();
  }

  if (errors.length > 0) {
    console.error('[bulk-add] Errors:', errors);
  }

  _addRowCount = 1;
}

/* ── CSV Import ──────────────────────────────────────────────────────────── */
function openCsvImportModal() {
  showModal(`
    <h2 style="margin-bottom:6px">📥 Import from CSV</h2>
    <p class="text-muted" style="margin-bottom:20px">
      Upload a spreadsheet of your cards. Two columns are required, the rest are optional.
    </p>

    <!-- Required columns -->
    <div style="margin-bottom:16px">
      <div style="font-size:12px;font-weight:600;color:var(--text-muted);
                  text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px">
        Required
      </div>
      <div style="display:flex;flex-direction:column;gap:6px">
        <div style="display:flex;align-items:center;gap:10px;font-size:13px">
          <code style="background:var(--surface2);padding:2px 8px;border-radius:4px;
                       color:var(--accent);font-size:12px">Card_Name</code>
          <span class="text-muted">Name of the card</span>
        </div>
        <div style="display:flex;align-items:center;gap:10px;font-size:13px">
          <code style="background:var(--surface2);padding:2px 8px;border-radius:4px;
                       color:var(--accent);font-size:12px">Purchase_Price</code>
          <span class="text-muted">What you paid in £</span>
        </div>
      </div>
    </div>

    <!-- Optional columns -->
    <div style="margin-bottom:20px">
      <div style="font-size:12px;font-weight:600;color:var(--text-muted);
                  text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px">
        Optional
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">
        ${['PC_URL', 'Condition', 'Region', 'Source'].map(col => '<code style="background:var(--surface2);padding:2px 8px;border-radius:4px;color:var(--text-muted);font-size:12px">' + col + '</code>').join('')}
      </div>
    </div>

    <!-- Drop zone -->
    <div class="photo-drop-zone" id="csv-drop-zone"
         onclick="document.getElementById('csv-file-input').click()"
         style="margin-bottom:12px">
      📄 Drop CSV file here or click to select
    </div>
    <input type="file" id="csv-file-input" accept=".csv,.txt" style="display:none"
           onchange="handleCsvSelect(this.files[0])">

    <div id="csv-preview" style="display:none;margin-bottom:12px;
         background:rgba(76,175,125,0.08);border:1px solid var(--success);
         border-radius:8px;padding:12px;font-size:13px">
      <div id="csv-preview-text"></div>
    </div>

    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="downloadCsvTemplate()">⬇️ Template</button>
      <div style="flex:1"></div>
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" id="csv-import-btn" onclick="submitCsvImport()" disabled>
        Import
      </button>
    </div>
  `);

  const drop = document.getElementById('csv-drop-zone');
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', e => {
    e.preventDefault();
    drop.classList.remove('dragover');
    handleCsvSelect(e.dataTransfer.files[0]);
  });
}

let _csvFile = null;

function handleCsvSelect(file) {
  if (!file) return;
  _csvFile = file;

  const reader = new FileReader();
  reader.onload = e => {
    const lines = e.target.result.split('\n').filter(Boolean);
    const preview = document.getElementById('csv-preview');
    const previewText = document.getElementById('csv-preview-text');
    const btn = document.getElementById('csv-import-btn');

    preview.style.display = 'block';
    previewText.innerHTML = `
      ✅ <strong style="color:var(--success)">${esc(file.name)}</strong><br>
      <span style="color:var(--text-muted)">${lines.length - 1} rows detected (excluding header)</span>
    `;
    btn.disabled = false;
    btn.textContent = `Import ${lines.length - 1} card${lines.length - 1 === 1 ? '' : 's'}`;
  };
  reader.readAsText(file);
}

async function submitCsvImport() {
  if (!_csvFile) return;
  const btn = document.getElementById('csv-import-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Importing…';

  const fd = new FormData();
  fd.append('file', _csvFile);

  try {
    const resp = await fetch('/api/inventory/import-csv', {
      method: 'POST',
      body: fd,
    }).then(r => r.json());

    if (resp.success) {
      closeModal();
      toast(`✅ Imported ${resp.imported} cards${resp.errors ? `, ${resp.errors} errors` : ''}`, 'success', 6000);
      if (resp.imported > 0) {
        const data = await api.get('/inventory');
        S.inventory = data.items;
        refreshInventoryGrid();
      }
    } else {
      toast(`❌ ${resp.error}`, 'error');
      btn.disabled = false;
      btn.textContent = 'Import';
    }
  } catch (e) {
    toast('Import failed: ' + extractError(e.message), 'error');
    btn.disabled = false;
    btn.textContent = 'Import';
  }
}

function downloadCsvTemplate() {
  const csv = [
    'Card_Name,PC_URL,Purchase_Price,Condition,Region,Source',
    'Charizard VMAX,https://www.pricecharting.com/game/pokemon-champion-path/charizard-vmax-74,25.00,Near mint or better,,Card Shop',
    'Pikachu V,,5.00,Lightly played,,eBay',
    'Sylveon GX 140,,16.39,Lightly played,,Card Shop',
    'Zekrom 114 (Pokemon Black & White),,45.00,Near mint or better,,Pack Opening',
  ].join('\n');

  const blob = new Blob([csv], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'pokemanager-import-template.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}


/* ── Browser notifications + global event stream ────────────────────────── */
function requestNotificationPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

function sendBrowserNotification(title, body, icon = '/static/icons/icon-192.png') {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    const n = new Notification(title, { body, icon, badge: icon, silent: false });
    setTimeout(() => n.close(), 8000);
  } catch {}
}

let _globalEventSource = null;
function startGlobalEventStream() {
  if (_globalEventSource) return;
  _globalEventSource = new EventSource('/api/sales/events');

  _globalEventSource.addEventListener('sale', e => {
    try {
      const d = JSON.parse(e.data);
      sendBrowserNotification(
        `PokeManager — Sale recorded!`,
        `${d.card_name} sold for £${(d.sell_price||0).toFixed(2)}  (+£${(d.profit||0).toFixed(2)} profit)`
      );
      toast(`💰 Sale: ${d.card_name} — £${(d.sell_price||0).toFixed(2)}`, 'success', 6000);
    } catch {}
  });

  _globalEventSource.addEventListener('error', () => {
    if (_globalEventSource?.readyState === EventSource.CLOSED) {
      _globalEventSource = null;
      setTimeout(startGlobalEventStream, 15000);
    }
  });
}

/* ── Price check ─────────────────────────────────────────────────────────── */
async function openPriceCheck(itemId) {
  toast('Checking prices…', 'info', 2000);
  try {
    const res = await api.get(`/pricing/check/${itemId}`);
    const c   = res.competitors;
    let msg = `Market: ${fmt(res.live_price)}`;
    if (c?.lowest)     msg += `  ·  eBay lowest: ${fmt(c.lowest)}`;
    if (c?.quick_sell) msg += `  ·  Quick sell: ${fmt(c.quick_sell)}`;
    toast(msg, 'info', 6000);
  } catch { toast('Price check failed', 'error'); }
}

async function refreshSinglePrice(itemId) {
  const btn = document.querySelector(`.inv-card[data-id="${itemId}"] .refresh-price-btn`);
  if (btn) { btn.textContent = '⏳'; btn.disabled = true; }

  try {
    const res = await api.post(`/pricing/refresh/${itemId}`, {});
    if (res.success) {
      const item = S.inventory.find(i => i.item_id === itemId);
      if (item) {
        item.live_price       = res.live_price;
        item.quick_price      = res.quick_price;
        item.potential_profit = res.potential_profit;
      }
      flashCard(itemId, 'success');
      const card = document.querySelector(`.inv-card[data-id="${itemId}"]`);
      if (card && item) {
        const newDiv = document.createElement('div');
        newDiv.innerHTML = renderInventoryCard(item);
        card.replaceWith(newDiv.firstElementChild);
        observeThumbs();
      }
      toast(`✅ Price updated: ${fmt(res.live_price)}`, 'success', 3000);
    } else {
      toast(`❌ ${res.error}`, 'error');
    }
  } catch (e) {
    toast('Price refresh failed: ' + extractError(e.message), 'error');
  } finally {
    if (btn) { btn.textContent = '🔄'; btn.disabled = false; }
  }
}

/* ── Analytics page ──────────────────────────────────────────────────────── */
function exportAccounting() {
  const year   = document.getElementById('hmrc-year')?.value   || '';
  const month  = document.getElementById('hmrc-month')?.value  || '';
  const format = document.getElementById('export-format')?.value || 'hmrc';
  const params = new URLSearchParams();
  if (year)  params.set('year',  year);
  if (month) params.set('month', month);
  window.location.href = `/api/analytics/export/${format}?${params.toString()}`;
}

function exportHMRC() { exportAccounting(); }

async function renderAnalytics() {
  document.getElementById('app').innerHTML = showPageLoader();
  const [summary, velocity, efficiency, forecast, bySource] = await Promise.all([
    api.get('/analytics/summary').catch(() => null),
    api.get('/analytics/velocity?group_by=set').catch(() => []),
    api.get('/analytics/efficiency').catch(() => ({ items: [] })),
    api.get('/analytics/forecast').catch(() => null),
    api.get('/analytics/by-source').catch(() => ({ sources: [] })),
  ]);
  const s   = summary  || {};
  const fc  = forecast || {};
  const src = bySource?.sources || [];

  const stat = (label, raw, cls = '') => {
    const isNum = typeof raw === 'number';
    return `<div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class="stat-value ${cls}" data-raw="${isNum ? raw : ''}">${isNum ? '0' : raw ?? '—'}</div>
    </div>`;
  };

  const nowY = new Date().getFullYear();
  const yearOptions = [nowY, nowY - 1, nowY - 2].map(y => `<option value="${y}">${y}</option>`).join('');
  const monthOptions = ['', '01','02','03','04','05','06','07','08','09','10','11','12']
    .map((m, i) => `<option value="${m}">${m ? new Date(0, i-1).toLocaleString('default',{month:'short'}) : 'All months'}</option>`).join('');

  const sourceRows = src.length === 0
    ? `<tr><td colspan="5" style="color:var(--text-muted);padding:12px">No sold items with source data yet</td></tr>`
    : src.map(r => `<tr>
        <td>${esc(r.source)}</td>
        <td>${r.count}</td>
        <td>${fmt(r.revenue)}</td>
        <td style="color:${r.profit >= 0 ? 'var(--success)' : 'var(--danger)'}">${fmt(r.profit)}</td>
        <td>${r.roi_pct}%</td>
      </tr>`).join('');

  document.getElementById('app').innerHTML = `
    <div class="page-header">
      <h1 class="page-title">Analytics</h1>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <select id="hmrc-year" class="form-input" style="width:auto;font-size:13px">
          <option value="">All years</option>${yearOptions}
        </select>
        <select id="hmrc-month" class="form-input" style="width:auto;font-size:13px">${monthOptions}</select>
        <select id="export-format" class="form-input" style="width:auto;font-size:13px">
          <option value="hmrc">HMRC Self-Assessment</option>
          <option value="xero">Xero</option>
          <option value="quickbooks">QuickBooks</option>
        </select>
        <button class="btn btn-sm btn-ghost" onclick="exportAccounting()">📥 Export</button>
      </div>
    </div>
    <div class="card-grid" style="margin-bottom:20px;grid-template-columns:repeat(auto-fill,minmax(170px,1fr))">
      ${stat('📦 Current Stock',    s.in_stock)}
      ${stat('💰 Lifetime Profit',  s.total_profit,                    (s.total_profit ?? 0) >= 0 ? 'stat-pos' : 'stat-neg')}
      ${stat('📈 Lifetime Revenue', s.total_revenue)}
      ${stat('🏷️ Cost in Stock',     s.total_cost_in_stock,             s.total_cost_in_stock > 0 ? 'stat-neg' : '')}
      ${stat('💎 Potential Value',  s.total_potential_in_stock)}
      ${stat('✨ Potential Profit', s.total_potential_profit_in_stock, (s.total_potential_profit_in_stock ?? 0) >= 0 ? 'stat-pos' : 'stat-neg')}
      ${stat('📊 Lifetime ROI %',    s.roi_pct,                         (s.roi_pct ?? 0) >= 0 ? 'stat-pos' : 'stat-neg')}
      ${stat('🎯 Avg Margin %',     s.avg_margin_pct)}
      ${stat('📅 MTD Profit',       s.mtd_profit,                      (s.mtd_profit ?? 0) >= 0 ? 'stat-pos' : 'stat-neg')}
      ${stat('⚡ 30d Est. Profit',  s.est_30d_profit,                  (s.est_30d_profit ?? 0) >= 0 ? 'stat-pos' : 'stat-neg')}
    </div>
    <div class="analytics-grid">
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="chart-card">
          <div class="chart-header">
            <span class="chart-title">Velocity</span>
            <div style="display:flex;gap:5px">
              <button class="filter-tab active" onclick="changeVelGroup('set',this)">Set</button>
              <button class="filter-tab" onclick="changeVelGroup('condition',this)">Condition</button>
              <button class="filter-tab" onclick="changeVelGroup('price',this)">Price</button>
            </div>
          </div>
          <div class="chart-scroll-wrapper"><div class="chart-wrap"><canvas id="velocity-chart"></canvas></div></div>
        </div>
        <div id="best-time-section"></div>
        <div class="chart-card">
          <div class="chart-header"><span class="chart-title">ROI by Source</span></div>
          <div style="overflow-x:auto">
            <table class="source-table">
              <thead><tr><th>Source</th><th>Items Sold</th><th>Revenue</th><th>Profit</th><th>ROI</th></tr></thead>
              <tbody>${sourceRows}</tbody>
            </table>
          </div>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="chart-card">
          <div class="chart-header"><span class="chart-title">Efficiency — Profit vs Days to Sell</span></div>
          <div class="chart-scroll-wrapper"><div class="chart-wrap-tall"><canvas id="efficiency-chart"></canvas></div></div>
          <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:10px;font-size:0.78rem;color:var(--text-muted)">
            <span style="color:var(--success)">● Fast &amp; profitable</span>
            <span style="color:var(--accent)">● Slow &amp; profitable</span>
            <span style="color:var(--warning)">● Fast &amp; cheap</span>
            <span style="color:var(--accent2)">● Avoid</span>
          </div>
        </div>
        <div id="concentration-section"></div>
        <div id="predictions-section"></div>
        <div id="restock-panel"></div>
      </div>
    </div>`;

  document.querySelectorAll('.stat-value[data-raw]').forEach(el => {
    const raw = el.dataset.raw;
    if (!raw) return;
    const n = parseFloat(raw);
    if (isNaN(n)) return;
    const label  = el.closest('.stat-card').querySelector('.stat-label').textContent;
    const hasPct = label.includes('%');
    const isCount = label.includes('Current Stock') || label.includes('Sold') || label.includes('Count');
    const prefix = hasPct ? '' : (isCount ? '' : '£');
    const suffix = hasPct ? '%' : '';
    animateCount(el, 0, n, 900, prefix, suffix);
  });

  S._velGroup = 'set';
  drawVelocityChart(Array.isArray(velocity) ? velocity : (velocity.items || velocity));
  drawEfficiencyChart(efficiency.items || [], efficiency.avg_days, efficiency.avg_profit);
  renderBestTimePanel();
  renderConcentrationChart();
  renderPredictions();
  renderRestockPanel();
}

async function changeVelGroup(group, btn) {
  document.querySelectorAll('.chart-header .filter-tab').forEach(b => b.classList.remove('active'));
  btn?.classList.add('active');
  S._velGroup = group;
  try {
    const data = await api.get(`/analytics/velocity?group_by=${group}`);
    drawVelocityChart(Array.isArray(data) ? data : (data.items || data));
  } catch {}
}

function drawVelocityChart(rows) {
  const canvas = document.getElementById('velocity-chart');
  if (!canvas) return;
  if (!rows?.length) {
    canvas.parentElement.innerHTML = emptyState('📊', 'No velocity data yet', 'Sell some cards to see which sets move fastest.');
    return;
  }
  const colors = rows.map(r => r.avg_days < 7 ? 'var(--success)' : r.avg_days <= 21 ? 'var(--warning)' : 'var(--danger)');
  S.charts.velocity = safeCreateChart('velocity-chart', {
    type: 'bar',
    data: {
      labels: rows.map(r => r.group),
      datasets: [{ label: 'Avg Days', data: rows.map(r => r.avg_days), backgroundColor: colors, borderRadius: 4 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: CHART_THEME.tick, maxRotation: window.innerWidth < 768 ? 45 : 35, minRotation: window.innerWidth < 768 ? 45 : 0, font: { size: window.innerWidth < 768 ? 9 : 11 } }, grid: { color: CHART_THEME.grid } },
        y: { ticks: { color: CHART_THEME.tick }, grid: { color: CHART_THEME.grid },
             title: { display: true, text: 'Days', color: CHART_THEME.tick } },
      },
    },
  });
}

function drawEfficiencyChart(items, avgDays, avgProfit) {
  const canvas = document.getElementById('efficiency-chart');
  if (!canvas) return;
  if (!items?.length) {
    canvas.parentElement.innerHTML = emptyState('📈', 'No efficiency data yet', 'Needs at least a few sold items with known buy & sell prices.');
    return;
  }
  const colorMap = {
    'Fast & profitable': CHART_THEME.success,
    'Slow & profitable': CHART_THEME.accent,
    'Fast & cheap':      CHART_THEME.warning,
    'Avoid':             CHART_THEME.danger,
  };
  S.charts.efficiency = safeCreateChart('efficiency-chart', {
    type: 'scatter',
    data: {
      datasets: Object.keys(colorMap).map(q => ({
        label: q,
        data: items.filter(i => i.quadrant === q).map(i => ({ x: i.days_to_sell, y: i.profit, label: i.card_name })),
        backgroundColor: colorMap[q] + 'cc', pointRadius: window.innerWidth < 768 ? 3 : 5, pointHoverRadius: window.innerWidth < 768 ? 5 : 7,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => `${ctx.raw.label || ''} — £${ctx.raw.y?.toFixed(2)}, ${ctx.raw.x}d` } },
      },
      scales: {
        x: { ticks: { color: CHART_THEME.tick, font: { size: window.innerWidth < 768 ? 9 : 11 } }, grid: { color: CHART_THEME.grid },
             title: { display: true, text: 'Days to Sell', color: CHART_THEME.tick } },
        y: { ticks: { color: CHART_THEME.tick, font: { size: window.innerWidth < 768 ? 9 : 11 } }, grid: { color: CHART_THEME.grid },
             title: { display: true, text: 'Profit (£)', color: CHART_THEME.tick } },
      },
    },
  });
}

/* ── Listings page ───────────────────────────────────────────────────────── */
async function renderListings() {
  document.getElementById('app').innerHTML = showPageLoader();
  if (!S.inventory.length) {
    const data = await api.get('/inventory').catch(() => ({ items: [] }));
    S.inventory = data.items;
  }
  buildListingsPage();
}

function buildListingsPage() {
  const listed   = S.inventory.filter(i => i.status === 'Inventory' && i.ebay_listed === 'Yes');
  const unlisted = S.inventory.filter(i => i.status === 'Inventory' && i.ebay_listed !== 'Yes')
                              .sort((a, b) => (b.potential_profit ?? 0) - (a.potential_profit ?? 0));

  const listedHtml = listed.length
    ? listed.map(i => `
      <div class="listed-card" data-id="${i.item_id}">
        <div class="listed-thumb card-thumb" data-item-id="${i.item_id}"><div class="thumb-spinner"></div></div>
        <div class="listed-info">
          <div class="listed-name">${esc(i.card_name || '')}</div>
          <div style="font-size:11px;color:var(--text-muted)">#${i.item_id}</div>
          <div class="listed-meta">
            <span class="price-tag">Listed: ${fmt(i.sell_price)}</span>
            <span class="price-tag" style="color:var(--accent)">Quick: ${fmt(i.quick_price)}</span>
          </div>
        </div>
        <div class="listed-actions">
          ${i.ebay_listing_id ? `<a href="https://www.ebay.co.uk/itm/${i.ebay_listing_id}" target="_blank" class="btn btn-ghost btn-sm">View ↗</a>` : ''}
          <button class="btn btn-sm btn-success" onclick="openSoldAndDelistModal(${i.item_id})">💰 Sold</button>
          <button class="btn btn-sm btn-ghost" onclick="openRepriceModal(${i.item_id})">Reprice</button>
          <button class="btn btn-sm btn-danger" onclick="doUnlist(${i.item_id},'${esc(i.card_name||'')}')">End Listing</button>
        </div>
      </div>`).join('')
    : emptyState('📋', 'No active eBay listings', 'List items from the "Not Listed" panel below.');

  const unlistedHtml = unlisted.length
    ? unlisted.map(i => `
      <div class="unlisted-card" data-id="${i.item_id}">
        <div class="unlisted-thumb card-thumb" data-item-id="${i.item_id}"><div class="thumb-spinner"></div></div>
        <div class="unlisted-info">
          <div class="unlisted-name">${esc(i.card_name || '')}</div>
          <div style="font-size:11px;color:var(--text-muted)">#${i.item_id}</div>
          <div class="unlisted-prices">
            <span class="price-tag">Market ${fmt(i.live_price)}</span>
            <span class="price-tag" style="color:var(--accent)">Quick ${fmt(i.quick_price)}</span>
          </div>
        </div>
        <button class="btn btn-accent btn-sm" onclick="openListingDrawer(${i.item_id})">List on eBay</button>
      </div>`).join('')
    : emptyState('✅', 'All items listed', 'Every card in your inventory has an active eBay listing.');

  const showListed = _listingsFilter === 'all' || _listingsFilter === 'listed';
  const showUnlisted = _listingsFilter === 'all' || _listingsFilter === 'unlisted';

  document.getElementById('app').innerHTML = `
    <div class="page-header"><h1 class="page-title">Listings</h1></div>
    <div style="display:flex;gap:8px;margin-bottom:16px">
      <button onclick="setListingsFilter('all')" id="filter-all" class="btn ${_listingsFilter==='all'?'btn-accent':'btn-ghost'} btn-sm">All</button>
      <button onclick="setListingsFilter('listed')" id="filter-listed" class="btn ${_listingsFilter==='listed'?'btn-accent':'btn-ghost'} btn-sm">Listed (${listed.length})</button>
      <button onclick="setListingsFilter('unlisted')" id="filter-unlisted" class="btn ${_listingsFilter==='unlisted'?'btn-accent':'btn-ghost'} btn-sm">Unlisted (${unlisted.length})</button>
    </div>
    <div class="listings-grid">
      ${showListed ? `
      <div class="listings-panel">
        <div class="panel-header"><span class="panel-title">Active eBay Listings (${listed.length})</span></div>
        ${listedHtml}
      </div>
      ` : ''}
      ${showUnlisted ? `
      <div class="listings-panel">
        <div class="panel-header"><span class="panel-title">Not Listed — by Potential Profit (${unlisted.length})</span></div>
        ${unlistedHtml}
      </div>
      ` : ''}
    </div>
    <div class="reprice-panel">
      <h2 style="font-size:1rem;font-weight:600;margin-bottom:14px">Reprice eBay Listings</h2>
      <div class="reprice-controls">
        <select id="reprice-strategy" class="form-input" style="width:auto">
          <option value="quicksell">Quick Sell</option>
          <option value="market">Market Price</option>
        </select>
        <label class="dry-run-toggle">
          <input type="checkbox" id="dry-run-cb" checked onchange="updateRepriceBtnLabel(${listed.length})" /> <span id="dry-run-label">Preview only</span>
        </label>
        <button class="btn btn-ghost" id="reprice-btn" onclick="runRepriceAll()">Preview Reprice (${listed.length})</button>
      </div>
      <div class="progress-bar-wrap hidden" id="reprice-progress-wrap">
        <div class="progress-bar" id="reprice-progress"></div>
      </div>
      <div id="reprice-results"></div>
    </div>`;

  observeThumbs();
  setTimeout(() => observeThumbs(), 300);
  connectWS();
}

function setListingsFilter(filter) {
  _listingsFilter = filter;
  buildListingsPage();
}

/* ── Listing drawer ──────────────────────────────────────────────────────── */
async function openListingDrawer(itemId) {
  const item = S.inventory.find(i => i.item_id === itemId)
             || await api.get(`/inventory/${itemId}`).catch(() => null);
  if (!item) { toast('Item not found', 'error'); return; }

  const liveP  = parseFloat(item.live_price  || 0);
  const quickP = parseFloat(item.quick_price || 0);
  const mktP15 = liveP ? (liveP * 1.15).toFixed(2) : '—';

  const drawer = document.getElementById('listing-drawer');
  drawer.innerHTML = `
    <div class="drawer-header">
      <h2>List on eBay</h2>
      <button class="btn-icon" onclick="closeDrawer()">✕</button>
    </div>
    <div class="drawer-body">
      <div class="drawer-card-preview">
        <div class="card-thumb" data-item-id="${itemId}"><div class="thumb-spinner"></div></div>
        <div>
          <h3>${esc(item.card_name || '')}</h3>
          <p class="text-muted" style="font-size:0.82rem">${esc(item.condition || '')}${item.region ? ' · ' + esc(item.region) : ''}</p>
        </div>
      </div>
      <div class="form-section">
        <label>Listing Title (max 80 chars)</label>
        <input id="listing-title" type="text" class="form-input" maxlength="80"
               value="${esc(item.card_name || '')} Pokemon TCG Card" />
        <span class="char-count"><span id="title-len">0</span>/80</span>
      </div>
      <div class="form-section">
        <label>Price Strategy</label>
        <div class="strategy-pills">
          <label class="strategy-pill"><input type="radio" name="listing-strategy" value="quicksell" checked>Quick Sell ${quickP ? fmt(quickP) : '—'}</label>
          <label class="strategy-pill"><input type="radio" name="listing-strategy" value="market">Market +15% £${mktP15}</label>
          <label class="strategy-pill"><input type="radio" name="listing-strategy" value="custom">Custom</label>
        </div>
        <input id="custom-price" type="number" class="form-input" step="0.01"
               placeholder="Custom price £" style="display:none;margin-top:8px" />
      </div>
      <div class="form-section">
        <label class="form-label">
          <input type="checkbox" id="use-promoted-listing" checked>
          Use Promoted Listing
        </label>
        <div id="promoted-listing-controls" style="display:block;margin-top:8px">
          <label class="form-label">Bid % (cost per sale)</label>
          <input id="promoted-listing-pct" type="number" class="form-input" step="0.1" min="0" max="100" value="0">
          <p style="color:var(--text-muted);font-size:12px;margin-top:4px">eBay charges this % of sale price to boost visibility</p>
        </div>
      </div>
      <div class="form-section">
        <label>Photos (drop or click to upload)</label>
        <div style="display:flex;gap:8px;margin-bottom:8px">
          <div class="photo-drop-zone" id="photo-drop" style="flex:1">📷 Drop photos here or click to select</div>
          <button class="btn btn-ghost btn-sm" onclick="openCamera()" title="Take photo with camera">📸 Camera</button>
        </div>
        <input type="file" id="photo-input" accept="image/*" multiple style="display:none" />
        <div class="photo-preview-grid" id="photo-preview"></div>
      </div>
      <div class="form-section">
        <label>Description</label>
        <div class="ai-loading" id="desc-loading">✨ Generating description…</div>
        <textarea id="listing-desc" class="form-input" rows="6" style="display:none"></textarea>
      </div>
      <div class="drawer-actions">
        <button class="btn btn-ghost" onclick="closeDrawer()">Cancel</button>
        <button class="btn btn-accent" id="list-submit-btn" onclick="submitListing(${itemId})">List on eBay →</button>
      </div>
    </div>`;

  drawer.classList.add('open');
  document.getElementById('drawer-overlay').classList.add('visible');

  // Load user's default promoted listing percentage
  const settings = await api.get('/settings').catch(() => ({}));
  const promotedPct = document.getElementById('promoted-listing-pct');
  if (promotedPct && settings.promoted_listing_pct !== undefined) {
    promotedPct.value = settings.promoted_listing_pct || 0;
  }

  const titleInput = document.getElementById('listing-title');
  const updateLen  = () => { document.getElementById('title-len').textContent = titleInput.value.length; };
  titleInput.addEventListener('input', updateLen);
  updateLen();

  // Toggle promoted listing controls visibility
  const usePromoted = document.getElementById('use-promoted-listing');
  const promotedControls = document.getElementById('promoted-listing-controls');
  if (usePromoted && promotedControls) {
    const updateVisibility = () => {
      promotedControls.style.display = usePromoted.checked ? 'block' : 'none';
    };
    usePromoted.addEventListener('change', updateVisibility);
    updateVisibility();
  }

  document.querySelectorAll('[name=listing-strategy]').forEach(r => {
    r.addEventListener('change', () => {
      document.getElementById('custom-price').style.display =
        document.querySelector('[name=listing-strategy]:checked').value === 'custom' ? 'block' : 'none';
    });
  });

  _wirePhotoDropZone();
  observeThumbs(drawer);
  generateListingDescription(item);
}

/* Shared by both the eBay and Vinted drawers — they never show at the same
   time, so both reuse the same #photo-drop/#photo-input/#photo-preview ids
   and the same _selectedPhotos state instead of keeping duplicate pickers. */
function _wirePhotoDropZone() {
  const dropZone  = document.getElementById('photo-drop');
  const fileInput = document.getElementById('photo-input');
  dropZone.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault(); dropZone.classList.remove('dragover');
    handlePhotoFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener('change', () => handlePhotoFiles(fileInput.files));
  _selectedPhotos = [];
}

function handlePhotoFiles(files) {
  _selectedPhotos = Array.from(files);
  const preview = document.getElementById('photo-preview');
  if (!preview) return;
  preview.innerHTML = '';
  _selectedPhotos.forEach((file, i) => {
    const reader = new FileReader();
    reader.onload = e => {
      const div = document.createElement('div');
      div.className = 'photo-thumb';
      div.innerHTML = `
        <img src="${e.target.result}" alt="photo ${i+1}">
        <button class="photo-remove" onclick="removePhoto(${i})">✕</button>
        ${i === 0 ? '<span class="photo-main-label">Main</span>' : ''}`;
      preview.appendChild(div);
    };
    reader.readAsDataURL(file);
  });
}

function removePhoto(idx) {
  _selectedPhotos.splice(idx, 1);
  handlePhotoFiles(_selectedPhotos);
}

async function generateListingDescription(item) {
  const loading  = document.getElementById('desc-loading');
  const textarea = document.getElementById('listing-desc');
  if (!loading || !textarea) return;
  try {
    const res = await api.post('/listings/generate-description', { item_id: item.item_id, condition: item.condition });
    textarea.value = res.description || `${item.card_name}\n${item.condition || ''}`;
    loading.style.display  = 'none';
    textarea.style.display = 'block';
  } catch {
    loading.textContent    = 'Write your own description:';
    textarea.style.display = 'block';
  }
}

async function submitListing(itemId) {
  const btn      = document.getElementById('list-submit-btn');
  const strategy = document.querySelector('[name=listing-strategy]:checked')?.value || 'quicksell';
  const title    = document.getElementById('listing-title')?.value || '';
  const desc     = document.getElementById('listing-desc')?.value  || '';
  const custP    = parseFloat(document.getElementById('custom-price')?.value || '0');
  const usePromoted = document.getElementById('use-promoted-listing')?.checked || false;
  const promotedPct = usePromoted ? (parseFloat(document.getElementById('promoted-listing-pct')?.value) || 0) : 0;
  if (_selectedPhotos.length === 0) { toast('Add at least one photo', 'error'); return; }
  btn.disabled = true; btn.textContent = '⏳ Listing…';
  const fd = new FormData();
  fd.append('item_id', itemId); fd.append('strategy', strategy);
  fd.append('title', title);    fd.append('description', desc);
  if (custP > 0) fd.append('custom_price', custP);
  if (promotedPct > 0) { fd.append('promoted_listing_pct', promotedPct); fd.append('use_promoted_listing', 'true'); }
  _selectedPhotos.slice(0, 5).forEach((f, i) => fd.append(`image${i+1}`, f));
  try {
    const res = await api.postForm('/listings/list-ebay', fd);
    if (res.success) {
      toast(`Listed for ${fmt(res.price)}!`, 'success');
      closeDrawer();
      const item = S.inventory.find(i => i.item_id === itemId);
      if (item) { item.ebay_listed = 'Yes'; item.sell_price = res.price; }
      buildListingsPage();
    } else {
      toast('Listing failed: ' + (res.error || 'unknown'), 'error');
      btn.disabled = false; btn.textContent = 'List on eBay →';
    }
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
    btn.disabled = false; btn.textContent = 'List on eBay →';
  }
}

async function doUnlist(itemId, name) {
  const ok = await confirmDialog('End eBay Listing',
    `End listing for "${name}"? It will be removed from eBay and marked as unlisted.`);
  if (!ok) return;
  try {
    const res  = await fetch(`/api/listings/delist/${itemId}`, { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      toast(data.warning ? `Ended (warning: ${data.warning})` : 'Listing ended',
            data.warning ? 'warning' : 'success');
      const item = S.inventory.find(i => i.item_id === itemId);
      if (item) { item.ebay_listed = ''; item.ebay_listing_id = ''; }
      buildListingsPage();
    } else { toast('Failed to end listing', 'error'); }
  } catch (e) { toast('Error: ' + extractError(e.message), 'error'); }
}

/* ── Reprice-all ─────────────────────────────────────────────────────────── */
let _ws = null;
function connectWS() {
  if (_ws && _ws.readyState < 2) return;
  try {
    _ws = new WebSocket(`ws://${location.host}/ws/updates`);
    _ws.onmessage = ev => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'reprice_progress') {
        const bar = document.getElementById('reprice-progress');
        if (bar) bar.style.width = Math.round((msg.current / msg.total) * 100) + '%';
      }
    };
  } catch {}
}

function updateRepriceBtnLabel(count) {
  const isDry = document.getElementById('dry-run-cb')?.checked;
  const btn   = document.getElementById('reprice-btn');
  const label = document.getElementById('dry-run-label');
  if (btn) {
    btn.textContent = isDry ? `Preview Reprice (${count})` : `Apply Reprice to ${count} Listings`;
    btn.className   = isDry ? 'btn btn-ghost' : 'btn btn-accent';
  }
  if (label) label.textContent = isDry ? 'Preview only' : 'Live — will update eBay';
}

async function runRepriceAll() {
  const strategy = document.getElementById('reprice-strategy').value;
  const dryRun   = document.getElementById('dry-run-cb').checked;
  const btn      = document.getElementById('reprice-btn');
  const wrap     = document.getElementById('reprice-progress-wrap');
  const bar      = document.getElementById('reprice-progress');
  const listed = S.inventory.filter(i => i.status === 'Inventory' && i.ebay_listed === 'Yes').length;
  btn.disabled = true; btn.textContent = '⏳ Processing…';
  wrap.classList.remove('hidden'); bar.style.width = '0%';
  try {
    const res = await api.post('/listings/reprice-all', { strategy, dry_run: dryRun });
    bar.style.width = '100%';
    renderRepriceResults(res);
    toast(dryRun ? 'Preview complete — review below'
                 : `Repriced ${res.items.filter(i=>i.applied).length} listings`,
          dryRun ? 'info' : 'success');
  } catch (e) { toast('Reprice failed: ' + extractError(e.message), 'error'); }
  finally {
    btn.disabled = false;
    updateRepriceBtnLabel(listed);
    setTimeout(() => wrap.classList.add('hidden'), 1500);
  }
}

function renderRepriceResults(res) {
  const el = document.getElementById('reprice-results');
  if (!el) return;
  const rows = res.items.map(r => {
    const dc = r.diff < 0 ? 'profit-neg' : r.diff > 0 ? 'profit-pos' : '';
    const ds = r.diff === 0 ? '±0.00' : (r.diff > 0 ? '+' : '') + fmt(r.diff);
    return `<div class="reprice-row">
      <span class="reprice-name">${esc(r.card_name)}</span>
      <span style="font-family:var(--mono);color:var(--text-muted)">${fmt(r.current_price)}</span>
      <span style="color:var(--text-muted)">→</span>
      <span style="font-family:var(--mono)">${fmt(r.new_price)}</span>
      <span class="${dc}">${ds}</span>
      ${!res.dry_run ? `<span class="badge ${r.applied ? 'badge-yes' : 'badge-danger'}">${r.applied ? 'Applied' : 'Failed'}</span>` : ''}
    </div>`;
  }).join('');
  el.innerHTML = `<p class="${res.dry_run ? 'text-muted' : ''}" style="margin-bottom:10px;font-size:0.82rem">
    ${res.dry_run ? '👁️ Preview — no changes made' : `✅ ${res.items.filter(i=>i.applied).length} listings updated live`} (${res.items.length} total)
  </p>${rows}`;
}

/* ── Watchlist page ──────────────────────────────────────────────────────── */
async function renderWatchlist() {
  document.getElementById('app').innerHTML = showPageLoader();
  const data = await api.get('/watchlist').catch(() => ({ entries: [] }));
  buildWatchlistPage(data.entries);
}

function buildWatchlistPage(entries) {
  const rows = entries.length ? entries.map(e => {
    const triggered = e.alert_sent || (e.current_price_gbp && e.target_price_gbp && e.current_price_gbp <= e.target_price_gbp);
    return `<tr class="${triggered ? 'watch-triggered' : ''}">
      <td style="font-size:11px;color:var(--text-muted)">#${e.id}</td>
      <td>${esc(e.card_name)}</td>
      <td style="font-family:var(--mono)">${fmt(e.target_price_gbp)}</td>
      <td style="font-family:var(--mono);${triggered ? 'color:var(--success);font-weight:600' : ''}">${fmt(e.current_price_gbp)}</td>
      <td>${e.alert_sent ? '<span class="badge badge-success">🔔 Triggered</span>' : '<span class="badge badge-muted">Watching</span>'}</td>
      <td>${e.added_date || '—'}</td>
      <td><button class="btn btn-icon btn-danger" onclick="removeWatch(${e.id})">🗑️</button></td>
    </tr>`;}).join('')
    : '';

  document.getElementById('app').innerHTML = `
    <div class="page-header">
      <h1 class="page-title">Watchlist</h1>
      <button class="btn btn-accent" onclick="openModal('modal-watch')">+ Add Watch</button>
    </div>
    ${entries.length ? `<div class="table-wrap">
      <table><thead><tr>
        <th style="position:static">ID</th><th style="position:static">Card</th><th style="position:static">Target</th><th style="position:static">Current</th><th style="position:static">Alert</th><th style="position:static">Added</th><th style="position:static"></th>
      </tr></thead><tbody>${rows}</tbody></table>
    </div>` : emptyState('👁️', 'No watched cards', 'Add a card to track its price against your target.')}`;
}

async function removeWatch(id) {
  if (!id || id === 'undefined') {
    toast('Error: could not find watch ID', 'error');
    return;
  }
  const ok = await confirmDialog('Remove Watch', 'Remove this watch entry?');
  if (!ok) return;
  try {
    await api.del(`/watchlist/${id}`);
    toast('Watch entry removed', 'success');
    renderWatchlist();
  } catch (e) { toast('Error: ' + (e.message || e.detail || 'Failed to delete'), 'error'); }
}

async function confirmWatch() {
  const name    = document.getElementById('watch-name').value.trim();
  const url     = document.getElementById('watch-url').value.trim();
  const target  = parseFloat(document.getElementById('watch-target').value);
  const current = parseFloat(document.getElementById('watch-current').value) || null;
  if (!name)                  { toast('Card name is required', 'error'); return; }
  if (!url)                   { toast('PC URL is required', 'error'); return; }
  if (!target || target <= 0) { toast('Target price is required', 'error'); return; }
  try {
    const res = await api.post('/watchlist', { card_name: name, pc_url: url, target_price: target, current_price: current });
    closeModal('modal-watch');
    toast(`Watch #${res.watch_id} added`, 'success');
    renderWatchlist();
  } catch (e) { toast('Error: ' + extractError(e.message), 'error'); }
}

/* ── Status bar ──────────────────────────────────────────────────────────── */
async function updateStatus() {
  try {
    const r = await fetch('/api/status');
    if (r.status === 401) return;  // not logged in yet — ignore
    const s = await r.json();
    if (!s) return;

    const statusEl = document.getElementById('status-text');
    const dotEl = document.querySelector('.status-dot');
    if (statusEl) statusEl.textContent = s.in_stock
      ? `${s.in_stock} in stock · ${s.sold} sold`
      : 'online';
    if (dotEl) dotEl.classList.remove('offline');
  } catch {
    const dotEl = document.querySelector('.status-dot');
    if (dotEl) dotEl.classList.add('offline');
  }
}

/* ── Sales page ──────────────────────────────────────────────────────────── */
let _salesEventSource = null;

async function renderSales() {
  document.getElementById('app').innerHTML = `
    <div class="sales-header">
      <h1 class="page-title">Sales Dashboard</h1>
      <div class="live-indicator"><span class="live-dot"></span> Live</div>
    </div>
    <div class="sales-ticker-row">
      <div class="ticker-card">
        <div class="ticker-label">Today's Sales</div>
        <div class="ticker-value" id="today-count">—</div>
      </div>
      <div class="ticker-card">
        <div class="ticker-label">Today's Revenue</div>
        <div class="ticker-value" id="today-revenue">—</div>
      </div>
      <div class="ticker-card profit">
        <div class="ticker-label">Today's Profit</div>
        <div class="ticker-value" id="today-profit">—</div>
      </div>
      <div class="ticker-card">
        <div class="ticker-label">This Month</div>
        <div class="ticker-value" id="month-profit">—</div>
      </div>
    </div>
    <div class="sales-chart-card">
      <h3>Last 7 Days</h3>
      <canvas id="week-chart" height="120"></canvas>
    </div>
    <div class="sales-chart-card" style="margin-top:16px">
      <h3>90-Day Profit Trend</h3>
      <div id="trend-chart-section" style="min-height:140px">${showPageLoader('Loading trend…')}</div>
    </div>
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);margin-top:16px;margin-bottom:20px;overflow:hidden">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border);flex-wrap:wrap;gap:10px">
        <span style="font-size:0.95rem;font-weight:600">Browse by Date</span>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <button class="btn btn-ghost btn-sm" onclick="changeSalesDate(-1)">← Prev</button>
          <input type="date" id="sales-date-picker" class="form-input"
                 style="width:150px"
                 value="${new Date().toISOString().slice(0,10)}"
                 onchange="loadSalesByDate(this.value)">
          <button class="btn btn-ghost btn-sm" onclick="changeSalesDate(1)">Next →</button>
          <button class="btn btn-ghost btn-sm" onclick="loadSalesByDate(new Date().toISOString().slice(0,10))">Today</button>
        </div>
      </div>
      <div id="sales-date-content"></div>
    </div>
  `;

  await loadSalesData();
  renderTrendChart();
  startSalesStream();
  loadSalesByDate(new Date().toISOString().slice(0,10));
}

async function loadSalesData() {
  try {
    const [todayData, weekData, monthData] = await Promise.all([
      api.get('/sales/today'),
      api.get('/sales/week'),
      api.get('/sales/month'),
    ]);

    const todayCountEl   = document.getElementById('today-count');
    const todayRevEl     = document.getElementById('today-revenue');
    const todayProfEl    = document.getElementById('today-profit');
    const monthProfEl    = document.getElementById('month-profit');

    if (todayCountEl) animateCount(todayCountEl, 0, todayData.count, 600, '', '');
    if (todayRevEl)   animateCount(todayRevEl, 0, todayData.revenue, 800, '£');
    if (todayProfEl)  animateCount(todayProfEl, 0, todayData.profit, 800, '£');
    if (monthProfEl)  animateCount(monthProfEl, 0, monthData.profit, 800, '£');

    renderWeekChart(weekData.days);
  } catch (e) {
    toast('Failed to load sales data', 'error');
  }
}

function renderWeekChart(days) {
  const canvas = document.getElementById('week-chart');
  if (!canvas) return;

  const ordered  = [...days].reverse();
  const labels   = ordered.map(d => new Date(d.date + 'T12:00:00').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric' }));
  const revenues = ordered.map(d => d.revenue);
  const profits  = ordered.map(d => d.profit);

  S.charts.weekChart = safeCreateChart('week-chart', {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Revenue', data: revenues, backgroundColor: 'rgba(108,99,255,0.3)',
          borderColor: 'rgba(108,99,255,0.8)', borderWidth: 2, borderRadius: 6 },
        { label: 'Profit',  data: profits,  backgroundColor: 'rgba(76,175,125,0.4)',
          borderColor: 'rgba(76,175,125,0.9)', borderWidth: 2, borderRadius: 6 },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: CHART_THEME.legend } } },
      scales: {
        x: { ticks: { color: CHART_THEME.tick }, grid: { color: CHART_THEME.grid } },
        y: { ticks: { color: CHART_THEME.tick, callback: v => '£' + v }, grid: { color: CHART_THEME.grid } },
      },
    },
  });
}

function renderTodaySalesList(sales) {
  const container = document.getElementById('today-sales-list');
  if (!container) return;
  if (!sales.length) {
    container.innerHTML = emptyState('📦', 'No sales today', 'Mark items as sold from the inventory page.');
    return;
  }
  container.innerHTML = sales.map(item => {
    const profit = (parseFloat(item.sell_price) || 0) - (parseFloat(item.purchase_price) || 0);
    const pCls   = profit >= 0 ? 'sale-profit-pos' : 'sale-profit-neg';
    return `
      <div class="sale-row">
        <div class="sale-thumb card-thumb" data-item-id="${item.item_id}"><div class="thumb-spinner"></div></div>
        <div class="sale-info">
          <div class="sale-name">${esc(item.card_name || '—')}</div>
          <div class="sale-meta">#${item.item_id} · ${esc(item.condition || 'NM')}</div>
        </div>
        <div class="sale-prices">
          <span class="sale-sold">${fmt(item.sell_price)}</span>
          <span class="sale-profit ${pCls}">${profit >= 0 ? '+' : ''}${fmt(profit)}</span>
        </div>
      </div>`;
  }).join('');
  observeThumbs(container);
}

function startSalesStream() {
  if (_salesEventSource) { _salesEventSource.close(); _salesEventSource = null; }
  _salesEventSource = new EventSource('/api/sales/stream');
  _salesEventSource.onmessage = e => {
    const data = JSON.parse(e.data);
    const ce = document.getElementById('today-count');
    const re = document.getElementById('today-revenue');
    const pe = document.getElementById('today-profit');
    if (ce) ce.textContent = data.count;
    if (re) re.textContent = fmt(data.revenue);
    if (pe) pe.textContent = fmt(data.profit);
  };
  _salesEventSource.onerror = () => {
    _salesEventSource?.close();
    _salesEventSource = null;
  };
}

async function changeSalesDate(direction) {
  const picker = document.getElementById('sales-date-picker');
  if (!picker) return;
  const current = new Date(picker.value + 'T12:00:00');
  current.setDate(current.getDate() + direction);
  const newDate = current.toISOString().slice(0, 10);
  picker.value = newDate;
  loadSalesByDate(newDate);
}

async function loadSalesByDate(date) {
    const container = document.getElementById('sales-date-content');
    if (!container) return;
    container.innerHTML = '<div class="page-loader"><div class="spinner"></div></div>';
    const data = await fetch(`/api/sales/by-date?date=${date}`)
        .then(r => r.json())
        .catch(() => ({ sales: [], count: 0, summary: {} }));
    if (data.count === 0) {
        container.innerHTML = '<div style="text-align:center;padding:32px 20px;color:var(--text-muted)"><div style="font-size:32px;margin-bottom:8px">📦</div><p>No sales on ' + date + '</p></div>';
        return;
    }
    const s = data.summary;
    const profitColor = s.total_profit >= 0 ? 'var(--success)' : 'var(--danger)';
    const profitVal = (s.total_profit >= 0 ? '+' : '') + fmt(s.total_profit);
    const roiColor = s.avg_roi >= 0 ? 'var(--success)' : 'var(--danger)';

    const rows = data.sales.map(function(sale) {
        const pc = sale.profit >= 0 ? 'var(--success)' : 'var(--danger)';
        const rc = sale.roi_pct >= 0 ? 'var(--success)' : 'var(--danger)';
        const link = sale.ebay_listing_id ? ' &middot; <a href="https://www.ebay.co.uk/itm/' + sale.ebay_listing_id + '" target="_blank" style="color:var(--accent)">eBay ↗</a>' : '';
        return '<tr style="border-bottom:1px solid var(--border)">' +
            '<td style="padding:10px 12px;vertical-align:middle">' +
                '<div style="display:flex;align-items:center;gap:10px">' +
                    '<div class="sale-thumb card-thumb" data-item-id="' + sale.item_id + '" style="width:36px;height:36px;flex-shrink:0;border-radius:4px;overflow:hidden;background:var(--surface2)"><div class="thumb-spinner"></div></div>' +
                    '<div style="min-width:0">' +
                        '<div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(sale.card_name) + '</div>' +
                        '<div style="font-size:12px;color:var(--text-muted);margin-top:2px">#' + sale.item_id + link + '</div>' +
                    '</div>' +
                '</div>' +
            '</td>' +
            '<td style="text-align:right;padding:10px 12px;font-family:monospace">' + fmt(sale.purchase_price) + '</td>' +
            '<td style="text-align:right;padding:10px 12px;font-family:monospace">' + fmt(sale.sell_price) + '</td>' +
            '<td style="text-align:right;padding:10px 12px;font-family:monospace;color:var(--danger)">−' + fmt(sale.ebay_fee) + '</td>' +
            '<td style="text-align:right;padding:10px 12px;font-family:monospace;color:var(--danger)">−' + fmt(sale.postage_cost) + '</td>' +
            '<td style="text-align:right;padding:10px 12px;font-family:monospace">' + fmt(sale.net_received) + '</td>' +
            '<td style="text-align:right;padding:10px 12px;font-family:monospace;font-weight:700;color:' + pc + '">' + (sale.profit >= 0 ? '+' : '') + fmt(sale.profit) + '</td>' +
            '<td style="text-align:right;padding:10px 12px;color:' + rc + '">' + sale.roi_pct + '%</td>' +
        '</tr>';
    }).join('');

    const html =
        '<table style="width:100%;table-layout:fixed;border-collapse:collapse;font-size:13px">' +
            '<thead>' +
                '<tr style="background:var(--surface);border-bottom:1px solid var(--border)">' +
                    '<th style="text-align:left;padding:7px 12px;color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;width:30%;position:static">Card</th>' +
                    '<th style="text-align:right;padding:7px 12px;color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;width:10%;position:static">Bought</th>' +
                    '<th style="text-align:right;padding:7px 12px;color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;width:10%;position:static">Sold</th>' +
                    '<th style="text-align:right;padding:7px 12px;color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;width:10%;position:static">eBay Fee</th>' +
                    '<th style="text-align:right;padding:7px 12px;color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;width:10%;position:static">Postage</th>' +
                    '<th style="text-align:right;padding:7px 12px;color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;width:10%;position:static">Net</th>' +
                    '<th style="text-align:right;padding:7px 12px;color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;width:10%;position:static">Profit</th>' +
                    '<th style="text-align:right;padding:7px 12px;color:var(--text-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;width:10%;position:static">ROI</th>' +
                '</tr>' +
            '</thead>' +
            '<tbody>' + rows + '</tbody>' +
            '<tfoot>' +
                '<tr style="background:var(--surface2);border-top:2px solid var(--border)">' +
                    '<th style="text-align:left;padding:11px 12px;font-weight:700;font-size:13px;width:30%">' + data.count + ' sale' + (data.count !== 1 ? 's' : '') + '</th>' +
                    '<th style="text-align:right;padding:11px 12px;font-family:monospace;font-weight:700;width:10%">' + fmt(s.total_cost) + '</th>' +
                    '<th style="text-align:right;padding:11px 12px;font-family:monospace;font-weight:700;width:10%">' + fmt(s.total_revenue) + '</th>' +
                    '<th style="text-align:right;padding:11px 12px;font-family:monospace;font-weight:700;color:var(--danger);width:10%">−' + fmt(s.total_fees) + '</th>' +
                    '<th style="text-align:right;padding:11px 12px;font-family:monospace;font-weight:700;color:var(--danger);width:10%">−' + fmt(s.total_postage) + '</th>' +
                    '<th style="text-align:right;padding:11px 12px;font-family:monospace;font-weight:700;width:10%">' + fmt(s.total_net) + '</th>' +
                    '<th style="text-align:right;padding:11px 12px;font-family:monospace;font-weight:700;color:' + profitColor + ';width:10%">' + profitVal + '</th>' +
                    '<th style="text-align:right;padding:11px 12px;font-weight:700;color:' + roiColor + ';width:10%">' + s.avg_roi + '%</th>' +
                '</tr>' +
            '</tfoot>' +
        '</table>';

    container.innerHTML = '<div style="overflow-x:auto;width:100%;box-sizing:border-box;position:relative">' + html + '</div>';
    observeThumbs(container);
}

/* ── Calculator page ─────────────────────────────────────────────────────── */
function renderCalculator() {
  document.getElementById('app').innerHTML = `
    <div class="calculator-page">
      <div class="page-header">
        <h1 class="page-title">Buying Calculator</h1>
      </div>
      <p class="text-muted" style="margin-bottom:16px">Paste an eBay listing URL to see if it's worth buying.</p>
      <div class="calc-input-card">
        <div class="calc-url-row">
          <input type="url" id="calc-url" class="form-input calc-url-input"
                 placeholder="https://www.ebay.co.uk/itm/... or Vinted URL" />
          <input type="number" id="calc-price" class="form-input calc-price-input"
                 placeholder="Override price £" step="0.01" min="0" />
          <button class="btn btn-accent" onclick="runCalculator()">Analyse →</button>
        </div>
        <p class="calc-hint">Price is scraped automatically — only enter an override if the scraper misses it.</p>
      </div>
      <div id="calc-result" style="display:none"></div>
      <div id="calc-history"></div>
    </div>`;

  document.getElementById('calc-url').addEventListener('keydown', e => {
    if (e.key === 'Enter') runCalculator();
  });
  renderCalcHistory();
}

async function runCalculator(pcUrlOverride) {
  const url           = document.getElementById('calc-url').value.trim();
  const priceOverride = parseFloat(document.getElementById('calc-price').value) || 0;
  if (!url) { toast('Enter a listing URL', 'warning'); return; }

  const resultDiv = document.getElementById('calc-result');
  resultDiv.style.display = 'block';
  resultDiv.innerHTML = showPageLoader('Analysing listing…');

  try {
    const body = { url, asking_price: priceOverride || null };
    if (pcUrlOverride) body.pc_url_override = pcUrlOverride;
    const data = await api.post('/calculator/analyse', body);
    renderCalcResult(data);
    saveCalcHistory(data);
  } catch (e) {
    resultDiv.innerHTML = `<div class="calc-error-msg">Analysis failed: ${extractError(e.message)}</div>`;
  }
}

async function rerunWithPcUrl() {
  const pcUrl = document.getElementById('pc-url-override')?.value.trim();
  if (!pcUrl) { toast('Enter a PriceCharting URL', 'warning'); return; }
  await runCalculator(pcUrl);
}

const VERDICT_CFG = {
  strong_buy: { label: '🚀 Strong Buy', color: 'var(--success)', bg: 'rgba(76,175,125,0.08)' },
  buy:        { label: '✅ Buy',         color: 'var(--success)', bg: 'rgba(76,175,125,0.05)' },
  marginal:   { label: '⚠️ Marginal',   color: 'var(--warning)', bg: 'rgba(255,169,77,0.08)' },
  pass:       { label: '❌ Pass',        color: 'var(--danger)',  bg: 'rgba(255,107,107,0.08)' },
};

function renderCalcResult(data) {
  const resultDiv = document.getElementById('calc-result');
  const v = VERDICT_CFG[data.verdict] || { label: '—', color: 'var(--text-muted)', bg: 'transparent' };
  const net = data.market_price ? (data.market_price * 0.8765 - 1.5) : null;

  resultDiv.style.display = 'block';
  resultDiv.innerHTML = `
    <div class="calc-result-card" style="border-color:${v.color};background:${v.bg}">
      <div class="calc-verdict" style="color:${v.color}">${v.label}</div>
      <div class="calc-title">${esc(data.listing_title || 'Unknown card')}</div>
      ${data.pc_name ? `<div class="calc-pc-name">Matched: <a href="${esc(data.pc_url)}" target="_blank">${esc(data.pc_name)}</a></div>` : ''}
      ${data.match_confidence === 'low' ? `<div class="calc-warning">⚠️ Card match is unverified — check the PriceCharting link is correct before buying</div>` : ''}
      ${data.error ? `<div class="calc-error" style="color:var(--warning);font-size:0.82rem;margin:8px 0">⚠️ ${esc(data.error)}</div>` : ''}
      <div class="calc-pc-override">
        <input type="url" id="pc-url-override" class="form-input" style="font-size:0.78rem;padding:6px 10px"
               placeholder="Paste correct PriceCharting URL if match is wrong"
               value="${esc(data.pc_url || '')}" />
        <button class="btn btn-sm btn-ghost" onclick="rerunWithPcUrl()">Re-analyse →</button>
      </div>
      <div class="calc-numbers">
        <div class="calc-num-row">
          <span>Asking price</span><strong>${fmt(data.asking_price)}</strong>
        </div>
        <div class="calc-num-row">
          <span>PriceCharting market</span><strong>${data.market_price ? fmt(data.market_price) : '—'}</strong>
        </div>
        <div class="calc-num-row">
          <span>After eBay fees + postage</span><strong>${net != null ? fmt(net) : '—'}</strong>
        </div>
        <div class="calc-num-row highlight">
          <span>Expected profit</span>
          <strong style="color:${(data.expected_profit ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'}">
            ${data.expected_profit != null ? fmt(data.expected_profit) : '—'}
          </strong>
        </div>
        <div class="calc-num-row">
          <span>ROI</span><strong>${data.roi_pct != null ? data.roi_pct + '%' : '—'}</strong>
        </div>
        ${data.avg_days_to_sell != null ? `
        <div class="calc-num-row">
          <span>Avg days to sell (${data.similar_sold} similar sold)</span>
          <strong>${data.avg_days_to_sell}d</strong>
        </div>` : ''}
      </div>
      <div class="calc-actions" style="display:flex;gap:8px;margin-top:10px">
        <a href="${esc(data.url)}" target="_blank" class="btn btn-ghost btn-sm">View Listing ↗</a>
        ${data.pc_url ? `<a href="${esc(data.pc_url)}" target="_blank" class="btn btn-ghost btn-sm">PriceCharting ↗</a>` : ''}
      </div>
    </div>`;
}

function saveCalcHistory(data) {
  const history = JSON.parse(localStorage.getItem('calc_history') || '[]');
  history.unshift({ ...data, analysed_at: new Date().toISOString() });
  localStorage.setItem('calc_history', JSON.stringify(history.slice(0, 10)));
  renderCalcHistory();
}

function clearCalcHistory() {
  localStorage.removeItem('calc_history');
  renderCalcHistory();
}

function deleteCalcHistoryEntry(idx) {
  const history = JSON.parse(localStorage.getItem('calc_history') || '[]');
  history.splice(idx, 1);
  localStorage.setItem('calc_history', JSON.stringify(history));
  renderCalcHistory();
}

function renderCalcHistory() {
  const history   = JSON.parse(localStorage.getItem('calc_history') || '[]');
  const container = document.getElementById('calc-history');
  if (!container) return;
  if (!history.length) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin:20px 0 10px">
      <h3 style="font-size:0.95rem;margin:0">Recent lookups</h3>
      <button class="btn btn-ghost btn-sm" onclick="clearCalcHistory()">Clear all</button>
    </div>
    ${history.map((d, i) => `
      <div class="calc-history-row">
        <span class="calc-hist-name" onclick="document.getElementById('calc-url').value='${esc(d.url)}';runCalculator()" style="cursor:pointer;flex:1">${esc(d.listing_title || d.url.slice(0, 50))}</span>
        <span class="calc-hist-price">${fmt(d.asking_price)}</span>
        <span class="calc-hist-profit" style="color:${(d.expected_profit ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)'}">
          ${d.expected_profit != null ? ((d.expected_profit >= 0 ? '+' : '') + fmt(d.expected_profit)) : '?'}
        </span>
        <button class="btn btn-icon btn-sm" onclick="deleteCalcHistoryEntry(${i})" title="Remove">✕</button>
      </div>`).join('')}`;
}

/* ── Sparklines ──────────────────────────────────────────────────────────── */
function drawSparkline(canvas, data, trend) {
  const prices = data.map(d => d.live_price_gbp).filter(p => p != null);
  if (prices.length < 2) return;

  const ctx   = canvas.getContext('2d');
  const w     = canvas.width;
  const h     = canvas.height;
  const min   = Math.min(...prices);
  const max   = Math.max(...prices);
  const range = max - min || 1;
  const color = trend === 'rising' ? CHART_THEME.success : trend === 'falling' ? CHART_THEME.danger : CHART_THEME.accent;

  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = color;
  ctx.lineWidth   = 1.5;
  ctx.beginPath();
  prices.forEach((p, i) => {
    const x = (i / (prices.length - 1)) * w;
    const y = h - ((p - min) / range) * (h - 4) - 2;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
  ctx.fillStyle = color + '22';
  ctx.fill();
}

const sparklineObserver = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    const card   = entry.target;
    const itemId = card.dataset.id;
    if (card.dataset.sparklineLoaded) return;
    card.dataset.sparklineLoaded = '1';
    sparklineObserver.unobserve(card);

    fetch(`/api/price-history/summary/${itemId}`)
      .then(r => r.json())
      .then(data => {
        const canvas  = card.querySelector('.sparkline-canvas');
        const trendEl = card.querySelector('.price-trend');
        if (canvas && data.history?.length > 1) {
          drawSparkline(canvas, data.history, data.trend);
        }
        if (trendEl && data.change_30d != null && data.change_30d !== 0) {
          const sign  = data.change_30d > 0 ? '↑' : '↓';
          const color = data.change_30d > 0 ? 'var(--success)' : 'var(--danger)';
          trendEl.innerHTML = `<span style="color:${color}">${sign}${Math.abs(data.change_30d)}% 30d</span>`;
        } else if (trendEl) {
          trendEl.textContent = '';
        }
      })
      .catch(() => {});
  });
}, { rootMargin: '100px' });

/* ── Restock panel ───────────────────────────────────────────────────────── */
async function renderRestockPanel() {
  const container = document.getElementById('restock-panel');
  if (!container) return;
  try {
    const data = await api.get('/analytics/restock');
    if (!data.suggestions.length) return;

    container.innerHTML = `
      <div class="chart-card" style="width:100%;max-width:100%;overflow:hidden;box-sizing:border-box">
        <div class="chart-header">
          <span class="chart-title">🔄 Restock Suggestions</span>
          <span class="text-muted" style="font-size:0.8rem">Fast-selling sets where you're low on stock</span>
        </div>
        <div style="overflow-y:auto;max-height:350px;-webkit-overflow-scrolling:touch;width:100%;box-sizing:border-box">
          <div class="restock-cards">
            ${data.suggestions.slice(0, 6).map(s => `
              <div class="restock-card">
                <div class="restock-set">${esc(s.set)}</div>
                <div class="restock-stats">
                  <span>⚡ ${s.avg_days}d avg sell</span>
                  <span>💰 £${s.avg_profit.toFixed(2)} avg profit</span>
                  <span class="${s.current_stock < 2 ? 'restock-critical' : ''}">📦 ${s.current_stock} in stock</span>
                  <span class="text-muted">${s.total_sold} sold total</span>
                </div>
              </div>`).join('')}
          </div>
        </div>
      </div>`;
  } catch {}
}

/* ── Bulk selection (Feature 3) ──────────────────────────────────────────── */
S.selection = new Set();

function toggleSelectItem(itemId) {
  // Called from onchange — read the checkbox state directly to avoid any desync
  const cb = document.querySelector(`.inv-card[data-id="${itemId}"] .bulk-cb`);
  const checked = cb ? cb.checked : !S.selection.has(itemId);
  if (checked) S.selection.add(itemId);
  else S.selection.delete(itemId);
  const card = document.querySelector(`.inv-card[data-id="${itemId}"]`);
  if (card) card.classList.toggle('is-selected', checked);
  updateBulkToolbar();
}

function toggleSelectAll() {
  const allVisible = _currentItems.map(i => i.item_id);
  const allSelected = allVisible.every(id => S.selection.has(id));
  if (allSelected) allVisible.forEach(id => S.selection.delete(id));
  else             allVisible.forEach(id => S.selection.add(id));
  document.querySelectorAll('.inv-card[data-id]').forEach(card => {
    const id = parseInt(card.dataset.id);
    card.classList.toggle('is-selected', S.selection.has(id));
  });
  updateBulkToolbar();
}

function clearSelection() {
  S.selection.clear();
  document.querySelectorAll('.inv-card.is-selected').forEach(c => c.classList.remove('is-selected'));
  updateBulkToolbar();
}

function updateBulkToolbar() {
  const toolbar = document.getElementById('bulk-toolbar');
  if (!toolbar) return;
  const count = S.selection.size;
  if (count === 0) { toolbar.classList.add('hidden'); return; }
  toolbar.classList.remove('hidden');
  const countEl = toolbar.querySelector('.bulk-count');
  if (countEl) countEl.textContent = `${count} selected`;
}

async function bulkUpdatePrices() {
  const ids = Array.from(S.selection);
  if (!ids.length) return;
  toast(`⏳ Refreshing prices for ${ids.length} items… this may take a minute`, 'info', 60000);
  try {
    const res = await api.post('/pricing/refresh-bulk', { item_ids: ids });
    toast(`✅ Updated ${res.updated}/${res.total} prices${res.failed ? `, ${res.failed} failed` : ''}`, 'success');
    const data = await api.get('/inventory');
    S.inventory = data.items;
    refreshInventoryGrid();
    clearSelection();
  } catch (e) { toast('Bulk update failed: ' + extractError(e.message), 'error'); }
}

async function refreshAllPrices() {
  const inStock = S.inventory.filter(i => i.status === 'Inventory').length;
  const ok = await confirmDialog(
    'Refresh All Prices',
    `Refresh prices for all ${inStock} in-stock items from PriceCharting? This will take several minutes.`
  );
  if (!ok) return;

  const btn = document.getElementById('refresh-all-prices-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Refreshing…'; }

  toast(`⏳ Refreshing all ${inStock} prices… check back in a few minutes`, 'info', 300000);

  try {
    const res = await api.post('/pricing/refresh-bulk', { item_ids: [] });
    if (res.success) {
      toast(`✅ ${res.updated} prices updated, ${res.failed || 0} failed`, 'success', 8000);
      const data = await api.get('/inventory');
      S.inventory = data.items;
      refreshInventoryGrid();
    } else {
      toast('Refresh all failed', 'error');
    }
  } catch (e) {
    toast('Refresh all failed: ' + extractError(e.message), 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Refresh All Prices'; }
  }
}

function bulkExport() {
  const ids = Array.from(S.selection);
  if (!ids.length) return;
  window.location.href = `/api/analytics/export/selected?item_ids=${ids.join(',')}`;
}

async function bulkRemove() {
  const ids = Array.from(S.selection);
  if (!ids.length) return;
  const ok = await confirmDialog('Remove Items', `Remove ${ids.length} selected item(s) permanently?`);
  if (!ok) return;
  let removed = 0;
  for (const id of ids) {
    try {
      await api.del(`/inventory/${id}`);
      S.inventory = S.inventory.filter(i => i.item_id !== id);
      removed++;
    } catch {}
  }
  clearSelection();
  refreshInventoryGrid();
  toast(`Removed ${removed} item(s)`, 'success');
}

/* ── Single-item reprice modal (Feature 4) ───────────────────────────────── */
function openRepriceModal(itemId) {
  const item = S.inventory.find(i => i.item_id === itemId);
  if (!item) return;
  const quick = parseFloat(item.quick_price || 0);
  const live  = parseFloat(item.live_price || 0);
  showModal(`
    <h2 style="margin-bottom:6px">Reprice</h2>
    <p class="text-muted" style="margin-bottom:16px">${esc(item.card_name || '')}</p>
    <div class="form-section">
      <label class="form-label">Strategy</label>
      <div class="strategy-pills">
        <label class="strategy-pill"><input type="radio" name="rp-strategy" value="quicksell" checked> Quick Sell ${quick ? fmt(quick) : '—'}</label>
        <label class="strategy-pill"><input type="radio" name="rp-strategy" value="market"> Market +15% ${live ? fmt(live * 1.15) : '—'}</label>
        <label class="strategy-pill"><input type="radio" name="rp-strategy" value="custom"> Custom</label>
      </div>
      <input id="rp-custom-price" type="number" class="form-input" step="0.01" placeholder="Custom price £" style="display:none;margin-top:8px">
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="applyReprice(${itemId})">Apply Reprice</button>
    </div>`);
  document.querySelectorAll('[name=rp-strategy]').forEach(r =>
    r.addEventListener('change', () => {
      document.getElementById('rp-custom-price').style.display =
        document.querySelector('[name=rp-strategy]:checked')?.value === 'custom' ? 'block' : 'none';
    })
  );
}

async function applyReprice(itemId) {
  const strategy  = document.querySelector('[name=rp-strategy]:checked')?.value || 'quicksell';
  const customPrc = parseFloat(document.getElementById('rp-custom-price')?.value || 0);
  const btn = document.querySelector('#modal-overlay .btn-accent');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Repricing…'; }
  try {
    const res = await api.post(`/listings/reprice/${itemId}`, { strategy, price: customPrc });
    if (res.success) {
      toast(`Repriced to ${fmt(res.new_price)}`, 'success');
      const item = S.inventory.find(i => i.item_id === itemId);
      if (item) item.sell_price = res.new_price;
      closeModal();
      buildListingsPage();
    } else {
      toast('Reprice failed: ' + (res.error || 'unknown'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Apply Reprice'; }
    }
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Apply Reprice'; }
  }
}

/* ── 90-day trend chart (Feature 5) ──────────────────────────────────────── */
async function renderTrendChart() {
  const container = document.getElementById('trend-chart-section');
  if (!container) return;
  try {
    const data = await api.get('/sales/trend?days=90');
    const days = data.days;
    if (!days?.length) {
      container.innerHTML = emptyState('📉', 'No trend data', 'Need sales over multiple days to build a trend.');
      return;
    }

    container.innerHTML = `<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;width:100%"><div style="min-width:500px"><canvas id="trend-chart" height="120"></canvas></div></div>`;
    const canvas = document.getElementById('trend-chart');

    S.charts.trend = safeCreateChart('trend-chart', {
      type: 'line',
      data: {
        labels: days.map(d => d.date.slice(5)),
        datasets: [
          { label: 'Daily Profit', data: days.map(d => d.profit),
            borderColor: 'rgba(76,175,125,0.4)', backgroundColor: 'rgba(76,175,125,0.08)',
            borderWidth: 1, pointRadius: 0, fill: true },
          { label: '7-day Avg', data: days.map(d => d.rolling_avg),
            borderColor: CHART_THEME.success, backgroundColor: 'transparent',
            borderWidth: 2.5, pointRadius: 0, tension: 0.4 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: CHART_THEME.legend, boxWidth: 12, font: { size: window.innerWidth < 768 ? 9 : 11 } } } },
        scales: {
          x: { ticks: { color: CHART_THEME.tick, maxTicksLimit: window.innerWidth < 768 ? 6 : 10, maxRotation: 0, font: { size: window.innerWidth < 768 ? 8 : 11 } }, grid: { color: CHART_THEME.grid } },
          y: { ticks: { color: CHART_THEME.tick, callback: v => '£' + v, font: { size: window.innerWidth < 768 ? 9 : 11 } }, grid: { color: CHART_THEME.grid } },
        },
      },
    });
  } catch {}
}

/* ── Best time to list (Feature 6) ──────────────────────────────────────── */
async function renderBestTimePanel() {
  const container = document.getElementById('best-time-section');
  if (!container) return;
  try {
    const data = await api.get('/analytics/best-time');
    if (!data?.by_day) return;

    container.innerHTML = `
      <div class="chart-card">
        <div class="chart-header">
          <span class="chart-title">Best Days to Sell</span>
          <span class="text-muted" style="font-size:0.8rem">${esc(data.recommendation || '')}</span>
        </div>
        <div class="chart-scroll-wrapper"><div class="chart-wrap"><canvas id="best-time-chart"></canvas></div></div>
      </div>`;

    const canvas = document.getElementById('best-time-chart');
    const colors = data.by_day.map(d =>
      d.day === data.best_day ? 'var(--success)' : 'rgba(108,99,255,0.6)'
    );
    S.charts.bestTime = safeCreateChart('best-time-chart', {
      type: 'bar',
      data: {
        labels: data.by_day.map(d => d.day),
        datasets: [{ label: 'Sales', data: data.by_day.map(d => d.count),
          backgroundColor: colors, borderRadius: 4 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: ctx => {
            const d = data.by_day[ctx.dataIndex];
            return [`Sales: ${d.count}`, `Avg profit: ${fmt(d.avg_profit)}`];
          }}} },
        scales: {
          x: { ticks: { color: CHART_THEME.tick, font: { size: window.innerWidth < 768 ? 9 : 11 } }, grid: { color: CHART_THEME.grid } },
          y: { ticks: { color: CHART_THEME.tick, font: { size: window.innerWidth < 768 ? 9 : 11 } }, grid: { color: CHART_THEME.grid },
               title: { display: true, text: 'Sales', color: CHART_THEME.tick } },
        },
      },
    });
  } catch {}
}

/* ── Portfolio concentration (Feature 7) ─────────────────────────────────── */
async function renderConcentrationChart() {
  const container = document.getElementById('concentration-section');
  if (!container) return;
  try {
    const data = await api.get('/analytics/concentration');
    if (!data?.sets?.length) {
      container.innerHTML = `<div class="chart-card"><div class="chart-header"><span class="chart-title">Portfolio by Set</span></div>${emptyState('🍩', 'No concentration data', 'Add inventory items to see portfolio breakdown.')}</div>`;
      return;
    }

    const top = data.sets.slice(0, 8);
    const highRisk = data.sets.filter(s => s.risk === 'high');

    container.innerHTML = `
      <div class="chart-card">
        <div class="chart-header">
          <span class="chart-title">Portfolio by Set</span>
          <span class="text-muted" style="font-size:0.8rem">Total value: ${fmt(data.total_value)}</span>
        </div>
        ${highRisk.length ? `<div class="risk-alert">⚠️ High concentration: ${highRisk.map(s => s.set).join(', ')} (&gt;30% each)</div>` : ''}
        <div style="overflow-x:auto;-webkit-overflow-scrolling:touch;width:100%">
          <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;min-width:500px">
            <div style="width:200px;height:200px;flex-shrink:0"><canvas id="concentration-chart"></canvas></div>
          <div style="flex:1;min-width:180px">
            ${top.map(s => `<div class="conc-row">
              <span class="conc-set">${esc(s.set)}</span>
              <span class="conc-pct" style="color:${s.risk==='high'?'var(--danger)':s.risk==='medium'?'var(--warning)':'var(--text-muted)'}">${s.pct}%</span>
              <span class="conc-val text-muted">${fmt(s.value)}</span>
            </div>`).join('')}
          </div>
        </div>
        </div>
      </div>`;

    const canvas = document.getElementById('concentration-chart');
    const palette = [CHART_THEME.accent, CHART_THEME.success, CHART_THEME.warning, CHART_THEME.danger,
      '#74c0fc','#f8a5c2','#a29bfe','#55efc4'];
    S.charts.concentration = safeCreateChart('concentration-chart', {
      type: 'doughnut',
      data: {
        labels: top.map(s => s.set),
        datasets: [{ data: top.map(s => s.pct),
          backgroundColor: palette, borderColor: CHART_THEME.grid, borderWidth: 2 }],
      },
      options: {
        responsive: true, maintainAspectRatio: true,
        plugins: { legend: { display: false, labels: { font: { size: window.innerWidth < 768 ? 9 : 11 } } },
          tooltip: { callbacks: { label: ctx => `${ctx.label}: ${ctx.parsed}%` }} },
        cutout: '60%',
      },
    });
  } catch {}
}

/* ── Price predictions (Feature 13) ─────────────────────────────────────── */
async function renderPredictions() {
  const container = document.getElementById('predictions-section');
  if (!container) return;
  try {
    const data = await api.get('/analytics/predictions');
    if (!data) return;

    const cardHtml = (items, trend) => items.length
      ? items.map(p => {
          const chg = p.weekly_change;
          const col = trend === 'rising' ? 'var(--success)' : 'var(--danger)';
          return `<div class="pred-row">
            <div class="pred-name">${esc(p.card_name)}</div>
            <div class="pred-prices">
              <span class="text-muted">${fmt(p.current_price)}</span>
              <span style="color:${col}">${trend === 'rising' ? '↑' : '↓'} ${Math.abs(chg)}%/wk</span>
              <span>${fmt(p.predicted_30d)} in 30d</span>
            </div>
          </div>`;
        }).join('')
      : `<p class="text-muted" style="padding:12px 0">Not enough price history yet.</p>`;

    container.innerHTML = `
      <div class="chart-card" style="width:100%;max-width:100%;overflow:hidden;box-sizing:border-box">
        <div class="chart-header"><span class="chart-title" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Price Predictions (Linear Regression)</span>
          <span class="text-muted" style="font-size:0.8rem">${data.total_analysed} cards analysed</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;width:100%;box-sizing:border-box;overflow-x:auto;-webkit-overflow-scrolling:touch">
          <div style="min-width:0;overflow:hidden">
            <div style="color:var(--success);font-weight:600;margin-bottom:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Rising ↑</div>
            ${cardHtml(data.rising || [], 'rising')}
          </div>
          <div style="min-width:0;overflow:hidden">
            <div style="color:var(--danger);font-weight:600;margin-bottom:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">Falling ↓</div>
            ${cardHtml(data.falling || [], 'falling')}
          </div>
        </div>
      </div>`;
  } catch {}
}

/* ── Camera capture (Feature 12) ────────────────────────────────────────── */
let _cameraStream = null;

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    toast('Camera not supported on this device/browser', 'warning');
    return;
  }
  const modal = document.getElementById('camera-modal');
  if (!modal) return;
  modal.classList.add('open');
  try {
    _cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } }
    });
    const video = document.getElementById('camera-video');
    if (video) { video.srcObject = _cameraStream; video.play(); }
  } catch (e) {
    toast('Cannot access camera: ' + e.message, 'error');
    closeCamera();
  }
}

function capturePhoto() {
  const video  = document.getElementById('camera-video');
  const canvas = document.getElementById('camera-canvas');
  if (!video || !canvas) return;
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  canvas.toBlob(blob => {
    const file = new File([blob], `capture_${Date.now()}.jpg`, { type: 'image/jpeg' });
    _selectedPhotos.push(file);
    handlePhotoFiles(_selectedPhotos);
    closeCamera();
    toast('Photo captured', 'success');
  }, 'image/jpeg', 0.9);
}

function closeCamera() {
  if (_cameraStream) {
    _cameraStream.getTracks().forEach(t => t.stop());
    _cameraStream = null;
  }
  const modal = document.getElementById('camera-modal');
  if (modal) modal.classList.remove('open');
}

/* ── Settings page ───────────────────────────────────────────────────────── */
async function renderSettings() {
  const app = document.getElementById('app');
  app.innerHTML = showPageLoader('Loading settings…');

  const [settings, me, inv] = await Promise.all([
    api.get('/settings').catch(() => null),
    api.get('/auth/me').catch(() => null),
    api.get('/inventory').catch(() => null),
  ]);
  const user = me?.user || null;
  if (inv) S.inventory = inv.items;

  const checklist = [
    { done: true,                   label: 'Account created' },
    { done: !!settings?.has_ebay,   label: 'eBay API connected', scrollTo: 'ebay' },
    { done: !!settings?.has_gemini, label: 'Gemini AI connected (optional)', scrollTo: 'integrations' },
    { done: S.inventory.length > 0, label: 'First card added', navTo: '/' },
  ];
  const allDone = checklist.every(c => c.done);

  const checklistHtml = allDone ? '' : `
    <div class="settings-checklist">
      <h4 style="margin-bottom:10px;font-size:14px">⚡ Setup checklist</h4>
      ${checklist.map(c => `
        <div class="checklist-item ${c.done ? 'done' : 'todo'}"
             ${!c.done && c.navTo ? `onclick="navigate('${c.navTo}')"` : ''}
             ${!c.done && c.scrollTo ? `onclick="document.querySelector('[data-section=${c.scrollTo}]')?.scrollIntoView({behavior:'smooth',block:'center'})"` : ''}>
          <span>${c.done ? '✅' : '⬜'}</span>
          <span>${c.label}</span>
        </div>
      `).join('')}
    </div>
  `;

  app.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">Settings</h1>
    </div>
    ${checklistHtml}

    <div class="settings-grid">
      <!-- Account -->
      <div class="settings-card">
        <h3 class="settings-section-title">Account</h3>
        <div class="form-section">
          <label class="form-label">Display Name</label>
          <input id="s-display-name" class="form-input" value="${esc(settings?.display_name || '')}">
        </div>
        <div class="form-section">
          <label class="form-label">Email</label>
          <input class="form-input" value="${esc(user?.email || '')}" disabled>
        </div>
        <div class="form-section">
          <label class="form-label">Plan</label>
          <div style="display:flex;align-items:center;gap:12px">
            ${(() => {
              const planNames = {
                free:       '🎒 Trainer',
                gym_leader: '🏅 Gym Leader',
                champion:   '🏆 Champion',
              };
              const planColors = {
                free:       'var(--text-muted)',
                gym_leader: 'var(--accent)',
                champion:   '#ffa94d',
              };
              const userPlan = user?.role === 'admin' ? 'admin' : (settings?.plan || 'free');
              if (user?.role === 'admin') {
                return `<span class="badge" style="background:rgba(255,107,107,0.2);color:#ff6b6b">⚡ Admin</span>`;
              }
              return `<span style="font-size:15px;font-weight:700;color:${planColors[userPlan]}">${planNames[userPlan]}</span>`;
            })()}
            ${user?.role === 'admin'
              ? `<span class="text-muted" style="font-size:12px">Full access</span>`
              : settings?.plan === 'free'
              ? `<button class="btn btn-accent btn-xs" style="padding:4px 12px;font-size:12px" onclick="navigate('/upgrade')">Upgrade →</button>`
              : `<button class="btn btn-ghost btn-xs" style="padding:4px 12px;font-size:12px" onclick="manageBilling()">Manage billing</button>`
            }
          </div>
        </div>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:16px">
          <button class="btn btn-accent" onclick="saveAccountSettings()">Save Settings</button>
          <button class="btn btn-ghost" onclick="confirmLogout()">Sign Out</button>
        </div>
      </div>

      <!-- Your Plan Features -->
      <div class="settings-card">
        <h3 class="settings-section-title">Your Plan Features</h3>
        <div style="display:flex;flex-direction:column;gap:10px;font-size:13px">
          ${[
            ['Inventory (up to 50 items)', true],
            ['Price tracking & analytics', true],
            ['Buying calculator', true],
            ['Unlimited items', canAccess('unlimited_items')],
            ['eBay listing', canAccess('ebay_listing')],
            ['AI descriptions (your API key)', canAccess('ai_descriptions')],
            ['AI descriptions (we provide API key)', canAccess('ai_descriptions_managed')],
            ['Accounting exports', canAccess('export_accounting')],
            ['Priority support', canAccess('priority_support')],
          ].map(([label, has]) => `
            <div style="display:flex;align-items:center;gap:10px">
              <span style="min-width:20px">${has ? '✅' : '🔒'}</span>
              <span style="color:${has ? 'var(--text)' : 'var(--text-muted)'};flex:1">${label}</span>
              ${!has ? `<a href="#" onclick="navigate('/upgrade'); return false" style="color:var(--accent);font-size:12px;white-space:nowrap">Upgrade</a>` : ''}
            </div>
          `).join('')}
        </div>
      </div>

      <!-- eBay API -->
      <div class="settings-card" data-section="ebay">
        <h3 class="settings-section-title">eBay API
          <span class="badge ${settings?.has_ebay ? 'badge-ebay' : 'badge-danger'}" style="margin-left:8px">
            ${settings?.has_ebay ? '✓ Connected' : 'Not set'}
          </span>
        </h3>
        <p class="text-muted" style="font-size:13px;margin-bottom:14px">
          Required for auto-listing and sale detection.
          <a href="https://developer.ebay.com" target="_blank" style="color:var(--accent)">Get keys ↗</a>
        </p>
        <div class="form-section">
          <label class="form-label">App ID</label>
          <input id="s-ebay-app-id" class="form-input" type="password" placeholder="${settings?.has_ebay ? '••••••• (set)' : 'Enter App ID'}">
        </div>
        <div class="form-section">
          <label class="form-label">Cert ID</label>
          <input id="s-ebay-cert-id" class="form-input" type="password" placeholder="${settings?.has_ebay ? '••••••• (set)' : 'Enter Cert ID'}">
        </div>
        <div class="form-section">
          <label class="form-label">Refresh Token</label>
          <input id="s-ebay-token" class="form-input" type="password" placeholder="${settings?.has_ebay ? '••••••• (set)' : 'Run generate_ebay_token.py'}">
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-accent btn-sm" onclick="saveEbaySettings()">Save eBay Keys</button>
          ${settings?.has_ebay ? `<button class="btn btn-ghost btn-sm" onclick="syncEbaySales()">🔄 Sync Sales Now</button>` : ''}
        </div>
      </div>

      <!-- Pricing settings -->
      <div class="settings-card">
        <h3 class="settings-section-title">Pricing</h3>
        <div class="form-section">
          <label class="form-label">eBay Fee Rate (%)</label>
          <input id="s-fee-rate" class="form-input" type="number" step="0.001"
                 value="${((settings?.ebay_fee_rate ?? 0.1235) * 100).toFixed(2)}">
        </div>
        <div class="form-section">
          <label class="form-label">Postage Cost (£)</label>
          <input id="s-postage" class="form-input" type="number" step="0.01"
                 value="${settings?.postage_cost ?? 0.00}">
          <p style="color:var(--text-muted);font-size:12px;margin-top:4px">eBay Simple Delivery — buyer pays shipping, set to £0.00</p>
        </div>
        <div class="form-section">
          <label class="form-label">Default Promoted Listing (%)</label>
          <input id="s-promoted-listing" class="form-input" type="number" step="0.1" min="0" max="100"
                 value="${settings?.promoted_listing_pct ?? 0}">
          <p style="color:var(--text-muted);font-size:12px;margin-top:4px">Applied to all new eBay listings. Set to 0 to disable promotions by default.</p>
        </div>
        <div class="form-section">
          <label class="form-label">Korean Price Multiplier</label>
          <input id="s-korean" class="form-input" type="number" step="0.01"
                 value="${settings?.korean_multiplier ?? 0.7}">
        </div>
        <div class="form-section">
          <label class="form-label">
            <input type="checkbox" id="s-auto-sync" ${settings?.auto_sync_ebay ? 'checked' : ''}>
            Auto-sync prices to eBay listings
          </label>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-accent btn-sm" onclick="savePricingSettings()">Save Pricing</button>
          <button class="btn btn-ghost btn-sm" onclick="applyPromotionToAll()">Apply to all listings</button>
        </div>
      </div>

      <!-- Integrations -->
      <div class="settings-card" data-section="integrations">
        <h3 class="settings-section-title">Integrations</h3>
        ${(() => {
          const plan = settings?.plan || 'free';
          if (plan === 'champion') {
            return `
              <div class="form-section">
                <label class="form-label">AI Descriptions</label>
                <div style="background:rgba(76,175,125,0.08);border:1px solid rgba(76,175,125,0.2);border-radius:8px;padding:12px;margin-bottom:12px">
                  <div style="display:flex;align-items:center;gap:8px;color:var(--success)">
                    <span>✅</span>
                    <span>AI descriptions powered by PokeManager — no setup needed</span>
                  </div>
                </div>
                <p class="text-muted" style="font-size:12px;margin-bottom:12px">Or bring your own Gemini API key to use instead:</p>
                <input id="s-gemini" class="form-input" type="password"
                       placeholder="${settings?.has_gemini ? '••••••• (optional override)' : 'Leave blank to use PokeManager key'}">
              </div>
            `;
          } else if (plan === 'gym_leader') {
            return `
              <div class="form-section">
                <label class="form-label">Gemini API Key (AI listing descriptions)</label>
                <p class="text-muted" style="font-size:12px;margin-bottom:8px">Get a free key at <a href="https://ai.google.dev" target="_blank" style="color:var(--accent)">ai.google.dev ↗</a></p>
                <input id="s-gemini" class="form-input" type="password"
                       placeholder="${settings?.has_gemini ? '••••••• (set)' : 'Paste your Gemini API key'}">
              </div>
            `;
          } else {
            return `
              <div class="form-section">
                <label class="form-label">AI Descriptions</label>
                <div style="background:rgba(200,200,200,0.1);border:1px solid var(--border);border-radius:8px;padding:12px">
                  <div style="display:flex;align-items:center;gap:8px;color:var(--text-muted)">
                    <span>🔒</span>
                    <span>Available on Gym Leader and Champion plans</span>
                  </div>
                  <button class="btn btn-accent btn-xs" style="margin-top:8px" onclick="navigate('/upgrade')">Upgrade →</button>
                </div>
              </div>
            `;
          }
        })()}
        <div class="form-section">
          <label class="form-label">Discord Webhook URL</label>
          <p class="text-muted" style="font-size:12px;margin-bottom:8px">
            Paste a Discord webhook URL to get notified of important events.
            <a href="https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks" target="_blank" style="color:var(--accent)">Create one ↗</a>
          </p>
          <input id="s-discord" class="form-input" type="password"
                 placeholder="${settings?.has_discord ? '••••••• (set)' : 'Optional — leave blank to disable'}">
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-accent btn-sm" onclick="saveIntegrationSettings()">Save</button>
          ${settings?.has_discord ? `<button class="btn btn-ghost btn-sm" onclick="testDiscordWebhook()">Test</button>` : ''}
        </div>
      </div>

    </div>
  `;
}

async function saveAccountSettings() {
  const name = document.getElementById('s-display-name')?.value.trim();
  try {
    const resp = await api.patch('/settings', { display_name: name });
    if (resp.success) toast('Account updated', 'success');
    else toast('Failed: ' + resp.error, 'error');
  } catch (e) {
    toast('Failed: ' + extractError(e.message), 'error');
  }
}

async function saveEbaySettings() {
  const updates = {};
  const appId  = document.getElementById('s-ebay-app-id')?.value.trim();
  const certId = document.getElementById('s-ebay-cert-id')?.value.trim();
  const token  = document.getElementById('s-ebay-token')?.value.trim();
  if (appId)  updates.ebay_app_id = appId;
  if (certId) updates.ebay_cert_id = certId;
  if (token)  updates.ebay_refresh_token = token;
  if (!Object.keys(updates).length) { toast('No changes', 'info'); return; }
  try {
    const resp = await api.patch('/settings', updates);
    if (resp.success) { toast('eBay keys saved', 'success'); renderSettings(); }
    else toast('Failed: ' + resp.error, 'error');
  } catch (e) {
    toast('Failed: ' + extractError(e.message), 'error');
  }
}

async function savePricingSettings() {
  const feeRate  = parseFloat(document.getElementById('s-fee-rate')?.value) / 100;
  const postage  = parseFloat(document.getElementById('s-postage')?.value);
  const promoted = parseFloat(document.getElementById('s-promoted-listing')?.value) || 0;
  const korean   = parseFloat(document.getElementById('s-korean')?.value);
  const autoSync = document.getElementById('s-auto-sync')?.checked;
  try {
    const resp = await api.patch('/settings', {
      ebay_fee_rate: feeRate, postage_cost: postage, promoted_listing_pct: promoted,
      korean_price_multiplier: korean, auto_sync_ebay_prices: autoSync,
    });
    if (resp.success) toast('Pricing saved', 'success');
    else toast('Failed: ' + resp.error, 'error');
  } catch (e) {
    toast('Failed: ' + extractError(e.message), 'error');
  }
}

async function applyPromotionToAll() {
  const promoted = parseFloat(document.getElementById('s-promoted-listing')?.value) || 0;
  if (promoted <= 0) { toast('Set a promotion % greater than 0', 'warning'); return; }
  const ok = await confirmDialog('Apply Promotion to All Listings',
    `Update all your active eBay listings to use ${promoted}% promotion?`);
  if (!ok) return;
  const btn = event?.target;
  if (btn) btn.disabled = true;
  try {
    toast('Updating all listings...', 'info');
    const resp = await api.post('/listings/apply-promotion-all', {});
    if (resp.updated > 0 || resp.failed === 0) {
      toast(`✅ Updated ${resp.updated} of ${resp.total} listings to ${resp.promotion_pct}%`, 'success');
    } else {
      toast(`Updated ${resp.updated}, failed ${resp.failed}`, resp.failed > 0 ? 'warning' : 'success');
    }
    if (resp.errors?.length > 0) {
      console.warn('Promotion errors:', resp.errors);
    }
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function saveIntegrationSettings() {
  const updates = {};
  const gemini  = document.getElementById('s-gemini')?.value.trim();
  const discord = document.getElementById('s-discord')?.value.trim();
  if (gemini)  updates.gemini_api_key = gemini;
  if (discord) updates.discord_webhook_url = discord;
  if (!Object.keys(updates).length) { toast('No changes', 'info'); return; }
  try {
    const resp = await api.patch('/settings', updates);
    if (resp.success) { toast('Integration keys saved', 'success'); renderSettings(); }
    else toast('Failed: ' + resp.error, 'error');
  } catch (e) {
    toast('Failed: ' + extractError(e.message), 'error');
  }
}

async function testDiscordWebhook() {
  try {
    const resp = await api.post('/settings/test-discord', {});
    if (resp.success) {
      toast('✅ Test notification sent to Discord', 'success');
    } else {
      toast('Failed: ' + resp.error, 'error');
    }
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
  }
}

async function syncEbaySales() {
  try {
    const btn = event?.target;
    if (btn) btn.disabled = true;
    toast('🔄 Syncing eBay sales...', 'info');
    const resp = await api.post('/ebay/sync-sales', {});
    if (resp.success) {
      const msg = `✅ Synced: ${resp.synced}, Skipped: ${resp.skipped}, Errors: ${resp.errors}`;
      toast(msg, 'success');
    } else {
      toast('Failed: ' + resp.error, 'error');
    }
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
  } finally {
    if (event?.target) event.target.disabled = false;
  }
}

async function confirmLogout() {
  const ok = await confirmDialog('Sign Out', 'Are you sure you want to sign out?');
  if (!ok) return;
  await api.post('/auth/logout', {});
  window.location.href = '/login';
}

async function updateNavUser() {
  const data = await api.get('/auth/me').catch(() => null);
  if (data?.authenticated) {
    S.user = data.user;
    S.plan = data.user.role === 'admin' ? 'champion' : (data.user.plan || 'free');
    const el = document.getElementById('nav-user');
    if (el) el.textContent = data.user.display_name || data.user.email;
  }
}

/* ── Upgrade page ────────────────────────────────────────────────────────── */
async function renderUpgrade() {
  const meResp = await api.get('/auth/me').catch(() => null);
  const plan = meResp?.user?.plan || 'free';
  const success   = new URLSearchParams(location.search).get('success');
  const cancelled = new URLSearchParams(location.search).get('cancelled');

  const planNames = { free: 'Trainer', gym_leader: 'Gym Leader', champion: 'Champion' };

  document.getElementById('app').innerHTML = `
    <div class="upgrade-page">
      ${success ? `<div class="upgrade-banner success">🎉 Welcome to ${planNames[plan] || 'your new plan'}! Your plan is now active.</div>` : ''}
      ${cancelled ? `<div class="upgrade-banner warning">Upgrade cancelled — you're still on the ${planNames[plan]} plan.</div>` : ''}

      <div class="upgrade-hero">
        <h1>Choose your plan</h1>
        <p class="text-muted">Manage your Pokémon TCG collection like a professional</p>
      </div>

      <div class="plans-grid">
        <!-- Trainer (Free) -->
        <div class="plan-card ${plan === 'free' ? 'plan-current' : ''}">
          <div class="plan-badge">🎒 Trainer</div>
          <div class="plan-price">Free</div>
          <div class="plan-desc">Perfect for getting started</div>
          <ul class="plan-features">
            <li>✅ Up to 50 items</li>
            <li>✅ Price tracking</li>
            <li>✅ Analytics dashboard</li>
            <li>✅ Buying calculator</li>
            <li>❌ eBay listing</li>
            <li>❌ Accounting exports</li>
            <li>❌ AI descriptions</li>
          </ul>
          ${plan === 'free'
            ? `<div class="plan-current-badge">Current plan</div>`
            : `<button class="btn btn-ghost" onclick="navigate('/settings')">Manage</button>`
          }
        </div>

        <!-- Gym Leader -->
        <div class="plan-card plan-featured ${plan === 'gym_leader' ? 'plan-current' : ''}">
          <div class="plan-popular">Most Popular</div>
          <div class="plan-badge">🏅 Gym Leader</div>
          <div class="plan-price">£7.99<span>/month</span></div>
          <div style="font-size:12px;color:var(--success);font-weight:600">✨ 7 days free, then £7.99/month</div>
          <div class="plan-desc">For serious resellers</div>
          <ul class="plan-features">
            <li>✅ Unlimited items</li>
            <li>✅ Everything in Trainer</li>
            <li>✅ eBay listing (your API keys)</li>
            <li>✅ AI descriptions (your Gemini key)</li>
            <li>✅ 📷 Scan & Add (your Gemini key)</li>
            <li>✅ 📷 Scan & Sell (your Gemini key)</li>
            <li>✅ Price history & sparklines</li>
            <li>✅ HMRC / Xero / QuickBooks export</li>
          </ul>
          ${plan === 'gym_leader'
            ? `<div class="plan-current-badge">Current plan</div>
               <button class="btn btn-ghost btn-sm" onclick="manageBilling()">Manage billing</button>`
            : plan === 'champion'
            ? `<button class="btn btn-ghost" onclick="manageBilling()">Downgrade</button>`
            : `<button class="btn btn-accent" onclick="startCheckout('gym_leader')">Start 7-day free trial →</button>`
          }
        </div>

        <!-- Champion -->
        <div class="plan-card plan-elite ${plan === 'champion' ? 'plan-current' : ''}">
          <div class="plan-badge">🏆 Champion</div>
          <div class="plan-price">£14.99<span>/month</span></div>
          <div style="font-size:12px;color:var(--success);font-weight:600">✨ 7 days free, then £14.99/month</div>
          <div class="plan-desc">The complete solution</div>
          <ul class="plan-features">
            <li>✅ Everything in Gym Leader</li>
            <li>✅ AI descriptions — no API key needed (we cover it)</li>
            <li>✅ 📷 Scan & Add — no API key needed</li>
            <li>✅ 📷 Scan & Sell — no API key needed</li>
            <li>✅ Priority support</li>
            <li>✅ Early access to new features</li>
          </ul>
          ${plan === 'champion'
            ? `<div class="plan-current-badge">Current plan</div>
               <button class="btn btn-ghost btn-sm" onclick="manageBilling()">Manage billing</button>`
            : `<button class="btn btn-accent plan-elite-btn" onclick="startCheckout('champion')">Start 7-day free trial →</button>`
          }
        </div>
      </div>

      <!-- eBay API setup guide for Gym Leader -->
      <div class="setup-guide">
        <h2>🔧 Setting up your eBay API keys (Gym Leader)</h2>
        <p class="text-muted">Gym Leader users bring their own eBay Developer API keys — it's free and takes about 10 minutes.</p>
        <div class="guide-steps">
          <div class="guide-step">
            <div class="guide-num">1</div>
            <div>
              <strong>Create a free eBay Developer account</strong>
              <p>Visit <a href="https://developer.ebay.com" target="_blank">developer.ebay.com</a> and sign in with your eBay account.</p>
            </div>
          </div>
          <div class="guide-step">
            <div class="guide-num">2</div>
            <div>
              <strong>Create a production application</strong>
              <p>Go to "My Account" → "Application Keys" → "Create a Keyset" → select "Production".</p>
            </div>
          </div>
          <div class="guide-step">
            <div class="guide-num">3</div>
            <div>
              <strong>Generate your OAuth token</strong>
              <p>Download and run the token generator from your PokeManager Settings page. It opens a browser, you log in to eBay, and it saves your token automatically.</p>
            </div>
          </div>
          <div class="guide-step">
            <div class="guide-num">4</div>
            <div>
              <strong>Set up your Business Policies</strong>
              <p>On eBay UK, go to Account → Business Policies and create a payment, postage, and return policy. Paste the IDs into PokeManager Settings.</p>
            </div>
          </div>
          <div class="guide-step">
            <div class="guide-num">5</div>
            <div>
              <strong>Paste keys into Settings</strong>
              <p>Go to <a href="#" onclick="navigate('/settings');return false">Settings → eBay API</a> and enter your App ID, Cert ID, and Refresh Token.</p>
            </div>
          </div>
        </div>
      </div>

      <div class="upgrade-faq">
        <h2>Frequently asked questions</h2>
        <div class="faq-item">
          <strong>Can I cancel anytime?</strong>
          <p>Yes — cancel anytime from Settings → Billing. You keep access until the end of your billing period.</p>
        </div>
        <div class="faq-item">
          <strong>What happens to my data if I downgrade?</strong>
          <p>Your inventory is never deleted. If you have more than 50 items, you can view them but not add new ones until you upgrade again.</p>
        </div>
        <div class="faq-item">
          <strong>Is my payment information secure?</strong>
          <p>All payments are processed by Stripe — we never see or store your card details.</p>
        </div>
        <div class="faq-item">
          <strong>Do you offer refunds?</strong>
          <p>By starting your subscription you waive your 14-day cooling-off right as the service begins immediately. Refunds are not offered, but you can cancel at any time.</p>
        </div>
        <div class="faq-item">
          <strong>Is there a free trial?</strong>
          <p>Yes — both Gym Leader and Champion come with a 7-day free trial. You won't be charged until the trial ends. Cancel anytime before then and you won't pay anything.</p>
        </div>
      </div>
    </div>
  `;

  // Clear URL params after showing banners
  if (success || cancelled) {
    history.replaceState(null, '', '/upgrade');
  }
}

async function startCheckout(plan) {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '⏳ Loading…';
  try {
    const resp = await api.post('/billing/create-checkout', { plan });
    if (resp.checkout_url) {
      window.location.href = resp.checkout_url;
    } else {
      toast('Could not start checkout — try again', 'error');
      btn.disabled = false;
      btn.textContent = 'Start 7-day free trial →';
    }
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
    btn.disabled = false;
  }
}

async function manageBilling() {
  window.location.href = '/api/billing/portal';
}

/* ── Admin Dashboard ──────────────────────────────────────────────────────── */
async function renderAdmin() {
  if (S.user?.role !== 'admin') { navigate('/'); return; }

  document.getElementById('app').innerHTML = `
    <div class="page-header">
      <h1 class="page-title">⚡ Admin Dashboard</h1>
      <div style="display:flex;gap:8px">
        <button class="btn btn-ghost btn-sm" onclick="renderAdmin()">🔄 Refresh</button>
      </div>
    </div>
    <div id="admin-content">
      <div class="page-loader"><div class="spinner"></div></div>
    </div>
  `;

  const [overview, revenue] = await Promise.all([
    fetch('/api/admin/overview').then(r => r.json()).catch(() => null),
    fetch('/api/admin/revenue').then(r => r.json()).catch(() => null),
  ]);

  const o = overview || {};
  const u = o.users || {};
  const rev = o.revenue || {};

  document.getElementById('admin-content').innerHTML = `
    <!-- KPI row -->
    <div class="admin-kpis">
      <div class="kpi-card">
        <div class="kpi-label">Total Users</div>
        <div class="kpi-value">${u.total || 0}</div>
        <div class="kpi-sub">+${u.new_this_month || 0} this month</div>
      </div>
      <div class="kpi-card kpi-revenue">
        <div class="kpi-label">MRR</div>
        <div class="kpi-value">£${rev.mrr?.toFixed(2) || '0.00'}</div>
        <div class="kpi-sub">ARR £${rev.arr?.toFixed(2) || '0.00'}</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Paying Users</div>
        <div class="kpi-value">${u.paying || 0}</div>
        <div class="kpi-sub">${u.total ? Math.round((u.paying/u.total)*100) : 0}% conversion</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Free Users</div>
        <div class="kpi-value">${u.free || 0}</div>
        <div class="kpi-sub">Potential upsell</div>
      </div>
    </div>

    <!-- Plan breakdown -->
    <div class="admin-grid">
      <div class="chart-card">
        <div class="chart-header"><span class="chart-title">Plan Breakdown</span></div>
        <div class="plan-breakdown">
          <div class="breakdown-row">
            <span class="breakdown-label">🎒 Trainer (Free)</span>
            <div class="breakdown-bar-wrap">
              <div class="breakdown-bar" style="width:${u.total ? (u.free/u.total*100) : 0}%;background:var(--border)"></div>
            </div>
            <span class="breakdown-count">${u.free || 0}</span>
          </div>
          <div class="breakdown-row">
            <span class="breakdown-label">🏅 Gym Leader</span>
            <div class="breakdown-bar-wrap">
              <div class="breakdown-bar" style="width:${u.total ? (u.gym_leader/u.total*100) : 0}%;background:var(--accent)"></div>
            </div>
            <span class="breakdown-count">${u.gym_leader || 0}</span>
          </div>
          <div class="breakdown-row">
            <span class="breakdown-label">🏆 Champion</span>
            <div class="breakdown-bar-wrap">
              <div class="breakdown-bar" style="width:${u.total ? (u.champion/u.total*100) : 0}%;background:#ffa94d"></div>
            </div>
            <span class="breakdown-count">${u.champion || 0}</span>
          </div>
          <div class="breakdown-row">
            <span class="breakdown-label">⚡ Admin</span>
            <div class="breakdown-bar-wrap">
              <div class="breakdown-bar" style="width:${u.total ? (u.admin/u.total*100) : 0}%;background:#ff6b6b"></div>
            </div>
            <span class="breakdown-count">${u.admin || 0}</span>
          </div>
        </div>
      </div>

      <!-- Revenue chart -->
      <div class="chart-card">
        <div class="chart-header"><span class="chart-title">Monthly Revenue</span></div>
        ${revenue?.configured === false
          ? `<p class="text-muted" style="padding:20px">Stripe not configured yet — add STRIPE_SECRET_KEY to .env</p>`
          : revenue?.error
          ? `<p class="text-muted" style="padding:20px">⚠️ ${revenue.error}</p>`
          : `<div class="chart-scroll-wrapper"><div class="chart-wrap"><canvas id="revenue-chart" height="160"></canvas></div></div>`
        }
      </div>
    </div>

    <!-- User management table -->
    <div class="chart-card" style="margin-top:16px">
      <div class="chart-header">
        <span class="chart-title">User Management</span>
        <div style="display:flex;gap:8px">
          <select id="admin-plan-filter" class="form-input" style="width:auto;font-size:13px"
                  onchange="loadAdminUsers()">
            <option value="">All plans</option>
            <option value="free">Free</option>
            <option value="gym_leader">Gym Leader</option>
            <option value="champion">Champion</option>
          </select>
        </div>
      </div>
      <div id="admin-users-table">
        <div class="page-loader"><div class="spinner"></div></div>
      </div>
    </div>
  `;

  // Draw revenue chart
  if (revenue?.monthly?.length > 0) {
    setTimeout(() => {
      const canvas = document.getElementById('revenue-chart');
      if (canvas) {
        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();
        new Chart(canvas, {
          type: 'bar',
          data: {
            labels: revenue.monthly.map(m => m.month),
            datasets: [{
              label: 'Revenue (£)',
              data: revenue.monthly.map(m => m.revenue),
              backgroundColor: 'rgba(108,99,255,0.5)',
              borderColor: 'rgba(108,99,255,1)',
              borderWidth: 2,
              borderRadius: 6,
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              x: { ticks: { color: '#888899' }, grid: { color: '#2e2e3e' } },
              y: { ticks: { color: '#888899', callback: v => '£'+v }, grid: { color: '#2e2e3e' } },
            },
          },
        });
      }
    }, 100);
  }

  loadAdminUsers();
}

async function loadAdminUsers() {
  const plan = document.getElementById('admin-plan-filter')?.value || '';
  const container = document.getElementById('admin-users-table');
  if (!container) return;

  container.innerHTML = '<div class="page-loader"><div class="spinner"></div></div>';

  const url = '/api/admin/users' + (plan ? `?plan=${plan}` : '');
  const data = await fetch(url).then(r => r.json()).catch(() => ({ users: [] }));
  const users = data.users || [];

  const planBadgeStyle = {
    free:       'background:rgba(136,136,153,0.15);color:#888899',
    gym_leader: 'background:rgba(108,99,255,0.15);color:var(--accent)',
    champion:   'background:rgba(255,169,77,0.15);color:#ffa94d',
    admin:      'background:rgba(255,107,107,0.15);color:#ff6b6b',
  };

  container.innerHTML = users.length === 0
    ? '<p class="text-muted" style="padding:20px">No users found.</p>'
    : `<div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="border-bottom:1px solid var(--border);color:var(--text-muted)">
            <th style="position:static;text-align:left;padding:10px 12px">User</th>
            <th style="position:static;text-align:left;padding:10px 12px">Plan</th>
            <th style="position:static;text-align:left;padding:10px 12px">Role</th>
            <th style="position:static;text-align:left;padding:10px 12px">Items</th>
            <th style="position:static;text-align:left;padding:10px 12px">Status</th>
            <th style="position:static;text-align:left;padding:10px 12px">Joined</th>
            <th style="position:static;text-align:left;padding:10px 12px">Actions</th>
          </tr>
        </thead>
        <tbody>
          ${users.map(u => `
            <tr style="border-bottom:1px solid var(--border)">
              <td style="padding:10px 12px">
                <div style="font-weight:500">${esc(u.display_name || u.email.split('@')[0])}</div>
                <div style="color:var(--text-muted);font-size:11px">${esc(u.email)}</div>
              </td>
              <td style="padding:10px 12px">
                <span style="padding:3px 8px;border-radius:10px;font-size:11px;font-weight:600;${planBadgeStyle[u.plan||'free'] || ''}">
                  ${u.plan || 'free'}
                </span>
              </td>
              <td style="padding:10px 12px">
                <span style="padding:3px 8px;border-radius:10px;font-size:11px;font-weight:600;${planBadgeStyle[u.role||'user'] || ''}">
                  ${u.role || 'user'}
                </span>
              </td>
              <td style="padding:10px 12px">${u.item_count || 0}</td>
              <td style="padding:10px 12px">
                <span style="color:${u.subscription_status === 'active' ? 'var(--success)' : 'var(--text-muted)'}">
                  ${u.subscription_status || 'free'}
                </span>
              </td>
              <td style="padding:10px 12px;color:var(--text-muted)">
                ${u.created_at?.slice(0,10) || '—'}
              </td>
              <td style="padding:10px 12px">
                <button class="btn btn-ghost btn-sm"
                        onclick="openAdminUserModal('${u.id}', '${esc(u.email)}', '${u.plan||'free'}', '${u.role||'user'}')">
                  Manage
                </button>
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>`;
}

function openAdminUserModal(userId, email, currentPlan, currentRole) {
  showModal(`
    <h2 style="margin-bottom:6px">Manage User</h2>
    <p class="text-muted" style="margin-bottom:20px">${esc(email)}</p>

    <div class="form-section">
      <label class="form-label">Plan</label>
      <select id="admin-edit-plan" class="form-input">
        <option value="free"       ${currentPlan==='free'       ?'selected':''}>🎒 Trainer (Free)</option>
        <option value="gym_leader" ${currentPlan==='gym_leader' ?'selected':''}>🏅 Gym Leader</option>
        <option value="champion"   ${currentPlan==='champion'   ?'selected':''}>🏆 Champion</option>
      </select>
    </div>

    <div class="form-section">
      <label class="form-label">Role</label>
      <select id="admin-edit-role" class="form-input">
        <option value="user"  ${currentRole==='user'  ?'selected':''}>User</option>
        <option value="admin" ${currentRole==='admin' ?'selected':''}>⚡ Admin</option>
      </select>
    </div>

    <div class="form-section">
      <label class="form-label">Subscription Status</label>
      <select id="admin-edit-status" class="form-input">
        <option value="free">Free</option>
        <option value="active">Active</option>
        <option value="canceled">Canceled</option>
        <option value="past_due">Past Due</option>
      </select>
    </div>

    <div style="background:rgba(255,107,107,0.08);border:1px solid rgba(255,107,107,0.2);
                border-radius:8px;padding:12px;margin:16px 0;font-size:13px;color:var(--text-muted)">
      ⚠️ Changes take effect immediately. Plan changes don't affect Stripe billing —
      cancel in Stripe separately if needed.
    </div>

    <div class="modal-actions">
      <button class="btn btn-danger btn-sm" onclick="confirmAdminDeleteUser('${userId}', '${esc(email)}')">
        Delete User
      </button>
      <div style="flex:1"></div>
      <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
      <button class="btn btn-accent" onclick="saveAdminUser('${userId}')">Save Changes</button>
    </div>
  `);
}

async function saveAdminUser(userId) {
  const plan   = document.getElementById('admin-edit-plan')?.value;
  const role   = document.getElementById('admin-edit-role')?.value;
  const status = document.getElementById('admin-edit-status')?.value;

  const resp = await fetch(`/api/admin/users/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan, role, subscription_status: status }),
  }).then(r => r.json()).catch(e => ({ success: false, error: e.message }));

  if (resp.success) {
    toast('✅ User updated', 'success');
    closeModal();
    loadAdminUsers();
  } else {
    toast(`❌ ${resp.error}`, 'error');
  }
}

async function confirmAdminDeleteUser(userId, email) {
  const ok = confirm(`Delete user "${email}" and all their data? This cannot be undone.`);
  if (!ok) return;

  const resp = await fetch(`/api/admin/users/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan: 'free', subscription_status: 'canceled' }),
  }).then(r => r.json());

  if (resp.success) {
    toast('User downgraded to free — delete from Supabase dashboard if needed', 'warning');
    closeModal();
    loadAdminUsers();
  }
}

/* ── Scan & Sell / Scan & Add (mobile camera) ────────────────────────────── */
let _scanMode = null; // 'add' or 'sell'
let _scannedCardData = null;
let _selectedInventoryId = null;
let _currentMatches = [];

function initScanFAB() {
  const existing = document.getElementById('scan-fab');
  if (existing) existing.remove();

  const fab = document.createElement('div');
  fab.id = 'scan-fab';
  fab.innerHTML = '<button onclick="openScanMenu()" style="width:60px;height:60px;border-radius:50%;background:#6c63ff;border:none;color:white;font-size:28px;cursor:pointer;box-shadow:0 4px 20px rgba(108,99,255,0.5);display:flex;align-items:center;justify-content:center">📷</button>';
  fab.style.cssText = 'position:fixed;bottom:80px;right:20px;z-index:9999;display:none';
  document.body.appendChild(fab);

  if (window.innerWidth <= 768) {
    fab.style.display = 'flex';
  }
}

function openScanMenu() {
  if (S.plan === 'free') {
    showModal(`
      <h2 style="margin-bottom:16px;text-align:center">📷 Scan & Identify</h2>
      <p style="color:var(--text-muted);margin-bottom:16px;text-align:center">This is a Gym Leader+ feature. Upgrade to scan cards with your camera.</p>
      <button class="btn btn-accent" onclick="navigate('/upgrade');closeModal()" style="width:100%;margin-bottom:8px">Upgrade to Gym Leader →</button>
      <button class="btn btn-ghost" onclick="closeModal()" style="width:100%">Cancel</button>
    `);
    return;
  }
  if (S.plan === 'gym_leader') {
    if (!S.user?.gemini_api_key) {
      showModal(`
        <h2 style="margin-bottom:16px;text-align:center">🔑 Gemini API Key Required</h2>
        <p style="color:var(--text-muted);margin-bottom:16px">Add your Gemini API key in Settings to use this feature.</p>
        <button class="btn btn-accent" onclick="navigate('/settings');closeModal()" style="width:100%;margin-bottom:8px">Open Settings →</button>
        <button class="btn btn-ghost" onclick="closeModal()" style="width:100%">Cancel</button>
      `);
      return;
    }
  }
  showModal(`
    <h2 style="margin-bottom:20px;text-align:center">📱 Scan Card</h2>
    <div style="display:flex;flex-direction:column;gap:12px">
      <button class="btn btn-accent" onclick="startScanFlow('add')" style="width:100%;padding:16px;font-size:16px">
        📦 Scan & Add
      </button>
      <button class="btn btn-ghost" onclick="startScanFlow('sell')" style="width:100%;padding:16px;font-size:16px">
        💰 Scan & Sell
      </button>
    </div>
    <button class="btn btn-ghost" onclick="closeModal()" style="width:100%;margin-top:12px">Cancel</button>
  `);
}

function startScanFlow(mode) {
  _scanMode = mode;
  _scannedCardData = null;
  showScanCapture(mode);
}

function showScanCapture(mode) {
  const modalBox = document.querySelector('.modal-box');
  if (!modalBox) {
    showModal(getScanCaptureHTML(mode));
    return;
  }
  modalBox.innerHTML = getScanCaptureHTML(mode);
}

function getScanCaptureHTML(mode) {
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
      <h3 style="margin:0">${mode === 'add' ? '📦 Scan & Add' : '💰 Scan & Sell'}</h3>
      <button onclick="closeScanFlow()" style="background:none;border:none;color:var(--text);font-size:24px;cursor:pointer;padding:0">✕</button>
    </div>
    <div id="scan-preview" style="display:none;margin-bottom:16px">
      <img id="scan-img-preview" style="width:100%;max-height:40vh;object-fit:contain;border-radius:8px">
    </div>
    <div id="scan-buttons" style="display:flex;flex-direction:column;gap:8px">
      <label style="display:block;width:100%;padding:16px;background:var(--accent);color:white;border-radius:10px;text-align:center;font-size:16px;font-weight:600;cursor:pointer">
        📷 Take Photo
        <input type="file" accept="image/*" capture="environment" style="display:none" onchange="handleScanImage(event, '${mode}')">
      </label>
      <label style="display:block;width:100%;padding:16px;background:var(--surface2);color:var(--text);border-radius:10px;text-align:center;font-size:16px;font-weight:600;cursor:pointer;border:1px solid var(--border)">
        🖼️ Choose from Gallery
        <input type="file" accept="image/*" style="display:none" onchange="handleScanImage(event, '${mode}')">
      </label>
    </div>
    <button class="btn btn-accent" id="identify-btn" onclick="identifyCard()" style="width:100%;display:none;margin-top:12px;margin-bottom:8px">🤖 Identify Card</button>
    <button class="btn btn-ghost" onclick="closeScanFlow()" style="width:100%;margin-top:8px">Cancel</button>
  `;
}

function handleScanImage(event, mode) {
  const file = event.target.files?.[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (e) => {
    const preview = document.getElementById('scan-preview');
    const img = document.getElementById('scan-img-preview');
    const btn = document.getElementById('identify-btn');
    const buttons = document.getElementById('scan-buttons');

    if (!preview || !img || !btn || !buttons) return;

    img.src = e.target.result;
    img.onload = () => {
      _scannedCardData = { image: e.target.result, file: file };
      preview.style.display = 'block';
      buttons.style.display = 'none';
      btn.style.display = 'block';
    };
  };
  reader.readAsDataURL(file);
}

async function identifyCard() {
  if (!_scannedCardData) return;
  const btn = document.getElementById('identify-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Analyzing…';

  try {
    const base64 = _scannedCardData.image.split(',')[1];
    const resp = await api.post('/scan/identify', {
      image: base64,
      mime_type: _scannedCardData.file.type || 'image/jpeg'
    });

    if (resp.error) {
      toast(resp.error === 'not a pokemon card' ? '❌ Not a Pokémon card' : `❌ ${resp.error}`, 'error');
      btn.disabled = false;
      btn.textContent = '🤖 Identify Card';
      return;
    }

    _scannedCardData = { ...resp };
    showCardConfirmation();
    closeModal();
  } catch (e) {
    toast('Identification failed: ' + extractError(e.message), 'error');
    btn.disabled = false;
    btn.textContent = '🤖 Identify Card';
  }
}

function showCardConfirmation() {
  const card = _scannedCardData;
  const title = _scanMode === 'add' ? '📦 Confirm Card' : '💰 Confirm Card';
  showModal(`
    <h2 style="margin-bottom:16px">${title}</h2>
    <div style="background:var(--surface2);border-radius:8px;padding:16px;margin-bottom:16px">
      <div style="margin-bottom:8px"><strong>${esc(card.card_name || 'Unknown')}</strong></div>
      <div style="color:var(--text-muted);font-size:14px">
        ${card.card_number ? `#${esc(card.card_number)} · ` : ''}${esc(card.set_name || 'Unknown Set')}
      </div>
      ${card.confidence ? `<div style="color:var(--accent);font-size:13px;margin-top:8px">Confidence: ${card.confidence}</div>` : ''}
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeScanFlow()">Rescan</button>
      <button class="btn btn-accent" onclick="${_scanMode === 'add' ? 'proceedScanAdd()' : 'proceedScanSell()'}">${_scanMode === 'add' ? 'Confirm & Add' : 'Confirm & Sell'}</button>
    </div>
  `);
}

async function proceedScanAdd() {
  closeModal();
  toast('Searching for card price…', 'info', 2000);

  try {
    // For now, just open the add modal with pre-filled data
    showModal(`
      <h2 style="margin-bottom:16px">Add Cards</h2>
      <div id="add-rows-container">
        ${renderAddRow(1)}
      </div>
      <div class="modal-actions" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
        <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
        <button class="btn btn-accent" onclick="submitBulkAdd()">Add Card</button>
      </div>
    `);

    // Pre-fill the card name
    setTimeout(() => {
      const nameInput = document.getElementById('pc-url-1');
      if (nameInput) {
        // For now just pre-fill card name in a comment; user fills in PC URL
        console.log('Card identified:', _scannedCardData);
        toast(`Card: ${_scannedCardData.card_name}. Enter PriceCharting URL and purchase price.`, 'info', 6000);
      }
    }, 100);

  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
  }
}

async function proceedScanSell() {
  closeModal();
  toast('Searching inventory…', 'info', 1000);

  try {
    const resp = await api.post('/scan/match-inventory', {
      card_name: _scannedCardData.card_name
    });

    if (!resp.matches || resp.matches.length === 0) {
      toast('No matching cards found in inventory', 'warning');
      closeScanFlow();
      return;
    }

    if (resp.matches.length === 1) {
      // Single match — go straight to sell
      const match = resp.matches[0];
      showSellConfirmation(match);
    } else {
      // Multiple matches — show list
      showMatchList(resp.matches);
    }
  } catch (e) {
    toast('Search failed: ' + extractError(e.message), 'error');
    closeScanFlow();
  }
}

function showMatchList(matches) {
  _currentMatches = matches;
  showModal(`
    <h2 style="margin-bottom:16px">Select Card to Sell</h2>
    <p class="text-muted" style="margin-bottom:12px">Found ${matches.length} matching cards</p>
    <div style="max-height:40vh;overflow-y:auto;background:var(--surface2);border-radius:8px">
      ${matches.map(m => `
        <div onclick="selectMatchAndShowSellForm(${m.item_id})"
             style="padding:12px;border-bottom:1px solid var(--border);cursor:pointer;display:flex;justify-content:space-between;align-items:center"
             onmouseover="this.style.background='rgba(108,99,255,0.1)'"
             onmouseout="this.style.background='transparent'">
          <div>
            <div style="font-weight:600">#${m.item_id}</div>
            <div style="font-size:13px;color:var(--text-muted)">${esc(m.card_name)}</div>
            <div style="font-size:12px;color:var(--text-muted)">${esc(m.condition)} · Bought ${fmt(m.purchase_price)}</div>
          </div>
          <div style="text-align:right">
            <div style="font-weight:600">${fmt(m.current_price)}</div>
            <div style="font-size:12px;color:var(--text-muted)">Current</div>
          </div>
        </div>
      `).join('')}
    </div>
    <button class="btn btn-ghost" onclick="closeScanFlow()" style="width:100%;margin-top:12px">Cancel</button>
  `);
}

function selectMatchAndShowSellForm(itemId) {
  const match = _currentMatches.find(m => m.item_id === itemId);
  if (match) showSellConfirmation(match);
}

function showSellConfirmation(match) {
  _selectedInventoryId = match.item_id;
  const market = parseFloat(match.current_price || 0);
  const suggested = market.toFixed(2);

  showModal(`
    <h2 style="margin-bottom:6px">💰 Sell Card</h2>
    <p class="text-muted" style="margin-bottom:16px">${esc(match.card_name)}</p>

    <div class="form-section">
      <label class="form-label">Sale Price (£)</label>
      <input type="number" id="scan-sell-price" class="form-input"
             value="${suggested}" step="0.01" min="0.01" />
      <div class="price-hints" style="margin-top:8px">
        <button class="pill-btn" onclick="document.getElementById('scan-sell-price').value='${suggested}'">Market ${fmt(market)}</button>
      </div>
    </div>

    <div class="form-section">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:500">
        <input type="checkbox" id="scan-ebay-fees" checked>
        Include eBay fees (12.35%)
      </label>
      <div id="fee-calc" style="font-size:13px;color:var(--text-muted);margin-top:6px"></div>
    </div>

    <div class="form-section">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:500">
        <input type="checkbox" id="scan-postage" checked>
        Include postage (£1.50)
      </label>
    </div>

    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeScanFlow()">Cancel</button>
      <button class="btn btn-success" onclick="confirmScanSell()">✅ Confirm Sale</button>
    </div>
  `);

  const priceInput = document.getElementById('scan-sell-price');
  const feesCheckbox = document.getElementById('scan-ebay-fees');
  const postageCheckbox = document.getElementById('scan-postage');

  function updateFeeCalc() {
    const price = parseFloat(priceInput.value) || 0;
    const feesOn = feesCheckbox.checked;
    const postageOn = postageCheckbox.checked;
    let fees = feesOn ? price * 0.1235 : 0;
    let postage = postageOn ? 1.50 : 0;
    let net = price - fees - postage;

    const feeDiv = document.getElementById('fee-calc');
    if (feesOn || postageOn) {
      feeDiv.innerHTML = `
        ${feesOn ? `Fees: -£${fees.toFixed(2)}<br>` : ''}
        ${postageOn ? `Postage: -£${postage.toFixed(2)}<br>` : ''}
        <strong>Net: £${Math.max(0, net).toFixed(2)}</strong>
      `;
    } else {
      feeDiv.innerHTML = '';
    }
  }

  priceInput.addEventListener('input', updateFeeCalc);
  feesCheckbox.addEventListener('change', updateFeeCalc);
  postageCheckbox.addEventListener('change', updateFeeCalc);
  updateFeeCalc();
  setTimeout(() => priceInput.focus(), 80);
}

async function confirmScanSell() {
  if (!_selectedInventoryId) return;

  const price = parseFloat(document.getElementById('scan-sell-price')?.value);
  if (!price || price <= 0) { toast('Enter a valid price', 'error'); return; }

  const btn = document.querySelector('.modal-actions .btn-success');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Processing…'; }

  try {
    const res = await api.post(`/inventory/${_selectedInventoryId}/sell`, { sell_price: price });
    if (res.success === false) {
      toast(res.error || 'Sell failed', 'error');
      if (btn) { btn.disabled = false; btn.textContent = '✅ Confirm Sale'; }
      return;
    }

    const item = S.inventory.find(i => i.item_id === _selectedInventoryId);
    if (item) { item.status = 'Sold'; item.sell_price = price; }

    closeModal();
    closeScanFlow();
    refreshInventoryGrid();
    toast(`Sold for ${fmt(price)} ✅`, 'success');
  } catch (e) {
    toast('Error: ' + extractError(e.message), 'error');
    if (btn) { btn.disabled = false; btn.textContent = '✅ Confirm Sale'; }
  }
}

function closeScanFlow() {
  _scanMode = null;
  _scannedCardData = null;
  _selectedInventoryId = null;
  closeModal();
}

function handleCameraCapture(file) {
  handleScanImage({ target: { files: [file] } }, _scanMode);
}

/* ── Routes ──────────────────────────────────────────────────────────────── */
const ROUTES = {
  '/':           renderInventory,
  '/analytics':  renderAnalytics,
  '/listings':   renderListings,
  '/watchlist':  renderWatchlist,
  '/sales':      renderSales,
  '/calculator': renderCalculator,
  '/settings':   renderSettings,
  '/upgrade':    renderUpgrade,
  '/admin':      renderAdmin,
};

document.querySelectorAll('.nav-link').forEach(a => {
  a.addEventListener('click', e => { e.preventDefault(); navigate(a.dataset.route); });
});
window.addEventListener('popstate', routeCurrentPath);

/* ── Boot ────────────────────────────────────────────────────────────────── */
(async () => {
  requestNotificationPermission();
  startGlobalEventStream();
  updateStatus();
  await updateNavUser();
  initMobileNav();
  initScanFAB();

  // Show/hide FAB on resize
  window.addEventListener('resize', () => {
    const fab = document.getElementById('scan-fab');
    if (fab) {
      if (window.innerWidth <= 768) {
        fab.style.display = 'flex';
      } else {
        fab.style.display = 'none';
      }
    }
  });

  // Add admin link if user is admin
  if (S.user?.role === 'admin') {
    const navLinks = document.querySelector('.nav-links');
    if (navLinks) {
      const settingsLink = Array.from(navLinks.querySelectorAll('a')).find(a => a.textContent.includes('Settings'));
      if (settingsLink) {
        const adminLi = document.createElement('li');
        adminLi.innerHTML = '<a href="#" data-route="/admin" class="nav-link">⚡ Admin</a>';
        settingsLink.parentElement.before(adminLi);
        adminLi.querySelector('a').addEventListener('click', e => {
          e.preventDefault();
          navigate('/admin');
        });
      }
    }
  }

  setInterval(updateStatus, 30000);
  routeCurrentPath();
})();