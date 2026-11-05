# -*- coding: utf-8 -*-
r"""
استخراج کامل ساختار دیتابیس از بکاپ MySQL — برای طراحی برنامه جدید
=================================================================

این اسکریپت کل اسکلت دیتابیس را استخراج می‌کند تا مبنای نوشتن برنامه و
داشبورد و گزارش‌ساز جدید باشد:

  - همه جدول‌ها و نماها + دستور CREATE کامل هرکدام
  - ستون‌ها با نوع MySQL، پیش‌فرض، NOT NULL، AUTO_INCREMENT، کلید اصلی
  - ایندکس‌ها و کلیدهای خارجیِ اعلام‌شده + ارتباط‌های کشف‌شده از نام ستون‌ها
  - شمارش واقعی ردیف‌ها (اگر viewer_data/parsed.sqlite موجود باشد)
  - چند ردیف نمونه واقعی از هر جدول (پیش‌فرض ۱۰ ردیف)
  - مقادیر یکتای ستون‌های وضعیت/نوع (برای ساخت لیست‌های کشویی دقیق)

منبع داده به ترتیب:
  ۱) viewer_data/parsed.sqlite  — اگر قبلاً با sql_viewer استخراج شده (سریع‌ترین)
  ۲) فایل بکاپ .sql             — خوانش استریمی سریع (فقط ساختار + نمونه‌ها)

خروجی در پوشه db_structure_out کنار همین اسکریپت:
  db_structure.json  ← داده کامل ساختاری (این فایل را برای من بفرستید)
  report.html        ← گزارش فارسی قابل مشاهده در مرورگر
  db_structure.zip   ← هر سه فایل یکجا

اجرا (PowerShell):
  python extract_db_structure.py
  python extract_db_structure.py --sql-file "D:\...\app2_backup_202611031659.sql" --samples 10
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
import zipfile

try:  # کنسول ویندوز ممکن است فارسی را پشتیبانی نکند
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# ======================================================================
#  توابع کمکی پارس بکاپ MySQL — خودکفا (بدون وابستگی به فایل دیگر)
# ======================================================================

BOM = b'\xef\xbb\xbf'
INT_TYPES = {'TINYINT', 'SMALLINT', 'MEDIUMINT', 'INT', 'INTEGER', 'BIGINT',
             'YEAR', 'BOOL', 'BOOLEAN', 'BIT', 'SERIAL'}
REAL_TYPES = {'FLOAT', 'DOUBLE', 'REAL'}
KEY_DEF_WORDS = {'PRIMARY', 'UNIQUE', 'KEY', 'INDEX', 'CONSTRAINT', 'CHECK',
                 'FOREIGN', 'FULLTEXT', 'SPATIAL'}
ESCAPE_MAP = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', '0': '\0',
              'Z': '\x1a', "'": "'", '"': '"', '\\': '\\'}
INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1

CREATE_TABLE_RE = re.compile(
    r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    r'((?:`(?:[^`]|``)+`|[\w$]+)(?:\.(?:`(?:[^`]|``)+`|[\w$]+))?)', re.I)

TARGET_RE = re.compile(
    r'^(?:INSERT|REPLACE)\s+(?:IGNORE\s+)?INTO\s+'
    r'((?:`(?:[^`]|``)+`|[\w$]+)(?:\.(?:`(?:[^`]|``)+`|[\w$]+))?)\s*'
    r'(?:\(([^)]*)\))?\s*VALUES?\b', re.I | re.S)

_QSTR = r"'(?:[^'\\]|\\.|'')*'"
_DQSTR = r'"(?:[^"\\]|\\.|"")*"'
TUPLE_RE = re.compile(r"""
    \(
      (
        (?: [^()'"]
          | %(Q)s
          | %(D)s
        )*
      )
    \)
""" % {'Q': _QSTR, 'D': _DQSTR}, re.VERBOSE)
FIELD_RE = re.compile(r"""
    b'(?:[^'\\]|\\.|'')*'
  | _[A-Za-z0-9]+\s*%(Q)s
  | %(Q)s
  | %(D)s
  | [^,]+
""" % {'Q': _QSTR, 'D': _DQSTR}, re.VERBOSE)
UNESC_SQ_RE = re.compile(r"\\.|''")
UNESC_DQ_RE = re.compile(r'\\.|""')
CHARSET_PREFIX_RE = re.compile(r'^_[A-Za-z0-9]+\s*')
COL_DEF_RE = re.compile(r"^(?:`((?:[^`]|``)+)`|([A-Za-z_$][\w$]*))\s+(.+)$", re.S)
COL_TYPE_RE = re.compile(r'([A-Za-z]+)(\s+(?:INT|INTEGER|PRECISION|VARYING)\b)?\s*(\([^)]*\))?', re.I)
DEFAULT_RE = re.compile(r"\bDEFAULT\s+('(?:[^'\\]|\\.)*'|[\w.+-]+)", re.I)
IDENT_PARTS_RE = re.compile(r'`((?:[^`]|``)+)`|([A-Za-z0-9_$]+)')


def unquote_ident(raw):
    parts = IDENT_PARTS_RE.findall(raw)
    if not parts:
        return raw.strip('`"')
    out = [g1.replace('``', '`') if g1 else g2 for g1, g2 in parts]
    return '.'.join(out)


def ident_list(s):
    out = []
    for g1, g2 in IDENT_PARTS_RE.findall(s or ''):
        out.append(g1.replace('``', '`') if g1 else g2)
    return out


def split_top(s, sep=','):
    parts, cur, depth, i, n = [], [], 0, 0, len(s)
    q = None
    while i < n:
        c = s[i]
        if q:
            cur.append(c)
            if c == '\\' and i + 1 < n:
                cur.append(s[i + 1])
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c in ("'", '"'):
            q = c
            cur.append(c)
            i += 1
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        if c == sep and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if cur:
        parts.append(''.join(cur))
    return parts


def _unescape(inner, q):
    rex = UNESC_SQ_RE if q == "'" else UNESC_DQ_RE

    def _un(m):
        t = m.group(0)
        if t == "''":
            return "'"
        if t == '""':
            return '"'
        ch = t[1]
        if ch in ('%', '_'):
            return '\\' + ch
        return ESCAPE_MAP.get(ch, ch)

    return rex.sub(_un, inner)


def parse_value(s):
    if s is None:
        return None
    s = s.strip()
    if not s or s == 'NULL':
        return None
    c = s[0]
    if c in ("'", '"'):
        inner = s[1:-1]
        doubled = "''" if c == "'" else '""'
        if '\\' in inner or doubled in inner:
            inner = _unescape(inner, c)
        return inner
    if c == 'b' and len(s) >= 3 and s[1] == "'":
        try:
            return int(s[2:-1], 2)
        except ValueError:
            return None
    if c == '_':
        m = CHARSET_PREFIX_RE.match(s)
        if m:
            rest = s[m.end():]
            if rest and (rest[0] in ("'", '"') or rest[:2].lower() == '0x'):
                return parse_value(rest)
        return s
    if s[:2].lower() == '0x' and len(s) > 2:
        try:
            return bytes.fromhex(s[2:])
        except ValueError:
            return s
    try:
        v = int(s)
        return v if INT64_MIN <= v <= INT64_MAX else s
    except ValueError:
        pass
    try:
        v = float(s)
        return v if math.isfinite(v) else s
    except ValueError:
        return s


def parse_col_def(part):
    m = COL_DEF_RE.match(part)
    if not m:
        return None
    name = m.group(1).replace('``', '`') if m.group(1) else m.group(2)
    rest = m.group(3).strip()
    tm = COL_TYPE_RE.match(rest)
    mtype = tm.group(0).strip() if tm else (rest.split()[0] if rest.split() else 'text')
    base = mtype.upper().split()[0].split('(')[0]
    if base in INT_TYPES:
        stype = 'INTEGER'
    elif base in REAL_TYPES:
        stype = 'REAL'
    else:
        stype = 'TEXT'
    dm = DEFAULT_RE.search(rest)
    return {'name': name, 'mtype': mtype, 'stype': stype,
            'notnull': bool(re.search(r'\bNOT\s+NULL\b', rest, re.I)),
            'ai': bool(re.search(r'\bAUTO_INCREMENT\b', rest, re.I)),
            'default': dm.group(1) if dm else None, 'pk': False}


def iter_statements(f, encoding='utf-8', errors='replace'):
    delim = b';'
    buf = None
    for raw in f:
        raw_len = len(raw)
        if raw.startswith(BOM):
            raw = raw[3:]
        line = raw.rstrip(b'\r\n')
        if buf is None:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(b'--') or stripped.startswith(b'#'):
                continue
            dm = re.match(rb'(?i)DELIMITER\s+(\S+)', stripped)
            if dm:
                delim = dm.group(1) or b';'
                continue
            buf = []
        buf.append(line)
        if line.rstrip().endswith(delim):
            text = b'\n'.join(buf).decode(encoding, errors)
            buf = None
            yield text, delim, raw_len
    if buf is not None:
        yield b'\n'.join(buf).decode(encoding, errors), delim, 0


def strip_delim(text, delim):
    d = delim.decode('ascii', 'ignore') if isinstance(delim, bytes) else delim
    t = text.rstrip()
    if d and t.endswith(d):
        t = t[:-len(d)]
    return t.rstrip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SQL = r'D:\Projects\sql_viewer\app2_backup_202611031659.sql'
SQLITE_REL = os.path.join('viewer_data', 'parsed.sqlite')

# ستون‌هایی که احتمالاً «کد وضعیت/نوع» هستند و مقادیر یکتایشان استخراج می‌شود
ENUM_HINTS = ('status', 'state', 'type', 'kind', 'mode', 'level', 'grade',
              'rank', 'role', 'gender', 'sex', 'service', 'confirmed',
              'approved', 'rejected', 'ems', 'danger', 'risk', 'priority',
              'result', 'degree', 'marital', 'education', 'situation',
              'category', 'group_type', 'pay', 'cause', 'reason', 'section')

MAX_ENUMS = 40          # سقف مقادیر یکتا برای هر ستون
TRUNCATE = 300          # حداکثر طول هر مقدار نمونه در خروجی
SAMPLE_LIMIT_SQLITE = 10

# نگاشت ستون‌های شناخته‌شده این دیتابیس به جدول والد
EXTRA_FK = {
    'userid': 'Users', 'usersid': 'Users', 'uid': 'Users',
    'eventid': 'Events', 'eventsid': 'Events',
    'cityid': 'Cities', 'citiesid': 'Cities',
    'roleid': 'Roles', 'rolesid': 'Roles',
    'reserveid': 'EventReserves', 'eventreserveid': 'EventReserves',
    'centerid': 'Centers', 'centersid': 'Centers',
    'provinceid': 'Provinces', 'adviserid': 'Users',
}
NON_FK = {'id', 'guid', 'uuid', 'androidid', 'deviceid', 'imei', 'valid'}
CONF_FA = {'verified': 'تأییدشده (تطابق داده)', 'high': 'نام ستون = نام جدول',
           'med': 'نام ستون ≈ نام جدول', 'low': 'احتمالی'}


def log(msg):
    print(msg, flush=True)


def trunc(v):
    if isinstance(v, (bytes, bytearray)):
        v = '0x' + v.hex()[:40]
    s = str(v)
    return s[:TRUNCATE] + '…' if len(s) > TRUNCATE else s


def is_enum_col(col):
    low = col['name'].lower()
    return any(h in low for h in ENUM_HINTS)


def parse_create_stmt(stmt):
    """پارس کامل CREATE TABLE: (name, cols, keys, fks_declared, tail_meta)"""
    m = CREATE_TABLE_RE.match(stmt)
    if not m:
        return None
    name = unquote_ident(m.group(1))
    i = stmt.find('(', m.end())
    if i < 0:
        return None
    depth, j, q, n = 0, i, None, len(stmt)
    while j < n:
        ch = stmt[j]
        if q:
            if ch == '\\':
                j += 2
                continue
            if ch == q:
                q = None
            j += 1
            continue
        if ch in ('"', "'") or ch == '`':
            q = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                break
        j += 1
    inner = stmt[i + 1:j]
    tail = stmt[j + 1:]

    cols, pk_cols, keys, fks = [], set(), [], []
    for part in split_top(inner):
        part = part.strip()
        if not part:
            continue
        first = part.split(None, 1)[0]
        if first.startswith('`'):
            col = parse_col_def(part)
            if col:
                cols.append(col)
            continue
        fw = first.strip('`"').upper()
        if fw in KEY_DEF_WORDS:
            if fw == 'PRIMARY':
                pk_cols.update(ident_list(part[part.find('('):]))
            elif fw in ('KEY', 'UNIQUE', 'INDEX', 'FULLTEXT', 'SPATIAL'):
                keys.append(part)
            elif fw == 'FOREIGN' or fw == 'CONSTRAINT':
                fks.append(part)
            continue
        col = parse_col_def(part)
        if col:
            cols.append(col)
    for c in cols:
        c['pk'] = c['name'] in pk_cols

    meta = {}
    m2 = re.search(r'ENGINE=(\w+)', tail, re.I)
    meta['engine'] = m2.group(1) if m2 else None
    m2 = re.search(r'DEFAULT CHARSET=(\w+)', tail, re.I)
    meta['charset'] = m2.group(1) if m2 else None
    m2 = re.search(r'AUTO_INCREMENT=(\d+)', tail, re.I)
    meta['auto_increment'] = int(m2.group(1)) if m2 else None
    return name, cols, keys, fks, meta


# ================= بخش ۱: مسیر سریع از parsed.sqlite =================

def extract_from_sqlite(db_path, samples_n, enums_limit):
    from schema import build_schema
    log('[1/4] خواندن ساختار از ' + db_path)
    sp = build_schema(db_path)
    if sp is None:
        return None
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=15)
    # sname (نام واقعی جدول در SQLite) فقط در _meta هست
    try:
        meta_rows = conn.execute(
            'SELECT name, sname, kind FROM _meta').fetchall()
    except sqlite3.Error:
        meta_rows = []
    snames = {n: s for n, s, k in meta_rows if k == 'table'}
    tables = []
    for t in sp['tables']:
        item = {'name': t['name'], 'kind': 'table', 'ddl': t['ddl'],
                'rows': t['rows'], 'cols': t['cols'],
                'fk_inferred': t.get('fk') or {},
                'samples': [], 'enums': {}, 'keys': [], 'fks_declared': [],
                'engine': None, 'charset': None, 'auto_increment': None}
        # اگر نوع ستون‌ها در _meta کامل نبود، از DDL ذخیره‌شده دوباره پارس کن
        if any(not (c.get('mtype') or c.get('stype')) for c in item['cols']) \
                and item['ddl']:
            parsed = parse_create_stmt(item['ddl'])
            if parsed and parsed[0] == t['name']:
                item['cols'] = parsed[1]
                item['keys'], item['fks_declared'] = parsed[2], parsed[3]
                item.update(parsed[4])
                log(f'      ستون‌های {t["name"]} از DDL بازخوانی شد')
        # engine/charset/AUTO_INCREMENT همیشه از DDL
        ddl = t['ddl'] or ''
        m = re.search(r'ENGINE=(\w+)', ddl, re.I)
        item['engine'] = m.group(1) if m else item['engine']
        m = re.search(r'DEFAULT CHARSET=(\w+)', ddl, re.I)
        item['charset'] = m.group(1) if m else item['charset']
        m = re.search(r'AUTO_INCREMENT=(\d+)', ddl, re.I)
        item['auto_increment'] = int(m.group(1)) if m else item['auto_increment']
        if not item['keys'] and not item['fks_declared']:
            for line in ddl.splitlines():
                s = line.strip().rstrip(',')
                u = s.upper()
                if u.startswith(('KEY ', 'UNIQUE KEY', 'INDEX', 'FULLTEXT KEY')):
                    item['keys'].append(s)
                if u.startswith('CONSTRAINT') or u.startswith('FOREIGN KEY'):
                    item['fks_declared'].append(s)
        sname = snames.get(t['name']) or t['name']
        try:
            cur = conn.execute(f'SELECT * FROM "{sname}" LIMIT {samples_n}')
            colnames = [c['name'] for c in t['cols']]
            item['samples'] = []
            for row in cur.fetchall():
                vals = [trunc(v) for v in row]
                if len(vals) == len(colnames):
                    item['samples'].append(dict(zip(colnames, vals)))
                else:
                    item['samples'].append(
                        {f'col{i + 1}': v for i, v in enumerate(vals)})
            for c in t['cols']:
                if is_enum_col(c):
                    try:
                        vals = [r[0] for r in conn.execute(
                            f'SELECT DISTINCT "{c["name"]}" FROM "{sname}" '
                            f'WHERE "{c["name"]}" IS NOT NULL LIMIT {enums_limit}')]
                        vals = [trunc(v) for v in vals]
                        if vals:
                            item['enums'][c['name']] = vals
                    except sqlite3.Error:
                        pass
        except sqlite3.Error:
            pass
        tables.append(item)
        log(f'      جدول {t["name"]}: {t["rows"]:,} ردیف، {len(t["cols"])} ستون')
    views = [{'name': v['name'], 'ddl': v['ddl']} for v in sp.get('views', [])]
    conn.close()
    return finish(sp, tables, views, {'type': 'sqlite', 'path': db_path,
                                      'size': os.path.getsize(db_path)})


# ================= بخش ۲: خوانش استریمی فایل بکاپ =================

class StreamExtractor:
    """خوانش خط‌به‌خط بکاپ: فقط CREATEها + N نمونه اول هر جدول نگه داشته می‌شود"""

    def __init__(self, samples_n):
        self.samples_n = samples_n
        self.tables = {}      # name → item
        self.order = []
        self.views = []
        self.header = {}      # Host / Database / Server version
        self.header_done = False

    # ---------- CREATE TABLE ----------

    def on_create(self, stmt):
        parsed = parse_create_stmt(stmt)
        if not parsed:
            return
        name, cols, keys, fks, meta = parsed
        if name in self.tables:
            return
        item = {'name': name, 'kind': 'table', 'ddl': stmt,
                'rows': None, 'cols': cols, 'fk_inferred': {},
                'samples': [], 'enums': {}, 'keys': keys,
                'fks_declared': fks}
        item.update(meta)
        self.tables[name] = item
        self.order.append(name)
        log(f'      جدول {name}: {len(cols)} ستون')

    # ---------- نمونه‌ها ----------

    def on_insert(self, stmt):
        m = TARGET_RE.match(stmt)
        if not m:
            return
        tname = unquote_ident(m.group(1))
        item = self.tables.get(tname)
        if item is None:
            # جدول بدون CREATE — فقط نامش را ثبت کن
            item = {'name': tname, 'kind': 'table', 'ddl': None,
                    'rows': None, 'cols': [], 'fk_inferred': {},
                    'samples': [], 'enums': {}, 'keys': [],
                    'fks_declared': [], 'engine': None, 'charset': None,
                    'auto_increment': None}
            self.tables[tname] = item
            self.order.append(tname)
            log(f'      جدول {tname} (بدون CREATE — از روی داده)')
        colstr = m.group(2)
        header_cols = ident_list(colstr) if (colstr and colstr.strip()) else None
        if header_cols and not item['cols']:
            item['cols'] = [{'name': c, 'mtype': '?', 'stype': 'TEXT',
                             'notnull': False, 'ai': False, 'default': None,
                             'pk': False} for c in header_cols]
        if len(item['samples']) >= self.samples_n:
            return
        want = self.samples_n - len(item['samples'])
        values_part = stmt[m.end():]
        colnames = [c['name'] for c in item['cols']]
        for tm in TUPLE_RE.finditer(values_part):
            if want <= 0:
                break
            vals = [trunc(parse_value(x.group(0)))
                    for x in FIELD_RE.finditer(tm.group(1))]
            if not vals:
                continue
            if header_cols:
                row = dict(zip(header_cols, vals))
            elif colnames and len(colnames) == len(vals):
                row = dict(zip(colnames, vals))
            else:
                row = {f'col{i + 1}': v for i, v in enumerate(vals)}
            item['samples'].append(row)
            # مقادیر یکتای ستون‌های وضعیت/نوع از همان نمونه‌ها
            for k, v in row.items():
                if v is None or v == '':
                    continue
                low = k.lower()
                if any(h in low for h in ENUM_HINTS):
                    lst = item['enums'].setdefault(k, [])
                    if v not in lst and len(lst) < MAX_ENUMS:
                        lst.append(v)
            want -= 1

    def on_view(self, stmt):
        m = re.search(r'VIEW\s+((?:`(?:[^`]|``)+`|[\w$]+))', stmt, re.I)
        if not m:
            return
        name = unquote_ident(m.group(1))
        if all(v['name'] != name for v in self.views):
            self.views.append({'name': name, 'ddl': stmt})

    def on_header(self, line):
        """کامنت‌های ابتدای بکاپ: -- Host: ... ، -- Database: ..."""
        if self.header_done:
            return
        m = re.match(r'--\s*(Host|Database|Server version|MySQL dump)\s*:?\s*(.*)',
                     line.strip())
        if m:
            self.header[m.group(1)] = m.group(2).strip()
        if re.match(r'--\s*$|CREATE|DROP|SET|/\*!', line.strip(), re.I):
            if len(self.header) >= 2:
                self.header_done = True


def extract_from_sql(sql_path, samples_n):
    size = os.path.getsize(sql_path)
    log(f'[1/4] خوانش استریمی {size / 1e9:.2f} گیگابایت از {sql_path}')
    ex = StreamExtractor(samples_n)
    t0 = time.time()
    done_bytes = 0
    next_pct = 2
    with open(sql_path, 'rb', buffering=1 << 20) as f:
        for stmt, delim, raw_len in iter_statements(f):
            done_bytes += raw_len
            pct = done_bytes * 100.0 / max(1, size)
            if pct >= next_pct:
                log(f'      … {pct:.0f}% — {time.time() - t0:.0f} ثانیه')
                next_pct += 2
            s = stmt.strip()
            if not s:
                continue
            head = s[:12].upper()
            if head.startswith('CREATE'):
                if 'TABLE' in head:
                    ex.on_create(strip_delim(s, delim))
                elif 'VIEW' in s[:40].upper() or 'VIEW' in s[:120].upper():
                    ex.on_view(strip_delim(s, delim))
            elif head.startswith(('INSERT', 'REPLACE')):
                ex.on_insert(strip_delim(s, delim))
            elif head.startswith('--'):
                ex.on_header(s.splitlines()[0])
    log(f'[2/4] خوانش تمام شد در {time.time() - t0:.0f} ثانیه — '
        f'{len(ex.tables)} جدول، {len(ex.views)} نما')
    return finish(None, [ex.tables[k] for k in ex.order], ex.views,
                  {'type': 'sql', 'path': sql_path, 'size': size,
                   'header': ex.header})


# ================= ارتباط‌های نام‌محور (برای مسیر بکاپ) =================

def _fk_stem(col_name):
    low = col_name.lower()
    if low in NON_FK or not low.endswith('id'):
        return None
    stem = re.sub(r'_?id$', '', col_name, flags=re.I).strip('_ ')
    return stem or None


def _parent_pk(t):
    for c in t['cols']:
        if c.get('pk'):
            return c['name']
    for c in t['cols']:
        if c['name'].lower() == 'id':
            return c['name']
    return t['cols'][0]['name'] if t['cols'] else 'Id'


def name_relations(tables):
    lower = {t['name'].lower(): t['name'] for t in tables if t['kind'] == 'table'}
    tmap = {t['name']: t for t in tables if t['kind'] == 'table'}
    rels = []
    for t in tables:
        if t['kind'] != 'table':
            continue
        for c in t['cols']:
            stem = _fk_stem(c['name'])
            extra = EXTRA_FK.get(c['name'].lower())
            parent = conf = None
            if extra and extra.lower() in lower and extra.lower() != t['name'].lower():
                parent, conf = lower[extra.lower()], 'high'
            if parent is None and stem:
                for cand, cf in ((stem, 'high'), (stem + 's', 'med'),
                                 (stem + 'es', 'med'),
                                 (stem[:-1] + 'ies', 'med') if stem.endswith('y') else (None, None)):
                    if cand and cand.lower() in lower:
                        hit = lower[cand.lower()]
                        if hit != t['name']:
                            parent, conf = hit, cf
                        elif not c.get('pk'):
                            parent, conf = hit, 'med'
                        break
            if parent:
                pt = tmap.get(parent)
                rels.append({'child': t['name'], 'child_col': c['name'],
                             'parent': parent,
                             'parent_col': _parent_pk(pt) if pt else 'Id',
                             'confidence': conf, 'ratio': None})
    return rels


# ================= بخش ۳: بسته‌بندی خروجی =================

def finish(sp, tables, views, source):
    relations = []
    if sp:  # از موتور schema.py (با راستی‌آزمایی داده)
        for r in sp.get('relations', []):
            if r['confidence'] in ('verified', 'high', 'med'):
                relations.append({'child': r['child'], 'child_col': r['child_col'],
                                  'parent': r['parent'], 'parent_col': r['parent_col'],
                                  'confidence': r['confidence'],
                                  'ratio': r.get('ratio')})
    else:
        relations = name_relations(tables)
    total_rows = sum(t['rows'] or 0 for t in tables if t['kind'] == 'table')
    known = sum(1 for t in tables if t.get('rows') is not None)
    return {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'source': source,
        'server_info': source.get('header', {}),
        'summary': {'tables': len([t for t in tables if t['kind'] == 'table']),
                    'views': len(views), 'total_rows': total_rows,
                    'rows_known_for': known,
                    'relations': len(relations)},
        'tables': tables,
        'views': views,
        'relations': relations,
        'conf_fa': CONF_FA,
    }


# ================= بخش ۴: گزارش HTML فارسی =================

HTML_TMPL = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<title>ساختار کامل دیتابیس</title>
<style>
 body{{font-family:Tahoma,Vazirmatn,sans-serif;background:#f4f5f7;margin:0;color:#1f2430}}
 .wrap{{max-width:1150px;margin:0 auto;padding:24px}}
 h1{{font-size:22px}} h2{{font-size:17px;margin:34px 0 10px;border-right:4px solid #e91e63;padding-right:10px}}
 .kpis{{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}}
 .kpi{{background:#fff;border:1px solid #e3e6ec;border-radius:12px;padding:14px 20px;min-width:150px}}
 .kpi b{{display:block;font-size:24px;color:#e91e63}}
 table{{border-collapse:collapse;width:100%;background:#fff;font-size:12.5px}}
 th,td{{border:1px solid #e3e6ec;padding:6px 9px;text-align:right;vertical-align:top}}
 th{{background:#fdeef3;position:sticky;top:0}}
 tr:nth-child(even) td{{background:#fafbfd}}
 .meta{{color:#667;font-size:12px}}
 .fk{{color:#0a7d38;font-size:11.5px}}
 code{{background:#eef1f6;padding:1px 5px;border-radius:5px;font-size:11.5px;direction:ltr;display:inline-block}}
 details{{margin-bottom:14px;border:1px solid #e3e6ec;border-radius:12px;background:#fff}}
 summary{{cursor:pointer;padding:12px 16px;font-weight:bold;background:#fff4f7;border-radius:12px}}
 details[open] summary{{border-bottom:1px solid #e3e6ec;border-radius:12px 12px 0 0}}
 .pad{{padding:12px 16px;overflow-x:auto}}
 .enums li{{font-size:12px}}
 .ddl{{direction:ltr;text-align:left;white-space:pre;background:#0f172a;color:#d7e3ff;border-radius:10px;padding:12px;font-size:11px;overflow-x:auto}}
 .num{{direction:ltr;display:inline-block}}
</style>
</head><body><div class="wrap">
<h1>ساختار کامل دیتابیس — {gen}</h1>
<div class="meta">منبع: <span class="num">{src_path}</span> ({src_size}) — روش: {src_type}
{srv}</div>
<div class="kpis">
 <div class="kpi"><b>{k_tables}</b>جدول</div>
 <div class="kpi"><b>{k_views}</b>نما</div>
 <div class="kpi"><b class="num">{k_rows}</b>مجموع ردیف‌ها</div>
 <div class="kpi"><b>{k_rels}</b>ارتباط کشف‌شده</div>
</div>
<h2>ارتباط بین جدول‌ها</h2>
<table><tr><th>جدول فرزند</th><th>ستون</th><th>جدول والد</th><th>نوع کشف</th><th>تطابق داده</th></tr>
{rel_rows}</table>
<h2>جدول‌ها ({k_tables})</h2>
{table_cards}
</div></body></html>"""


def esc(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def build_report(data):
    rel_rows = []
    for r in data['relations']:
        ratio = ('%' + str(int(r['ratio'] * 100))) if r.get('ratio') is not None else '—'
        rel_rows.append(
            f"<tr><td><code>{esc(r['child'])}</code></td><td><code>{esc(r['child_col'])}</code></td>"
            f"<td><code>{esc(r['parent'])}</code></td>"
            f"<td>{esc(data['conf_fa'].get(r['confidence'], r['confidence']))}</td>"
            f"<td class='num'>{ratio}</td></tr>")
    cards = []
    for t in data['tables']:
        fk = t.get('fk_inferred') or {}
        rows = []
        for c in t['cols']:
            f = fk.get(c['name'])
            ftxt = (f"<div class='fk'>→ {esc(f['table'])}.{esc(f.get('col') or '')}"
                    f" ({esc(data['conf_fa'].get(f['confidence'], ''))})</div>") if f else ''
            badge = ' 🔑' if c.get('pk') else ''
            rows.append(
                f"<tr><td><code>{esc(c['name'])}</code>{badge}</td>"
                f"<td class='num'>{esc(c.get('mtype') or '')}</td>"
                f"<td>{'✔' if c.get('notnull') else ''}{' · AI' if c.get('ai') else ''}</td>"
                f"<td class='num'>{esc(c.get('default') or '')}</td><td>{ftxt}</td></tr>")
        samples = ''
        if t.get('samples'):
            heads = list(t['samples'][0].keys())
            srows = ''.join('<tr>' + ''.join(
                f"<td class='num'>{esc(r.get(h, ''))}</td>" for h in heads) + '</tr>'
                for r in t['samples'][:10])
            samples = (f"<h4 style='margin:10px 0 4px'>نمونه داده‌ها</h4>"
                       f"<table><tr>{''.join(f'<th>{esc(h)}</th>' for h in heads)}</tr>{srows}</table>")
        enums = ''
        if t.get('enums'):
            lis = ''.join(f"<li><code>{esc(k)}</code>: {esc(' | '.join(str(x) for x in v[:20]))}</li>"
                          for k, v in t['enums'].items())
            enums = f"<h4 style='margin:10px 0 4px'>مقادیر وضعیت/نوع</h4><ul class='enums'>{lis}</ul>"
        rc = f"{t['rows']:,}" if isinstance(t.get('rows'), int) else '؟'
        ddl = f"<div class='ddl'>{esc(t.get('ddl') or '—')}</div>" if t.get('ddl') else ''
        meta_bits = []
        if t.get('engine'):
            meta_bits.append('Engine: ' + t['engine'])
        if t.get('charset'):
            meta_bits.append('Charset: ' + t['charset'])
        if t.get('auto_increment'):
            meta_bits.append('AI=' + str(t['auto_increment']))
        if t.get('keys'):
            meta_bits.append(f"{len(t['keys'])} ایندکس")
        cards.append(f"""
<details><summary>📄 <code>{esc(t['name'])}</code> — {rc} ردیف، {len(t['cols'])} ستون
{' · '.join(esc(b) for b in meta_bits)}</summary>
<div class="pad">
<table><tr><th>ستون</th><th>نوع MySQL</th><th>شرایط</th><th>پیش‌فرض</th><th>ارتباط</th></tr>{''.join(rows)}</table>
{samples}{enums}{ddl}
</div></details>""")
    s = data['source']
    srv = ''
    if data.get('server_info'):
        srv = '<br>' + ' — '.join(f"{esc(k)}: {esc(v)}" for k, v in data['server_info'].items())
    return HTML_TMPL.format(
        gen=data['generated_at'], src_path=esc(s.get('path') or '?'),
        src_size=f"{(s.get('size') or 0) / 1e9:.2f} GB",
        src_type='دیتابیس استخراج‌شده' if s['type'] == 'sqlite' else 'فایل بکاپ SQL',
        srv=srv,
        k_tables=data['summary']['tables'], k_views=data['summary']['views'],
        k_rows=f"{data['summary']['total_rows']:,}",
        k_rels=data['summary']['relations'],
        rel_rows=''.join(rel_rows), table_cards=''.join(cards))


# ================= اجرا =================

def main():
    ap = argparse.ArgumentParser(description='استخراج کامل ساختار دیتابیس')
    ap.add_argument('--sql-file', help='مسیر فایل بکاپ .sql (پیش‌فرض: کشف خودکار)')
    ap.add_argument('--db-file', help='مسیر parsed.sqlite (پیش‌فرض: viewer_data/parsed.sqlite)')
    ap.add_argument('--samples', type=int, default=10, help='تعداد ردیف نمونه هر جدول')
    ap.add_argument('--enums-limit', type=int, default=MAX_ENUMS,
                    help='سقف مقادیر یکتای هر ستون وضعیت/نوع')
    a = ap.parse_args()

    out_dir = os.path.join(BASE_DIR, 'db_structure_out')
    os.makedirs(out_dir, exist_ok=True)

    data = None
    db_path = a.db_file or os.path.join(BASE_DIR, SQLITE_REL)
    if os.path.isfile(db_path):
        try:
            data = extract_from_sqlite(db_path, a.samples, a.enums_limit)
        except Exception as e:  # noqa: BLE001
            log(f'[!] خواندن parsed.sqlite ناموفق بود ({e})؛ از فایل بکاپ می‌خوانم.')
            data = None
    if data is None or not data['tables']:
        sql = a.sql_file
        if not sql:
            for cand in (DEFAULT_SQL, os.path.join(BASE_DIR, 'app2_backup_202611031659.sql')):
                if os.path.isfile(cand):
                    sql = cand
                    break
            if not sql:
                cands = [os.path.join(BASE_DIR, p) for p in os.listdir(BASE_DIR)
                         if p.lower().endswith('.sql') and os.path.getsize(
                             os.path.join(BASE_DIR, p)) > 1024]
                if cands:
                    sql = max(cands, key=os.path.getsize)
        if not sql or not os.path.isfile(sql):
            log('[!] نه parsed.sqlite و نه فایل بکاپ .sql پیدا نشد.')
            log('    مسیر را بدهید:  python extract_db_structure.py --sql-file "مسیر فایل"')
            sys.exit(1)
        data = extract_from_sql(sql, a.samples)

    log('[3/4] ساخت گزارش فارسی…')
    json_path = os.path.join(out_dir, 'db_structure.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    html = build_report(data)
    html_path = os.path.join(out_dir, 'report.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    readme = (
        'این بسته، ساختار کامل دیتابیس سامانه را دارد.\n'
        f'ساخته‌شده: {data["generated_at"]}\n'
        f'منبع: {data["source"]["path"]}\n\n'
        'محتوا:\n'
        '  db_structure.json  ← فایل داده کامل (برای طراحی برنامه جدید)\n'
        '  report.html        ← گزارش فارسی برای مشاهده در مرورگر\n'
        'این فایل zip را برای سازنده برنامه بفرستید.\n')
    readme_path = os.path.join(out_dir, 'README.txt')
    with open(readme_path, 'w', encoding='utf-8') as f:
        f.write(readme)

    zip_path = os.path.join(BASE_DIR, 'db_structure.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(json_path, 'db_structure.json')
        z.write(html_path, 'report.html')
        z.write(readme_path, 'README.txt')

    s = data['summary']
    log('[4/4] تمام شد ✔')
    log('=' * 60)
    log(f'  جدول‌ها: {s["tables"]}   نماها: {s["views"]}   ارتباط‌ها: {s["relations"]}')
    log(f'  مجموع ردیف‌ها: {s["total_rows"]:,}')
    log(f'  خروجی JSON:  {json_path}')
    log(f'  گزارش فارسی: {html_path}')
    log(f'  فایل ارسالی: {zip_path}')
    log('  ← این فایل zip را برای من بفرستید تا برنامه کامل را بسازم')
    log('=' * 60)


if __name__ == '__main__':
    main()
