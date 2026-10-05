# -*- coding: utf-8 -*-
"""分块视觉识别调度 — v6.10 视觉精度突破(核心)

思路(实测驱动): 整图渲染 2100×1500px 对 A0/A1 施工图意味着 1px≈40mm 实物, 小符号
(门窗号/索引/规格标注)不足 1px 必然漏检。改为:
  ① 切块高 dpi 渲染(render_dxf.render_tiles, 密度自适应切块)
  ② 逐块视觉识别(每块单独调用, 块内文字/符号相对像素成倍放大)
  ③ 坐标反算: 模型给出的归一化坐标 → 图纸坐标系(块 bbox 线性映射, y 轴翻转)
  ④ 合并去重: 同文本/同位置构件跨块重复观测 → 归并(重叠区裁决: 取坐标最靠块心者)
  ⑤ 汇总: 文字清单 + 构件计数 + 块明细 + 统计(供突破前后量化对比)

v6.5 纪律不变: 分块视觉只产出"视觉识别"结果, 与几何冲突一律标待核, 永不覆盖几何量。

用法:
  python3 vision_tiles.py 图纸.dxf [--grid 3x3] [--backend qwen] [--workers 4] [--out r.json]
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

import render_dxf
import vision_query as vq

# 分块专用 prompt: 在整图 prompt 基础上要求"逐条 + 归一化坐标", 便于坐标反算与去重
PROMPT_TILE_JSON = (
    '你是 CAD 图纸视觉识别助手。这是一张施工图的**局部切块**, 请只报你在这块图里看到的。\n'
    '硬性要求:\n'
    '1) 只输出一个 JSON 对象, 不要 markdown 代码块(不要 ```), 不要前后说明;\n'
    '2) "可见文字" 逐条列出你能辨认的文字(标注/规格/编号/图名/尺寸), 每条给出归一化坐标 '
    '坐标[0~1], 图像左上角为原点, 格式 [x, y];\n'
    '3) "构件" 逐条列出你数出的构件(窗户/门/柱/设备符号/苗木块), 同样给归一化坐标;\n'
    '4) 看不清/不确定的**不要编造**: 文字看不清的不列, 构件不确定的类型填 "未定";\n'
    '5) 坐标是估计值, 宁可粗略也要给出, 便于图纸坐标反算。\n'
    'JSON 结构:\n'
    '{\n'
    '  "可见文字": [{"文本": "", "坐标": [0.0, 0.0]}],\n'
    '  "构件": [{"类型": "窗户", "坐标": [0.0, 0.0]}],\n'
    '  "构件计数": {"窗户": 0, "门": 0, "柱": 0, "设备符号": 0, "苗木块": 0},\n'
    '  "备注": ""\n'
    '}'
)
KEEP_TYPES = ('窗户', '门', '柱', '设备符号', '苗木块')

# ── 分块必要性判定(v6.10 实测驱动) ──
# 实测对比: 合成小图(跨度约 4.6 万单位)整图渲染 1px≈22 单位 → 分块无增益却多花 9.8 倍 token;
# 真实 A0 图(跨度数百万单位)整图 1px≫200 单位 → 小字必然漏检, 分块带来 25~30 倍文字召回。
# 判据: 整图渲染下 1 像素对应的图纸跨度超过阈值 → 建议分块。
TILING_MM_PER_PX_THRESHOLD = 30.0
FIG_PX_LONG = 2100  # render_dxf 整图 14×10in @150dpi 的像素跨度


def estimate_pixel_scale(dxf_path, fig_px=FIG_PX_LONG):
    """估算整图渲染下 1 像素对应多少图纸单位(通常为 mm)。返回 None 表示无实体。"""
    try:
        import ezdxf
        from render_dxf import _collect_entities
        doc = ezdxf.readfile(dxf_path)
        _, bbox = _collect_entities(doc.modelspace())
    except Exception:
        return None
    if not bbox:
        return None
    span = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
    return round(span / float(fig_px), 2)


def tiling_recommended(dxf_path, threshold=None):
    """→ (是否建议分块, 1px≈多少单位, 阈值)。阈值可用 VISION_TILE_MM_PER_PX 覆盖。"""
    th = threshold if threshold is not None else float(
        os.environ.get('VISION_TILE_MM_PER_PX', TILING_MM_PER_PX_THRESHOLD))
    mmpp = estimate_pixel_scale(dxf_path)
    if mmpp is None:
        return False, None, th
    return mmpp > th, mmpp, th


def _norm_text(t):
    """文字归一化(用于跨块去重): 去空白、统一中英文括号、全角转半角。"""
    s = str(t or '').strip()
    for a, b in (('（', '('), ('）', ')'), ('，', ','), ('：', ':'), ('；', ';'), ('　', '')):
        s = s.replace(a, b)
    return ''.join(s.split()).lower()


def tile_to_drawing(band, nx, ny):
    """归一化坐标(图像左上原点) → 图纸坐标(块 bbox 线性映射, y 轴翻转)。"""
    x0, y0, x1, y1 = band
    try:
        nx = max(0.0, min(1.0, float(nx)))
        ny = max(0.0, min(1.0, float(ny)))
    except (TypeError, ValueError):
        return None
    return (round(x0 + nx * (x1 - x0), 1), round(y1 - ny * (y1 - y0), 1))


def _parse_coord(item, band):
    """从模型返回项里取坐标并反算。支持 {"坐标":[x,y]} / [x,y] / {"x":..,"y":..}。"""
    if isinstance(item, dict):
        c = item.get('坐标') or item.get('coord') or item.get('point')
        if c is None and ('x' in item or 'y' in item):
            c = [item.get('x'), item.get('y')]
    else:
        c = item
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        return tile_to_drawing(band, c[0], c[1])
    return None


def _coord_suspect(coord, global_bbox, tol=0.5):
    """坐标合理性校验(v6.10 实测): 密度切块的块 bbox 可能横跨离群图元(实测出现 x≈449万
    而主体在 215万 的'1:500'文字)。超出全局裁剪 bbox 的 tol 倍 → 判可疑, 保留文本但
    丢弃坐标(不参与跨块去重距离计算), 避免离群坐标污染合并结果。
    """
    if not coord or not global_bbox or len(global_bbox) != 4:
        return False
    x0, y0, x1, y1 = global_bbox
    w, h = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    return not (x0 - w * tol <= coord[0] <= x1 + w * tol
                and y0 - h * tol <= coord[1] <= y1 + h * tol)


def _merge_texts(observations, dist_thresh):
    """文字合并去重: 同归一化文本归并; 坐标取观测中"最靠近其块中心"的那次(重叠区裁决)。"""
    buckets = {}
    for obs in observations:
        key = _norm_text(obs['文本'])
        if not key:
            continue
        buckets.setdefault(key, []).append(obs)
    merged = []
    for key, obs_list in buckets.items():
        # 裁决: 优先"坐标已知且离块心最近"; 坐标可疑者(离群)不参与
        scored = [(o['_center_dist'], o) for o in obs_list
                  if o.get('坐标') and not o.get('坐标可疑')]
        if scored:
            scored = [s for s in scored if s[0] is not None]
        if scored:
            scored.sort(key=lambda x: x[0])
            best = scored[0][1]
            coord = best.get('坐标')
        else:
            best, coord = obs_list[0], None
        merged.append({
            '文本': max((o['文本'] for o in obs_list), key=len),
            '图纸坐标': coord,
            '观测次数': len(obs_list),
            '来源块': sorted({o['块'] for o in obs_list}),
            '后端': sorted({o.get('后端', '') for o in obs_list if o.get('后端')}),
            '坐标可疑观测数': sum(1 for o in obs_list if o.get('坐标可疑')),
        })
    merged.sort(key=lambda m: (-m['观测次数'], m['文本']))
    return merged


def _merge_components(observations, dist_thresh):
    """构件合并去重: 同类型 + 图纸坐标邻近(重叠区同一构件被两块看到) → 聚类。"""
    clusters = []
    for obs in observations:
        typ = obs['类型']
        coord = None if obs.get('坐标可疑') else obs.get('图纸坐标')
        hit = None
        for c in clusters:
            if c['类型'] != typ:
                continue
            if coord and c['图纸坐标']:
                dx = abs(coord[0] - c['图纸坐标'][0])
                dy = abs(coord[1] - c['图纸坐标'][1])
                if max(dx, dy) <= dist_thresh:
                    hit = c
                    break
            elif coord is None and c['图纸坐标'] is None and c['来源块'] == obs['块']:
                hit = c
                break
        if hit:
            hit['观测次数'] += 1
            hit['来源块'] = sorted(set(hit['来源块']) | {obs['块']})
        else:
            clusters.append({'类型': typ, '图纸坐标': coord, '观测次数': 1,
                             '来源块': [obs['块']]})
    return clusters


def _parse_result(r, band, name, gb, texts, comps):
    """把单块视觉结果解析并入 texts/comps(主循环与二级细分共用)→ (n_text, n_comp, 统计)。"""
    cx, cy = (band[0] + band[2]) / 2, (band[1] + band[3]) / 2
    texts_raw = r.get('可见文字') or []
    if isinstance(texts_raw, dict):
        texts_raw = [{'文本': k, '坐标': v} for k, v in texts_raw.items()]
    n_text = 0
    for it in texts_raw:
        txt = it.get('文本') if isinstance(it, dict) else it
        coord = _parse_coord(it, band)
        if not txt:
            continue
        suspect = _coord_suspect(coord, gb)
        dist = None
        if coord and not suspect:
            dist = max(abs(coord[0] - cx), abs(coord[1] - cy))
        texts.append({'文本': str(txt), '坐标': coord, '块': name, '_center_dist': dist,
                      '坐标可疑': suspect,
                      '后端': (r.get('_meta') or {}).get('模型', '')})
        n_text += 1
    n_comp = 0
    for it in (r.get('构件') or []):
        typ = (it.get('类型') if isinstance(it, dict) else None) or '未定'
        coord = _parse_coord(it, band)
        comps.append({'类型': typ, '坐标': coord, '块': name,
                      '坐标可疑': _coord_suspect(coord, gb)})
        n_comp += 1
    # 兜底: 模型没给"构件"明细时, 用 构件计数 之和作为该块构件数(无坐标)
    cnt = r.get('构件计数') or {}
    cnt_sum = 0
    try:
        cnt_sum = sum(int(v or 0) for v in cnt.values()
                      if str(v).isdigit() or isinstance(v, (int, float)))
    except Exception:
        cnt_sum = 0
    if not n_comp and cnt_sum:
        for typ, v in cnt.items():
            try:
                v = int(v or 0)
            except (TypeError, ValueError):
                continue
            for _ in range(v):
                comps.append({'类型': typ, '坐标': None, '块': name})
                n_comp += 1
    stat = {
        '块': name, 'OK': True, '文字数': n_text, '构件数': n_comp,
        '模型': (r.get('_meta') or {}).get('模型', ''),
        '后端': (r.get('_meta') or {}).get('后端', ''),
        'tokens': (r.get('_meta') or {}).get('total_tokens', 0),
        '解析失败': bool(r.get('_解析失败')),
        '输出截断': bool(r.get('_输出截断')),
    }
    flag = ''
    if r.get('_解析失败'):
        flag = f" ⚠解析失败(截断={bool(r.get('_输出截断'))})"
    elif r.get('_输出截断'):
        flag = ' ⚠输出截断'
    print(f"  块 {name:8} 文字 {n_text:2} 构件 {n_comp:2} "
          f"({(r.get('_meta') or {}).get('后端', '?')}){flag}")
    return n_text, n_comp, stat


def identify_tiles(dxf_path, backend=None, grid=(3, 3), tiles=None, dpi=300,
                   timeout=180, workers=4, out_json=None, out_dir=None, subdivide=True):
    """分块识别主流程 → 合并结果 dict。"""
    t_start = time.time()
    meta = render_dxf.render_tiles(dxf_path, out_dir=out_dir, grid=grid, tiles=tiles,
                                   dpi=dpi, density_aware=True)
    tile_list = meta.get('tiles') or []
    if not tile_list:
        return {'图纸': os.path.basename(dxf_path), '错误': '切块为空(无可渲染实体)', '块': []}
    print(f'分块识别: {len(tile_list)} 块 @{dpi}dpi (模式 {meta.get("tile_mode")}, '
          f'后端 {backend or "默认"}) → 并发 {workers}')

    def _one(t):
        band = tuple(t['bbox'])
        cx, cy = (band[0] + band[2]) / 2, (band[1] + band[3]) / 2
        r = vq.query_vision(t['file'], prompt=PROMPT_TILE_JSON, enable=True,
                            backend=backend, timeout=timeout)
        if not r:
            return t['name'], None, band, (cx, cy)
        return t['name'], r, band, (cx, cy)

    texts, comps, tile_stats = [], [], []
    gb = meta.get('bbox')  # 全局分位裁剪 bbox, 用于坐标合理性校验
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for name, r, band, (cx, cy) in ex.map(_one, tile_list):
            if not r:
                tile_stats.append({'块': name, 'OK': False})
                continue
            _n_t, _n_c, stat = _parse_result(r, band, name, gb, texts, comps)
            tile_stats.append(stat)

    # v6.10 二级细分: 截断/解析失败的块 → 该块再切 2×2 重试(越密集越细分)
    # 实测: 船体大楼 r2c2 标注密集块即使 max_tokens=8192 仍输出截断 → 细分后每子块内容减半
    if subdivide and not tiles:
        bad = [t for t in tile_stats if t.get('OK') and (t.get('解析失败') or t.get('输出截断'))]
        sub_ids = set()
        for b in bad:
            src = next((t for t in tile_list if t['name'] == b['块']), None)
            if not src:
                continue
            bx0, by0, bx1, by1 = src['bbox']
            mx, my = (bx0 + bx1) / 2, (by0 + by1) / 2
            sub_spec = []
            for si, band_s in enumerate(((bx0, my, mx, by1), (mx, my, bx1, by1),
                                         (bx0, by0, mx, my), (mx, by0, bx1, my)), 1):
                # 子块带 5% 内重叠, 防切缝处丢字
                px, py = (mx - bx0) * 0.05, (my - by0) * 0.05
                sx0, sy0, sx1, sy1 = band_s
                sub_spec.append((f"{b['块']}s{si}",
                                 (sx0 - px, sy0 - py, sx1 + px, sy1 + py)))
            try:
                sm = render_dxf.render_tiles(dxf_path, out_dir=out_dir, tiles=sub_spec, dpi=dpi)
            except Exception as e:
                print(f"    ⚠ {b['块']} 细分渲染失败: {e}")
                continue
            subs = sm.get('tiles') or []
            print(f"  ↻ 二级细分 {b['块']} → {len(subs)} 个子块重试")

            def _one_sub(t):
                return t['name'], vq.query_vision(t['file'], prompt=PROMPT_TILE_JSON,
                                                 enable=True, backend=backend,
                                                 timeout=timeout), tuple(t['bbox'])

            with ThreadPoolExecutor(max_workers=max(1, workers)) as ex2:
                for sname, sr, sband in ex2.map(_one_sub, subs):
                    if not sr:
                        continue
                    _t, _c, sstat = _parse_result(sr, sband, sname, gb, texts, comps)
                    sstat['二级细分'] = True
                    sstat['父块'] = b['块']
                    sstat['OK'] = not (sstat.get('解析失败') or sstat.get('输出截断'))
                    tile_stats.append(sstat)
                    sub_ids.add(sname)

    # 去重阈值: 块对角线的 3%(重叠区同一目标在两块的坐标估计差通常在此量级内)
    dx = (meta.get('bbox') or [0, 0, 1, 1])
    diag = ((dx[2] - dx[0]) ** 2 + (dx[3] - dx[1]) ** 2) ** 0.5 if len(dx) == 4 else 1000
    dist_thresh = max(diag * 0.03, 100.0)

    merged_texts = _merge_texts(texts, dist_thresh)
    merged_comps = _merge_components(comps, dist_thresh)

    counts = {}
    for c in merged_comps:
        counts[c['类型']] = counts.get(c['类型'], 0) + 1

    result = {
        '图纸': os.path.basename(dxf_path),
        '方式': '分块视觉识别(坐标反算+去重)',
        '切块': {'模式': meta.get('tile_mode'), '块数': len(tile_list), 'dpi': dpi},
        '后端': backend or '默认(deepseek→qwen)',
        '统计': {
            '文字原始观测': len(texts),
            '文字去重后': len(merged_texts),
            '去重率': round(1 - len(merged_texts) / len(texts), 3) if texts else 0,
            '构件原始观测': len(comps),
            '构件去重后': len(merged_comps),
            '有效块': sum(1 for t in tile_stats if t.get('OK')),
            '总块数': len(tile_stats),
            '耗时s': round(time.time() - t_start, 1),
            'tokens': sum(t.get('tokens', 0) for t in tile_stats),
        },
        '文字': merged_texts,
        '构件': merged_comps,
        '构件计数': counts,
        '块明细': tile_stats,
    }
    if out_json:
        with open(out_json, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'  落盘: {out_json}')
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description='分块视觉识别(切块→识别→坐标反算→去重合并)')
    ap.add_argument('dxf', help='DXF 图纸路径')
    ap.add_argument('--grid', default='3x3', help='切块网格 cols x rows(默认 3x3)')
    ap.add_argument('--backend', default=None, choices=('deepseek', 'qwen'), help='视觉后端')
    ap.add_argument('--dpi', type=int, default=300, help='块渲染 dpi(默认 300)')
    ap.add_argument('--workers', type=int, default=4, help='并发数(默认 4)')
    ap.add_argument('--timeout', type=int, default=180, help='单块超时秒')
    ap.add_argument('--out', default=None, help='结果 JSON 落盘路径')
    ap.add_argument('--out-dir', default=None, help='块图输出目录(默认图纸旁 renders)')
    args = ap.parse_args(argv)
    try:
        cols, rows = (int(x) for x in args.grid.lower().split('x'))
    except Exception:
        print(f'--grid 格式错误: {args.grid} (应为 3x3)')
        return
    r = identify_tiles(args.dxf, backend=args.backend, grid=(cols, rows), dpi=args.dpi,
                       workers=args.workers, timeout=args.timeout, out_json=args.out,
                       out_dir=args.out_dir)
    st = r.get('统计') or {}
    print('--- 统计 ---')
    print(f"  有效块 {st.get('有效块')}/{st.get('总块数')} | 文字 {st.get('文字原始观测')}→"
          f"{st.get('文字去重后')} (去重率 {st.get('去重率')}) | 构件 {st.get('构件原始观测')}→"
          f"{st.get('构件去重后')} | {st.get('耗时s')}s | {st.get('tokens')} tokens")
    print('  构件计数:', json.dumps(r.get('构件计数', {}), ensure_ascii=False))
    for t in r.get('文字', [])[:15]:
        print(f"    [{t['观测次数']}x] {t['文本'][:40]:42} {t['图纸坐标']}")
    return r


if __name__ == '__main__':
    main()
