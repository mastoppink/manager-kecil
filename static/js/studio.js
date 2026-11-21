// manager-kecil — helper UI bersama (toast, dialog konfirmasi, statistik server, escape HTML)
(function () {
  function toast(message, type) {
    const wrap = document.getElementById('st-toasts');
    if (!wrap) return;
    const el = document.createElement('div');
    el.className = 'st-toast' + (type === 'error' ? ' error' : '');
    el.textContent = String(message || '');
    wrap.appendChild(el);
    setTimeout(() => el.remove(), type === 'error' ? 6000 : 3500);
  }

  // Mengembalikan Promise<boolean>. danger=true membuat tombol OK berwarna merah.
  function confirmDialog(title, text, okLabel, danger) {
    const dlg = document.getElementById('st-dialog');
    if (!dlg || typeof dlg.showModal !== 'function') return Promise.resolve(window.confirm(title + '\n\n' + (text || '')));
    document.getElementById('st-dialog-title').textContent = title;
    document.getElementById('st-dialog-text').textContent = text || '';
    const ok = document.getElementById('st-dialog-ok');
    const cancel = document.getElementById('st-dialog-cancel');
    ok.textContent = okLabel || 'Lanjutkan';
    ok.className = 'st-btn ' + (danger ? 'st-btn-danger' : 'st-btn-primary');
    return new Promise(resolve => {
      const done = v => { dlg.close(); ok.onclick = cancel.onclick = dlg.oncancel = null; resolve(v); };
      ok.onclick = () => done(true);
      cancel.onclick = () => done(false);
      dlg.oncancel = e => { e.preventDefault(); done(false); };
      dlg.showModal();
      cancel.focus();
    });
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function pollSysStats() {
    fetch('/api/get_dashboard_stats').then(r => r.json()).then(d => {
      if (d.sys_cpu === undefined) return;
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
      set('sys-cpu', d.sys_cpu + '%');
      set('sys-ram', d.sys_ram_pct + '%');
      set('sys-ram-txt', d.sys_ram_text);
    }).catch(() => {});
  }

  window.Studio = { toast, confirmDialog, esc };
  if (!window.toast) window.toast = toast;

  document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('sys-cpu')) { pollSysStats(); setInterval(pollSysStats, 5000); }
    document.addEventListener('keydown', e => { if (e.key === 'Escape') document.body.classList.remove('nav-open'); });
  });
})();
