/* رندرکننده داشبورد — بدون هیچ وابستگی خارجی (SVG خالص) */
(function () {
  'use strict';

  // ---------- حالت تاریک/روشن (مستقل از app.js) ----------
  var rootEl = document.documentElement;
  try {
    if (localStorage.getItem('theme') === 'dark') rootEl.classList.add('dark');
  } catch (e) { /* دسترسی به localStorage ممکن نیست */ }
  var themeBtn = document.getElementById('themeBtn');
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var dark = rootEl.classList.toggle('dark');
      try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch (e) {}
    });
  }

  var dataEl = document.getElementById('dash-data');
  if (!dataEl) return;
  var DATA;
  try { DATA = JSON.parse(dataEl.textContent); } catch (e) { return; }
  var J = window.Jalali;

  var PALETTE = ['#2563eb', '#16a34a', '#dc2626', '#d97706', '#7c3aed',
    '#0891b2', '#be185d', '#4d7c0f', '#b45309', '#475569'];

  // ---------- ابزار ----------
  function el(tag, cls, text) {
    var d = document.createElement(tag);
    if (cls) d.className = cls;
    if (text !== undefined && text !== null) d.textContent = text;
    return d;
  }
  function faNum(n) {
    try { return Number(n).toLocaleString('fa-IR'); }
    catch (e) { return J.faDigits(String(n)); }
  }
  function faPct(x) { return faNum(x) + '٪'; }

  function svgEl(tag, attrs) {
    var s = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) s.setAttribute(k, attrs[k]);
    return s;
  }

  // ---------- CSV ----------
  function chartCsv(ch) {
    var heads, rows;
    if (ch.kind === 'donut' || ch.kind === 'bars') {
      heads = ['عنوان', 'تعداد'];
      rows = ch.labels.map(function (l, i) { return [l, ch.values[i]]; });
    } else if (ch.kind === 'line') {
      heads = ['ماه'].concat(ch.series.map(function (s) { return s.name; }));
      rows = ch.labels.map(function (m, i) {
        return [m].concat(ch.series.map(function (s) { return s.values[i]; }));
      });
    } else if (ch.kind === 'table') {
      heads = ch.headers; rows = ch.rows;
    } else { return null; }
    var esc = function (v) {
      if (v === null || v === undefined) v = '';
      v = String(v);
      return '"' + v.replace(/"/g, '""') + '"';
    };
    var out = '\ufeff' + heads.map(esc).join(',') + '\n';
    rows.forEach(function (r) { out += r.map(esc).join(',') + '\n'; });
    return out;
  }

  function csvButton(ch) {
    var b = el('button', 'btn small ghost csv-btn', 'CSV');
    b.type = 'button';
    b.title = 'دانلود CSV این نمودار';
    b.addEventListener('click', function () {
      var txt = chartCsv(ch);
      if (!txt) return;
      var blob = new Blob([txt], { type: 'text/csv;charset=utf-8' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = ch.id + '.csv';
      document.body.appendChild(a);
      a.click();
      setTimeout(function () {
        URL.revokeObjectURL(a.href);
        a.remove();
      }, 500);
    });
    return b;
  }

  // ---------- کارت نمودار ----------
  function chartCard(ch) {
    var card = el('div', 'chart-card');
    // جدول‌ها و میله‌های بلند، تمام‌عرض باشند
    if (ch.kind === 'table' || (ch.kind === 'bars' && ch.labels.length > 8)) {
      card.classList.add('wide');
    }
    var head = el('div', 'chart-head');
    head.appendChild(el('h3', 'chart-title', ch.title));
    head.appendChild(csvButton(ch));
    card.appendChild(head);
    var body = el('div', 'chart-body');
    if (ch.kind === 'donut') body.appendChild(renderDonut(ch));
    else if (ch.kind === 'bars') body.appendChild(renderBars(ch));
    else if (ch.kind === 'line') body.appendChild(renderLine(ch));
    else if (ch.kind === 'table') body.appendChild(renderTable(ch));
    else body.appendChild(el('div', 'muted small', 'نوع نمودار پشتیبانی نمی‌شود'));
    card.appendChild(body);
    return card;
  }

  // ---------- دونات ----------
  function renderDonut(ch) {
    var wrap = el('div', 'donut-wrap');
    var size = 220, r = 82, cx = size / 2, cy = size / 2, sw = 36;
    var C = 2 * Math.PI * r;
    var total = ch.total || ch.values.reduce(function (a, b) { return a + b; }, 0) || 1;
    var svg = svgEl('svg', { viewBox: '0 0 ' + size + ' ' + size, width: '220', height: '220', role: 'img' });
    svg.classList.add('donut-svg');
    var acc = 0;
    ch.values.forEach(function (v, i) {
      var frac = v / total;
      if (frac <= 0) return;
      var c = svgEl('circle', {
        cx: cx, cy: cy, r: r, fill: 'none',
        stroke: PALETTE[i % PALETTE.length],
        'stroke-width': sw,
        'stroke-dasharray': (frac * C - 1.5) + ' ' + C,
        'stroke-dashoffset': -(acc * C),
        transform: 'rotate(-90 ' + cx + ' ' + cy + ')'
      });
      var t = svgEl('title');
      t.textContent = ch.labels[i] + ': ' + faNum(v) + ' (' + faPct(Math.round(frac * 1000) / 10) + ')';
      c.appendChild(t);
      svg.appendChild(c);
      acc += frac;
    });
    var txt = svgEl('text', { x: cx, y: cy - 4, 'text-anchor': 'middle', 'class': 'donut-total' });
    txt.textContent = faNum(total);
    svg.appendChild(txt);
    var sub = svgEl('text', { x: cx, y: cy + 18, 'text-anchor': 'middle', 'class': 'donut-sub' });
    sub.textContent = 'مجموع';
    svg.appendChild(sub);
    wrap.appendChild(svg);

    var lg = el('div', 'donut-legend');
    ch.labels.forEach(function (lb, i) {
      var v = ch.values[i];
      var row = el('div', 'legend-row');
      var chip = el('span', 'legend-chip');
      chip.style.background = PALETTE[i % PALETTE.length];
      row.appendChild(chip);
      row.appendChild(el('span', 'legend-label', lb));
      row.appendChild(el('span', 'legend-val',
        faNum(v) + ' — ' + faPct(Math.round(v * 1000 / total) / 10)));
      lg.appendChild(row);
    });
    wrap.appendChild(lg);
    return wrap;
  }

  // ---------- میله‌ای افقی ----------
  function renderBars(ch) {
    var max = Math.max.apply(null, ch.values.concat([1]));
    var box = el('div', 'hbars');
    ch.labels.forEach(function (lb, i) {
      var v = ch.values[i];
      var row = el('div', 'hbar-row');
      var lab = el('span', 'hbar-label', lb);
      lab.title = lb;
      row.appendChild(lab);
      var track = el('span', 'hbar-track');
      var fill = el('span', 'hbar-fill');
      fill.style.width = (v * 100.0 / max).toFixed(2) + '%';
      fill.style.background = PALETTE[0];
      fill.title = faNum(v);
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el('span', 'hbar-val', faNum(v)));
      box.appendChild(row);
    });
    if (!ch.labels.length) box.appendChild(el('div', 'muted small', 'داده‌ای نیست'));
    return box;
  }

  // ---------- خطی (روند ماهانه) ----------
  function niceMax(v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v)));
    for (var m = 1; m <= 10; m++) if (m * p >= v) return m * p;
    return v;
  }

  function renderLine(ch) {
    var W = 640, H = 280, padL = 56, padR = 14, padT = 16, padB = 40;
    var n = ch.labels.length;
    var box = el('div', 'line-wrap');

    if (ch.series.length > 1) {
      var lgd = el('div', 'line-legend');
      ch.series.forEach(function (s, i) {
        var item = el('span', 'line-legend-item');
        var chip = el('span', 'legend-chip');
        chip.style.background = s.color || PALETTE[i % PALETTE.length];
        item.appendChild(chip);
        item.appendChild(el('span', null, s.name));
        lgd.appendChild(item);
      });
      box.appendChild(lgd);
    }

    var maxV = 0;
    ch.series.forEach(function (s) {
      s.values.forEach(function (v) { if (v > maxV) maxV = v; });
    });
    var yMax = niceMax(maxV);
    var x = function (i) { return padL + (n <= 1 ? (W - padL - padR) / 2 : i * (W - padL - padR) / (n - 1)); };
    var y = function (v) { return padT + (H - padT - padB) * (1 - v / yMax); };

    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'line-svg' });
    svg.classList.add('line-svg');

    for (var g = 0; g <= 4; g++) {
      var gv = yMax * g / 4;
      var gy = y(gv);
      svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: gy, y2: gy, class: 'grid-line' }));
      var t = svgEl('text', { x: padL - 8, y: gy + 4, 'text-anchor': 'end', class: 'axis-text' });
      t.textContent = faNum(gv);
      svg.appendChild(t);
    }

    var step = Math.max(1, Math.ceil(n / 9));
    ch.labels.forEach(function (m, i) {
      if (i % step !== 0 && i !== n - 1) return;
      var t = svgEl('text', { x: x(i), y: H - 12, 'text-anchor': 'middle', class: 'axis-text' });
      t.textContent = J.monthLabel(m);
      svg.appendChild(t);
    });

    ch.series.forEach(function (s, si) {
      var color = s.color || PALETTE[si % PALETTE.length];
      var pts = s.values.map(function (v, i) { return x(i) + ',' + y(v); }).join(' ');
      if (s.values.length > 1) {
        var area = svgEl('polygon', {
          points: padL + ',' + y(0) + ' ' + pts + ' ' + x(n - 1) + ',' + y(0),
          fill: color, 'fill-opacity': '0.07', stroke: 'none'
        });
        svg.appendChild(area);
      }
      svg.appendChild(svgEl('polyline', {
        points: pts, fill: 'none', stroke: color,
        'stroke-width': '2.5', 'stroke-linejoin': 'round', 'stroke-linecap': 'round'
      }));
      s.values.forEach(function (v, i) {
        var c = svgEl('circle', { cx: x(i), cy: y(v), r: 3, fill: color });
        var t = svgEl('title');
        t.textContent = J.monthLabel(ch.labels[i]) + ' — ' + s.name + ': ' + faNum(v);
        c.appendChild(t);
        svg.appendChild(c);
      });
    });

    box.appendChild(svg);
    return box;
  }

  // ---------- جدول ----------
  function renderTable(ch) {
    var tbl = el('table', 'list-table dash-table');
    var thead = el('thead');
    var trh = el('tr');
    ch.headers.forEach(function (h) { trh.appendChild(el('th', null, h)); });
    thead.appendChild(trh);
    tbl.appendChild(thead);
    var tbody = el('tbody');
    ch.rows.forEach(function (r) {
      var tr = el('tr');
      r.forEach(function (cell, ci) {
        var td = el('td');
        var isNum = typeof cell === 'number';
        var isPct = isNum && /٪/.test(ch.headers[ci] || '');
        if (isPct) {
          var bar = el('span', 'mini-bar-track');
          var fill = el('span', 'mini-bar-fill');
          fill.style.width = Math.min(100, cell) + '%';
          bar.appendChild(fill);
          td.appendChild(bar);
          td.appendChild(document.createTextNode(' ' + faNum(cell)));
          td.dir = 'ltr';
          td.style.textAlign = 'right';
        } else {
          td.textContent = isNum ? faNum(cell) : String(cell);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    var wrap = el('div', 'table-wrap');
    wrap.appendChild(tbl);
    return wrap;
  }

  // ---------- KPI ----------
  function renderKpis() {
    var host = document.getElementById('kpis');
    if (!host || !DATA.kpis) return;
    DATA.kpis.forEach(function (k) {
      var card = el('div', 'kpi');
      card.appendChild(el('div', 'kpi-num', faNum(k.value)));
      card.appendChild(el('div', 'kpi-label', k.label));
      if (k.sub) {
        var s = el('div', 'kpi-sub', k.sub);
        if (typeof k.success_rate === 'number') {
          s.classList.add(k.success_rate >= 90 ? 'ok' : (k.success_rate >= 70 ? 'mid' : 'bad'));
        }
        card.appendChild(s);
      }
      host.appendChild(card);
    });
  }

  // ---------- بخش‌ها ----------
  function render() {
    renderKpis();
    var root = document.getElementById('dash-root');
    if (!root || !DATA.sections) return;
    DATA.sections.forEach(function (sec) {
      var s = el('section', 'dash-section');
      s.appendChild(el('h2', 'dash-sec-title', sec.title));
      var grid = el('div', 'dash-grid');
      sec.charts.forEach(function (ch) { grid.appendChild(chartCard(ch)); });
      s.appendChild(grid);
      root.appendChild(s);
    });
  }

  render();
})();
