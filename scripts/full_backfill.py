# -*- coding: utf-8 -*-
"""数据库全字段高效补录工具 v2 — 问题页定位 + 并发 + 全字段提取

相比 v1(逐页全扫、单字段) 的效率优化:
1. **问题页定位**: 用数据库 page_num 只扫"有问题条目所在页"(基价<=0/名称噪音/unit缺失),
   而非 2000+ 页全扫
2. **全字段提取**: 每页一次视觉调用, 同时提取 编号+名称+单位+基价 —
   一遍扫描解决 基价577 + 名称噪音9914 + unit残留 三个问题
3. **并发3路**: 实测 9 连发无 403 限流, 速度 ×3

用法:
  python3 full_backfill.py --dry-run     # 只识别不写库
  python3 full_backfill.py --resume      # 断点续跑
  python3 full_backfill.py --concurrency 3
"""
import os
import sys
import re
import json
import glob
import time
import threading

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE)
DATA_DIR = os.path.join(SKILL_DIR, 'data')
LIAONING_DB = os.path.join(DATA_DIR, 'liaoning_24.db')
NATIONAL_DB = os.path.join(DATA_DIR, 'national_24.db')
PROGRESS_FILE = os.path.join(DATA_DIR, 'full_backfill_progress.json')
REPORT_FILE = os.path.join(DATA_DIR, 'full_backfill_report.json')

QUOTA_PDF_DIR = r'D:\用户文件\Desktop\2024年辽宁省建设工程计价依据'
LIST_PDF_DIR = r'D:\用户文件\Desktop\2024建设工程工程量清单计价标准+工程量计算标准'
VISION_TIMEOUT = 180
RENDER_DPI = 120  # v2.1: 120dpi 足够表格识别, 渲染+识别更快
LINE_THRESHOLD = 40  # v2.1: 页内绘图线条 < 40 视为非表格页, 跳过不调 API


def is_table_page_pdf(pdf_path, page_no):
    """v2.1 预筛: 判断是否为可跳过的非表格页(省 API 调用)。
    - 有文字层(非扫描)且线条 < 40 → 说明页, 跳过
    - 无文字层(扫描件) → 无法预筛, 交给视觉判断(扫描件表格也可能无线条)
    - 线条 >= 40 → 表格页, 保留
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        page = doc[page_no]
        drawings = page.get_drawings()
        n_lines = sum(1 for d in drawings for item in d['items'] if item[0] in ('l', 're'))
        n_text = len(page.get_text().strip())
        doc.close()
        if n_lines >= LINE_THRESHOLD:
            return True
        if n_text == 0:
            return True  # 扫描件, 无法判断, 交给视觉
        return False  # 有文字层但线条少 → 说明页, 跳过
    except Exception:
        return True  # 预筛失败不阻塞, 交给视觉判断

# 全字段提取 prompt(一次调用拿全字段)
QUOTA_PROMPT = (
    '这是辽宁建设工程定额PDF的定额表格页。表格列通常为: 定额编号/项目名称/单位/'
    '人工费/材料费/机械费/基价 等。\n'
    '请提取表格每一行的【定额编号】【项目名称】【单位】【基价】, 输出JSON:\n'
    '{"rows": [{"编号": "1-1", "名称": "人工挖土方", "单位": "m³", "基价": 1234.56}, ...]}\n'
    '规则: 1.名称为项目实际名称(不要表头噪音如"公称直径"或尺寸数字) 2.无表格输出{"rows":[]}'
)

LIST_PROMPT = (
    '这是工程建设标准PDF的清单表格页。表格列: 项目编码/项目名称/项目特征/计量单位 等。\n'
    '请提取每一行的【项目编码】【项目名称】【计量单位】, 输出JSON:\n'
    '{"rows": [{"编码": "010101001", "名称": "挖一般土方", "单位": "m³"}, ...]}\n'
    '规则: 1.名称取实际名称(跳过"按设计图示...计算"等规则文字) 2.无表格输出{"rows":[]}'
)


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'done_pages': [], 'filled': [], 'failed_pages': []}


def save_progress(p):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=1)


# ────────── 问题页定位 ──────────

def target_quota_pages():
    """定额问题条目 → [(pdf路径, 页码0-based)]。"""
    import sqlite3
    con = sqlite3.connect(LIAONING_DB)
    rows = con.execute('''SELECT source_file, page_num FROM quota_items WHERE 
        (base_price<=0 OR base_price IS NULL)
        OR (item_name GLOB '*[0-9]×[0-9]*' OR item_name LIKE '%项目%' OR item_name LIKE '%名称%'
         OR item_name LIKE '%单位%' OR length(item_name) > 40 OR item_name LIKE '%覆土深度%'
         OR item_name LIKE '%井深%' OR item_name IS NULL OR item_name='')
        OR (unit IS NULL OR unit='')''').fetchall()
    con.close()
    targets = set()
    for src, page in rows:
        if not src or page is None:
            continue
        fname = os.path.basename(src)
        pdf = os.path.join(QUOTA_PDF_DIR, fname)
        if os.path.exists(pdf):
            # 页号容错: 数据库 page_num 可能偏移 1 页, 双窗口扫描
            for off in (0, 1):
                targets.add((pdf, max(0, int(page) - 1 - off)))
    return sorted(targets)


def target_list_pages():
    """清单问题条目(占位名/错位名/unit缺失) → [(pdf, 页码)]。"""
    import sqlite3
    con = sqlite3.connect(NATIONAL_DB)
    rows = con.execute(r'''SELECT source_file, page_num FROM standard_items WHERE 
        (item_name LIKE '%\_%' ESCAPE '\' AND item_name GLOB '*_[0-9]*')
        OR item_name LIKE '按设计%' OR item_name LIKE '计算规则%'
        OR (unit IS NULL OR unit='')''').fetchall()
    con.close()
    targets = set()
    for src, page in rows:
        if not src or page is None:
            continue
        fname = os.path.basename(src)
        pdf = os.path.join(LIST_PDF_DIR, fname)
        if os.path.exists(pdf):
            # 页号容错: 双窗口
            for off in (0, 1):
                targets.add((pdf, max(0, int(page) - 1 - off)))
    return sorted(targets)


# ────────── 并发处理 ──────────

class WorkerPool:
    def __init__(self, n):
        self.n = n
        self.lock = threading.Lock()

    def run(self, tasks, fn, progress=None, total=None, on_batch=None):
        results = [None] * len(tasks)
        idx = [0]
        counter = [0]

        def _worker():
            while True:
                with self.lock:
                    i = idx[0]
                    idx[0] += 1
                if i >= len(tasks):
                    return
                r = fn(tasks[i])
                results[i] = r
                with self.lock:
                    counter[0] += 1
                    done = counter[0]
                if total and done % 10 == 0:
                    print(f'  进度 {done}/{total}', flush=True)
                if on_batch and done % 20 == 0:
                    on_batch(done, results)

        threads = [threading.Thread(target=_worker) for _ in range(self.n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results


def process_quota_page(task):
    """(pdf, page) → [{编号,名称,单位,基价}] 或 None(失败)。"""
    pdf, pno = task
    if not is_table_page_pdf(pdf, pno):
        return []  # 非表格页直接跳过
    from vision_query import query_vision
    import fitz
    try:
        doc = fitz.open(pdf)
        pix = doc[pno].get_pixmap(dpi=RENDER_DPI)
        tmp = os.path.join(DATA_DIR, 'full_pages')
        os.makedirs(tmp, exist_ok=True)
        png = os.path.join(tmp, f'q_{os.path.basename(pdf)}_{pno}.png')
        pix.save(png)
        doc.close()
    except Exception:
        return None
    r = query_vision(png, enable=True, prompt=QUOTA_PROMPT, timeout=VISION_TIMEOUT)
    if r is None:
        return None
    content = r.get('原始回复') or r.get('工程类型') or ''
    c = content.strip()
    if '```' in c:
        c = c.split('```')[1]
        if c.startswith('json'):
            c = c[4:]
    try:
        data = json.loads(c)
    except Exception:
        data = r.get('rows') if isinstance(r.get('rows'), list) else None
    # qwen 可能直接结构化返回 rows(list), 也可能包在 dict 里
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get('rows'), list):
        rows = data['rows']
    else:
        return []
    out = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        code = str(x.get('编号') or x.get('编码') or '').strip()
        name = str(x.get('名称') or x.get('项目名称') or '').strip()
        unit = str(x.get('单位') or x.get('计量单位') or '').strip()
        price = x.get('基价') or x.get('综合单价')
        if not code:
            continue
        row = {'编号': code}
        if name:
            row['名称'] = name
        if unit:
            row['单位'] = unit
        if price is not None:
            try:
                row['基价'] = float(price)
            except (TypeError, ValueError):
                pass
        out.append(row)
    return out


def process_list_page(task):
    """清单页 → [{编码,名称,单位}] 或 None。"""
    pdf, pno = task
    if not is_table_page_pdf(pdf, pno):
        return []
    from vision_query import query_vision
    import fitz
    try:
        doc = fitz.open(pdf)
        pix = doc[pno].get_pixmap(dpi=RENDER_DPI)
        tmp = os.path.join(DATA_DIR, 'full_pages')
        os.makedirs(tmp, exist_ok=True)
        png = os.path.join(tmp, f'l_{os.path.basename(pdf)}_{pno}.png')
        pix.save(png)
        doc.close()
    except Exception:
        return None
    r = query_vision(png, enable=True, prompt=LIST_PROMPT, timeout=VISION_TIMEOUT)
    if r is None:
        return None
    content = r.get('原始回复') or r.get('工程类型') or ''
    c = content.strip()
    if '```' in c:
        c = c.split('```')[1]
        if c.startswith('json'):
            c = c[4:]
    try:
        data = json.loads(c)
    except Exception:
        data = r.get('rows') if isinstance(r.get('rows'), list) else None
    # qwen 可能直接结构化返回 rows(list), 也可能包在 dict 里
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get('rows'), list):
        rows = data['rows']
    else:
        return []
    out = []
    for x in rows:
        if not isinstance(x, dict):
            continue
        code = str(x.get('编码') or '').strip()
        name = str(x.get('名称') or '').strip()
        unit = str(x.get('单位') or '').strip()
        if not code:
            continue
        row = {'编码': code}
        if name and '按设计' not in name and '计算规则' not in name:
            row['名称'] = name
        if unit:
            row['单位'] = unit
        out.append(row)
    return out


def fill_quota(filled):
    """回填定额: 基价(仅<=0) + 名称(仅噪音) + 单位(仅空)。"""
    import sqlite3
    con = sqlite3.connect(LIAONING_DB)
    up_price = up_name = up_unit = 0
    for row in filled:
        code = re.sub(r'[\s\u3000]', '', str(row.get('编号', '')))
        if not code:
            continue
        if '基价' in row:
            p = row['基价']
            if p > 0:
                c = con.execute("UPDATE quota_items SET base_price=? WHERE quota_code=? AND (base_price<=0 OR base_price IS NULL)", (p, code))
                up_price += c.rowcount
        if '名称' in row and row['名称']:
            nm = row['名称']
            c = con.execute("""UPDATE quota_items SET item_name=? WHERE quota_code=? AND 
                (item_name GLOB '*[0-9]×[0-9]*' OR item_name LIKE '%项目%' OR item_name LIKE '%名称%'
                 OR item_name LIKE '%单位%' OR length(item_name) > 40 OR item_name LIKE '%覆土深度%'
                 OR item_name LIKE '%井深%' OR item_name IS NULL OR item_name='')""", (nm, code))
            up_name += c.rowcount
        if '单位' in row and row['单位']:
            u = row['单位']
            if re.match(r'^[a-zA-Zµ㎡³]', u) and len(u) <= 6:
                c = con.execute("UPDATE quota_items SET unit=? WHERE quota_code=? AND (unit IS NULL OR unit='')", (u, code))
                up_unit += c.rowcount
    con.commit()
    con.close()
    return up_price, up_name, up_unit


def fill_list(filled):
    """回填清单: 名称(仅占位/错位) + 单位(仅空)。"""
    import sqlite3
    con = sqlite3.connect(NATIONAL_DB)
    up_name = up_unit = 0
    for row in filled:
        code = re.sub(r'[\s\u3000]', '', str(row.get('编码', '')))
        if not code:
            continue
        if '名称' in row and row['名称']:
            c = con.execute(r"""UPDATE standard_items SET item_name=? WHERE item_code=? AND 
                (item_name LIKE '%\_%' ESCAPE '\' AND item_name GLOB '*_[0-9]*'
                 OR item_name LIKE '按设计%' OR item_name LIKE '计算规则%')""", (row['名称'], code))
            up_name += c.rowcount
        if '单位' in row and row['单位']:
            u = row['单位']
            if re.match(r'^[a-zA-Zµ㎡³]', u) and len(u) <= 6:
                c = con.execute("UPDATE standard_items SET unit=? WHERE item_code=? AND (unit IS NULL OR unit='')", (u, code))
                up_unit += c.rowcount
    con.commit()
    con.close()
    return up_name, up_unit


def run_all(dry_run=False, resume=False, concurrency=3):
    quota_tasks = target_quota_pages()
    list_tasks = target_list_pages()
    print(f'问题页定位: 定额 {len(quota_tasks)} 页 + 清单 {len(list_tasks)} 页')
    prog = load_progress() if resume else {'done_pages': [], 'filled': [], 'failed_pages': []}
    done_set = set(prog['done_pages']) if resume else set()
    pool = WorkerPool(concurrency)

    def _key(task):
        return os.path.basename(task[0]) + '#' + str(task[1])

    # 定额
    todo = [t for t in quota_tasks if _key(t) not in done_set]
    print(f'\n=== 定额问题页: 待扫 {len(todo)} 页 (并发{concurrency}) ===')
    if todo:
        t0 = time.time()

        def _save_quota_batch(done, results):
            with pool.lock:
                for i in range(done):
                    r = results[i]
                    if r is None:
                        continue
                    k = _key(todo[i])
                    if k in prog['done_pages']:
                        continue
                    prog['done_pages'].append(k)
                    if r:
                        prog['filled'].extend(r)
            save_progress(prog)

        results = pool.run(todo, process_quota_page, total=len(todo), on_batch=_save_quota_batch)
        for task, r in zip(todo, results):
            k = _key(task)
            if k not in prog['done_pages']:
                prog['done_pages'].append(k)
            if r is None:
                prog.setdefault('failed_pages', []).append(k)
            elif r:
                prog['filled'].extend(r)
        print(f'定额扫描完成: 新增 {sum(1 for r in results if r)} 页成功, 耗时 {(time.time()-t0)/60:.1f} 分钟')
        save_progress(prog)

    # 清单
    todo2 = [t for t in list_tasks if _key(t) not in done_set]
    print(f'\n=== 清单问题页: 待扫 {len(todo2)} 页 ===')
    if todo2:
        t0 = time.time()

        def _save_list_batch(done, results2):
            with pool.lock:
                for i in range(done):
                    r = results2[i]
                    if r is None:
                        continue
                    k = _key(todo2[i])
                    if k in prog['done_pages']:
                        continue
                    prog['done_pages'].append(k)
                    if r:
                        prog['filled'].extend(r)
            save_progress(prog)

        results2 = pool.run(todo2, process_list_page, total=len(todo2), on_batch=_save_list_batch)
        for task, r in zip(todo2, results2):
            k = _key(task)
            if k not in prog['done_pages']:
                prog['done_pages'].append(k)
            if r is None:
                prog.setdefault('failed_pages', []).append(k)
            elif r:
                prog['filled'].extend(r)
        print(f'清单扫描完成: 新增 {sum(1 for r in results2 if r)} 页成功, 耗时 {(time.time()-t0)/60:.1f} 分钟')
        save_progress(prog)

    print(f'\n总识别行: {len(prog["filled"])} | 失败页: {len(prog.get("failed_pages", []))}')
    if not dry_run:
        up_price, up_name, up_unit = fill_quota(prog['filled'])
        up_lname, up_lunit = fill_list(prog['filled'])
        print(f'定额回填: 基价+{up_price} 名称+{up_name} 单位+{up_unit}')
        print(f'清单回填: 名称+{up_lname} 单位+{up_lunit}')
        with open(REPORT_FILE, 'w', encoding='utf-8') as f:
            json.dump({'quota_price': up_price, 'quota_name': up_name, 'quota_unit': up_unit,
                       'list_name': up_lname, 'list_unit': up_lunit,
                       'rows': len(prog['filled']), 'pages': len(prog['done_pages'])},
                      f, ensure_ascii=False, indent=2)
    else:
        print('(dry-run, 未写库)')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='数据库全字段高效补录 v2')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--concurrency', type=int, default=8)
    args = ap.parse_args()
    run_all(dry_run=args.dry_run, resume=args.resume, concurrency=args.concurrency)
