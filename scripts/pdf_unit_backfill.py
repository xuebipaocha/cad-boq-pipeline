# -*- coding: utf-8 -*-
"""国标清单 unit 批量补录工具 — 视觉模型重读 PDF 计算标准

背景: national_24.db 的 standard_items 有 1,639 条 unit 为空
(源 PDF 为扫描件, 初次 OCR 漏读"单位"列)。
本工具: 渲染 PDF 页 → qwen3.7-flash 视觉识别表格 → 解析(编码,单位) → 回填数据库。

用法:
  python3 pdf_unit_backfill.py                          # 全量 11 本
  python3 pdf_unit_backfill.py --pdf 路径.pdf           # 单本
  python3 pdf_unit_backfill.py --dry-run                # 只识别不写库
  python3 pdf_unit_backfill.py --resume                 # 断点续跑(跳过已处理页)

输出: data/unit_backfill_progress.json (进度) + data/unit_backfill_report.json (报告)
"""
import os
import sys
import re
import json
import glob
import time

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE)
DATA_DIR = os.path.join(SKILL_DIR, 'data')
NATIONAL_DB = os.path.join(DATA_DIR, 'national_24.db')
PROGRESS_FILE = os.path.join(DATA_DIR, 'unit_backfill_progress.json')
REPORT_FILE = os.path.join(DATA_DIR, 'unit_backfill_report.json')

# 默认 PDF 目录(用户桌面)
DEFAULT_PDF_DIR = r'D:\用户文件\Desktop\2024建设工程工程量清单计价标准+工程量计算标准'

# 每页识别的视觉超时(秒)
VISION_TIMEOUT = 180


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'done_pages': [], 'filled': [], 'failed': []}


def save_progress(p):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


def page_done(p, key):
    """页是否已完成: done_pages 中存在 且 不在 failed_pages(403等失败可重跑)。"""
    if key not in p.get('done_pages', []):
        return False
    return key not in p.get('failed_pages', [])


def render_page(pdf_path, page_no, out_dir):
    """渲染 PDF 页 → PNG(150dpi)。返回图片路径。"""
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[page_no]
    pix = page.get_pixmap(dpi=150)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f'page_{page_no:03d}.png')
    pix.save(out)
    doc.close()
    return out


PROMPT = (
    '这是工程建设标准 PDF 的表格页。表格列通常为: 项目编码/项目名称/计量单位 等。\n'
    '请提取表格中每一行的【项目编码】和【计量单位】, 输出 JSON(不要其他文字):\n'
    '{"rows": [{"编码": "010101001", "单位": "m³"}, ...]}\n'
    '规则:\n'
    '1. 单位只取计量单位列的值(如 m³/m²/m/t/个/樘/kg/座 等), 不要含数量\n'
    '2. 若无表格或无法辨认, 输出 {"rows": []}\n'
    '3. 编码形如 010101001 (9位数字) 或含字母的清单码\n'
)


def parse_vision_json(content):
    """从视觉回复提取 JSON(兼容 ```json 包裹)。"""
    c = (content or '').strip()
    if '```' in c:
        c = c.split('```')[1]
        if c.startswith('json'):
            c = c[4:]
    try:
        return json.loads(c)
    except Exception:
        return None


def is_table_page(pix_path):
    """快速预筛: 表格页文字密度高(用视觉太贵, 这里用像素启发式跳过空页)。"""
    try:
        from PIL import Image
        im = Image.open(pix_path).convert('L')
        w, h = im.size
        px = im.load()
        dark = sum(1 for x in range(0, w, 20) for y in range(0, h, 20)
                   if px[x, y] < 128)
        total = (w // 20) * (h // 20)
        ratio = dark / total if total else 0
        # 表格页通常线条/文字覆盖率 5%~40%; 纯空白页 <2%
        return ratio > 0.03
    except Exception:
        return True


def process_page(pdf_path, page_no, out_dir, dry_run=False):
    """处理单页: 渲染 → 视觉识别 → 返回 [{编码, 单位}]。失败(403/超时)返回 None。"""
    from vision_query import query_vision
    pix = render_page(pdf_path, page_no, out_dir)
    if not is_table_page(pix):
        return []
    r = query_vision(pix, enable=True, prompt=PROMPT, timeout=VISION_TIMEOUT)
    if r is None:
        return None  # 调用失败(403/超时/无Key) — 区分于"正常但无表格"
    content = r.get('原始回复') or r.get('工程类型') or ''
    data = parse_vision_json(content)
    if not data:
        # qwen 可能直接结构化返回
        rows = r.get('rows')
        if isinstance(rows, list):
            return rows
        return []
    rows = data.get('rows', []) or []
    return [x for x in rows if isinstance(x, dict) and x.get('编码') and x.get('单位')]


def fill_db(filled_rows):
    """回填 unit 到 national_24.db。返回 (更新数, 匹配数, 未匹配数)。"""
    import sqlite3
    con = sqlite3.connect(NATIONAL_DB)
    updated = matched = unmatched = 0
    for row in filled_rows:
        code = str(row.get('编码', '') or '').strip()
        unit = str(row.get('单位', '') or '').strip()
        # 兼容其他常见字段名
        if not unit:
            unit = str(row.get('计量单位', '') or '').strip()
        if not code or not unit:
            continue
        # 编码规范化: 去空格/全角
        code = re.sub(r'[\s\u3000]', '', code)
        if not re.fullmatch(r'[0-9A-Za-z-]{6,12}', code):
            continue
        # 更新空 unit 的记录
        cur = con.execute(
            "UPDATE standard_items SET unit=? WHERE item_code=? AND (unit IS NULL OR unit='')",
            (unit, code))
        if cur.rowcount > 0:
            updated += cur.rowcount
            matched += 1
        else:
            # 尝试模糊: 编码前缀匹配(同章不同子目可能印刷差异)
            unmatched += 1
    con.commit()
    con.close()
    return updated, matched, unmatched


def run_all(pdf_dir=None, dry_run=False, resume=False, single=None):
    if single:
        pdfs = [single]
    else:
        pdf_dir = pdf_dir or DEFAULT_PDF_DIR
        pdfs = sorted(glob.glob(os.path.join(pdf_dir, '*.pdf')))
    if not pdfs:
        print('未找到 PDF')
        return

    prog = load_progress() if resume else {'done_pages': [], 'filled': [], 'failed_pages': []}
    out_dir = os.path.join(DATA_DIR, 'pdf_pages')
    total_filled = 0
    t0 = time.time()

    for pdf in pdfs:
        name = os.path.basename(pdf)
        try:
            import fitz
            n_pages = len(fitz.open(pdf))
        except Exception as e:
            print(f'  ⚠ {name}: 打开失败 {e}')
            continue
        print(f'\n=== {name}: {n_pages} 页 ===')
        for pno in range(n_pages):
            key = f'{name}#{pno}'
            if resume and page_done(prog, key):
                continue
            rows = process_page(pdf, pno, out_dir, dry_run=dry_run)
            prog['done_pages'].append(key)
            if rows is None:
                # 调用失败(403/超时) — 记入 failed_pages, 下次 --resume 可重跑
                prog.setdefault('failed_pages', []).append(key)
                print(f'  第{pno+1}页: ⚠ 识别失败(将重试)')
                continue
            if rows:
                prog['filled'].extend(rows)
                total_filled += len(rows)
                print(f'  第{pno+1}页: 提取 {len(rows)} 行 | 累计 {total_filled} | '
                      f'{time.time()-t0:.0f}s')
            if len(prog['done_pages']) % 5 == 0:
                save_progress(prog)
        save_progress(prog)

    print(f'\n识别完成: {total_filled} 行候选(耗时 {(time.time()-t0)/60:.1f} 分钟)')
    if not dry_run:
        updated, matched, unmatched = fill_db(prog['filled'])
        print(f'回填: 更新 {updated} 条 | 匹配 {matched} | 未匹配 {unmatched}')
        with open(REPORT_FILE, 'w', encoding='utf-8') as f:
            json.dump({'updated': updated, 'matched': matched, 'unmatched': unmatched,
                       'total_rows': total_filled, 'pages': len(prog['done_pages'])},
                      f, ensure_ascii=False, indent=2)
    else:
        print('(dry-run 模式, 未写库)')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='国标清单 unit 批量补录(视觉重读 PDF)')
    ap.add_argument('--pdf', default=None, help='单本 PDF 路径')
    ap.add_argument('--dir', default=None, help='PDF 目录')
    ap.add_argument('--dry-run', action='store_true', help='只识别不写库')
    ap.add_argument('--resume', action='store_true', help='断点续跑')
    args = ap.parse_args()
    run_all(pdf_dir=args.dir, dry_run=args.dry_run, resume=args.resume, single=args.pdf)
