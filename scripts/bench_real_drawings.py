# -*- coding: utf-8 -*-
"""真实图纸评分表 — v6.10.3（真实样本能力评估）

用途: 把"真实图纸能不能用"从主观判断变成可测数字。
对每份真实图纸跑完整流水线(识图→审图→算量→编清单), 汇总关键指标并输出评分表:
  识别层: 专业/置信度/工程性质/面积/构造层/门窗/房间
  扩展层: 多线墙/填充/引线/真表格/天正对象(v6.10 新增实体能力是否生效)
  算量层: 分项数 / 待提取数 / 估算数 / 范围外数
  清单层: 清单项数 / 国标匹配率 / 自补数 / 特征完整度
  质量层: 图纸问题数 / 未核实项
输出: Markdown 表(终端可见) + JSON 落盘(供跨轮对比, 防能力退化)

用法:
  python3 bench_real_drawings.py 图纸1.dxf [图纸2.dwg ...] [--out-json path] [--vision]
  python3 bench_real_drawings.py --dir ../benchmarks/cases/真实图纸
DWG 会先用 ODA(现版) 转 DXF 再评估。
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(BASE)


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _collect(out_dir):
    """从流水线输出目录汇总指标。"""
    pid = _read_json(os.path.join(out_dir, '识图结果.json')) or {}
    calc = _read_json(os.path.join(out_dir, '算量结果.json')) or []
    boq = _read_json(os.path.join(out_dir, '清单结果.json')) or []
    pend = _read_json(os.path.join(out_dir, '待提取清单.json')) or []
    qrep = _read_json(os.path.join(out_dir, '算量质量报告.json')) or {}
    if not isinstance(calc, list):
        calc = calc.get('分项', []) if isinstance(calc, dict) else []
    if not isinstance(boq, list):
        boq = boq.get('items', []) if isinstance(boq, dict) else []

    ext = pid.get('扩展实体') or {}
    ext_s = ext.get('summary') or {}
    est = [i for i in calc if '估算' in str(i.get('备注', '')) or i.get('数据来源') == '估算']
    gb = [i for i in boq if str(i.get('code', '')).isdigit() or '-' in str(i.get('code', ''))]
    sup = [i for i in boq if 'B' in str(i.get('code', '')) and '01B' in str(i.get('code', ''))]
    pros = pid.get('专业识别') or {}
    return {
        '专业类型': pid.get('专业类型', ''),
        '专业置信度': pros.get('置信度'),
        '工程性质': pid.get('工程性质', ''),
        '主区域面积_m2': ((pid.get('面积区域') or [{}])[0] or {}).get('面积_m2'),
        '构造层数': len(pid.get('构造层') or []),
        '门窗条数': len(pid.get('门窗') or []),
        '房间数': len(pid.get('房间') or []),
        '扩展实体': ext_s,
        '算量分项数': len(calc),
        '估算项数': len(est),
        '待提取项数': len(pend),
        '范围外项数': len([i for i in calc if i.get('范围外')]),
        '清单项数': len(boq),
        '国标匹配数': len(gb),
        '自补数': len(sup),
        '国标匹配率': round(len(gb) / len(boq), 3) if boq else None,
        '图纸问题数': len(pid.get('图纸问题候选') or []),
        '质量分': (qrep or {}).get('质量分') or (qrep or {}).get('总分'),
        '视觉细部文字数': ((pid.get('视觉细部') or {}).get('文字数')),
        '视觉验证状态': (pid.get('视觉验证') or {}).get('状态'),
    }


def evaluate_one(path, out_root, vision=False, timeout=900):
    """单图评估: (DWG→DXF) → pipeline → 指标。"""
    import analyze_cad as ac
    name = os.path.splitext(os.path.basename(path))[0]
    dxf = path
    conv_note = ''
    if path.lower().endswith('.dwg'):
        dst = os.path.join(out_root, '_dxf')
        os.makedirs(dst, exist_ok=True)
        dxf, err = ac.dwg_to_dxf(path, dst)
        if err:
            return {'图纸': name, '转换失败': err}
        conv_note = f'ODA({ac._local_oda_version()}) 转 DXF 成功'
    out_dir = os.path.join(out_root, name)
    os.makedirs(out_dir, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8='1')
    if not vision:
        env['VISION_OFF'] = '1'
    env.setdefault('VISION_TILED', 'auto' if vision else '0')
    cmd = [sys.executable, os.path.join(BASE, 'pipeline.py'), dxf,
           '--steps', '识图,审图,算量,编清单', '--keep-json', '--output-dir', out_dir]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           env=env, timeout=timeout, cwd=BASE)
        ok = r.returncode == 0
        tail = (r.stdout or '').strip().splitlines()[-3:]
    except subprocess.TimeoutExpired:
        return {'图纸': name, '流水线失败': f'超时(>{timeout}s)'}
    m = _collect(out_dir)
    m['图纸'] = name
    m['转换'] = conv_note
    m['流水线OK'] = ok
    if not ok:
        m['流水线失败'] = ' | '.join(tail)[:200]
    return m


def main(argv=None):
    ap = argparse.ArgumentParser(description='真实图纸评分表')
    ap.add_argument('drawings', nargs='*', help='DXF/DWG 路径')
    ap.add_argument('--dir', default=None, help='扫描目录(DXF/DWG)')
    ap.add_argument('--out-json', default=os.path.join(SKILL, 'benchmarks', 'real_drawings_report.json'))
    ap.add_argument('--vision', action='store_true', help='启用视觉(VISION_TILED=auto)')
    ap.add_argument('--timeout', type=int, default=900)
    args = ap.parse_args(argv)

    paths = list(args.drawings)
    if args.dir:
        # Windows 文件系统大小写不敏感 → 只用一个模式 + realpath 去重(否则同图评估两次)
        seen = set()
        for ext in ('*.dxf', '*.dwg'):
            for p in sorted(glob.glob(os.path.join(args.dir, ext))):
                key = os.path.realpath(p).lower()
                if key not in seen:
                    seen.add(key)
                    paths.append(p)
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        print('未找到图纸文件。用法: python3 bench_real_drawings.py 图纸.dxf 或 --dir 目录')
        return 1

    out_root = os.path.join(tempfile.gettempdir(), '_real_eval')
    os.makedirs(out_root, exist_ok=True)
    print(f'真实图纸评分: {len(paths)} 份 (视觉 {"开" if args.vision else "关"})')
    rows = []
    for p in paths:
        print(f'--- {os.path.basename(p)} ---')
        m = evaluate_one(p, out_root, vision=args.vision, timeout=args.timeout)
        rows.append(m)
        if m.get('转换失败') or m.get('流水线失败'):
            print(f"  ✗ {m.get('转换失败') or m.get('流水线失败')}")
            continue
        print(f"  专业={m['专业类型']}({m['专业置信度']}) 性质={m['工程性质']} "
              f"面积={m['主区域面积_m2']}m² 构造层={m['构造层数']} 门窗={m['门窗条数']}")
        print(f"  扩展实体={json.dumps(m['扩展实体'], ensure_ascii=False)}")
        print(f"  算量 {m['算量分项数']} 项(估算 {m['估算项数']}/待提取 {m['待提取项数']}/范围外 {m['范围外项数']}) "
              f"| 清单 {m['清单项数']} 项(国标 {m['国标匹配数']}/自补 {m['自补数']}, "
              f"匹配率 {m['国标匹配率']}) | 图纸问题 {m['图纸问题数']}")

    # 汇总
    ok_rows = [r for r in rows if not (r.get('转换失败') or r.get('流水线失败'))]
    summary = {
        '图纸数': len(rows), '成功数': len(ok_rows),
        '平均国标匹配率': round(sum(r['国标匹配率'] for r in ok_rows if r['国标匹配率'] is not None)
                          / max(len([r for r in ok_rows if r['国标匹配率'] is not None]), 1), 3),
        '平均待提取项': round(sum(r['待提取项数'] for r in ok_rows) / max(len(ok_rows), 1), 1),
        '平均估算项': round(sum(r['估算项数'] for r in ok_rows) / max(len(ok_rows), 1), 1),
        '扩展实体生效图数': sum(1 for r in ok_rows if any((r['扩展实体'] or {}).values())),
    }
    print('=== 汇总 ===')
    for k, v in summary.items():
        print(f'  {k}: {v}')
    with open(args.out_json, 'w', encoding='utf-8') as f:
        json.dump({'明细': rows, '汇总': summary}, f, ensure_ascii=False, indent=2)
    print(f'落盘: {args.out_json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
