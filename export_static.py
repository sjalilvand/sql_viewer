# -*- coding: utf-8 -*-
"""
ساخت خروجی HTML ثابت از جداول استخراج‌شده

این اسکریپت از دیتابیس SQLite که با dump_parser.py ساخته شده، برای هر جدول
یک فایل HTML مستقل (به‌همراه یک فایل index.html) تولید می‌کند — بدون نیاز
به اجرای سرور.

اجرا:
    python export_static.py
    python export_static.py --limit 500 --out my_export
"""

import argparse
import html
import json
import os
import re
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, 'viewer_data', 'parsed.sqlite')
DEFAULT_OUT = os.path.join(BASE_DIR, 'export_html')

MIN_CSS = """
body{font-family:Vazirmatn,'Segoe UI',Tahoma,sans-serif;background:#f2f5fa;color:#0f172a;margin:0;line-height:1.7}
.container{max-width:1240px;margin:0 auto;padding:24px 20px}
h1{font-size:24px}h2{font-size:17px}
.muted{color:#64748b;font-size:13px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:16px 20px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:right;padding:8px 12px;border-bottom:1px solid #e2e8f0}
thead th{background:#f8fafc;font-size:12.5px}
tbody tr:hover{background:#f1f5f9}
td{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;unicode-bidi:plaintext}
.null{color:#94a3b8;font-style:italic;font-size:11.5px}
code{font-family:Consolas,Menlo,monospace;font-size:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:1px 6px}
pre{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:14px;overflow-x:auto;font-size:12.5px;text-align:left;direction:ltr}
a{color:#2563eb;text-decoration:none}
.btn{display:inline-block;border:1px solid #e2e8f0;border-radius:10px;padding:6px 14px;color:#0f172a;text-decoration:none}
.type-badge{display:inline-block;background:#f8fafc;border:1px solid #e2e8f0;color:#64748b;border-radius:999px;font-size:10.5px;padding:0 8px;margin-right:6px;direction:ltr}
.pk-badge{display:inline-block;background:linear-gradient(135deg,#f59e0b,#f97316);color:#fff;border-radius:999px;font-size:10px;font-weight:800;padding:1px 8px}
.topbar{background:linear-gradient(90deg,#0f172a,#1e3a8a);color:#fff;padding:12px 0}
.brand{font-weight:700}
"""


def load_css():
    p = os.path.join(BASE_DIR, 'static', 'style.css')
    try:
        with open(p, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return MIN_CSS


def cell_html(v):
    if v is None:
        return '<span class="null">NULL</span>'
    if isinstance(v, bytes):
        h = v.hex()
        if len(h) > 40:
            h = h[:40] + '…'
        return '<code dir="ltr">0x' + html.escape(h) + '</code>'
    s = str(v)
    if len(s) > 2000:
        s = s[:2000] + '…'
    return html.escape(s)


def page(title, body, css):
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
<body>
<nav class="topbar"><div class="container brand">نمایشگر بکاپ MySQL — خروجی ثابت</div></nav>
<div class="container">
{body}
<footer class="muted" style="text-align:center;padding:20px 0">این فایل به‌صورت ثابت از بکاپ ساخته شده است.</footer>
</div>
</body>
</html>
"""


def safe_name(name):
    return re.sub(r'[^\w.-]', '_', name, flags=re.UNICODE) or 'table'


def main():
    ap = argparse.ArgumentParser(description='ساخت خروجی HTML ثابت از بکاپ استخراج‌شده')
    ap.add_argument('--db', default=DEFAULT_DB, help='مسیر دیتابیس SQLite')
    ap.add_argument('--out', default=DEFAULT_OUT, help='پوشه خروجی HTML')
    ap.add_argument('--limit', type=int, default=1000,
                    help='حداکثر ردیف برای هر جدول (0 = بدون محدودیت)')
    a = ap.parse_args()

    if not os.path.exists(a.db):
        print(f'[!] دیتابیس پیدا نشد: {a.db}')
        print('    ابتدا استخراج را انجام دهید:  python dump_parser.py --file "backup.sql"')
        return 1

    conn = sqlite3.connect(f'file:{a.db}?mode=ro', uri=True, timeout=10)
    try:
        metas = conn.execute(
            'SELECT name, sname, kind, create_sql, columns, rows FROM _meta '
            'ORDER BY kind DESC, name COLLATE NOCASE').fetchall()
    except sqlite3.Error as e:
        print(f'[!] خواندن دیتابیس ممکن نشد: {e}')
        return 1

    css = load_css()
    os.makedirs(a.out, exist_ok=True)

    tables = [m for m in metas if m[2] == 'table']
    views = [m for m in metas if m[2] != 'table']
    links = []

    for i, (name, sname, kind, ddl, cols_json, total_rows) in enumerate(tables, 1):
        try:
            cols = json.loads(cols_json or '[]')
        except Exception:
            cols = []
        col_names = [c.get('name', '') for c in cols]

        q = f'SELECT * FROM "{sname}"'
        if a.limit and a.limit > 0:
            q += f' LIMIT {int(a.limit)}'
        cur = conn.execute(q)
        rows = cur.fetchall()

        head = ''
        for c in cols:
            pk = ' <span class="pk-badge">PK</span>' if c.get('pk') else ''
            head += (f'<th><span dir="ltr">{html.escape(c.get("name", ""))}</span>'
                     f'{pk}<br><span class="type-badge">{html.escape(c.get("mtype") or "")}</span></th>')
        body_rows = []
        for r in rows:
            tds = ''.join(f'<td>{cell_html(v)}</td>' for v in r)
            body_rows.append(f'<tr>{tds}</tr>')

        note = ''
        if a.limit and a.limit > 0 and len(rows) >= a.limit and total_rows > len(rows):
            note = (f'<p class="muted">نمایش {len(rows):,} ردیف اول از '
                    f'{total_rows:,} ردیف (برای مشاهده کامل از برنامه وب یا خروجی CSV استفاده کنید).</p>')

        body = f"""
<h1><span dir="ltr">{html.escape(name)}</span></h1>
<p class="muted">{total_rows:,} ردیف &middot; {len(cols)} ستون</p>
{note}
<div class="card" style="padding:0;overflow-x:auto">
<table>
<thead><tr>{head}</tr></thead>
<tbody>{''.join(body_rows)}</tbody>
</table>
</div>
"""
        fname = f'table_{i:02d}_{safe_name(name)}.html'
        with open(os.path.join(a.out, fname), 'w', encoding='utf-8') as f:
            f.write(page(f'جدول {name}', body, css))
        links.append(f'<a class="btn" href="{fname}"><span dir="ltr">{html.escape(name)}</span></a>'
                     f' <span class="muted">{total_rows:,} ردیف</span>')
        print(f'  [x] {fname}  ({len(rows):,} ردیف)')

    view_blocks = []
    for name, sname, kind, ddl, cols_json, total_rows in views:
        view_blocks.append(
            f'<div class="card"><h2><span dir="ltr">{html.escape(name)}</span></h2>'
            f'<pre>{html.escape(ddl or "")}</pre></div>')

    index_body = f"""
<h1>فهرست جداول</h1>
<p class="muted">تعداد {len(tables)} جدول و {len(views)} نما — تولیدشده به‌صورت ثابت.</p>
<div class="card">{'<br>'.join(links) if links else '<span class="muted">جدولی یافت نشد.</span>'}</div>
{''.join(view_blocks)}
"""
    with open(os.path.join(a.out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(page('نمایشگر بکاپ MySQL', index_body, css))
    print(f'  [x] index.html')

    conn.close()
    print(f'\nخروجی کامل در پوشه: {a.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
