# -*- coding: utf-8 -*-
"""突破前后量化 — v6.10（p5c）

同一张 DXF 两种视觉路径对比, 量化"视觉分块瓦片识别"的收益:
  ① 整图识别(突破前基线): 单张整图渲染(14×10in @150dpi) → 一次视觉调用
  ② 分块识别(突破后):     密度自适应切块 @300dpi → 并发逐块 + 坐标反算 + 去重
比较维度: 文字召回数 / 构件识别数 / tokens / 耗时。

用途: 用合成"高密度小符号图"(96 门窗号 + 96 索引号)与真实图纸量化提升幅度;
结果落盘 benchmarks/vision_breakthrough.json 供汇报与回归引用。

用法:
  python3 bench_breakthrough.py 图纸.dxf [--grid 3x3] [--backend qwen] [--out json]
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def count_symbols(dxf_path):
    """统计图内"小符号"参照量: 门窗号样式文字(字母+4位数字) 与 索引号(纯2~3位数字)。"""
    import re
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        return {}
    code_like = idx_like = 0
    for e in doc.modelspace():
        if e.dxftype() == 'TEXT':
            t = (e.dxf.text or '').strip()
            if re.fullmatch(r'[A-Za-z]{1,3}\d{4}', t):
                code_like += 1
            elif re.fullmatch(r'\d{2,3}', t):
                idx_like += 1
    return {'门窗号样式文字': code_like, '索引号样式文字': idx_like}


def run(dxf_path, grid=(3, 3), backend=None, out_json=None, workers=4, timeout=180):
    import vision_query as vq
    from render_dxf import render_dxf
    from vision_tiles import identify_tiles

    base = os.path.splitext(os.path.basename(dxf_path))[0]
    out_dir = os.path.join(os.path.dirname(os.path.abspath(dxf_path)), 'renders_breakthrough')
    os.makedirs(out_dir, exist_ok=True)
    symbols = count_symbols(dxf_path)

    print(f'=== 突破对比: {base} ===')
    print(f'图内小符号参照: {symbols}')

    # ① 整图(突破前)
    meta = render_dxf(dxf_path, out_dir, per_layer=False)
    png = meta['files']['full']
    print(f'整图渲染: {os.path.basename(png)} ({os.path.getsize(png)} bytes)')
    t0 = time.time()
    whole = vq.query_vision(png, enable=True, backend=backend, timeout=timeout)
    dt_whole = round(time.time() - t0, 1)
    w_texts = []
    if whole:
        wt = whole.get('可见文字') or []
        w_texts = [t if isinstance(t, str) else t.get('文本', '') for t in wt] if isinstance(wt, list) else [str(wt)]
    w_meta = (whole or {}).get('_meta') or {}
    print(f"  整图识别: 文字 {len(w_texts)} 条 | tokens {w_meta.get('total_tokens', 0)} | {dt_whole}s")
    if w_texts:
        print(f"    前8条: {w_texts[:8]}")

    # ② 分块(突破后)
    tiled = identify_tiles(dxf_path, backend=backend, grid=grid, dpi=300,
                           workers=workers, timeout=timeout, out_dir=out_dir)
    t_stats = tiled.get('统计') or {}
    t_texts = [t.get('文本', '') for t in (tiled.get('文字') or [])]
    print(f"  分块识别: 文字 {t_stats.get('文字去重后', 0)} 条(原始观测 {t_stats.get('文字原始观测', 0)}, "
          f"去重率 {t_stats.get('去重率', 0)}) | 块 {t_stats.get('有效块', 0)}/{t_stats.get('总块数', 0)} "
          f"| tokens {t_stats.get('tokens', 0)} | {t_stats.get('耗时s', 0)}s")
    if t_texts:
        print(f"    前12条: {t_texts[:12]}")

    # ③ 对比(以"代码型编号"为可比口径: 能对上小符号参照的文字)
    import re

    def code_hits(texts):
        s = set()
        for t in texts:
            for m in re.finditer(r'\b([A-Za-z]{1,3})[-]?(\d{3,4})\b', str(t)):
                s.add(f'{m.group(1).upper()}{m.group(2)}')
        return s

    w_codes, t_codes = code_hits(w_texts), code_hits(t_texts)
    ref_total = sum(symbols.values()) or 1
    result = {
        '图纸': base, '切块': f'{grid[0]}x{grid[1]}',
        '小符号参照': symbols,
        '整图': {
            '文字数': len(w_texts), '文字': w_texts[:60],
            '编号类命中': sorted(w_codes), '编号命中数': len(w_codes),
            'tokens': w_meta.get('total_tokens', 0), '耗时s': dt_whole,
            'OK': bool(whole),
        },
        '分块': {
            '文字数': t_stats.get('文字去重后', 0),
            '文字': t_texts[:80],
            '编号类命中': sorted(t_codes), '编号命中数': len(t_codes),
            '原始观测': t_stats.get('文字原始观测', 0),
            '去重率': t_stats.get('去重率', 0),
            'tokens': t_stats.get('tokens', 0), '耗时s': t_stats.get('耗时s', 0),
            '有效块': f"{t_stats.get('有效块', 0)}/{t_stats.get('总块数', 0)}",
        },
        '提升': {
            '文字数倍数': round(len(t_texts) / max(len(w_texts), 1), 2),
            '编号命中倍数': round(len(t_codes) / max(len(w_codes), 1), 2),
            '小符号召回率(整图)': round(len(w_codes) / ref_total, 3),
            '小符号召回率(分块)': round(len(t_codes) / ref_total, 3),
        },
    }
    print('--- 提升 ---')
    for k, v in result['提升'].items():
        print(f'  {k}: {v}')
    if out_json:
        os.makedirs(os.path.dirname(out_json), exist_ok=True)
        with open(out_json, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'  落盘: {out_json}')
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description='视觉突破前后量化(整图 vs 分块)')
    ap.add_argument('dxf', help='DXF 图纸路径')
    ap.add_argument('--grid', default='3x3', help='分块网格(默认 3x3)')
    ap.add_argument('--backend', default=None, choices=('deepseek', 'qwen'))
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--timeout', type=int, default=180)
    ap.add_argument('--out', default=os.path.join(SKILL_DIR, 'benchmarks', 'vision_breakthrough.json'))
    args = ap.parse_args(argv)
    cols, rows = (int(x) for x in args.grid.lower().split('x'))
    run(args.dxf, grid=(cols, rows), backend=args.backend, out_json=args.out,
        workers=args.workers, timeout=args.timeout)


if __name__ == '__main__':
    main()
