# -*- coding: utf-8 -*-
"""视觉识别基准 — v6.10

对同一批渲染图分别跑各视觉后端, 记录可量化指标并落盘:
  工程类型 / 置信度 / 可见文字数 / 可见文字列表 / 构件计数合计 /
  备注长度 / 推理链长度 / tokens / 耗时
用途: 突破(分块瓦片/多尺度)前后对比 —— 同一批图、同一 prompt, 量化提升幅度。

用法:
  python3 bench_vision.py                          # 默认图集 × 全部可用后端
  python3 bench_vision.py --images a.png b.png     # 指定图
  python3 bench_vision.py --backend deepseek       # 只测一个后端
  python3 bench_vision.py --tag baseline           # 落盘基线(默认)
  python3 bench_vision.py --out ../benchmarks/vision_baseline.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import vision_query as vq

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 默认图集: output/renders 下有代表性的渲染图(真实图优先)
DEFAULT_IMAGES = [
    'output/renders/船体大楼_plan.png',          # 真实工程图(大修)
    'output/renders/园林测试图_plan.png',        # 合成测试图(园林)
    'output/renders/园林_总平面_plan.png',       # 合成测试图(总平面)
]


def _counts_sum(r):
    c = r.get('构件计数') or {}
    try:
        return sum(int(v or 0) for v in c.values() if isinstance(v, (int, float)) or str(v).isdigit())
    except Exception:
        return 0


def measure(image_path, backend_cfg, timeout=180):
    """跑单个(图, 后端)组合 → 指标字典。"""
    t0 = time.time()
    r, err = vq._call_backend(backend_cfg, image_path, vq.PROMPT_JSON, timeout)
    dt = round(time.time() - t0, 1)
    if not r:
        return {'图': os.path.basename(image_path), '后端': backend_cfg['name'],
                'OK': False, '错误': err, '耗时s': dt}
    meta = r.get('_meta') or {}
    texts = r.get('可见文字') or []
    if not isinstance(texts, list):
        texts = [str(texts)]
    return {
        '图': os.path.basename(image_path),
        '后端': backend_cfg['name'],
        'OK': True,
        '工程类型': r.get('工程类型', ''),
        '枚举越界': bool(r.get('枚举越界')),
        '置信度': r.get('工程类型置信度'),
        '可见文字数': len(texts),
        '可见文字': texts,
        '构件计数合计': _counts_sum(r),
        '构件计数': r.get('构件计数') or {},
        '备注长度': len(str(r.get('备注', ''))),
        '推理链长度': len(str(meta.get('推理链', ''))),
        'total_tokens': meta.get('total_tokens', 0),
        '耗时s': dt,
    }


def run(images, backends=None, out_path=None, tag='baseline', timeout=180):
    cfgs = vq.resolve_backends()
    if backends:
        cfgs = [c for c in cfgs if c['name'] in backends]
    if not cfgs:
        print('无可用视觉后端(检查 DEEPSEEK_API_KEY/QWEN_API_KEY)')
        return None
    print(f'视觉基准 [{tag}] — 图 {len(images)} 张 × 后端 {len(cfgs)} 个 '
          f'({", ".join(c["name"] for c in cfgs)})')
    rows = []
    for img in images:
        p = img if os.path.isabs(img) else os.path.join(SKILL_DIR, img)
        if not os.path.exists(p):
            print(f'  跳过(不存在): {img}')
            continue
        for cfg in cfgs:
            m = measure(p, cfg, timeout=timeout)
            rows.append(m)
            if m['OK']:
                print(f"  {m['图'][:26]:28} {m['后端']:9} 类型={m['工程类型'] or '(空)':10} "
                      f"文字={m['可见文字数']:2} 计数={m['构件计数合计']:3} "
                      f"tok={m['total_tokens']:6} {m['耗时s']:6}s")
            else:
                print(f"  {m['图'][:26]:28} {m['后端']:9} FAIL {m.get('错误', '')[:60]}")

    # 聚合: 每后端平均指标(突破前后只比同一批图, 故平均值可比)
    summary = {}
    for cfg in cfgs:
        rs = [r for r in rows if r['后端'] == cfg['name'] and r['OK']]
        if not rs:
            summary[cfg['name']] = {'成功': 0, '总数': len([r for r in rows if r['后端'] == cfg['name']])}
            continue
        summary[cfg['name']] = {
            '成功': len(rs),
            '总数': len([r for r in rows if r['后端'] == cfg['name']]),
            '模型': cfg['model'],
            '平均可见文字数': round(sum(r['可见文字数'] for r in rs) / len(rs), 2),
            '平均构件计数合计': round(sum(r['构件计数合计'] for r in rs) / len(rs), 2),
            '平均总tokens': round(sum(r['total_tokens'] for r in rs) / len(rs), 0),
            '平均耗时s': round(sum(r['耗时s'] for r in rs) / len(rs), 1),
            '枚举越界次数': sum(1 for r in rs if r.get('枚举越界')),
        }
    result = {'tag': tag, 'prompt': 'PROMPT_JSON(v6.10 强化)',
              '图集': [os.path.basename(i) for i in images],
              '后端': [c['name'] for c in cfgs], '明细': rows, '汇总': summary}
    print('--- 汇总 ---')
    for k, v in summary.items():
        print(f'  {k}: {json.dumps(v, ensure_ascii=False)}')
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'  落盘: {out_path}')
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description='视觉识别基准(多后端对比)')
    ap.add_argument('--images', nargs='*', default=None, help='渲染图路径(默认内置图集)')
    ap.add_argument('--backend', nargs='*', default=None, help='只测指定后端(deepseek/qwen)')
    ap.add_argument('--tag', default='baseline', help='结果标签(如 baseline/after_tiling)')
    ap.add_argument('--timeout', type=int, default=180, help='单次调用超时秒')
    ap.add_argument('--out', default=os.path.join(SKILL_DIR, 'benchmarks', 'vision_baseline.json'),
                    help='结果落盘路径')
    args = ap.parse_args(argv)
    run(args.images or DEFAULT_IMAGES, backends=args.backend, out_path=args.out,
        tag=args.tag, timeout=args.timeout)


if __name__ == '__main__':
    main()
