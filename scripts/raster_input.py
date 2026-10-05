# -*- coding: utf-8 -*-
"""光栅输入通道 — v6.10 让"非 CAD 图纸"也能进流程

现实场景: 甲方常给 PDF 施工图或图片扫描件, 此前流程只吃 DWG/DXF → 直接进不来。
本模块提供零几何依赖的输入通道:
  图片(PNG/JPG/BMP/TIF)  → 视觉识别 → pid 骨架(无几何证据)
  PDF                    → 逐页光栅化(需可选依赖) → 同上
诚实原则(PRINCIPLES 元原则三):
  - 光栅输入**没有 CAD 几何**, 所有工程量必须人工量取或请对方提供 DWG/DXF;
  - pid 显式标 '无几何证据': True + 图纸问题候选说明, 下游算量据此产出"待提取"而非编造数字;
  - PDF 光栅化依赖(pypdfium2/PyMuPDF/pdf2image)缺失时给出明确安装指引, 不静默失败。

用法:
  python3 raster_input.py 图纸.pdf  [--out-dir out] [--dpi 200]
  python3 raster_input.py 扫描件.png
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

RASTER_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp')
PDF_EXTS = ('.pdf',)

PDF_BACKENDS = (
    ('pypdfium2', 'pip install pypdfium2'),
    ('fitz', 'pip install PyMuPDF'),
    ('pdf2image', 'pip install pdf2image (另需 poppler)'),
)


def is_raster(path):
    """是否光栅图片输入。"""
    return str(path).lower().endswith(RASTER_EXTS)


def is_pdf(path):
    return str(path).lower().endswith(PDF_EXTS)


def is_raster_or_pdf(path):
    return is_raster(path) or is_pdf(path)


def pdf_available_backend():
    """返回可用 PDF 光栅化后端名, 无则 None(附安装指引由调用方打印)。"""
    for name, _ in PDF_BACKENDS:
        try:
            __import__(name)
            return name
        except Exception:
            continue
    return None


def pdf_to_images(pdf_path, out_dir, dpi=200, max_pages=20):
    """PDF → PNG 列表(逐页光栅化)。返回 (files, err)。"""
    os.makedirs(out_dir, exist_ok=True)
    backend = pdf_available_backend()
    if not backend:
        hint = '; '.join(f'{n}: {c}' for n, c in PDF_BACKENDS)
        return [], (f'PDF 光栅化依赖缺失 — 请任选安装: {hint}; '
                    f'或把 PDF 页面另存为 PNG 后直接作为图片输入(零依赖)')
    files = []
    try:
        if backend == 'pypdfium2':
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(pdf_path)
            n = min(len(pdf), max_pages)
            for i in range(n):
                page = pdf[i]
                img = page.render(scale=dpi / 72.0).to_pil()
                p = os.path.join(out_dir, f'{os.path.splitext(os.path.basename(pdf_path))[0]}_p{i + 1}.png')
                img.save(p)
                files.append(p)
        elif backend == 'fitz':
            import fitz
            doc = fitz.open(pdf_path)
            for i in range(min(len(doc), max_pages)):
                pix = doc[i].get_pixmap(dpi=dpi)
                p = os.path.join(out_dir, f'{os.path.splitext(os.path.basename(pdf_path))[0]}_p{i + 1}.png')
                pix.save(p)
                files.append(p)
        elif backend == 'pdf2image':
            from pdf2image import convert_from_path
            pages = convert_from_path(pdf_path, dpi=dpi, size=(max_pages, None))[:max_pages]
            for i, im in enumerate(pages):
                p = os.path.join(out_dir, f'{os.path.splitext(os.path.basename(pdf_path))[0]}_p{i + 1}.png')
                im.save(p, 'PNG')
                files.append(p)
    except Exception as e:
        return files, f'PDF 光栅化失败({backend}): {e}'
    if not files:
        return [], 'PDF 未产生任何页面图(文件损坏或为空?)'
    return files, None


def image_to_pid(image_path, backend=None, timeout=180):
    """图片 → pid 骨架(视觉识别工程类型/文字; 无 CAD 几何)。

    返回 (pid | None, err)。无可用视觉后端时返回 err(不编造专业类型)。
    """
    try:
        from vision_query import query_vision
    except Exception as e:
        return None, f'vision_query 不可用: {e}'
    r = query_vision(image_path, enable=True, backend=backend, timeout=timeout)
    if not r:
        return None, ('视觉识别不可用(检查 DEEPSEEK_API_KEY / QWEN_API_KEY 或网络) — '
                      '光栅输入必须依赖视觉, 未识别不编造专业类型')
    meta = r.get('_meta') or {}
    vis_type = r.get('工程类型') or ''
    conf = r.get('工程类型置信度')
    texts = r.get('可见文字') or []
    if not isinstance(texts, list):
        texts = [str(texts)]
    pid = {
        '专业类型': vis_type,
        '专业识别': {'置信度': conf if isinstance(conf, (int, float)) else 0.0,
                     '候选': [], '来源': '视觉识别(光栅输入)'},
        '工程性质': '',
        '图纸元数据': {'单位': 'unknown(光栅)', '实体总数': 0, 'insunits': None,
                       '光栅输入': os.path.basename(image_path)},
        '来源': '光栅输入(无CAD几何)',
        '无几何证据': True,
        '面积区域': [],
        '构造层': [],
        '线性构件': [],
        '施工说明': [],
        '表格': [],
        '门窗': [],
        '房间': [],
        '视觉识别': {
            '工程类型': vis_type,
            '工程类型置信度': conf,
            '构件计数': r.get('构件计数', {}),
            '模型': meta.get('模型'),
            '来源': '视觉识别',
        },
        '光栅文字': texts[:100],
        '图纸问题候选': [
            f'[光栅输入] 本图为图片/PDF(无 CAD 几何数据): 工程量无法自动量取, '
            f'需人工量取或请设计方提供 DWG/DXF; 视觉识别结果仅作工程类型/内容参考',
            f'[光栅输入] 视觉识别的工程类型: {vis_type or "未识别"}(置信度 {conf}) — 请人工确认',
        ],
    }
    if meta.get('推理链'):
        pid['视觉识别']['推理链'] = meta['推理链']
    return pid, None


def build_pid(path, out_dir=None, backend=None, dpi=200):
    """统一入口: PDF → 逐页图 → pid(主页面); 图片 → pid。返回 (pid | None, err, extra)。"""
    out_dir = out_dir or os.path.dirname(os.path.abspath(path))
    extra = {}
    if is_pdf(path):
        imgs, err = pdf_to_images(path, out_dir, dpi=dpi)
        extra['页图'] = imgs
        if err:
            return None, err, extra
        pid, err2 = image_to_pid(imgs[0], backend=backend)
        if err2:
            return None, err2, extra
        pid['图纸问题候选'].append(
            f'[光栅输入] PDF 共光栅化 {len(imgs)} 页, 本次仅识别第 1 页; '
            f'其余页需逐页处理(多页图纸建议按专业拆分)')
        return pid, None, extra
    if is_raster(path):
        pid, err = image_to_pid(path, backend=backend)
        return pid, err, extra
    return None, f'不支持的输入类型: {path}(本模块只处理图片与 PDF)', extra


def main(argv=None):
    ap = argparse.ArgumentParser(description='光栅输入通道(图片/PDF → 视觉识别 → pid 骨架)')
    ap.add_argument('file', help='图片(PNG/JPG/...) 或 PDF')
    ap.add_argument('--out-dir', default=None, help='输出目录(默认输入文件旁)')
    ap.add_argument('--dpi', type=int, default=200, help='PDF 光栅化 dpi')
    ap.add_argument('--backend', default=None, choices=('deepseek', 'qwen'), help='视觉后端')
    args = ap.parse_args(argv)

    pid, err, extra = build_pid(args.file, args.out_dir, args.backend, args.dpi)
    if err:
        print(f'✗ {err}')
        return 1
    out = os.path.join(args.out_dir or os.path.dirname(os.path.abspath(args.file)), '识图结果.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(pid, f, ensure_ascii=False, indent=2)
    print(f'✓ 光栅输入 pid 已生成: {out}')
    print(f"  工程类型: {pid.get('专业类型')} (置信度 {(pid.get('专业识别') or {}).get('置信度')})")
    print(f"  可见文字: {len(pid.get('光栅文字') or [])} 条 | 无几何证据: {pid.get('无几何证据')}")
    if extra.get('页图'):
        print(f"  页图: {len(extra['页图'])} 张")
    return 0


if __name__ == '__main__':
    sys.exit(main())
