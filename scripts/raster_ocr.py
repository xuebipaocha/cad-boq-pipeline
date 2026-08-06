# -*- coding: utf-8 -*-
"""栅格图(扫描件)OCR 兜底 — v5.15 视觉化 V-4

场景: 扫描件/图片版 PDF 转换的 DXF, 没有可解析的 CAD 实体(或实体极少),
图形语义全在嵌入的位图里。此时几何/文字解析失效, 需要 OCR 兜底。

两级策略(视觉路径内自动分流):
1. **视觉模型直接读图**(qwen3.7-flash): 栅格图即 PNG, 视觉可"看到"文字与图形
   —— 识图必经视觉路径(v5.15)已天然覆盖
2. **Windows OCR 兜底**(无视觉模型/离线): 用 Windows.Media.Ocr 引擎提取文字,
   走与 step1 相同的文字→专业识别/工程性质判定流程

用法:
  python3 raster_ocr.py 图纸.dxf            # 检测栅格化程度
  python3 raster_ocr.py 图纸.dxf --ocr      # Windows OCR 提取文字
"""
import os
import sys
import json

sys.stdout.reconfigure(encoding='utf-8')


def is_raster_drawing(dxf_path, entity_threshold=5):
    """检测图纸是否栅格化: 可解析实体极少(< threshold) 且含 IMAGE/位图。

    返回 (是否栅格图, 实体数, IMAGE 数)。
    """
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        ents = list(msp)
        img_n = sum(1 for e in ents if e.dxftype() == 'IMAGE')
        # 语义实体: 排除纯文字(文字本身可 OCR)
        semantic = [e for e in ents if e.dxftype() in
                    ('LWPOLYLINE', 'POLYLINE', 'LINE', 'CIRCLE', 'ARC', 'INSERT', 'HATCH', 'SOLID')]
        raster = (len(semantic) < entity_threshold and (img_n > 0 or len(ents) < entity_threshold))
        return raster, len(ents), img_n
    except Exception:
        return False, 0, 0


def windows_ocr_image(image_path):
    """Windows.Media.Ocr 引擎提取 PNG/JPG 文字 → 文本行列表。

    依赖: Python 3.14 需 winrt 兼容层; 失败返回 None(调用方回退视觉/跳过)。
    """
    try:
        import asyncio
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage import StorageFile
        from winrt.windows.storage.streams import RandomAccessStreamReference

        async def _run():
            f = await StorageFile.get_file_from_path_async(os.path.abspath(image_path))
            stream = RandomAccessStreamReference.create_from_file(f)
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            # 尝试中文语言, 失败用系统默认
            lang = None
            try:
                lang = Language('zh-Hans-CN')
                engine = OcrEngine.try_create_from_language(lang)
            except Exception:
                engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                return None
            result = await engine.recognize_async(bitmap)
            return [line.text for line in result.lines]

        return asyncio.run(_run())
    except Exception as e:
        print(f'  ⚠ Windows OCR 不可用: {e}')
        return None


def ocr_fallback(dxf_path, output_dir=None):
    """栅格图 OCR 兜底入口: 检测 → 渲染/提取位图 → OCR → 返回文字列表。

    输出与 step1 消费的 施工说明/文字 兼容(调用方自行汇入 pid)。
    """
    raster, ents, imgs = is_raster_drawing(dxf_path)
    if not raster:
        return None
    print(f'  栅格图检测: 实体{ents}个(IMAGE {imgs}), 判定为栅格化图纸, 走 OCR 兜底')

    # 1. 渲染(视觉路径/OCR 共用)
    texts = []
    try:
        from render_dxf import render_dxf
        render_dir = os.path.join(output_dir or os.path.dirname(dxf_path), 'renders')
        meta = render_dxf(dxf_path, render_dir, per_layer=False)
        png = meta['files']['full']
    except Exception as e:
        print(f'  ⚠ 渲染失败({e}), 尝试直接 OCR 嵌入位图')
        png = None

    # 2. 优先视觉模型(识图必经视觉路径已覆盖, 这里文字+结构化识别)
    vision_info = {}
    try:
        from vision_query import query_vision
        if png and os.path.exists(png):
            r = query_vision(png, enable=True, prompt=(
                '这是 CAD 图纸渲染图(栅格扫描件)。请完成: '
                '1) 提取图中所有可见文字标注、图名、说明文字逐条列出; '
                '2) 判断工程类型(房屋建筑与装饰/安装/市政/园林绿化/钢结构/其他); '
                '3) 判断主要构件或内容(如: 道路结构层/苗木/管道/墙体/设备等)。'
                '文字模糊请说明。只输出 JSON: {"文字":["..."],"工程类型":"...","主要构件":"..."}'))
            if r:
                if r.get('原始回复'):
                    texts.append(str(r['原始回复']))
                if r.get('工程类型'):
                    vision_info['工程类型'] = str(r['工程类型'])
                if r.get('主要构件'):
                    vision_info['主要构件'] = str(r['主要构件'])
                if r.get('文字'):
                    for t in r['文字']:
                        if isinstance(t, str) and t.strip():
                            texts.append(t)
    except Exception:
        pass

    # 3. Windows OCR 兜底(视觉不可用时)
    if not texts and png and os.path.exists(png):
        ocr_lines = windows_ocr_image(png)
        if ocr_lines:
            texts.extend(ocr_lines)
    out = {'文字': [t for t in texts if t], '工程类型': vision_info.get('工程类型', ''),
           '主要构件': vision_info.get('主要构件', '')}
    return out if (out['文字'] or out['工程类型']) else None


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='栅格图 OCR 兜底(V-4)')
    ap.add_argument('dxf', help='DXF 文件')
    ap.add_argument('--ocr', action='store_true', help='强制 Windows OCR(不走视觉)')
    args = ap.parse_args(argv)

    raster, ents, imgs = is_raster_drawing(args.dxf)
    print(f'栅格化检测: {raster} | 实体{ents}个 IMAGE{imgs}个')
    if args.ocr:
        png = None
        try:
            from render_dxf import render_dxf
            meta = render_dxf(args.dxf, os.path.join(os.path.dirname(args.dxf), 'renders'), per_layer=False)
            png = meta['files']['full']
        except Exception as e:
            print('渲染失败:', e)
        if png:
            lines = windows_ocr_image(png)
            print('OCR 结果:')
            for l in (lines or []):
                print(' ', l)
    else:
        r = ocr_fallback(args.dxf)
        print('OCR 兜底结果:', r)


if __name__ == '__main__':
    main()
