# -*- coding: utf-8 -*-
"""
موتور تحلیلی داشبورد — همه آمارها از parsed.sqlite

اصول طراحی:
- «تشخیص تدافعی ستون‌ها»: نام ستون‌های هر جدول از _meta خوانده می‌شود و برای
  هر نمودار بین نام‌های محتمل (CreateTime/Date/SendTime/...) جستجو می‌شود؛
  اگر ستون مناسب نبود، آن نمودار به‌جای خطا حذف می‌شود.
- هیچ وابستگی به افزونه JSON اسکیولایت نیست؛ تحلیل ستون Data در پایتون انجام
  می‌شود (نمونه‌گیری هوشمند، نه خواندن همه ردیف‌ها).
- نتیجه با کش زمانی ذخیره می‌شود تا هر بار همه کوئری‌ها دوباره اجرا نشود.
"""

import json
import sqlite3
import threading
import time
from collections import Counter

# ---------------- نام‌های محتمل ستون‌ها ----------------

DATE_CANDS = ['CreateTime', 'CreatedAt', 'CreateDate', 'CreateDate2', 'Date',
              'SendDate', 'SendTime', 'LoginTime', 'StartTime', 'ReserveTime',
              'ReserveDate', 'SubmitDate', 'ActionDate', 'UpdateTime']
TYPE_CANDS = ['Type', 'Action', 'Operation', 'Command', 'Method', 'Kind', 'EventType']
STATUS_CANDS = ['Status', 'State', 'ResultStatus']
TITLE_CANDS = ['Title', 'Name', 'Subject', 'Caption', 'FullName']
EVENTFK_CANDS = ['EventID', 'EventId', 'EventsID']
CITYFK_CANDS = ['CityID', 'CityId', 'CitiesID']
ROLEFK_CANDS = ['RoleID', 'RoleId', 'RolesID']
USERFK_CANDS = ['UserID', 'UserId', 'UsersID']

PALETTE = ['#2563eb', '#16a34a', '#dc2626', '#d97706', '#7c3aed', '#0891b2',
           '#be185d', '#4d7c0f', '#b45309', '#475569']

# بخش‌ها و جدول‌های اصلی هر بخش
SECTIONS_DEF = [
    ('api', 'وضعیت APIها', 'ApiReports'),
    ('users', 'کاربران و نشست‌ها', 'Users'),
    ('reserve', 'رزرو و ارجاع', 'EventReserves'),
    ('events', 'رویدادها و تعامل', 'Events'),
    ('sms', 'پیامک‌ها', 'SMS'),
    ('log', 'لاگ سیستم', 'Log'),
]


# ---------------- ابزارهای پایه ----------------

def open_ro(db_path):
    import sqlite3
    return sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=15)


def load_meta(conn):
    """نام جدول‌ها + ستون‌ها از _meta (خروجی پارسر)"""
    try:
        rows = conn.execute(
            'SELECT name, sname, kind, columns FROM _meta').fetchall()
    except sqlite3.Error:
        return {}
    meta = {}
    for name, sname, kind, cols_json in rows:
        try:
            cols = [c['name'] for c in json.loads(cols_json or '[]')]
        except Exception:
            cols = []
        meta[name] = {'sname': sname, 'kind': kind, 'cols': cols}
    return meta


def find_col(cols, candidates, exclude=None):
    """اولین ستون موجود بین نام‌های محتمل (اول تطابق کامل، بعد تطابق جزئی)"""
    exclude = set(exclude or [])
    low = {c.lower(): c for c in cols if c not in exclude}
    for cand in candidates:
        if cand.lower() in low:
            return low[cand.lower()]
    for cand in candidates:
        cl = cand.lower()
        for c in cols:
            if c not in exclude and cl in c.lower():
                return c
    return None


def q_table(conn, sname, sql, params=()):
    """اجرا با جدول کوتیشن‌شده؛ در صورت خطا None"""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return None


def scalar(conn, sname, col_expr, where='1=1'):
    rows = q_table(conn, sname,
                   f'SELECT {col_expr} FROM "{sname}" WHERE {where}')
    if not rows:
        return 0
    try:
        return rows[0][0] or 0
    except Exception:
        return 0


# ---------------- سازنده‌های نمودار ----------------

def chart(kind, cid, title, **kw):
    c = {'kind': kind, 'id': cid, 'title': title}
    c.update(kw)
    return c


def donut_from_groups(rows, cid, title, top=8):
    """rows = [(label, count)] → نمودار دونات"""
    rows = [r for r in (rows or []) if r[0] is not None]
    rows.sort(key=lambda r: -r[1])
    labels = [str(r[0]) for r in rows[:top]]
    values = [int(r[1]) for r in rows[:top]]
    rest = sum(int(r[1]) for r in rows[top:])
    if rest:
        labels.append('سایر')
        values.append(rest)
    total = sum(values) or 1
    return chart('donut', cid, title, labels=labels, values=values,
                 total=total)


def bars_from_groups(rows, cid, title, top=12, value_label='تعداد'):
    rows = [r for r in (rows or []) if r[0] is not None]
    rows.sort(key=lambda r: -r[1])
    return chart('bars', cid, title, value_label=value_label,
                 labels=[str(r[0])[:60] for r in rows[:top]],
                 values=[int(r[1]) for r in rows[:top]])


def monthly_trend(conn, sname, datecol, cid, title, split_col=None,
                  split_limit=4):
    """روند ماهانه؛ اگر split_col بدهیم چند سری (مثلاً به تفکیک Status)"""
    if not datecol:
        return None
    if not split_col:
        rows = q_table(
            conn, sname,
            f'SELECT strftime(\'%Y-%m\', "{datecol}") m, COUNT(*) '
            f'FROM "{sname}" WHERE "{datecol}" IS NOT NULL '
            f'GROUP BY m ORDER BY m')
        if not rows:
            return None
        return chart('line', cid, title,
                     labels=[r[0] for r in rows],
                     series=[{'name': title, 'values': [int(r[1]) for r in rows]}])
    rows = q_table(
        conn, sname,
        f'SELECT strftime(\'%Y-%m\', "{datecol}") m, "{split_col}", COUNT(*) '
        f'FROM "{sname}" WHERE "{datecol}" IS NOT NULL '
        f'GROUP BY m, "{split_col}" ORDER BY m')
    if not rows:
        return None
    months, per, totals = [], {}, Counter()
    for m, sv, cnt in rows:
        key = str(sv) if sv is not None else 'نامشخص'
        if m not in per:
            per[m] = {}
            months.append(m)
        per[m][key] = int(cnt)
        totals[key] += int(cnt)
    top_keys = [k for k, _ in totals.most_common(split_limit)]
    series = []
    for i, k in enumerate(top_keys):
        series.append({'name': str(k), 'values': [per[m].get(k, 0) for m in months],
                       'color': PALETTE[i % len(PALETTE)]})
    return chart('line', cid, title, labels=months, series=series)


def status_donut(conn, sname, status_col, cid, title):
    if not status_col:
        return None
    rows = q_table(conn, sname,
                   f'SELECT "{status_col}", COUNT(*) FROM "{sname}" '
                   f'GROUP BY "{status_col}"')
    return donut_from_groups(rows, cid, title)


def table_chart(cid, title, headers, rows):
    return chart('table', cid, title, headers=headers, rows=rows)


# ---------------- بخش‌ها ----------------

def sec_api(conn, meta, tname, info):
    sname, cols = info['sname'], info['cols']
    charts = []
    type_col = find_col(cols, TYPE_CANDS)
    status_col = find_col(cols, STATUS_CANDS)
    date_col = find_col(cols, DATE_CANDS, exclude=['UpdateTime'])

    c = status_donut(conn, sname, status_col, 'api_status', 'توزیع وضعیت فراخوانی‌ها')
    if c:
        charts.append(c)

    if type_col:
        rows = q_table(conn, sname,
                       f'SELECT "{type_col}", COUNT(*) FROM "{sname}" '
                       f'GROUP BY "{type_col}"')
        c = bars_from_groups(rows, 'api_types', 'پرکاربردترین سرویس‌ها (Type)',
                             top=15)
        if c and c['labels']:
            charts.append(c)

    c = monthly_trend(conn, sname, date_col, 'api_monthly',
                      'روند ماهانه فراخوانی‌ها', split_col=status_col)
    if c:
        charts.append(c)

    # جدول تحلیلی به تفکیک Type: تعداد، نرخ موفقیت، میانگین تلاش
    if type_col and status_col:
        ok = 'Success'
        rows = q_table(
            conn, sname,
            f'SELECT "{type_col}", COUNT(*), '
            f'SUM(CASE WHEN "{status_col}" = \'{ok}\' THEN 1 ELSE 0 END), '
            f'AVG(COALESCE("Efforts", 0)), MAX(COALESCE("Efforts", 0)) '
            f'FROM "{sname}" GROUP BY "{type_col}" ORDER BY 2 DESC LIMIT 20')
        if rows:
            out = []
            for tp, cnt, succ, avg_e, max_e in rows:
                cnt = int(cnt or 0)
                succ = int(succ or 0)
                out.append([str(tp or 'نامشخص'), cnt,
                            round(succ * 100.0 / cnt, 1) if cnt else 0,
                            round(float(avg_e or 0), 2), int(max_e or 0)])
            charts.append(table_chart(
                'api_bytype', 'تحلیل سرویس‌ها: نرخ موفقیت و تلاش',
                ['سرویس (Type)', 'تعداد', 'نرخ موفقیت ٪', 'میانگین تلاش',
                 'بیشترین تلاش'], out))

    # تحلیل کلیدهای JSON داخل ستون Data (نمونه‌گیری)
    data_col = find_col(cols, ['Data', 'Data2', 'Body', 'Payload'])
    if data_col and type_col:
        c = json_keys_chart(conn, sname, data_col, type_col)
        if c:
            charts.append(c)
    return charts


def json_keys_chart(conn, sname, data_col, type_col, per_type=300, top_types=8):
    """نمونه‌گیری از ردیف‌های هر Type و شمارش کلیدهای سطح اول JSON"""
    tops = q_table(conn, sname,
                   f'SELECT "{type_col}", COUNT(*) FROM "{sname}" '
                   f'GROUP BY "{type_col}" ORDER BY 2 DESC LIMIT {top_types}')
    if not tops:
        return None
    has_id = 'id' in [c.lower() for c in _cols_of(conn, sname)]
    rows_out = []
    for (tp,) in [(r[0],) for r in tops]:
        order = 'ROWID DESC' if has_id else 'RANDOM()'
        rows = q_table(
            conn, sname,
            f'SELECT "{data_col}" FROM "{sname}" WHERE "{type_col}" = ? '
            f'ORDER BY {order} LIMIT {per_type}', (tp,))
        if not rows:
            continue
        keys = Counter()
        examples = {}
        n = 0
        for (v,) in rows:
            if v is None:
                continue
            try:
                obj = json.loads(v) if isinstance(v, str) else v
            except Exception:
                continue
            if isinstance(obj, dict):
                n += 1
                for k in obj.keys():
                    keys[k] += 1
                    if k not in examples:
                        s = str(obj[k])
                        examples[k] = s[:60]
            elif isinstance(obj, list):
                n += 1
                keys['(آرایه)'] += 1
                if '(آرایه)' not in examples:
                    examples['(آرایه)'] = f'{len(obj)} عنصر'
        if n and keys:
            for k, cnt in keys.most_common(6):
                rows_out.append([str(tp or 'نامشخص'), k,
                                 round(cnt * 100.0 / n), examples.get(k, '')])
        if len(rows_out) >= 40:
            break
    if not rows_out:
        return None
    return table_chart('api_jsonkeys', 'کلیدهای رایج JSON در ستون Data',
                       ['سرویس', 'کلید JSON', 'حضور در نمونه‌ها ٪', 'نمونه مقدار'],
                       rows_out[:40])


def _cols_of(conn, sname):
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info("{sname}")')]
    except Exception:
        return []


def sec_users(conn, meta, tname, info):
    charts = []
    sname, cols = info['sname'], info['cols']
    date_col = find_col(cols, DATE_CANDS, exclude=['UpdateTime'])
    c = monthly_trend(conn, sname, date_col, 'users_monthly',
                      'روند ماهانه ثبت‌نام کاربران')
    if c:
        charts.append(c)

    # نقش‌ها: UserRoles + Roles
    ur = meta.get('UserRoles')
    roles = meta.get('Roles')
    if ur and roles:
        rfk = find_col(ur['cols'], ROLEFK_CANDS)
        rname = find_col(roles['cols'], TITLE_CANDS) or 'Name'
        if rfk:
            rid_col_roles = find_col(roles['cols'], ['ID', 'ID2', 'Id']) or 'ID'
            rows = q_table(
                conn, ur['sname'],
                f'SELECT r."{rname}", COUNT(*) FROM "{ur["sname"]}" ur '
                f'LEFT JOIN "{roles["sname"]}" r '
                f'ON ur."{rfk}" = r."{rid_col_roles}" '
                f'GROUP BY r."{rname}"')
            c = donut_from_groups(rows, 'users_roles', 'توزیع نقش کاربران')
            if c:
                charts.append(c)

    se = meta.get('Sessions')
    if se:
        sdate = find_col(se['cols'], DATE_CANDS, exclude=['UpdateTime'])
        c = monthly_trend(conn, se['sname'], sdate, 'users_sessions',
                          'روند ماهانه نشست‌ها (Sessions)')
        if c:
            charts.append(c)
    return charts


def sec_reserve(conn, meta, tname, info):
    charts = []
    sname, cols = info['sname'], info['cols']
    date_col = find_col(cols, DATE_CANDS, exclude=['UpdateTime'])
    status_col = find_col(cols, STATUS_CANDS)
    c = monthly_trend(conn, sname, date_col, 'res_monthly',
                      'روند ماهانه رزروها', split_col=status_col)
    if c:
        charts.append(c)
    c = status_donut(conn, sname, status_col, 'res_status', 'وضعیت رزروها')
    if c:
        charts.append(c)

    rf = meta.get('Referrs')
    if rf:
        rdate = find_col(rf['cols'], DATE_CANDS, exclude=['UpdateTime'])
        rstatus = find_col(rf['cols'], STATUS_CANDS)
        c = monthly_trend(conn, rf['sname'], rdate, 'ref_monthly',
                          'روند ماهانه ارجاع‌ها', split_col=rstatus)
        if c:
            charts.append(c)
        c = status_donut(conn, rf['sname'], rstatus, 'ref_status', 'وضعیت ارجاع‌ها')
        if c:
            charts.append(c)
        # شهرها اگر امکان اتصال باشد
        cityfk = find_col(rf['cols'], CITYFK_CANDS)
        cities = meta.get('Cities')
        if cityfk and cities:
            cid_col = find_col(cities['cols'], ['ID', 'Id']) or 'ID'
            cname = find_col(cities['cols'], TITLE_CANDS) or 'Name'
            rows = q_table(
                conn, rf['sname'],
                f'SELECT c."{cname}", COUNT(*) FROM "{rf["sname"]}" rf '
                f'LEFT JOIN "{cities["sname"]}" c ON rf."{cityfk}" = c."{cid_col}" '
                f'GROUP BY c."{cname}"')
            c = bars_from_groups(rows, 'ref_cities', 'ارجاع به تفکیک شهر', top=12)
            if c and c['labels']:
                charts.append(c)
    return charts


def sec_events(conn, meta, tname, info):
    charts = []
    ev = meta.get('Events')
    com = meta.get('EventComments')
    per = meta.get('EventPersonal')
    qz = meta.get('EventQuizzes')

    # پرترددترین رویدادها بر اساس تعداد نظر
    if ev and com:
        efk = find_col(com['cols'], EVENTFK_CANDS)
        title_col = find_col(ev['cols'], TITLE_CANDS)
        eid_col = find_col(ev['cols'], ['ID', 'Id']) or 'ID'
        if efk and title_col:
            rows = q_table(
                conn, com['sname'],
                f'SELECT e."{title_col}", COUNT(*) FROM "{com["sname"]}" c '
                f'LEFT JOIN "{ev["sname"]}" e ON c."{efk}" = e."{eid_col}" '
                f'GROUP BY e."{title_col}" ORDER BY 2 DESC LIMIT 10')
            c = bars_from_groups(rows, 'ev_top_comments',
                                 'پرترددترین رویدادها (بر اساس تعداد نظر)',
                                 top=10)
            if c and c['labels']:
                charts.append(c)

    for tbl, cid, title in ((com, 'ev_comments_monthly', 'روند ماهانه نظرات'),
                            (per, 'ev_personal_monthly', 'روند ماهانه رویدادهای شخصی'),
                            (qz, 'ev_quizzes_monthly', 'روند ماهانه کوییز‌ها')):
        if tbl:
            dc = find_col(tbl['cols'], DATE_CANDS, exclude=['UpdateTime'])
            c = monthly_trend(conn, tbl['sname'], dc, cid, title)
            if c:
                charts.append(c)

    # وضعیت رزروها به تفکیک وضعیت در خود Events اگر ستون وضعیت دارد
    if ev:
        est = find_col(ev['cols'], STATUS_CANDS)
        if est:
            rows = q_table(conn, ev['sname'],
                           f'SELECT "{est}", COUNT(*) FROM "{ev["sname"]}" '
                           f'GROUP BY "{est}"')
            c = donut_from_groups(rows, 'ev_status', 'وضعیت رویدادها')
            if c:
                charts.append(c)
    return charts


def sec_sms(conn, meta, tname, info):
    charts = []
    sname, cols = info['sname'], info['cols']
    date_col = find_col(cols, DATE_CANDS, exclude=['UpdateTime'])
    status_col = find_col(cols, STATUS_CANDS)
    c = monthly_trend(conn, sname, date_col, 'sms_monthly', 'روند ماهانه پیامک‌ها')
    if c:
        charts.append(c)
    c = status_donut(conn, sname, status_col, 'sms_status', 'وضعیت پیامک‌ها')
    if c:
        charts.append(c)
    return charts


def sec_log(conn, meta, tname, info):
    charts = []
    sname, cols = info['sname'], info['cols']
    date_col = find_col(cols, DATE_CANDS, exclude=['UpdateTime'])
    type_col = find_col(cols, TYPE_CANDS)
    c = monthly_trend(conn, sname, date_col, 'log_monthly', 'روند ماهانه لاگ‌ها')
    if c:
        charts.append(c)
    if type_col:
        rows = q_table(conn, sname,
                       f'SELECT "{type_col}", COUNT(*) FROM "{sname}" '
                       f'GROUP BY "{type_col}"')
        c = bars_from_groups(rows, 'log_types', 'پرتکرارترین عملیات‌ها', top=12)
        if c and c['labels']:
            charts.append(c)
    if date_col:
        rows = q_table(
            conn, sname,
            f'SELECT strftime(\'%H\', "{date_col}") h, COUNT(*) FROM "{sname}" '
            f'WHERE "{date_col}" IS NOT NULL GROUP BY h ORDER BY h')
        if rows:
            labels = [f'{int(r[0] or 0):02d}:00' for r in rows]
            charts.append(chart('bars', 'log_hours', 'پرترددترین ساعت‌های شبانه‌روز',
                                labels=labels, values=[int(r[1]) for r in rows],
                                value_label='تعداد'))
    return charts


SECTION_BUILDERS = {'api': sec_api, 'users': sec_users, 'reserve': sec_reserve,
                    'events': sec_events, 'sms': sec_sms, 'log': sec_log}


# ---------------- KPI ها ----------------

def build_kpis(conn, meta):
    kpis = []

    def cnt(name):
        info = meta.get(name)
        if not info or info['kind'] != 'table':
            return None
        return scalar(conn, info['sname'], 'COUNT(*)')

    def add(label, value, sub='', fmt='num'):
        if value is not None:
            kpis.append({'label': label, 'value': value, 'sub': sub, 'fmt': fmt})

    add('کاربران', cnt('Users'))
    add('رزروها', cnt('EventReserves'))
    add('ارجاع‌ها', cnt('Referrs'))
    add('رویدادها', cnt('Events'))
    add('نظرات', cnt('EventComments'))
    add('پیامک‌ها', cnt('SMS'))
    add('رکوردهای لاگ', cnt('Log'))

    api = meta.get('ApiReports')
    if api and api['kind'] == 'table':
        total = scalar(conn, api['sname'], 'COUNT(*)')
        add('فراخوانی API', total)
        if total:
            succ = scalar(conn, api['sname'], 'COUNT(*)',
                          f"\"{find_col(api['cols'], STATUS_CANDS) or 'Status'}\"='Success'")
            kpis[-1]['sub'] = f'نرخ موفقیت {succ * 100.0 / total:.1f}٪'
            kpis[-1]['success_rate'] = round(succ * 100.0 / total, 1)
    return kpis


# ---------------- سرهم‌بندی ----------------

def build_payload(db_path, sections=None):
    """ساخت کامل داده داشبورد؛ خروجی: dict سریالایزپذیر JSON"""
    t0 = time.time()
    conn = open_ro(db_path)
    try:
        meta = load_meta(conn)
        wanted = sections or [s[0] for s in SECTIONS_DEF]
        kpis = build_kpis(conn, meta)
        secs = []
        for sid, stitle, main_table in SECTIONS_DEF:
            if sid not in wanted:
                continue
            info = meta.get(main_table)
            if not info or info['kind'] != 'table':
                # هیچ‌کدام از جدول‌های بخش موجود نیست؟ بخش حذف می‌شود
                has_any = any(meta.get(t, {}).get('kind') == 'table'
                              for t in _SECTION_TABLES.get(sid, [main_table]))
                if not has_any:
                    continue
                info = None
            builder = SECTION_BUILDERS.get(sid)
            if not builder:
                continue
            try:
                charts = builder(conn, meta, main_table, info) if info else []
            except Exception:
                charts = []
            if charts:
                secs.append({'id': sid, 'title': stitle, 'charts': charts})
        return {
            'available': bool(secs),
            'generated_at': time.strftime('%Y-%m-%d %H:%M'),
            'elapsed': round(time.time() - t0, 2),
            'kpis': kpis,
            'sections': secs,
        }
    finally:
        conn.close()


_SECTION_TABLES = {
    'api': ['ApiReports'],
    'users': ['Users', 'UserRoles', 'Sessions'],
    'reserve': ['EventReserves', 'Referrs', 'ReferrFollowUps'],
    'events': ['Events', 'EventComments', 'EventPersonal', 'EventQuizzes'],
    'sms': ['SMS'],
    'log': ['Log'],
}


# ---------------- کش ----------------

class DashCache:
    def __init__(self, ttl=300):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.data = None
        self.at = 0.0

    def get(self, db_path, force=False):
        with self.lock:
            if (not force and self.data is not None
                    and time.time() - self.at < self.ttl):
                return self.data, False
            self.data = build_payload(db_path)
            self.at = time.time()
            return self.data, True

    def peek(self):
        with self.lock:
            return self.data

    def invalidate(self):
        """پس از استخراج بکاپ جدید، کش کهنه را باطل کن"""
        with self.lock:
            self.data = None
            self.at = 0.0


CACHE = DashCache(ttl=300)


# ---------------- CSV ----------------

def chart_to_csv(ch):
    """تبدیل یک نمودار به ردیف‌های CSV: [ستون‌ها], [ردیف‌ها]"""
    kind = ch['kind']
    if kind in ('donut', 'bars'):
        return ['عنوان', 'تعداد'], [[l, v] for l, v in
                                    zip(ch['labels'], ch['values'])]
    if kind == 'line':
        heads = ['ماه'] + [s['name'] for s in ch['series']]
        rows = []
        for i, m in enumerate(ch['labels']):
            rows.append([m] + [s['values'][i] for s in ch['series']])
        return heads, rows
    if kind == 'table':
        return ch['headers'], ch['rows']
    return None, None
