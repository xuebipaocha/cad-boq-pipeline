"""全本扫描: 对清单标准 PDF 逐页识别, 提取全部(编码,名称,单位), 一次性清掉残余占位名/错位名/unit空。
用法: python full_scan_list.py  （内置断点续跑, 进度 data/list_scan_progress.json）"""
import sys, os, json, time, re, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
from full_backfill import LIST_PDF_DIR, WorkerPool, process_list_page

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
PROG = os.path.join(DATA_DIR, 'list_scan_progress.json')

def list_all_pages():
    """所有清单标准 PDF 的所有页。"""
    tasks = []
    for f in sorted(os.listdir(LIST_PDF_DIR)):
        if f.endswith('.pdf'):
            import fitz
            doc = fitz.open(os.path.join(LIST_PDF_DIR, f))
            n = len(doc)
            doc.close()
            for p in range(n):
                tasks.append((os.path.join(LIST_PDF_DIR, f), p))
    return tasks

def main():
    all_tasks = list_all_pages()
    prog = {'done': [], 'rows': []}
    if os.path.exists(PROG):
        prog = json.load(open(PROG, encoding='utf-8'))
    done = set(prog['done'])
    todo = [t for t in all_tasks if os.path.basename(t[0]) + '#' + str(t[1]) not in done]
    print(f'全本清单扫描: 共 {len(all_tasks)} 页, 待扫 {len(todo)} 页', flush=True)
    if todo:
        pool = WorkerPool(16)
        t0 = time.time()
        results = pool.run(todo, process_list_page, total=len(todo))
        for task, r in zip(todo, results):
            k = os.path.basename(task[0]) + '#' + str(task[1])
            prog['done'].append(k)
            if r:
                prog['rows'].extend(r)
        json.dump(prog, open(PROG, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'扫描完成: {time.time()-t0:.0f}s, 识别行 {len(prog["rows"])}', flush=True)

    # 按编码索引, 回填(名称仅占位/错位, 单位仅空)
    by_code = {}
    for r in prog['rows']:
        code = re.sub(r'[\s\u3000]', '', str(r.get('编码') or ''))
        if code:
            by_code.setdefault(code, []).append(r)
    con = sqlite3.connect(os.path.join(DATA_DIR, 'national_24.db'))
    up_name = up_unit = 0
    for code, rows in by_code.items():
        # 取第一个有名称/单位的
        name = next((r['名称'] for r in rows if r.get('名称')), None)
        unit = next((r['单位'] for r in rows if r.get('单位')), None)
        if name and re.match(r'^[a-zA-Zµ㎡³]', str(name)) is None and '按设计' not in name:
            c = con.execute(r"""UPDATE standard_items SET item_name=? WHERE item_code=? AND 
                (item_name LIKE '%\_%' ESCAPE '\' AND item_name GLOB '*_[0-9]*'
                 OR item_name LIKE '按设计%' OR item_name LIKE '计算规则%')""", (name, code))
            up_name += c.rowcount
        if unit and re.match(r'^[a-zA-Zµ㎡³]', unit) and len(unit) <= 6:
            c = con.execute("UPDATE standard_items SET unit=? WHERE item_code=? AND (unit IS NULL OR unit='')", (unit, code))
            up_unit += c.rowcount
    con.commit(); con.close()
    print(f'全本回填: 清单名称+{up_name} 单位+{up_unit}')

if __name__ == '__main__':
    main()
