# -*- coding: utf-8 -*-
"""
موتور «ساختار دیتابیس» — جدول‌ها، ستون‌ها و ارتباط بین جدول‌ها

بکاپ mysqldump معمولاً FOREIGN KEY فیزیکی ندارد (InnoDB بکاپ را بدون
CONSTRAINT می‌ریزد)، پس ارتباط‌ها «استنتاجی» کشف می‌شوند:

۱) نام ستون: هر ستونی که به Id/ID/_id ختم شود، بخش اول نام آن (User از UserId،
   City از CityID، ...) با نام جدول‌ها مقایسه می‌شود (تطابق دقیق، جمع بستن با
   s/es/ies، و جدول‌های معروف Users/Events/Cities/Roles).
۲) راستی‌آزمایی با نمونه‌گیری: اگر جدول والد کوچک باشد (پیش‌فرض ≤ ۳۰۰ هزار
   ردیف)، از مقادیر ستون فرزند نمونه می‌گیریم و سهم مقادیری که در والد وجود
   دارند محاسبه می‌شود؛ نسبت بالا یعنی ارتباط «تأییدشده».

نتیجه با کش زمانی (و ابطال خودکار پس از تغییر فایل دیتابیس) نگه داشته می‌شود.
هیچ کوئری سنگینی در هر بار باز شدن صفحه اجرا نمی‌شود.
"""

import json
import os
import re
import sqlite3
import threading
import time

# ---------------- تنظیم‌ها ----------------

VALIDATE_MAX_PARENT_ROWS = 300_000   # والد بزرگ‌تر از این را راستی‌آزمایی نکن
VALIDATE_MAX_CHILD_ROWS = 5_000_000  # فرزند بزرگ‌تر از این هم نه
SAMPLE_LIMIT = 400                   # تعداد نمونه از ستون فرزند
CHUNK = 200                          # اندازه هر کوئری IN (...)
VERIFIED_RATIO = 0.6                 # حداقل نسبت تطابق برای «تأییدشده»

# نگاشت ستون‌های معروف این دیتابیس به جدول والد (اطمینان بالا)
EXTRA_FK = {
    'userid': 'Users', 'usersid': 'Users', 'uid': 'Users',
    'eventid': 'Events', 'eventsid': 'Events',
    'cityid': 'Cities', 'citiesid': 'Cities',
    'roleid': 'Roles', 'rolesid': 'Roles',
    'reserveid': 'EventReserves', 'eventreserveid': 'EventReserves',
}

# ستون‌هایی که با اینکه به id ختم می‌شوند، کلید خارجی نیستند
NON_FK = {'id', 'guid', 'uuid', 'androidid', 'deviceid', 'imei', 'valid'}

CONF_RANK = {'verified': 3, 'high': 2, 'med': 1, 'low': 0}
CONF_FA = {
    'verified': 'تأییدشده (تطابق داده)',
    'high': 'نام ستون = نام جدول',
    'med': 'نام ستون ≈ نام جدول',
    'low': 'احتمالی (تطابق داده کم)',
}


# ---------------- خواندن _meta ----------------

def open_ro(db_path):
    return sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=15)


def load_meta(conn):
    """همه ردیف‌های _meta: نام، نام SQLite، نوع، DDL، ستون‌ها، تعداد ردیف"""
    try:
        rows = conn.execute(
            'SELECT name, sname, kind, create_sql, columns, rows '
            'FROM _meta ORDER BY kind DESC, name COLLATE NOCASE').fetchall()
    except sqlite3.Error:
        return []
    out = []
    for name, sname, kind, ddl, cols_json, r in rows:
        try:
            cols = json.loads(cols_json or '[]')
        except Exception:
            cols = []
        out.append({'name': name, 'sname': sname, 'kind': kind or 'table',
                    'ddl': ddl or '', 'cols': cols, 'rows': r or 0})
    return out


# ---------------- استنتاج ارتباط ----------------

def _fk_stem(col_name):
    """از UserId → User ، از CityID → City ؛ اگر کلید خارجی نبود None"""
    low = col_name.lower()
    if low in NON_FK or not low.endswith('id'):
        return None
    stem = re.sub(r'_?id$', '', col_name, flags=re.I).strip('_ ')
    if not stem:
        return None
    return stem


def _parent_pk(cols):
    """کلید اصلی جدول والد: اولین ستون PK، وگرنه ستون Id، وگرنه اولین ستون"""
    for c in cols:
        if c.get('pk'):
            return c['name']
    for c in cols:
        if c['name'].lower() == 'id':
            return c['name']
    return cols[0]['name'] if cols else None


def _plural_candidates(stem):
    cands = [stem, stem + 's', stem + 'es']
    if stem.endswith('y'):
        cands.append(stem[:-1] + 'ies')
    return cands


def _find_parent_table(stem, tables_lower):
    for cand in _plural_candidates(stem):
        hit = tables_lower.get(cand.lower())
        if hit:
            return hit, 'high' if cand.lower() == stem.lower() else 'med'
    return None, None


def infer_relations(tables):
    """tables = خروجی load_meta ؛ خروجی: لیست ارتباط‌های نام‌محور"""
    tables_lower = {t['name'].lower(): t['name'] for t in tables
                    if t['kind'] == 'table'}
    relations = []
    for t in tables:
        if t['kind'] != 'table' or not t['cols']:
            continue
        for c in t['cols']:
            cname = c['name']
            stem = _fk_stem(cname)
            extra = EXTRA_FK.get(cname.lower())
            parent = conf = None
            if extra and extra.lower() in tables_lower \
                    and extra.lower() != t['name'].lower():
                parent = tables_lower[extra.lower()]
                conf = 'high'
            if parent is None and stem:
                hit, hconf = _find_parent_table(stem, tables_lower)
                if hit and hit != t['name']:
                    parent, conf = hit, hconf
                elif hit == t['name'] and not c.get('pk'):
                    # اشاره به خود جدول (مثل ParentId) یا ستون غیر-PK هم‌نام
                    parent, conf = hit, 'med'
            if parent is None and stem and stem.lower() in (
                    'parent', 'root', 'owner') and not c.get('pk'):
                # ارجاع به خود جدول حتی اگر نام جدول شبیه stem نباشد
                parent, conf = t['name'], 'low'
            if parent is None:
                continue
            pt = next(x for x in tables if x['name'] == parent)
            relations.append({
                'child': t['name'], 'child_col': cname,
                'parent': parent, 'parent_col': _parent_pk(pt['cols']),
                'confidence': conf, 'verified': None, 'ratio': None,
                'child_rows': t['rows'], 'parent_rows': pt['rows'],
            })
    return relations


def validate_relation(conn, rel):
    """نمونه‌گیری و اندازه‌گیری تطابق مقادیر ستون فرزند با کلید والد"""
    if rel['parent_rows'] > VALIDATE_MAX_PARENT_ROWS \
            or rel['child_rows'] > VALIDATE_MAX_CHILD_ROWS \
            or rel['child_rows'] == 0 or not rel['parent_col']:
        return None
    try:
        vals = [r[0] for r in conn.execute(
            f'SELECT DISTINCT "{rel["child_col"]}" FROM "{rel["child_sname"]}" '
            f'WHERE "{rel["child_col"]}" IS NOT NULL LIMIT {SAMPLE_LIMIT}')]
    except sqlite3.Error:
        return None
    if not vals:
        return None
    matched = set()
    try:
        for i in range(0, len(vals), CHUNK):
            chunk = vals[i:i + CHUNK]
            ph = ','.join('?' * len(chunk))
            for r in conn.execute(
                    f'SELECT DISTINCT "{rel["parent_col"]}" '
                    f'FROM "{rel["parent_sname"]}" '
                    f'WHERE "{rel["parent_col"]}" IN ({ph})', chunk):
                matched.add(r[0])
    except sqlite3.Error:
        return None
    ratio = (len(matched) / len(vals)) if vals else 0.0
    return round(ratio, 3)


# ---------------- ساخت خروجی کامل ----------------

def build_schema(db_path):
    t0 = time.time()
    if not os.path.exists(db_path):
        return None
    conn = open_ro(db_path)
    try:
        tables = load_meta(conn)
        # اگر ردیف‌های _meta صفر بود (دست‌ساز/جدول خالی)، شمارش دقیق
        for t in tables:
            if t['kind'] == 'table' and not t['rows']:
                try:
                    t['rows'] = conn.execute(
                        f'SELECT COUNT(*) FROM "{t["sname"]}"').fetchone()[0]
                except sqlite3.Error:
                    pass
        for t in tables:
            t['sname_q'] = t['sname']
        rels = infer_relations(tables)
        sinfo = {t['name']: t for t in tables}
        for r in rels:
            r['child_sname'] = sinfo[r['child']]['sname']
            r['parent_sname'] = sinfo[r['parent']]['sname']
            ratio = validate_relation(conn, r)
            r['ratio'] = ratio
            if ratio is not None:
                r['verified'] = ratio >= VERIFIED_RATIO
                if ratio == 0 and r['confidence'] == 'med':
                    r['confidence'] = 'low'
                elif r['verified']:
                    r['confidence'] = 'verified'
            del r['child_sname'], r['parent_sname']

        table_items = [t for t in tables if t['kind'] == 'table']
        views = [t for t in tables if t['kind'] != 'table']
        er_conf = {'verified', 'high', 'med'}

        # درجه هر جدول در نمودار ER
        degree = {}
        for r in rels:
            if r['confidence'] not in er_conf:
                continue
            degree[r['child']] = degree.get(r['child'], 0) + 1
            degree[r['parent']] = degree.get(r['parent'], 0) + 1

        # نقشه FK برای درج در ساختار هر جدول
        fk_map = {}
        for r in rels:
            if CONF_RANK[r['confidence']] >= CONF_RANK['low'] and r['confidence'] != 'low':
                prev = fk_map.get((r['child'], r['child_col']))
                if prev is None or CONF_RANK[r['confidence']] > CONF_RANK[prev['confidence']]:
                    fk_map[(r['child'], r['child_col'])] = {
                        'table': r['parent'], 'col': r['parent_col'],
                        'confidence': r['confidence'], 'ratio': r['ratio']}

        total_rows = sum(t['rows'] for t in table_items)
        try:
            db_size = os.path.getsize(db_path)
        except OSError:
            db_size = 0

        return {
            'built_at': time.time(),
            'build_seconds': round(time.time() - t0, 2),
            'kpi': {
                'tables': len(table_items),
                'views': len(views),
                'rows': total_rows,
                'relations': len([r for r in rels
                                  if r['confidence'] in er_conf]),
                'db_size': db_size,
            },
            'tables': [{
                'name': t['name'], 'rows': t['rows'],
                'cols': t['cols'], 'ddl': t['ddl'],
                'fk': {c['name']: fk_map[(t['name'], c['name'])]
                       for c in t['cols']
                       if (t['name'], c['name']) in fk_map},
                'degree': degree.get(t['name'], 0),
            } for t in table_items],
            'views': [{'name': v['name'], 'ddl': v['ddl']} for v in views],
            'relations': sorted(
                rels,
                key=lambda r: (-CONF_RANK[r['confidence']],
                               r['child'].lower())),
            'conf_fa': CONF_FA,
        }
    finally:
        conn.close()


# ---------------- کش ----------------

class SchemaCache:
    """کش ساختار با TTL؛ با تغییر mtime فایل دیتابیس خودش را باطل می‌کند"""

    def __init__(self, ttl=600):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.data = None
        self.at = 0.0
        self.mtime = 0.0

    def get(self, db_path, force=False):
        with self.lock:
            try:
                mt = os.path.getmtime(db_path)
            except OSError:
                mt = 0.0
            fresh = (self.data is not None and not force
                     and time.time() - self.at < self.ttl
                     and mt == self.mtime)
            if fresh:
                return self.data, False
            self.data = build_schema(db_path)
            self.at = time.time()
            self.mtime = mt
            return self.data, True

    def peek(self):
        with self.lock:
            return self.data

    def invalidate(self):
        with self.lock:
            self.data = None
            self.at = 0.0


CACHE = SchemaCache(ttl=600)
