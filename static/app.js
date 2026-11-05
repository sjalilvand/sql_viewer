/* نمایشگر بکاپ MySQL — اسکریپت سمت کلاینت (بدون وابستگی خارجی) */

// ---------- حالت تاریک/روشن ----------
(function () {
  var root = document.documentElement;
  try {
    if (localStorage.getItem('theme') === 'dark') root.classList.add('dark');
  } catch (e) { /* دسترسی به localStorage ممکن نیست */ }

  var btn = document.getElementById('themeBtn');
  if (btn) {
    btn.addEventListener('click', function () {
      var dark = root.classList.toggle('dark');
      try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch (e) {}
    });
  }
})();

// ---------- به‌روزرسانی زنده پیشرفت استخراج (صفحه اصلی) ----------
(function () {
  var card = document.getElementById('progressCard');
  if (!card) return;

  var fmtFa = function (n) {
    try { return Number(n).toLocaleString('fa-IR'); } catch (e) { return String(n); }
  };

  var tick = function () {
    fetch('/api/status')
      .then(function (r) { return r.json(); })
      .then(function (s) {
        var bar = document.getElementById('pbar');
        var txt = document.getElementById('ptext');
        var sub = document.getElementById('psub');
        if (bar) bar.style.width = Math.min(100, s.percent || 0) + '%';
        if (txt) txt.textContent = (s.percent || 0).toFixed(1) + '٪';
        if (sub) {
          var mb = ((s.bytes_done || 0) / 1048576).toFixed(1);
          var tmb = ((s.total_bytes || 0) / 1048576).toFixed(1);
          sub.textContent = 'جدول فعلی: ' + (s.current_table || '—') +
            ' | ' + mb + ' از ' + tmb + ' مگابایت' +
            ' | ردیف‌های درج‌شده: ' + fmtFa(s.rows || 0);
        }
        if (s.state === 'done' || s.state === 'error') {
          window.location.reload();
        }
      })
      .catch(function () { /* خطای شبکه نادیده گرفته می‌شود */ });
  };

  tick();
  setInterval(tick, 1500);
})();

// ---------- فیلتر فهرست جداول ----------
(function () {
  var tf = document.getElementById('tableFilter');
  if (!tf) return;
  tf.addEventListener('input', function () {
    var q = tf.value.trim().toLowerCase();
    document.querySelectorAll('[data-table-row]').forEach(function (tr) {
      var name = tr.getAttribute('data-table-row').toLowerCase();
      tr.style.display = name.indexOf(q) !== -1 ? '' : 'none';
    });
  });
})();

// ---------- باز کردن متن کامل سلول با کلیک ----------
document.querySelectorAll('.data-table td:not(.rownum)').forEach(function (td) {
  td.addEventListener('click', function () {
    td.classList.toggle('expanded');
  });
});
