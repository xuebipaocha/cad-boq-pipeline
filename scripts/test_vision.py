# -*- coding: utf-8 -*-
"""v6.10.6 视觉路径冒烟测试（防"改坏了还全绿"）

动机（真实教训，2026-10-05）:
  全量回归（benchmark_accuracy / regression_test / test_core / regression_real /
  test_entities）**全部在 VISION_OFF=1 下运行** —— 视觉路径的代码一行都不执行。
  本轮一个 `try:` 漏写 `except` 使 render_dxf.py 抛 SyntaxError, 视觉链路整体静默
  失效（识图 14s 就"跑完"、文字/房间全为 0）, 而**回归依然全绿**。本文件专补此缺口。

覆盖:
  ① 视觉相关模块导入（拦 SyntaxError / ImportError）—— 当日那类错误的直接拦网
  ② 跨块碎片拼接 _stitch_fragments（完整词不被破坏 / 碎片可拼回 / 空输入安全）
  ③ 渲染输出（真实渲染 → PNG 非空; 像素上限钳制不越界）
  ④ 视觉回填链路（mock query_vision → run_vision_for_drawing → pid 回填 + 房间补位）

API 调用以 mock 替代: 无需 Key、无网络、无费用, 秒级完成。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✓ ' if cond else '  ✗ ') + name + ('' if cond else '   ' + str(extra)))


DXF = os.path.join(ROOT, 'benchmarks', 'cases', '实体扩展', 'drawings', '实体扩展.dxf')

# ── ① 导入层（拦 SyntaxError/ImportError）──
print('1) 视觉链路模块导入')
MODS = {}
try:
    import render_dxf
    import vision_fusion
    import vision_tiles
    import vision_query
    MODS = {'render_dxf': render_dxf, 'vision_fusion': vision_fusion,
            'vision_tiles': vision_tiles, 'vision_query': vision_query}
    check('import render_dxf/vision_fusion/vision_tiles/vision_query', True)
except Exception as e:
    check('import 视觉模块', False, repr(e))

if MODS:
    # ── ② 跨块碎片拼接 ──
    print('2) 跨块碎片拼接')
    vt = MODS['vision_tiles']
    try:
        r = vt._stitch_fragments(
            [{'文本': '卫', 'x': 0, 'y': 0}, {'文本': '生', 'x': 10, 'y': 0},
             {'文本': '间', 'x': 20, 'y': 0}, {'文本': '办公室', 'x': 200, 'y': 0}], 1000)
        words = [t.get('文本') for t in r if isinstance(t, dict)]
        check('碎片拼回完整词（卫+生+间→卫生间）', '卫生间' in words, words)
        check('完整词不被破坏', '办公室' in words, words)
        check('空输入安全', vt._stitch_fragments([], 0) == [])
    except Exception as e:
        check('拼接函数可用', False, repr(e))

    # ── ③ 渲染输出（含像素上限钳制）──
    print('3) 渲染输出')
    try:
        rdxf = MODS['render_dxf']
        out = tempfile.mkdtemp(prefix='visrender')
        meta = rdxf.render_dxf(DXF, out, per_layer=False)
        pngs = [f for f in os.listdir(out) if f.endswith('.png')]
        check('渲染产出 PNG', bool(pngs), os.listdir(out)[:3])
        if pngs:
            p = os.path.join(out, pngs[0])
            check('PNG 非空(>1KB)', os.path.getsize(p) > 1024, os.path.getsize(p))
        # 像素上限: 300dpi 请求应被钳到 <=2400px 长边对应 dpi
        os.environ['VISION_RENDER_DPI'] = '300'
        out2 = tempfile.mkdtemp(prefix='visrender2')
        rdxf.render_dxf(DXF, out2, per_layer=False)
        png2 = [f for f in os.listdir(out2) if f.endswith('.png')]
        if png2:
            try:
                from PIL import Image  # 可能未安装
                im = Image.open(os.path.join(out2, png2[0]))
                w, h = im.size
                check('像素上限钳制生效(长边<=2600px)', max(w, h) <= 2600, f'{w}x{h}')
            except ImportError:
                check('像素上限钳制(跳过: 无 PIL)', True)
        del os.environ['VISION_RENDER_DPI']
    except Exception as e:
        check('渲染可用', False, repr(e))

    # ── ④ 视觉回填链路（mock API）──
    print('4) 视觉回填链路（mock query_vision）')
    try:
        vq = MODS['vision_query']
        vf = MODS['vision_fusion']
        FAKE = {'工程类型': '安装工程', '工程类型置信度': 0.9, '枚举外判断': '',
                '构件计数': {'窗户': 0, '门': 0, '柱': 0, '设备符号': 0, '苗木块': 0},
                '可见文字': ['W1 生活给水管道', 'DN100 管道公称直径'],
                '房间部位': ['卫生间', '办公室'],
                '区域': [{'名称': '主区域', '描述': ''}], '备注': '',
                '_meta': {'模型': 'mock', '来源': '视觉识别'}}
        orig = vq.query_vision
        vq.query_vision = lambda *a, **k: dict(FAKE)
        try:
            # 端到端: 走 step1 完整识图（几何 + 视觉(mock) + 房间补位 都在链路内;
            # 补位逻辑位于 step1 层, 单测 vision_fusion 覆盖不到 —— 故此处跑整链）
            import step1_recognize as s1
            out3 = tempfile.mkdtemp(prefix='vischain')
            pid = s1.run(DXF, out3)
            vr = pid.get('视觉识别') or {}
            check('视觉结果回填 pid[视觉识别]', bool(vr), list(pid.keys())[:8])
            check('语义透传: 房间部位', vr.get('房间部位') == ['卫生间', '办公室'], vr.get('房间部位'))
            check('语义透传: 可见文字', len(vr.get('可见文字') or []) == 2, len(vr.get('可见文字') or []))
            rooms = [r.get('房间名') for r in (pid.get('房间') or [])]
            check('房间视觉补位（几何为空时）', '卫生间' in rooms and '办公室' in rooms, rooms)
        finally:
            vq.query_vision = orig
    except Exception as e:
        check('视觉回填链路可用', False, repr(e))

print()
print(f'视觉冒烟测试: {len(PASS)} 通过, {len(FAIL)} 失败, 共 {len(PASS) + len(FAIL)} 项')
if FAIL:
    print('失败项:', FAIL)
sys.exit(1 if FAIL else 0)
