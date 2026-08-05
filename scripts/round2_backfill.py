"""第二轮补录: 宽窗口(±5页)扫描残余问题条目。用法: python round2_backfill.py"""
import sys, os, json, time, re, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
from full_backfill import process_quota_page, process_list_page, fill_quota, fill_list, WorkerPool

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
TASKS = os.path.join(DATA_DIR, 'round2_tasks.json')
PROG = os.path.join(DATA_DIR, 'round2_progress.json')

def main():
    tasks = json.load(open(TASKS, encoding='utf-8'))
    prog = {'done': [], 'filled': []}
    if os.path.exists(PROG):
        prog = json.load(open(PROG, encoding='utf-8'))
    done = set(prog['done'])
    pool = WorkerPool(16)

    for kind in ('quota', 'list'):
        todo = [t for t in tasks[kind] if os.path.basename(t[0]) + '#' + str(t[1]) not in done]
        if not todo:
            continue
        fn = process_quota_page if kind == 'quota' else process_list_page
        print(f'=== 第二轮 {kind}: {len(todo)} 页 ===', flush=True)
        t0 = time.time()
        results = pool.run(todo, fn, total=len(todo))
        for task, r in zip(todo, results):
            k = os.path.basename(task[0]) + '#' + str(task[1])
            prog['done'].append(k)
            if r:
                prog['filled'].extend(r)
        print(f'{kind} 完成: {sum(1 for r in results if r)} 页有产出, {time.time()-t0:.0f}s', flush=True)
        json.dump(prog, open(PROG, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    # 回填
    up_price, up_name, up_unit = fill_quota(prog['filled'])
    up_lname, up_lunit = fill_list(prog['filled'])
    print(f'第二轮回填: 定额基价+{up_price} 名称+{up_name} 单位+{up_unit} | 清单名称+{up_lname} 单位+{up_lunit}')

if __name__ == '__main__':
    main()
