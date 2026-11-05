# -*- coding: utf-8 -*-
"""
نمایشگر بکاپ MySQL — برنامه وب فلسک

فایل بکاپ mysqldump را می‌خواند، جدول‌ها و داده‌ها را استخراج و در دیتابیس
SQLite محلی ذخیره می‌کند و سپس آن‌ها را با رابط فارسی در مرورگر نمایش می‌دهد.

اجرا:
    python app.py
    python app.py --file "C:\\path\\to\\backup.sql" --port 5000
سپس در مرورگر:  http://127.0.0.1:5000
"""

import argparse
import glob
import json
import math
import os
import sqlite3
import threading
import time
import traceback
import urllib.parse

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, url_for)

from dump_parser import Importer
import dashboard as dash
import schema as schemamod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'viewer_data')
DB_PATH = os.path.join(DATA_DIR, 'parsed.sqlite')
STATUS_PATH = os.path.join(DATA_DIR, 'status.json')
PATH_PREF = os.path.join(DATA_DIR, 'path.json')
SEEN_PATH = os.path.join(DATA_DIR, 'seen_backups.json')

# پوشه‌هایی که بکاپ جدید در آن‌ها جستجو می‌شود
BACKUP_DIRS = (os.path.join(BASE_DIR, 'backups'), os.path.join(BASE_DIR, 'backup'))
WATCH_INTERVAL = 20  # ثانیه

# اگر فایل بکاپ در این مسیر پیش‌فرض باشد، خودکار پیدا می‌شود
# (پوشه پروژه هم همیشه جستجو می‌شود — کافی است فایل را کنار app.py بگذارید)
DEFAULT_SQL_FILE = r'D:\Projects\sql_viewer\app2_backup_202611031659.sql'

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)

_import_lock = threading.Lock()
_import_thread = None
_import_started = 0.0  # زمان شروع آخرین استخراج (برای تشخیص نخ مرده)


# ---------------- ابزارهای وضعیت و استخراج ----------------

def load_status():
    try:
        with open(STATUS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'state': 'idle'}


def save_status(st):
    """ذخیره وضعیت — ضدخطا. روی ویندوز os.replace وقتی status.json توسط
    خواننده دیگری یا آنتی‌ویروس باز باشد با WinError 5 شکست می‌خورد؛
    چند بار تلاش می‌کنیم و در نهایت مستقیم می‌نویسیم. هرگز خطا پرتاب نمی‌کند."""
    try:
        payload = json.dumps(st, ensure_ascii=False)
    except Exception:
        return
    tmp = STATUS_PATH + '.tmp'
    for attempt in range(5):
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(payload)
            os.replace(tmp, STATUS_PATH)
            return
        except PermissionError:
            if attempt < 4:
                time.sleep(0.05 + 0.1 * attempt)
        except OSError:
            break
    try:
        with open(STATUS_PATH, 'w', encoding='utf-8') as f:
            f.write(payload)
    except OSError:
        pass


def resolve_sql_file():
    env = os.environ.get('SQL_VIEWER_FILE')
    if env and os.path.isfile(env):
        return env
    try:
        with open(PATH_PREF, encoding='utf-8') as f:
            p = json.load(f).get('file')
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    if os.path.isfile(DEFAULT_SQL_FILE):
        return DEFAULT_SQL_FILE
    st = load_status()
    last = st.get('file')
    if last and os.path.isfile(last):
        return last
    for d in (BASE_DIR, os.path.join(BASE_DIR, 'backup'),
              os.path.join(BASE_DIR, 'backups')):
        cands = [p for p in glob.glob(os.path.join(d, '*.sql'))
                 if os.path.basename(p) != 'sample_dump.sql']
        if cands:
            return max(cands, key=os.path.getsize)
    return None


def start_import(sql_file, auto=False, replace=False):
    """شروع استخراج در نخ جداگانه.

    replace=True یعنی دیتابیس فعال داریم؛ ابتدا در فایل موقت استخراج می‌کنیم
    و فقط اگر کامل شد، جای دیتابیس قبلی می‌نشینیم تا حین استخراج، مرورگری
    که صفحه را باز کرده با قطعی داده مواجه نشود.
    """
    global _import_thread, _import_started
    with _import_lock:
        if _import_thread and _import_thread.is_alive():
            return False

        def worker():
            try:
                if replace and os.path.exists(DB_PATH):
                    tmp = DB_PATH + '.new'
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except OSError:
                        pass
                    imp = Importer(sql_file, tmp, STATUS_PATH)
                    imp.run()
                    st = load_status()
                    if st.get('state') == 'done' and os.path.exists(tmp):
                        if atomic_replace(tmp, DB_PATH):
                            st['db_path'] = DB_PATH
                            st['auto'] = bool(auto)
                            save_status(st)
                            try:
                                dash.CACHE.invalidate()
                            except Exception:
                                pass
                            try:
                                schemamod.CACHE.invalidate()
                            except Exception:
                                pass
                        else:
                            st['state'] = 'error'
                            st['phase'] = 'خطا در جایگزینی دیتابیس'
                            st['error'] = (
                                'استخراج کامل شد ولی جایگزینی فایل ناموفق بود؛ '
                                'برنامه را ببندید و دوباره باز کنید. '
                                'فایل آماده: ' + tmp)
                            save_status(st)
                else:
                    imp = Importer(sql_file, DB_PATH, STATUS_PATH)
                    imp.run()
                    if auto:
                        st = load_status()
                        st['auto'] = True
                        save_status(st)
            except Exception as e:  # noqa: BLE001
                # هرگز اجازه نده نخ کارگر بی‌صدا بمیرد؛ وگرنه صفحه برای همیشه
                # روی «در حال استخراج» گیر می‌کند.
                save_status({'state': 'error', 'phase': 'خطا در استخراج',
                             'error': str(e), 'file': sql_file,
                             'total_bytes': 0, 'percent': 0, 'tables': [],
                             'errors': [traceback.format_exc()[-1500:]],
                             'warnings': 0})

        _import_thread = threading.Thread(target=worker, daemon=True)
        _import_started = time.time()
        _import_thread.start()
        return True


def atomic_replace(src, dst):
    """os.replace با تحمل قفل لحظه‌ای ویندوز (خواننده همزمان/آنتی‌ویروس)"""
    for _ in range(24):
        try:
            os.replace(src, dst)
            return True
        except PermissionError:
            time.sleep(0.25)
        except OSError:
            return False
    return False


def ensure_import():
    if _import_thread and _import_thread.is_alive():
        return
    st = load_status()
    if st.get('state') == 'done' and os.path.exists(DB_PATH):
        return
    if st.get('state') == 'error':
        # خطای صریح — شروع خودکار مجدد نمی‌کنیم؛ کاربر دکمه «تلاش دوباره» را دارد
        return
    sql = resolve_sql_file()
    if not sql:
        if st.get('state') not in ('no_file',):
            save_status({'state': 'no_file',
                         'phase': 'فایل بکاپ پیدا نشد',
                         'file': None, 'total_bytes': 0, 'percent': 0,
                         'tables': [], 'errors': [], 'warnings': 0})
        return
    start_import(sql)


# ---------------- پایش خودکار بکاپ جدید ----------------

def load_seen():
    try:
        with open(SEEN_PATH, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return None


def save_seen(d):
    payload = json.dumps(d, ensure_ascii=False)
    try:
        with open(SEEN_PATH + '.tmp', 'w', encoding='utf-8') as f:
            f.write(payload)
        os.replace(SEEN_PATH + '.tmp', SEEN_PATH)
    except OSError:
        try:
            with open(SEEN_PATH, 'w', encoding='utf-8') as f:
                f.write(payload)
        except OSError:
            pass


def seed_seen():
    """اولین اجرا: بکاپ فعال فعلی را «دیده‌شده» علامت بزن تا در نخستین
    راه‌اندازی نسخه جدید، دوباره از صفر استخراج نشود."""
    seen = load_seen()
    if seen is not None:
        return seen
    seen = {}
    if os.path.exists(DB_PATH):
        f = load_status().get('file')
        if f and os.path.isfile(f):
            try:
                seen[os.path.basename(f) + ':' + str(os.path.getsize(f))] = time.time()
            except OSError:
                pass
    save_seen(seen)
    return seen


def backup_candidates():
    """همه فایل‌های .sql معتبر در پوشه‌های زیر نظر: backups/، کنار برنامه،
    پوشه آخرین بکاپ و پوشه بکاپ پیش‌فرض. کلید = نام + حجم."""
    dirs = list(BACKUP_DIRS) + [BASE_DIR]
    f = load_status().get('file')
    if f:
        dirs.append(os.path.dirname(f))
    if os.path.isfile(DEFAULT_SQL_FILE):
        dirs.append(os.path.dirname(DEFAULT_SQL_FILE))
    out, seen_dirs = {}, set()
    for d in dirs:
        if not d or not os.path.isdir(d) or d in seen_dirs:
            continue
        seen_dirs.add(d)
        for p in glob.glob(os.path.join(d, '*.sql')):
            b = os.path.basename(p)
            if b.startswith('sample_') or b.startswith('edge_'):
                continue
            try:
                sz = os.path.getsize(p)
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if sz < 1024:
                continue
            out[b + ':' + str(sz)] = {'path': p, 'name': b, 'size': sz,
                                      'mtime': mt}
    return out


def maybe_auto_import():
    """اگر بکاپ تازه‌ای پیدا شد، در پس‌زمینه استخراجش کن"""
    if _import_thread and _import_thread.is_alive():
        return
    if load_status().get('state') == 'importing':
        return
    seen = seed_seen()
    news = [c for sig, c in backup_candidates().items() if sig not in seen]
    if not news:
        return
    news.sort(key=lambda c: -c['mtime'])
    target = news[0]
    seen[target['name'] + ':' + str(target['size'])] = time.time()
    save_seen(seen)
    start_import(target['path'], auto=True,
                 replace=os.path.exists(DB_PATH))


def watcher_loop():
    time.sleep(5)
    while True:
        try:
            maybe_auto_import()
        except Exception:
            pass
        time.sleep(WATCH_INTERVAL)


def start_watcher():
    threading.Thread(target=watcher_loop, daemon=True,
                     name='backup-watcher').start()


def warm_caches():
    """پس از بالا آمدن برنامه، ساختار و ارتباط‌ها را در پس‌زمینه حساب کن
    تا اولین کلیک روی جدول‌ها منتظر نماند."""
    def _w():
        time.sleep(3)
        try:
            if os.path.exists(DB_PATH):
                schemamod.CACHE.get(DB_PATH)
        except Exception:
            pass
    threading.Thread(target=_w, daemon=True, name='cache-warm').start()

def open_ro():
    if not os.path.exists(DB_PATH):
        return None
    try:
        return sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=10)
    except sqlite3.Error:
        return None


def get_tables_meta():
    conn = open_ro()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            'SELECT name, sname, kind, create_sql, columns, rows FROM _meta '
            'ORDER BY kind DESC, name COLLATE NOCASE').fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    out = []
    for name, sname, kind, ddl, cols_json, r in rows:
        try:
            cols = json.loads(cols_json or '[]')
        except Exception:
            cols = []
        out.append({'name': name, 'sname': sname, 'kind': kind,
                    'ddl': ddl or '', 'cols': cols, 'rows': r or 0})
    return out


def get_table_meta(name):
    tables = get_tables_meta() or []
    for t in tables:
        if t['name'] == name:
            return t
    return None


# ---------------- فیلترهای قالب ----------------

@app.template_filter('nf')
def nf(v):
    try:
        return f'{int(v):,}'
    except (TypeError, ValueError):
        return v


@app.template_filter('size')
def sizef(v):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return v
    for unit in ('بایت', 'کیلوبایت', 'مگابایت', 'گیگابایت'):
        if v < 1024 or unit == 'گیگابایت':
            return f'{v:,.1f} {unit}'
        v /= 1024


# ---------------- ساخت شرط جستجو ----------------

def build_search(cols, q):
    if not q or not cols:
        return '', []
    esc = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    conds = []
    for c in cols:
        safe = c.replace('"', '""')
        conds.append(f'CAST("{safe}" AS TEXT) LIKE ? ESCAPE \'\\\'')
    return ' OR '.join(conds), ['%' + esc + '%'] * len(conds)


def cellify(v):
    """آماده‌سازی مقدار یک سلول برای نمایش"""
    if v is None:
        return {'t': 'null', 'v': ''}
    if isinstance(v, bytes):
        h = v.hex()
        if len(h) > 40:
            h = h[:40] + '…'
        return {'t': 'blob', 'v': '0x' + h}
    s = str(v)
    if len(s) > 2000:
        s = s[:2000] + '…'
    return {'t': 'text', 'v': s}


def csv_escape(v):
    if v is None:
        return ''
    if isinstance(v, bytes):
        v = '0x' + v.hex()
    s = str(v)
    return '"' + s.replace('"', '""') + '"'


# ---------------- مسیرها ----------------

@app.route('/')
def index():
    ensure_import()
    st = load_status()
    meta = get_tables_meta()
    table_items = [t for t in (meta or []) if t['kind'] == 'table']
    views = [t for t in (meta or []) if t['kind'] != 'table']
    total_rows = sum(t['rows'] for t in table_items)
    seen = seed_seen()
    bk_items = []
    for sig, c in backup_candidates().items():
        item = dict(c)
        item['seen'] = sig in seen
        bk_items.append(item)
    bk_items.sort(key=lambda x: (-x['mtime'], x['name']))
    return render_template('index.html', status=st, table_items=table_items,
                           views=views, total_rows=total_rows,
                           bk_items=bk_items,
                           importing=bool(_import_thread and _import_thread.is_alive()),
                           nf_error=request.args.get('nf'))


@app.route('/table/<path:name>')
def table_page(name):
    meta = get_table_meta(name)
    if meta is None:
        abort(404)
    if meta['kind'] != 'table':
        return render_template('view.html', meta=meta)
    conn = open_ro()
    if conn is None:
        return redirect(url_for('index'))

    cols = [c['name'] for c in meta['cols']]
    q = (request.args.get('q') or '').strip()
    try:
        per_page = int(request.args.get('per_page') or 50)
    except ValueError:
        per_page = 50
    per_page = min(max(10, per_page), 500)
    sort = request.args.get('sort') or ''
    sdir = 'DESC' if request.args.get('dir') == 'desc' else 'ASC'
    if sort not in cols:
        sort = ''

    where, params = build_search(cols, q)
    wsql = f' WHERE {where}' if where else ''
    tname = meta['sname']

    try:
        total = conn.execute(
            f'SELECT COUNT(*) FROM "{tname}"{wsql}', params).fetchone()[0]
        pages = max(1, math.ceil(total / per_page))
        try:
            page = int(request.args.get('page') or 1)
        except ValueError:
            page = 1
        page = min(max(1, page), pages)
        order = f' ORDER BY "{sort}" {sdir}' if sort else ''
        off = (page - 1) * per_page
        cur = conn.execute(
            f'SELECT rowid AS __rid, * FROM "{tname}"{wsql}{order} LIMIT ? OFFSET ?',
            params + [per_page, off])
        raw_rows = cur.fetchall()
        conn.close()
    except sqlite3.Error:
        conn.close()
        abort(500)

    rows = [{'n': off + i + 1, 'cells': [cellify(v) for v in r[1:]]}
            for i, r in enumerate(raw_rows)]

    def sort_url(col):
        d = 'desc' if (sort == col and sdir == 'ASC') else 'asc'
        return url_for('table_page', name=name, page=page, per_page=per_page,
                       q=q or None, sort=col, dir=d)

    win = {1, pages}
    for p in (page - 1, page, page + 1, 2, pages - 1):
        if 1 <= p <= pages:
            win.add(p)
    pitems = []
    prev = 0
    for p in sorted(win):
        if p - prev > 1:
            pitems.append({'gap': True, 'n': None})
        pitems.append({'gap': False, 'n': p, 'active': p == page})
        prev = p

    return render_template('table.html', meta=meta, rows=rows, total=total,
                           page=page, pages=pages, per_page=per_page, q=q,
                           sort=sort, sdir=sdir, pitems=pitems,
                           fk_map=fk_map_for(name),
                           sort_url=sort_url)


def fk_map_for(name):
    """نقشه ستون → جدول والد برای کارت ساختار صفحه جدول (از کش ساختار)"""
    try:
        sp, _ = schemamod.CACHE.get(DB_PATH)
        if sp:
            for t in sp['tables']:
                if t['name'] == name:
                    return t.get('fk') or {}
    except Exception:
        pass
    return {}


@app.route('/table/<path:name>/export.csv')
def table_csv(name):
    meta = get_table_meta(name)
    if meta is None or meta['kind'] != 'table':
        abort(404)
    conn = open_ro()
    if conn is None:
        return redirect(url_for('index'))
    cols = [c['name'] for c in meta['cols']]
    q = (request.args.get('q') or '').strip()
    where, params = build_search(cols, q)
    wsql = f' WHERE {where}' if where else ''
    tname = meta['sname']

    def gen():
        yield '\ufeff'
        yield ','.join(csv_escape(c) for c in cols) + '\n'
        try:
            for r in conn.execute(f'SELECT * FROM "{tname}"{wsql}', params):
                yield ','.join(csv_escape(v) for v in r) + '\n'
        finally:
            conn.close()

    fname_ascii = 'table_export.csv'
    fname_utf8 = urllib.parse.quote(f'{name}.csv')
    return Response(gen(), mimetype='text/csv; charset=utf-8', headers={
        'Content-Disposition':
            f'attachment; filename="{fname_ascii}"; filename*=UTF-8\'\'{fname_utf8}',
    })


@app.route('/api/status')
def api_status():
    st = load_status()
    # اگر وضعیت روی دیسک هنوز «در حال استخراج» است ولی نخ کارگر مرده،
    # خطا را ثبت کن تا رابط کاربری برای همیشه روی همان درصد گیر نکند.
    # (فاصله امن ۳ ثانیه‌ای برای پرهیز از لحظه آغاز به کار نخ)
    if (st.get('state') == 'importing' and _import_started
            and time.time() - _import_started > 3
            and not (_import_thread and _import_thread.is_alive())):
        st['state'] = 'error'
        st['phase'] = 'استخراج متوقف شد'
        st['error'] = (st.get('error')
                       or 'فرایند استخراج بدون پایان موفق متوقف شد. '
                          'دکمه «تلاش دوباره» را بزنید.')
        save_status(st)
    return jsonify(st)


@app.route('/api/tables')
def api_tables():
    return jsonify(get_tables_meta() or [])


# ---------------- ساختار دیتابیس ----------------

@app.route('/schema')
def schema_page():
    ensure_import()
    if not os.path.exists(DB_PATH):
        return redirect(url_for('index'))
    force = request.args.get('refresh') == '1'
    payload, _ = schemamod.CACHE.get(DB_PATH, force=force)
    if payload is None:
        return redirect(url_for('index'))
    return render_template('schema.html', payload=payload)


@app.route('/api/schema')
def api_schema():
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'no_data'}), 503
    payload, _ = schemamod.CACHE.get(DB_PATH)
    return jsonify(payload)


# ---------------- فایل‌های بکاپ ----------------

@app.route('/api/backups')
def api_backups():
    seen = seed_seen()
    items = []
    for sig, c in backup_candidates().items():
        item = dict(c)
        item['seen'] = sig in seen
        item['sig'] = sig
        items.append(item)
    items.sort(key=lambda x: (-x['mtime'], x['name']))
    return jsonify(items)


@app.route('/import_file', methods=['POST'])
def import_file():
    """استخراج دستی یکی از فایل‌های بکاپ کشف‌شده (بدون ریسک مسیر دلخواه)"""
    name = request.form.get('name') or ''
    try:
        size = int(request.form.get('size') or 0)
    except ValueError:
        size = 0
    c = backup_candidates().get(name + ':' + str(size))
    if not c:
        return redirect(url_for('index', nf=1))
    seen = seed_seen()
    seen[name + ':' + str(size)] = time.time()
    save_seen(seen)
    start_import(c['path'], replace=os.path.exists(DB_PATH))
    return redirect(url_for('index'))


# ---------------- داشبورد تحلیلی ----------------

@app.route('/dashboard')
def dashboard_page():
    ensure_import()
    force = request.args.get('refresh') == '1'
    payload, recomputed = dash.CACHE.get(DB_PATH, force=force)
    return render_template('dashboard.html', payload=payload,
                           recomputed=recomputed, live=True)


@app.route('/api/dashboard')
def api_dashboard():
    force = request.args.get('refresh') == '1'
    payload, _ = dash.CACHE.get(DB_PATH, force=force)
    return jsonify(payload)


@app.route('/api/dashboard/csv/<cid>')
def api_dashboard_csv(cid):
    payload = dash.CACHE.peek()
    if not payload:
        payload, _ = dash.CACHE.get(DB_PATH)
    ch = None
    for sec in payload.get('sections', []):
        for c in sec['charts']:
            if c['id'] == cid:
                ch = c
                break
    if ch is None:
        abort(404)
    heads, rows = dash.chart_to_csv(ch)
    if not heads:
        abort(404)

    def gen():
        yield '\ufeff'
        yield ','.join(csv_escape(h) for h in heads) + '\n'
        for r in rows:
            yield ','.join(csv_escape(v) for v in r) + '\n'

    return Response(gen(), mimetype='text/csv; charset=utf-8', headers={
        'Content-Disposition':
            f'attachment; filename="{cid}.csv"'})


@app.route('/export/dashboard')
def export_dashboard():
    """ساخت داشبورد HTML مستقل (CSS/JS/داده داخل فایل) و دانلود آن"""
    import export_dashboard as exp
    if not os.path.exists(DB_PATH):
        return redirect(url_for('dashboard_page'))
    try:
        out = exp.build_html(DB_PATH, exp.DEFAULT_OUT)
        from flask import send_file
        return send_file(out, as_attachment=True,
                         download_name='dashboard.html')
    except Exception as e:  # noqa: BLE001
        return render_template('error.html', code=500,
                               msg='ساخت نسخه استاتیک ناموفق بود: ' + str(e)), 500



@app.route('/setup', methods=['POST'])
def setup():
    path = (request.form.get('sqlfile') or '').strip().strip('"')
    if not path or not os.path.isfile(path):
        return redirect(url_for('index', nf=1))
    with open(PATH_PREF, 'w', encoding='utf-8') as f:
        json.dump({'file': path}, f, ensure_ascii=False)
    reset_data()
    ensure_import()
    return redirect(url_for('index'))


@app.route('/restart', methods=['POST'])
def restart_import():
    if not (_import_thread and _import_thread.is_alive()):
        reset_data()
        sql = resolve_sql_file()
        if sql:
            start_import(sql)
    return redirect(url_for('index'))


def reset_data():
    for p in (DB_PATH, STATUS_PATH):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404,
                           msg='صفحه یا جدول موردنظر پیدا نشد.'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500,
                           msg='خطای داخلی سرور. احتمالاً استخراج هنوز کامل نشده است.'), 500


def main():
    ap = argparse.ArgumentParser(description='نمایشگر بکاپ MySQL')
    ap.add_argument('--file', help='مسیر فایل بکاپ .sql')
    ap.add_argument('--port', type=int, default=5000)
    ap.add_argument('--host', default='127.0.0.1')
    a = ap.parse_args()

    if a.file:
        if not os.path.isfile(a.file):
            print(f'[!] فایل پیدا نشد: {a.file}')
            raise SystemExit(1)
        with open(PATH_PREF, 'w', encoding='utf-8') as f:
            json.dump({'file': os.path.abspath(a.file)}, f, ensure_ascii=False)

    ensure_import()
    seed_seen()
    start_watcher()  # هر بکاپ جدید را خودکار پیدا و استخراج می‌کند
    warm_caches()
    st = load_status()
    os.makedirs(os.path.join(BASE_DIR, 'backups'), exist_ok=True)
    print('=' * 56)
    print('  MySQL Backup Viewer - Flask')
    print(f'  URL:             http://{a.host}:{a.port}')
    print(f"  Backup file:     {st.get('file') or resolve_sql_file() or '-'}")
    print(f'  SQLite DB:       {DB_PATH}')
    print('  Watch folder:    ' + os.path.join(BASE_DIR, 'backups'))
    print('  (new .sql files in the watch folder are imported automatically)')
    print('=' * 56)
    app.run(host=a.host, port=a.port, debug=False, use_reloader=False,
            threaded=True)


if __name__ == '__main__':
    main()
