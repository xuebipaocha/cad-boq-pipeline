"""CAD-BOQ Pipeline v5.14 — 调度瘦身版

v5.14 工作流 P1 变更:
- 多视图合并逻辑移入 merge_views.py 的 merge_drawing_files()(CLI: python merge_views.py 图1 图2)
- 新增 --steps 组合执行(如 --steps 识图,算量), --step 单步向后兼容
- QC 从"产物存在性"升级为"产物完整性断言"(断言函数在 qc_chk 内执行)
- 移除内联多视图拼接代码(约 30 行)
"""
import argparse
import importlib.util
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE_DIR)
STEPS_DIR = BASE_DIR
DEFAULT_OUTPUT_DIR = os.path.join(SKILL_DIR, 'output')
REQUIRED = {
    'ezdxf': 'ezdxf',
    'openpyxl': 'openpyxl',
    'matplotlib': 'matplotlib',
}
sys.path.insert(0, STEPS_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'pipeline'))

# 步骤依赖: 算量依赖识图, 编清单依赖算量, 组价依赖清单
STEP_DEP = {'审图': ['识图'], '算量': ['识图'], '编清单': ['识图', '算量'], '组价': ['识图', '算量', '编清单']}
ALL_STEPS = ('识图', '审图', '算量', '编清单', '组价')


def check_env():
    """检查运行依赖，不在业务运行时静默安装。"""
    missing = []
    for module, package in REQUIRED.items():
        try:
            __import__(module)
            print(f'  ✅ {package}')
        except ImportError:
            missing.append(package)
            print(f'  ❌ {package}')
    if missing:
        print('\n缺少依赖，请先运行：')
        print('  python -m pip install -r requirements.txt')
        return False
    return True


def load_mod(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def run_step(sn, mn, inf, outf, output_dir):
    p = os.path.join(STEPS_DIR, f'{mn}.py')
    if not os.path.exists(p):
        print(f'  ❌ 未找到 {mn}.py')
        return None
    m = load_mod(sn, p)
    inp = inf if (os.path.isabs(inf) or sn == '1') else os.path.join(output_dir, inf)
    if sn != '1' and not os.path.exists(inp):
        print(f'  ⏭ 跳过：缺{inp}')
        return None
    r = m.run(inp, output_dir)
    print(f'  ✅ -> {outf}')
    return r


QP, QW = 0, 0


def qc(l, c, m):
    global QP, QW
    if c:
        QP += 1
        print(f'  ✅ QC-{l}: {m}')
    else:
        QW += 1
        print(f'  ⚠ QC-{l}: {m}')


def qc_chk(l, fp, fn):
    """v5.14: QC 断言化 — 产物存在 + 完整性断言(断言失败计警告)。"""
    if not os.path.exists(fp):
        print(f'  ⏭ QC-{l}: 无数据')
        return
    try:
        with open(fp, encoding='utf-8') as f:
            d = json.load(f)
        fn(d)
    except Exception as e:
        print(f'  ⚠ QC-{l}: 检查失败({e})')


def parse_args(argv):
    parser = argparse.ArgumentParser(description='CAD-BOQ 全流程：识图、审图、算量、编清单、组价。')
    parser.add_argument('drawings', nargs='*', help='DXF/DWG 图纸文件；审图可传多张图纸。')
    parser.add_argument('--step', default='全流程',
                        choices=['识图', '审图', '算量', '编清单', '组价', '全流程'],
                        help='执行单步(向后兼容)。')
    parser.add_argument('--steps', default=None,
                        help='组合步骤，逗号分隔，如 识图,算量 或 识图,审图,算量,编清单,组价。')
    parser.add_argument('--specialty', default=None, help='指定专业，例如：市政工程、安装工程、园林绿化工程。')
    parser.add_argument('--render', action='store_true',
                        help='v5.15 V-1: 识图后渲染 DXF→PNG(整图+分层图)并写入识图结果。')
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR, help='输出目录，默认写入 skill/output。')
    parser.add_argument('--check-env', action='store_true', help='只检查依赖环境，不执行流水线。')
    return parser.parse_args(argv)


def resolve_steps(step, steps_arg):
    """v5.14: 解析执行步骤序列(含依赖补全)。--steps 优先, 否则 --step。"""
    if steps_arg:
        seq = [s.strip() for s in steps_arg.split(',') if s.strip()]
        bad = [s for s in seq if s not in ALL_STEPS]
        if bad:
            print(f'未知步骤: {bad} (可选: {",".join(ALL_STEPS)})')
            return []
        seq = list(dict.fromkeys(seq))  # 去重保序
        # 依赖补全: 后面的步骤所需前置自动加入
        for s in list(seq):
            for dep in STEP_DEP.get(s, []):
                if dep not in seq:
                    seq.insert(seq.index(s), dep)
        return seq
    if step == '全流程':
        return list(ALL_STEPS)
    return [step] if step in ALL_STEPS else []


def main(argv=None):
    global QP, QW
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.check_env:
        ok = check_env()
        raise SystemExit(0 if ok else 1)
    if not check_env():
        raise SystemExit(1)

    drawings = args.drawings
    specialty = args.specialty
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    steps = resolve_steps(args.step, args.steps)
    if not steps:
        print('请提供有效的 --step 或 --steps')
        return
    print(f'Pipeline | 步骤: {" → ".join(steps)}' + (f' | {specialty}' if specialty else ''))

    drawing = drawings[0] if drawings else ''
    if not drawing:
        print('请提供图纸文件')
        return

    # ── Step 1: 识图(多视图走 merge_views 合并) ──
    if '识图' in steps:
        if len(drawings) > 1:
            # v5.14: 多视图合并移入 merge_views.merge_drawing_files(CLI 独立可测)
            try:
                from merge_views import merge_drawing_files
                merge_drawing_files(drawings, output_dir, specialty)
            except Exception as e:
                print(f'  多视图合并失败: {e}')
                return
        else:
            run_step('1', 'step1_recognize', drawing, '识图结果.json', output_dir)

        # 专业覆盖
        if specialty:
            rp = os.path.join(output_dir, '识图结果.json')
            if os.path.exists(rp):
                with open(rp, encoding='utf-8') as f:
                    d = json.load(f)
                d['专业类型'] = specialty
                with open(rp, 'w', encoding='utf-8') as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)

        # ── v5.15 V-1: --render 渲染开关(独立出图, 与视觉路径内的渲染互不干扰) ──
        if args.render:
            try:
                from render_dxf import render_dxf
                meta = render_dxf(drawing if len(drawings) <= 1 else drawings[0],
                                  os.path.join(output_dir, 'renders'), per_layer=True)
                print(f'  ✅ 渲染: {meta["files"]["full"]}')
                # 渲染路径写入识图结果(供下游/用户查看)
                rp = os.path.join(output_dir, '识图结果.json')
                if os.path.exists(rp):
                    with open(rp, encoding='utf-8') as f:
                        d = json.load(f)
                    d['渲染'] = {'整图': meta['files']['full'], '图层数': meta.get('layer_count', 0)}
                    with open(rp, 'w', encoding='utf-8') as f:
                        json.dump(d, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f'  ⚠ 渲染失败(跳过): {e}')

    # ── QC-A: 识图完整性断言 ──
    if '识图' in steps and any(s in steps for s in ('审图', '算量', '编清单', '组价')):
        qc_chk('A-识图', os.path.join(output_dir, '识图结果.json'),
               lambda d: (qc('A', len(d.get('面积区域', [])) > 0, f'面积{len(d.get("面积区域", []))}个'),
                          qc('A', len(d.get('构造层', [])) > 0, f'构造层{len(d.get("构造层", []))}个')))

    # ── Step 2: 审图 ──
    if '审图' in steps:
        run_step('2', 'step2_review', '识图结果.json', '图纸问题清单.xlsx', output_dir)

    # ── Step 3: 算量 ──
    if '算量' in steps:
        run_step('3', 'step3_calculate', '识图结果.json', '算量结果.json', output_dir)
        qc_chk('B-算量', os.path.join(output_dir, '算量结果.json'),
               lambda d: [qc('B', i.get('工程量', 0) > 0, f'{i.get("分项名称", "")}: 量={i.get("工程量", 0)}')
                          for i in d])

    # ── Step 4: 编清单 ──
    if '编清单' in steps:
        run_step('4', 'step4_compile_boq', '算量结果.json', '清单结果.json', output_dir)
        qc_chk('C-清单', os.path.join(output_dir, '清单结果.json'),
               lambda d: [qc('C', '待匹配' not in i.get('code', ''), f'{i.get("name", "")}: 编码匹配')
                          for i in d])

    # ── Step 5: 组价 ──
    if '组价' in steps:
        run_step('5', 'step5_price', '清单结果.json', '已组价清单.xlsx', output_dir)
        qc('D', os.path.exists(os.path.join(output_dir, '已组价清单.xlsx')), '已组价清单')
        for f in ['图纸问题清单.xlsx', '工程量计算书.xlsx', '分部分项工程量清单计价表.xlsx', '已组价清单.xlsx']:
            qc('E', os.path.exists(os.path.join(output_dir, f)), f'{f}')

    # ── v5.14 工作流 P2: 质量聚合报告(三环节质量合一) ──
    if {'审图', '算量', '编清单'} & set(steps):
        try:
            from quality_report import run as run_qreport
            run_qreport(output_dir)
        except Exception as e:
            print(f'  ⚠ 质量聚合报告失败: {e}')

    print(f'\nQC: {QP}通过, {QW}警告')
    print(f'输出: {output_dir}')


if __name__ == '__main__':
    main()
