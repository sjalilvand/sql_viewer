# -*- coding: utf-8 -*-
"""
ساخت نسخه استاتیک داشبورد — یک فایل HTML مستقل با همه CSS/JS/داده به‌صورت inline

اجرا:
    python export_dashboard.py
    python export_dashboard.py --out my_dash.html
همچنین از داخل برنامه وب: دکمه «نسخه استاتیک» در صفحه داشبورد.
"""

import argparse
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, 'viewer_data', 'parsed.sqlite')
DEFAULT_OUT = os.path.join(BASE_DIR, 'export_html', 'dashboard.html')

VAZIR_LINK = ('<link href="https://cdn.jsdelivr.net/gh/rastikerdar/'
              'vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet">')


def build_html(db_path, out_path):
    """داده داشبورد را محاسبه و فایل HTML مستقل می‌سازد؛ مسیر خروجی برمی‌گردد"""
    from flask import Flask, render_template

    import dashboard as dash

    payload = dash.build_payload(db_path)

    # اجرای رندر قالب در یک اپ فلسک موقت
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))

    with open(os.path.join(BASE_DIR, 'static', 'style.css'), encoding='utf-8') as f:
        css = f.read()
    with open(os.path.join(BASE_DIR, 'static', 'jalali.js'), encoding='utf-8') as f:
        jalali_js = f.read()
    with open(os.path.join(BASE_DIR, 'static', 'dashboard.js'), encoding='utf-8') as f:
        dash_js = f.read()

    css_html = VAZIR_LINK + '\n<style>\n' + css + '\n</style>'
    js_html = ('<script>\n' + jalali_js + '\n</script>\n'
               '<script>\n' + dash_js + '\n</script>')

    with app.app_context():
        html = render_template(
            'dashboard.html', payload=payload, live=False, inline=True,
            css_html=css_html, js_html=js_html)

    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return out_path


def main():
    ap = argparse.ArgumentParser(description='ساخت داشبورد HTML مستقل')
    ap.add_argument('--db', default=DEFAULT_DB, help='مسیر دیتابیس SQLite')
    ap.add_argument('--out', default=DEFAULT_OUT, help='مسیر فایل خروجی HTML')
    a = ap.parse_args()

    if not os.path.exists(a.db):
        print('[!] دیتابیس پیدا نشد:', a.db)
        print('    ابتدا استخراج را انجام دهید:  python dump_parser.py --file "backup.sql"')
        return 1

    out = build_html(a.db, a.out)
    print('[✓] داشبورد استاتیک ساخته شد:')
    print('   ', os.path.abspath(out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
