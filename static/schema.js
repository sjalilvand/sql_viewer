/* صفحه ساختار دیتابیس — نمودار ER تعاملی با SVG خالص (بدون وابستگی خارجی)
   امکان‌ها: چیدمان حلقوی جدول‌های پرهیوب، زوم/جابه‌جایی، هایلایت ارتباط‌ها،
   فیلتر نام جدول، کلیک برای رفتن به داده‌های جدول */
(function () {
  'use strict';

  var dataEl = document.getElementById('schema-data');
  if (!dataEl) return;
  var payload;
  try { payload = JSON.parse(dataEl.textContent); } catch (e) { return; }

  var faDigits = (window.Jalali && Jalali.faDigits) || function (s) { return s; };
  var SVGNS = 'http://www.w3.org/2000/svg';

  function el(tag, attrs, parent) {
    var n = document.createElementNS(SVGNS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  /* ---------------- داده‌های نمودار ---------------- */
  var NW = 148, NH = 42;           // اندازه جعبه هر جدول
  var nodes = (payload.tables || []).map(function (t) {
    return { name: t.name, rows: t.rows || 0, degree: t.degree || 0,
             cols: (t.cols || []).length };
  });
  var byName = {};
  nodes.forEach(function (n) { byName[n.name] = n; });

  var erConf = { verified: 1, high: 1, med: 1 };
  var edges = (payload.relations || []).filter(function (r) {
    return erConf[r.confidence] && byName[r.child] && byName[r.parent];
  });

  /* ---------------- چیدمان حلقوی ---------------- */
  (function layout() {
    var W = 1160, H = 800, cx = W / 2, cy = H / 2;
    nodes.sort(function (a, b) {
      return b.degree - a.degree || b.rows - a.rows ||
             a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    });
    var hubs = nodes.filter(function (n) { return n.degree >= 3; });
    var rest = nodes.filter(function (n) { return n.degree < 3; });

    var rings = [];
    if (hubs.length) rings.push(hubs.slice(0, 14));
    if (hubs.length > 14) rings.push(hubs.slice(14));
    // جدول‌های کم‌ارتباط در حلقه‌های بیرونی با ظرفیت فزاینده
    var cap = 16, i = 0;
    while (i < rest.length) {
      rings.push(rest.slice(i, i + cap));
      i += cap;
      cap += 8;
    }
    var r0 = 190 + Math.min(70, (rings[0] ? rings[0].length : 0) * 10);
    rings.forEach(function (ring, k) {
      var R = r0 + k * 105;
      var step = (2 * Math.PI) / ring.length;
      var off = k % 2 ? step / 2 : 0;
      ring.forEach(function (n, j) {
        var a = j * step + off - Math.PI / 2;
        // بیضی: عرض بیشتر تا جعبه‌ها روی هم نیفتند
        n.x = cx + Math.cos(a) * R * 1.30;
        n.y = cy + Math.sin(a) * R * 0.82;
      });
    });
    nodes.forEach(function (n) {
      n.x = Math.max(NW / 2 + 6, Math.min(W - NW / 2 - 6, n.x));
      n.y = Math.max(NH / 2 + 6, Math.min(H - NH / 2 - 6, n.y));
    });
  })();

  /* ---------------- رسم ---------------- */
  var svg = document.getElementById('erSvg');
  var W0 = 1160, H0 = 800;
  var vb = { x: 0, y: 0, w: W0, h: H0 };

  function applyVB() {
    svg.setAttribute('viewBox', vb.x + ' ' + vb.y + ' ' + vb.w + ' ' + vb.h);
  }
  applyVB();

  var defs = el('defs', {}, svg);
  var marker = el('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 9,
    refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
  el('path', { d: 'M 0 0 L 10 5 L 0 10 z', 'class': 'er-arrow' }, marker);

  var edgeG = el('g', { 'class': 'er-edges' }, svg);
  var nodeG = el('g', { 'class': 'er-nodes' }, svg);

  function rectClip(x, y, tx, ty) {
    // نقطه تقاطع پاره‌خط مرکز‌به‌مرکز با لبه جعبه مقصد
    var dx = tx - x, dy = ty - y;
    var sx = dx !== 0 ? (NW / 2 + 3) / Math.abs(dx) : 1e9;
    var sy = dy !== 0 ? (NH / 2 + 3) / Math.abs(dy) : 1e9;
    var s = Math.min(sx, sy);
    return [x + dx * s, y + dy * s];
  }

  var edgeEls = [];
  edges.forEach(function (r) {
    var a = byName[r.child], b = byName[r.parent];
    var p1 = rectClip(a.x, a.y, b.x, b.y);
    var p2 = rectClip(b.x, b.y, a.x, a.y);
    var mx = (p1[0] + p2[0]) / 2, my = (p1[1] + p2[1]) / 2;
    var dx = p2[0] - p1[0], dy = p2[1] - p1[1];
    var d = Math.sqrt(dx * dx + dy * dy) || 1;
    // خمش ملایم و یکنواخت برای خوانا شدن خط‌های موازی
    var px = -dy / d, py = dx / d;
    var bow = Math.min(34, d * 0.14);
    var path = el('path', {
      'class': 'er-edge conf-' + r.confidence,
      d: 'M ' + p1[0].toFixed(1) + ' ' + p1[1].toFixed(1) +
         ' Q ' + (mx + px * bow).toFixed(1) + ' ' + (my + py * bow).toFixed(1) +
         ' ' + p2[0].toFixed(1) + ' ' + p2[1].toFixed(1),
      'marker-end': 'url(#arrow)',
      'data-child': r.child, 'data-parent': r.parent,
    }, edgeG);
    var t = el('title', {}, path);
    t.textContent = r.child + '.' + r.child_col + '  \u2190  ' +
                    r.parent + '.' + (r.parent_col || '?');
    edgeEls.push(path);
  });

  var nodeEls = {};
  nodes.forEach(function (n) {
    var g = el('g', { 'class': 'er-node', transform:
      'translate(' + (n.x - NW / 2).toFixed(1) + ',' + (n.y - NH / 2).toFixed(1) + ')',
      'data-name': n.name, tabindex: 0, role: 'link',
      'aria-label': 'جدول ' + n.name }, nodeG);
    el('rect', { x: 0, y: 0, width: NW, height: NH, rx: 9, ry: 9 }, g);
    el('text', { x: NW / 2, y: 18, 'class': 'er-node-name', 'text-anchor': 'middle' }, g)
      .textContent = n.name;
    el('text', { x: NW / 2, y: 33, 'class': 'er-node-sub', 'text-anchor': 'middle' }, g)
      .textContent = faDigits(n.rows.toLocaleString('en-US')) + ' \u0631\u062f\u06cc\u0641';
    nodeEls[n.name] = g;
    var t = el('title', {}, g);
    t.textContent = n.name + ' — ' + n.cols + ' ستون';
  });

  /* ---------------- هایلایت ارتباط‌ها ---------------- */
  var neighborCache = null;
  function neighborsOf(name) {
    if (!neighborCache) {
      neighborCache = {};
      edgeEls.forEach(function (p) {
        var c = p.getAttribute('data-child'), pa = p.getAttribute('data-parent');
        (neighborCache[c] = neighborCache[c] || {})[pa] = 1;
        (neighborCache[pa] = neighborCache[pa] || {})[c] = 1;
      });
    }
    return Object.keys(neighborCache[name] || {});
  }

  nodeG.addEventListener('mouseover', function (ev) {
    var g = ev.target.closest('.er-node');
    if (!g) return;
    var name = g.getAttribute('data-name');
    svg.classList.add('has-focus');
    g.classList.add('node-on');
    edgeEls.forEach(function (p) {
      if (p.getAttribute('data-child') === name ||
          p.getAttribute('data-parent') === name) {
        p.classList.add('edge-on');
        var other = p.getAttribute('data-child') === name
          ? p.getAttribute('data-parent') : p.getAttribute('data-child');
        if (nodeEls[other]) nodeEls[other].classList.add('node-on');
      }
    });
  });
  nodeG.addEventListener('mouseout', function (ev) {
    var g = ev.target.closest('.er-node');
    if (!g) return;
    svg.classList.remove('has-focus');
    g.classList.remove('node-on');
    edgeEls.forEach(function (p) { p.classList.remove('edge-on'); });
    Object.keys(nodeEls).forEach(function (k) { nodeEls[k].classList.remove('node-on'); });
  });

  /* ---------------- جابه‌جایی، زوم و کلیک ---------------- */
  var drag = null, moved = 0;
  svg.addEventListener('pointerdown', function (ev) {
    drag = { px: ev.clientX, py: ev.clientY, vx: vb.x, vy: vb.y };
    moved = 0;
    svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener('pointermove', function (ev) {
    if (!drag) return;
    var rect = svg.getBoundingClientRect();
    var kx = vb.w / rect.width, ky = vb.h / rect.height;
    var dx = (ev.clientX - drag.px) * kx, dy = (ev.clientY - drag.py) * ky;
    moved += Math.abs(ev.clientX - drag.px) + Math.abs(ev.clientY - drag.py);
    vb.x = drag.vx - dx;
    vb.y = drag.vy - dy;
    applyVB();
  });
  svg.addEventListener('pointerup', function () { drag = null; });

  function zoomAt(cxRatio, cyRatio, factor) {
    var wx = vb.x + cxRatio * vb.w, wy = vb.y + cyRatio * vb.h;
    vb.w = Math.max(160, Math.min(W0 * 3, vb.w * factor));
    vb.h = Math.max(110, Math.min(H0 * 3, vb.h * factor));
    vb.x = wx - cxRatio * vb.w;
    vb.y = wy - cyRatio * vb.h;
    applyVB();
  }
  svg.addEventListener('wheel', function (ev) {
    if (!ev.ctrlKey && !ev.metaKey) return; // بدون Ctrl، اسکرول صفحه
    ev.preventDefault();
    var rect = svg.getBoundingClientRect();
    zoomAt((ev.clientX - rect.left) / rect.width,
           (ev.clientY - rect.top) / rect.height,
           ev.deltaY > 0 ? 1.12 : 0.89);
  }, { passive: false });

  document.getElementById('erZoomIn').addEventListener('click', function () {
    zoomAt(0.5, 0.5, 0.8);
  });
  document.getElementById('erZoomOut').addEventListener('click', function () {
    zoomAt(0.5, 0.5, 1.25);
  });
  document.getElementById('erReset').addEventListener('click', function () {
    vb = { x: 0, y: 0, w: W0, h: H0 };
    applyVB();
  });

  // کلیک روی جدول → صفحه داده‌ها (مگر در حال کشیدن)
  var base = document.body.getAttribute('data-base') || '';
  nodeG.addEventListener('click', function (ev) {
    if (moved > 6) return;
    var g = ev.target.closest('.er-node');
    if (!g) return;
    window.location.href = base + '/table/' +
      encodeURIComponent(g.getAttribute('data-name'));
  });
  nodeG.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Enter') return;
    var g = ev.target.closest('.er-node');
    if (g) window.location.href = '/table/' + encodeURIComponent(g.getAttribute('data-name'));
  });

  /* ---------------- فیلتر جعبه‌ها ---------------- */
  var erFilter = document.getElementById('erFilter');
  var erCount = document.getElementById('erCount');
  function applyErFilter() {
    var q = (erFilter.value || '').trim().toLowerCase();
    var shown = 0;
    nodes.forEach(function (n) {
      var show = !q || n.name.toLowerCase().indexOf(q) !== -1;
      nodeEls[n.name].style.display = show ? '' : 'none';
      if (show) shown++;
    });
    edgeEls.forEach(function (p) {
      var c = byName[p.getAttribute('data-child')];
      var pa = byName[p.getAttribute('data-parent')];
      var show = (!q || (c && c.name.toLowerCase().indexOf(q) !== -1) ||
                        (pa && pa.name.toLowerCase().indexOf(q) !== -1));
      p.style.display = show ? '' : 'none';
    });
    erCount.textContent = q ? ' — ' + faDigits(shown) + ' جدول' : '';
  }
  if (erFilter) erFilter.addEventListener('input', applyErFilter);

  /* ---------------- فیلتر جدول ارتباط‌ها ---------------- */
  var relFilter = document.getElementById('relFilter');
  var relConf = document.getElementById('relConf');
  function applyRelFilter() {
    var q = (relFilter && relFilter.value || '').trim().toLowerCase();
    var conf = relConf ? relConf.value : '';
    document.querySelectorAll('#relTable tbody tr').forEach(function (tr) {
      var okC = !conf || tr.getAttribute('data-conf') === conf;
      var okQ = !q || tr.textContent.toLowerCase().indexOf(q) !== -1;
      tr.style.display = (okC && okQ) ? '' : 'none';
    });
  }
  if (relFilter) relFilter.addEventListener('input', applyRelFilter);
  if (relConf) relConf.addEventListener('change', applyRelFilter);

  /* ---------------- فیلتر ساختار جدول‌ها ---------------- */
  var sf = document.getElementById('structFilter');
  if (sf) {
    var structs = document.querySelectorAll('[data-struct]');
    var noneMsg = document.getElementById('structEmpty');
    sf.addEventListener('input', function () {
      var q = sf.value.trim().toLowerCase();
      var shown = 0;
      structs.forEach(function (d) {
        var name = d.getAttribute('data-struct');
        var show = name.indexOf(q) !== -1;
        d.style.display = show ? '' : 'none';
        if (show) shown++;
      });
      if (noneMsg) noneMsg.style.display = shown ? 'none' : '';
    });
  }
})();
