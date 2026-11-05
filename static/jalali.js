/* تبدیل تاریخ میلادی به شمسی — الگوریتم jalaali (عمومی، بدون وابستگی) */
(function (global) {
  'use strict';

  function div(a, b) { return ~~(a / b); }
  function mod(a, b) { return a - ~~(a / b) * b; }

  var BREAKS = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
    1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];

  function jalCal(jy) {
    var bl = BREAKS.length, gy = jy + 621, leapJ = -14, jp = BREAKS[0],
      jm, jump, leap, leapG, march, n, i;
    if (jy < jp || jy >= BREAKS[bl - 1]) throw new Error('Invalid Jalaali year ' + jy);
    for (i = 1; i < bl; i += 1) {
      jm = BREAKS[i];
      jump = jm - jp;
      if (jy < jm) break;
      leapJ = leapJ + div(jump, 33) * 8 + div(mod(jump, 33), 4);
      jp = jm;
    }
    n = jy - jp;
    leapJ = leapJ + div(n, 33) * 8 + div(mod(n, 33) + 3, 4);
    if (mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;
    leapG = div(gy, 4) - div((div(gy, 100) + 1) * 3, 4) - 150;
    march = 20 + leapJ - leapG;
    if (jump - n < 6) n = n - jump + div(jump + 4, 33) * 33;
    leap = mod(mod(n + 1, 33) - 1, 4);
    if (leap === -1) leap = 4;
    return { leap: leap, gy: gy, march: march };
  }

  function g2d(gy, gm, gd) {
    var d = div((gy + div(gm - 8, 6) + 100100) * 1461, 4)
      + div(153 * mod(gm + 9, 12) + 2, 5) + gd - 34840408;
    d = d - div(div(gy + 100100 + div(gm - 8, 6), 100) * 3, 4) + 752;
    return d;
  }

  function d2g(jdn) {
    var j, i, gd, gm, gy;
    j = 4 * jdn + 139361631;
    j = j + div(div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
    i = div(mod(j, 1461), 4) * 5 + 308;
    gd = div(mod(i, 153), 5) + 1;
    gm = mod(div(i, 153), 12) + 1;
    gy = div(j, 1461) - 100100 + div(8 - gm, 6);
    return { gy: gy, gm: gm, gd: gd };
  }

  function toJalaali(gy, gm, gd) {
    var jdn = g2d(gy, gm, gd);
    var g = d2g(jdn);
    var jy = g.gy - 621;
    var r = jalCal(jy);
    var jdn1f = g2d(g.gy, 3, r.march);
    var k = jdn - jdn1f, jm, jd;
    if (k >= 0) {
      if (k <= 185) {
        jm = 1 + div(k, 31);
        jd = mod(k, 31) + 1;
        return { jy: jy, jm: jm, jd: jd };
      }
      k -= 186;
    } else {
      jy -= 1;
      k += 179;
      if (r.leap === 1) k += 1;
    }
    jm = 7 + div(k, 30);
    jd = mod(k, 30) + 1;
    return { jy: jy, jm: jm, jd: jd };
  }

  var MONTHS = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
    'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'];

  function faDigits(s) {
    var fa = '۰۱۲۳۴۵۶۷۸۹';
    return String(s).replace(/[0-9]/g, function (d) { return fa[+d]; });
  }

  /* «2024-08» → «مرداد ۱۴۰۳» */
  function monthLabel(ym) {
    var m = /^(\d{4})-(\d{2})/.exec(String(ym));
    if (!m) return String(ym);
    var j = toJalaali(+m[1], +m[2], 15);
    return MONTHS[j.jm - 1] + ' ' + faDigits(j.jy);
  }

  /* «2024-08-05 13:42:11» → «۱۴۰۳/۰۵/۱۵» */
  function dateLabel(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s));
    if (!m) return String(s);
    var j = toJalaali(+m[1], +m[2], +m[3]);
    var mm = ('0' + j.jm).slice(-2), dd = ('0' + j.jd).slice(-2);
    return faDigits(j.jy + '/' + mm + '/' + dd);
  }

  var api = { toJalaali: toJalaali, monthLabel: monthLabel, dateLabel: dateLabel,
    faDigits: faDigits, MONTHS: MONTHS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.Jalali = api;
})(typeof window !== 'undefined' ? window : globalThis);
