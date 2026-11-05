# -*- coding: utf-8 -*-
"""
استخراج‌کننده فایل بکاپ MySQL (خروجی mysqldump)

این ماژول فایل بکاپ را به صورت «استریم» و خط‌به‌خط می‌خواند تا برای فایل‌های
چند گیگابایتی هم مناسب باشد (کل فایل هرگز یکجا در حافظه لود نمی‌شود).
دستورهای CREATE TABLE و INSERT استخراج و داده‌ها در یک دیتابیس SQLite
ذخیره می‌شود تا صفحه‌بندی و جستجو سریع انجام شود.

اجرا از خط فرمان (اختیاری — می‌توانید استخراج را پیش از اجرای برنامه وب انجام دهید):
    python dump_parser.py --file "مسیر فایل بکاپ.sql"
"""

import argparse
import json
import math
import os
import re
import sqlite3
import time
import traceback

BOM = b'\xef\xbb\xbf'

# انواع داده MySQL به نوع معادل SQLite
INT_TYPES = {'TINYINT', 'SMALLINT', 'MEDIUMINT', 'INT', 'INTEGER', 'BIGINT',
             'YEAR', 'BOOL', 'BOOLEAN', 'BIT', 'SERIAL'}
REAL_TYPES = {'FLOAT', 'DOUBLE', 'REAL'}

KEY_DEF_WORDS = {'PRIMARY', 'UNIQUE', 'KEY', 'INDEX', 'CONSTRAINT', 'CHECK',
                 'FOREIGN', 'FULLTEXT', 'SPATIAL'}

ESCAPE_MAP = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', '0': '\0',
              'Z': '\x1a', "'": "'", '"': '"', '\\': '\\'}

INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1

# ---------- الگوهای آماده ----------

CREATE_TABLE_RE = re.compile(
    r'^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    r'((?:`(?:[^`]|``)+`|[\w$]+)(?:\.(?:`(?:[^`]|``)+`|[\w$]+))?)',
    re.I)

TARGET_RE = re.compile(
    r'^(?:INSERT|REPLACE)\s+(?:IGNORE\s+)?INTO\s+'
    r'((?:`(?:[^`]|``)+`|[\w$]+)(?:\.(?:`(?:[^`]|``)+`|[\w$]+))?)\s*'
    r'(?:\(([^)]*)\))?\s*VALUES?\b',
    re.I | re.S)

VIEW_NAME_RE = re.compile(r'VIEW\s+((?:`(?:[^`]|``)+`|[\w$]+))', re.I)

# رشته‌های نقل‌قولی SQL — با پشتیبانی از بک‌اسلش (\') و نقل‌قول دوبل ('')
_QSTR = r"'(?:[^'\\]|\\.|'')*'"
_DQSTR = r'"(?:[^"\\]|\\.|"")*"'

# یک تاپل مقدار مثل (1,'a',NULL) — پرانتز داخل رشته‌های نقل‌قولی پشتیبانی می‌شود
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

# جداسازی فیلدهای داخل یک تاپل
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
    """تبدیل `نام` یا db.`t` به نام ساده"""
    parts = IDENT_PARTS_RE.findall(raw)
    if not parts:
        return raw.strip('`"')
    out = []
    for g1, g2 in parts:
        out.append(g1.replace('``', '`') if g1 else g2)
    return '.'.join(out)


def ident_list(s):
    """استخراج فهرست نام ستون‌ها از بخش (a,b,c)"""
    out = []
    for g1, g2 in IDENT_PARTS_RE.findall(s or ''):
        out.append(g1.replace('``', '`') if g1 else g2)
    return out


def split_top(s, sep=','):
    """جداسازی با درنظر گرفتن پرانتز تودرتو و رشته‌های نقل‌قولی"""
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
    """بازکردن escape های داخل رشته (طبق قواعد MySQL)"""
    rex = UNESC_SQ_RE if q == "'" else UNESC_DQ_RE

    def _un(m):
        t = m.group(0)
        if t == "''":
            return "'"
        if t == '""':
            return '"'
        ch = t[1]
        if ch in ('%', '_'):
            return '\\' + ch  # طبق قواعد MySQL، \% و \_ به همان شکل می‌مانند
        return ESCAPE_MAP.get(ch, ch)

    return rex.sub(_un, inner)


def parse_value(s):
    """تبدیل مقدار متنی بکاپ به مقدار Python/SQLite"""
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
    """پارس تعریف یک ستون از CREATE TABLE"""
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
    return {
        'name': name,
        'mtype': mtype,
        'stype': stype,
        'notnull': bool(re.search(r'\bNOT\s+NULL\b', rest, re.I)),
        'ai': bool(re.search(r'\bAUTO_INCREMENT\b', rest, re.I)),
        'default': dm.group(1) if dm else None,
        'pk': False,
    }


def iter_statements(f, encoding='utf-8', errors='replace'):
    """خواندن استریم فایل و بازدهی جمله‌های کامل: (متن, جداکننده, طول بایت)"""
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


class Importer:
    """استخراج کامل یک فایل بکاپ به SQLite همراه گزارش پیشرفت"""

    def __init__(self, sql_path, db_path, status_path, encoding='utf-8'):
        self.sql_path = sql_path
        self.db_path = db_path
        self.status_path = status_path
        self.encoding = encoding
        self.tables = {}
        self.used_snames = set()
        self._last_save = 0.0
        self._since_commit = 0
        self.status = {
            'state': 'idle', 'phase': '', 'error': None,
            'file': sql_path, 'total_bytes': 0, 'bytes_done': 0, 'percent': 0.0,
            'current_table': None, 'rows': 0, 'tables': [], 'errors': [],
            'warnings': 0, 'started_at': None, 'finished_at': None,
            'elapsed': None, 'db_path': db_path,
        }

    # ---------- وضعیت ----------

    def save_status(self, force=False):
        """ذخیره وضعیت — تضمین‌شده هرگز خطا پرتاب نمی‌کند.

        روی ویندوز os.replace وقتی فایل مقصد توسط پروسه دیگری (خواندن همزمان
        /api/status یا اسکن آنتی‌ویروس) باز باشد با خطای WinError 5 شکست
        می‌خورد. اگر این خطا به نخ استخراج برسد، کل استخراج می‌میرد و صفحه
        برای همیشه روی همان درصد می‌ماند. پس: چند بار تلاش مجدد، بعد نوشتن
        مستقیم جایگزین، و در بدترین حالت فقط از دست دادن همین یک به‌روزرسانی.
        """
        now = time.time()
        if not force and now - self._last_save < 0.5:
            return
        self._last_save = now
        try:
            d = os.path.dirname(self.status_path)
            if d:
                os.makedirs(d, exist_ok=True)
            payload = json.dumps(self.status, ensure_ascii=False)
            tmp = self.status_path + '.tmp'
            for attempt in range(5):
                try:
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(payload)
                    os.replace(tmp, self.status_path)
                    return
                except PermissionError:
                    if attempt < 4:
                        time.sleep(0.05 + 0.1 * attempt)
                except OSError:
                    break
            # روش جایگزین: نوشتن مستقیم (غیر اتمیک). خواننده‌ها با try/except
            # فایل نیمه‌نوشته را تحمل می‌کنند.
            try:
                with open(self.status_path, 'w', encoding='utf-8') as f:
                    f.write(payload)
            except OSError:
                pass
        except Exception:
            pass

    def add_error(self, msg):
        lst = self.status.setdefault('errors', [])
        if len(lst) < 30:
            lst.append(str(msg)[:500])

    def touch_table_status(self, name, cols_count=None):
        info = self.tables.get(name)
        if info is None:
            return
        for t in self.status['tables']:
            if t['name'] == name:
                t['rows'] = info['rows']
                break
        else:
            self.status['tables'].append({
                'name': name, 'kind': info['kind'], 'rows': info['rows'],
                'cols': cols_count if cols_count is not None else len(info['cols']),
            })
        self.status['current_table'] = name

    # ---------- نام یکتای جدول در SQLite ----------

    def unique_sname(self, name):
        base = name.replace('.', '__') or 'table'
        s = base
        k = 2
        while s in self.used_snames:
            s = f'{base}_{k}'
            k += 1
        self.used_snames.add(s)
        return s

    # ---------- پارس CREATE TABLE ----------

    def parse_create(self, conn, stmt):
        m = CREATE_TABLE_RE.match(stmt)
        if not m:
            return
        name = unquote_ident(m.group(1))
        i = stmt.find('(', m.end())
        if i < 0:
            return
        # پیدا کردن پرانتز بسته متناظر با احترام به نقل‌قول‌ها
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

        cols = []
        pk_cols = set()
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
                continue
            col = parse_col_def(part)
            if col:
                cols.append(col)
        for c in cols:
            c['pk'] = c['name'] in pk_cols

        if name in self.tables:
            return  # تعریف تکراری
        sname = self.unique_sname(name)
        cols_sql = ', '.join('"' + c['name'].replace('"', '""') + '" ' + c['stype']
                             for c in cols) or '"_id" INTEGER'
        conn.execute(f'CREATE TABLE "{sname}" ({cols_sql})')
        self.tables[name] = {'sname': sname, 'cols': cols, 'rows': 0, 'kind': 'table'}
        conn.execute(
            'INSERT OR REPLACE INTO _meta(name,sname,kind,create_sql,columns,rows) '
            'VALUES(?,?,?,?,?,0)',
            (name, sname, 'table', stmt, json.dumps(cols, ensure_ascii=False)))
        conn.commit()
        self.touch_table_status(name, len(cols))
        self.save_status()

    # ---------- نماها ----------

    def record_view(self, conn, sql):
        m = VIEW_NAME_RE.search(sql)
        if not m:
            return
        name = unquote_ident(m.group(1))
        if name in self.tables:
            return
        sname = self.unique_sname(name)
        self.tables[name] = {'sname': sname, 'cols': [], 'rows': 0, 'kind': 'view'}
        conn.execute(
            'INSERT OR REPLACE INTO _meta(name,sname,kind,create_sql,columns,rows) '
            'VALUES(?,?,?,?,?,0)',
            (name, sname, 'view', sql, '[]'))
        conn.commit()
        self.touch_table_status(name, 0)

    # ---------- درج داده‌ها ----------

    def flush(self, conn, sql, batch):
        try:
            conn.executemany(sql, batch)
            self._since_commit += len(batch)
            if self._since_commit >= 50000:
                conn.commit()
                self._since_commit = 0
        except sqlite3.Error as e:
            conn.rollback()
            bad = 0
            for row in batch:
                try:
                    conn.execute(sql, row)
                except sqlite3.Error:
                    bad += 1
            conn.commit()
            if bad:
                self.status['warnings'] += 1
                self.add_error(f'{bad} ردیف درج نشد: {e}')

    def ensure_unknown_table(self, conn, tname, header_cols):
        """جدولی که CREATE آن در بکاپ نبوده — از روی INSERT ساخته می‌شود"""
        cols = [{'name': c, 'mtype': 'text', 'stype': 'TEXT', 'notnull': False,
                 'ai': False, 'default': None, 'pk': False} for c in (header_cols or [])]
        sname = self.unique_sname(tname)
        cols_sql = ', '.join('"' + c['name'].replace('"', '""') + '" TEXT' for c in cols) \
            or '"_id" TEXT'
        try:
            conn.execute(f'CREATE TABLE "{sname}" ({cols_sql})')
        except sqlite3.Error as e:
            self.add_error(f'ساخت جدول {tname} ممکن نشد: {e}')
            return None
        self.tables[tname] = {'sname': sname, 'cols': cols, 'rows': 0, 'kind': 'table'}
        conn.execute(
            'INSERT OR REPLACE INTO _meta(name,sname,kind,create_sql,columns,rows) '
            'VALUES(?,?,?,?,?,0)',
            (tname, sname, 'table', None, json.dumps(cols, ensure_ascii=False)))
        conn.commit()
        self.status['warnings'] += 1
        self.add_error(f'جدول «{tname}» بدون CREATE TABLE ساخته شد (فقط از روی داده‌ها).')
        return self.tables[tname]

    def handle_insert(self, conn, stmt):
        m = TARGET_RE.match(stmt)
        if not m:
            self.status['warnings'] += 1
            return
        tname = unquote_ident(m.group(1))
        colstr = m.group(2)
        header_cols = ident_list(colstr) if (colstr and colstr.strip()) else None

        info = self.tables.get(tname)
        if info is None or info['kind'] != 'table':
            info = self.ensure_unknown_table(conn, tname, header_cols)
            if info is None:
                return

        is_replace = stmt[:7].upper() == 'REPLACE'
        is_ignore = bool(re.match(r'^INSERT\s+IGNORE\b', stmt, re.I))
        or_clause = 'OR REPLACE ' if is_replace else ('OR IGNORE ' if is_ignore else '')

        values_part = stmt[m.end():]

        def build_sql(width):
            if header_cols:
                cs = ', '.join('"' + c.replace('"', '""') + '"' for c in header_cols)
            else:
                cs = ', '.join('"' + c['name'].replace('"', '""') + '"'
                               for c in info['cols'])
            ph = ','.join('?' * width)
            return f'INSERT {or_clause}INTO "{info["sname"]}" ({cs}) VALUES ({ph})'

        # استخراج تاپل‌ها همراه با بررسی پوشش کامل متن VALUES
        rows = []
        gap = False
        last_end = 0
        for tm in TUPLE_RE.finditer(values_part):
            between = values_part[last_end:tm.start()]
            if between.strip(' \t\r\n,'):
                gap = True
            last_end = tm.end()
            vals = [parse_value(x.group(0)) for x in FIELD_RE.finditer(tm.group(1))]
            if vals:
                rows.append(vals)
        if values_part[last_end:].strip(' \t\r\n,'):
            gap = True
        if gap:
            self.status['warnings'] += 1
            self.add_error(f'بخشی از INSERT جدول «{tname}» قابل پارس نبود و نادیده گرفته شد.')
        if not rows:
            if values_part.strip():
                self.status['warnings'] += 1
                self.add_error(f'هیچ تاپلی از INSERT جدول «{tname}» پارس نشد؛ فرمت ممکن است پشتیبانی نشده باشد.')
            return

        def flush_rows(batch):
            if not batch:
                return
            width = max(len(v) for v in batch)
            # گسترش جدول برای ستون‌های اضافه (با commit تا ALTER در rollback از بین نرود)
            if not header_cols and width > len(info['cols']):
                while len(info['cols']) < width:
                    cname = f'col{len(info["cols"]) + 1}'
                    conn.execute(f'ALTER TABLE "{info["sname"]}" ADD COLUMN "{cname}" TEXT')
                    info['cols'].append({'name': cname, 'mtype': 'text', 'stype': 'TEXT',
                                         'notnull': False, 'ai': False, 'default': None,
                                         'pk': False})
                conn.execute('UPDATE _meta SET columns=? WHERE name=?',
                             (json.dumps(info['cols'], ensure_ascii=False), tname))
                conn.commit()
            target_width = len(header_cols) if header_cols else len(info['cols'])
            if header_cols:
                extra = sum(1 for v in batch if len(v) > target_width)
                if extra:
                    self.status['warnings'] += 1
                    self.add_error(f'{extra} ردیف در جدول «{tname}» ستون بیشتری از سرستون داشت.')
            norm = []
            for v in batch:
                if len(v) < target_width:
                    v = v + [None] * (target_width - len(v))
                elif len(v) > target_width:
                    v = v[:target_width]
                norm.append(tuple(v))
            sql = build_sql(target_width)
            try:
                conn.executemany(sql, norm)
                self._since_commit += len(norm)
                if self._since_commit >= 50000:
                    conn.commit()
                    self._since_commit = 0
            except sqlite3.Error as e:
                conn.rollback()
                bad = 0
                for row in norm:
                    try:
                        conn.execute(sql, row)
                    except sqlite3.Error:
                        bad += 1
                conn.commit()
                if bad:
                    self.status['warnings'] += 1
                    self.add_error(f'{bad} ردیف در جدول «{tname}» درج نشد: {e}')

        # پردازش به‌صورت دسته‌ای برای مصرف حافظه کم
        CHUNK = 2000
        for i in range(0, len(rows), CHUNK):
            chunk = rows[i:i + CHUNK]
            flush_rows(chunk)
            info['rows'] += len(chunk)
            self.status['rows'] += len(chunk)
            self.touch_table_status(tname)
            self.save_status()

    # ---------- اجرای اصلی ----------

    def run(self):
        t0 = time.time()
        st = self.status
        try:
            total_bytes = os.path.getsize(self.sql_path)
            d = os.path.dirname(self.db_path)
            if d:
                os.makedirs(d, exist_ok=True)

            # محافظ: اگر فایل انتخابی متنی-SQL نباشد (مثلاً اشتباهاً یک فایل
            # باینری مثل خود دیتابیس SQLite)، هرگز دیتابیس قبلی را پاک نکن؛
            # خطا بده تا کاربر مسیر درست بکاپ را بدهد.
            with open(self.sql_path, 'rb') as probe:
                head = probe.read(8192)
            if b'\x00' in head:
                raise ValueError(
                    'فایل انتخاب‌شده بکاپ متنی SQL نیست (باینری به نظر می‌رسد). '
                    'مسیر فایل بکاپ .sql را بررسی کنید.')

            if os.path.exists(self.db_path):
                os.remove(self.db_path)

            st.update(state='importing', phase='آماده‌سازی…', total_bytes=total_bytes,
                      bytes_done=0, percent=0.0, current_table=None, rows=0,
                      tables=[], started_at=t0, finished_at=None)
            self.save_status(force=True)

            conn = sqlite3.connect(self.db_path)
            conn.execute('PRAGMA journal_mode=MEMORY')
            conn.execute('PRAGMA synchronous=OFF')
            conn.execute('PRAGMA temp_store=MEMORY')
            conn.execute('PRAGMA cache_size=-65536')
            conn.execute(
                'CREATE TABLE IF NOT EXISTS _meta('
                'name TEXT PRIMARY KEY, sname TEXT NOT NULL, kind TEXT NOT NULL, '
                'create_sql TEXT, columns TEXT, rows INTEGER DEFAULT 0)')
            conn.commit()

            done = 0
            with open(self.sql_path, 'rb') as f:
                for text, delim, raw_len in iter_statements(f, self.encoding):
                    done += raw_len
                    st['bytes_done'] = done
                    st['percent'] = min(99.9, done * 100.0 / max(1, total_bytes))
                    body = strip_delim(text, delim).strip()
                    if not body:
                        self.save_status()
                        continue
                    if body.startswith('/*'):
                        if body.startswith('/*!'):
                            inner = re.sub(r'^/\*!\d*\s*', '', body)
                            inner = re.sub(r'\*/\s*$', '', inner).strip().rstrip(';').strip()
                            if inner[:6].upper() == 'CREATE' and VIEW_NAME_RE.search(inner):
                                self.record_view(conn, inner)
                        self.save_status()
                        continue
                    ul = body.upper()
                    if ul.startswith('CREATE TABLE'):
                        self.parse_create(conn, body)
                    elif ul.startswith('INSERT') or ul.startswith('REPLACE'):
                        self.handle_insert(conn, body)
                    elif ul.startswith('CREATE') and 'VIEW' in ul[:300]:
                        self.record_view(conn, body)
                    self.save_status()

            conn.commit()
            # شمارش دقیق ردیف‌ها
            for name, info in self.tables.items():
                if info['kind'] == 'table':
                    cnt = conn.execute(f'SELECT COUNT(*) FROM "{info["sname"]}"').fetchone()[0]
                    info['rows'] = cnt
                    conn.execute('UPDATE _meta SET rows=? WHERE name=?', (cnt, name))
            conn.commit()
            conn.close()

            st['tables'] = [{'name': n, 'kind': i['kind'], 'rows': i['rows'],
                             'cols': len(i['cols'])} for n, i in self.tables.items()]
            st.update(state='done', phase='استخراج کامل شد', percent=100.0,
                      finished_at=time.time(), elapsed=round(time.time() - t0, 1))
            self.save_status(force=True)
        except Exception as e:  # noqa: BLE001
            st['state'] = 'error'
            st['error'] = str(e)
            st['phase'] = 'خطا در استخراج'
            st['finished_at'] = time.time()
            self.add_error(traceback.format_exc()[-1500:])
            try:
                self.save_status(force=True)
            except Exception:
                pass


def safe_print(s):
    try:
        print(s)
    except UnicodeEncodeError:
        print(str(s).encode('ascii', 'replace').decode())


def find_sql_file(base_dirs=None):
    """جستجوی خودکار فایل بکاپ در مسیرهای داده‌شده"""
    import glob
    for d in (base_dirs or []):
        if not d or not os.path.isdir(d):
            continue
        cands = [p for p in glob.glob(os.path.join(d, '*.sql'))]
        if cands:
            return max(cands, key=os.path.getsize)
    return None


def main():
    ap = argparse.ArgumentParser(description='استخراج بکاپ MySQL به دیتابیس SQLite')
    ap.add_argument('--file', help='مسیر فایل بکاپ .sql')
    ap.add_argument('--db', help='مسیر دیتابیس SQLite خروجی')
    ap.add_argument('--status', help='مسیر فایل وضعیت')
    ap.add_argument('--encoding', default='utf-8', help='کدگذاری فایل بکاپ (پیش‌فرض utf-8)')
    a = ap.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    sql = a.file or find_sql_file([base, os.path.join(base, 'backup')])
    if not sql or not os.path.isfile(sql):
        safe_print('[!] فایل بکاپ پیدا نشد. مسیر را با --file مشخص کنید.')
        return 1
    db = a.db or os.path.join(base, 'viewer_data', 'parsed.sqlite')
    status_path = a.status or os.path.join(base, 'viewer_data', 'status.json')

    safe_print(f'فایل ورودی: {sql}')
    safe_print(f'حجم: {os.path.getsize(sql) / 1048576:,.1f} مگابایت — در حال استخراج…')
    imp = Importer(sql, db, status_path, a.encoding)
    imp.run()
    st = imp.status
    if st['state'] == 'error':
        safe_print(f"[!] خطا: {st.get('error')}")
        return 2
    safe_print(f"استخراج در {st['elapsed']} ثانیه کامل شد.")
    for t in st['tables']:
        kind = 'نما' if t['kind'] == 'view' else 'جدول'
        safe_print(f"  [{kind}] {t['name']}: {t['rows']:,} ردیف، {t['cols']} ستون")
    if st.get('warnings'):
        safe_print(f"هشدارها: {st['warnings']}")
    safe_print(f'دیتابیس خروجی: {db}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
