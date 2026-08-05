# -*- coding: utf-8 -*-
"""依赖健康自检 + 自动修复 — v5.9 V3-1

问题教训(dxf2svg 事件): 依赖失效被 try/except 吞掉, 功能"看起来存在"实际
从未跑通, 静默坏了一年。

设计:
1. check_env(): 逐依赖探测 → 返回 {依赖: {ok, version, 问题, 修复建议}}
2. auto_repair(): 失效依赖自动尝试修复(按修复脚本) → 报告结果
3. 关键能力(几何验证)在自检中直接跑通测试, 不是"能导入"而是"能出结果"

调用时机: 每次识图前(step1 run 开头) + --check-env 手动。
"""
import importlib
import json
import shutil
import subprocess
import sys
import os

# 关键依赖: 名称 / 导入路径 / 可用性测试(返回 bool) / 修复命令
DEPENDENCIES = [
    {
        'name': 'ezdxf',
        'import': 'ezdxf',
        'test': lambda m: hasattr(m, 'readfile'),
        'repair': [sys.executable, '-m', 'pip', 'install', '--upgrade', 'ezdxf'],
        'desc': '核心 CAD 解析库',
    },
    {
        'name': '几何验证(闭合/填充/范围)',
        'import': 'ezdxf',
        'test': lambda m: _geo_validate_works(),
        'repair': None,  # 逻辑自研, 无外部依赖; 测试失败说明代码回归
        'desc': '独立几何交叉验证(自研, 替代 dxf2svg)',
    },
    {
        'name': 'ODA File Converter',
        'import': None,
        'test': lambda m: _oda_works(),
        'repair': ['_ensure_oda_auto'],  # 特殊: 走 analyze_cad._ensure_oda
        'desc': 'DWG→DXF 转换',
    },
    {
        'name': 'matplotlib',
        'import': 'matplotlib',
        'test': lambda m: _matplotlib_works(),
        'repair': [sys.executable, '-m', 'pip', 'install', 'matplotlib'],
        'desc': '图纸渲染(视觉理解前置)',
    },
    {
        'name': 'openpyxl',
        'import': 'openpyxl',
        'test': lambda m: hasattr(m, 'Workbook'),
        'repair': [sys.executable, '-m', 'pip', 'install', 'openpyxl'],
        'desc': 'Excel 输出',
    },
]


def _geo_validate_works():
    """几何验证可用性: 真实跑一次, 不只是导入"""
    try:
        import ezdxf
        from units import shoelace_area, to_m2
        # 最小测试: 构造一个矩形闭合多段线 → 算面积
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (1000, 0), (1000, 1000), (0, 1000)], close=True)
        for e in msp.query('LWPOLYLINE'):
            if not e.closed:
                return False
            pts = list(e.get_points('xy'))
            if len(pts) < 3:
                return False
            a = to_m2(shoelace_area(pts), 4)
            return abs(a - 1.0) < 0.01  # 1m×1m = 1m²
        return False
    except Exception:
        return False


def _matplotlib_works():
    """matplotlib 可用性: pyplot 是延迟导入, 显式验证"""
    try:
        import matplotlib.pyplot as plt
        return hasattr(plt, 'figure')
    except Exception:
        return False


def _oda_works():
    """ODA 可用性: 可执行文件存在(预期路径或已知解包路径)"""
    try:
        import sys as _s
        base = os.path.dirname(os.path.abspath(__file__))
        _s.path.insert(0, base)
        import importlib.util
        spec = importlib.util.spec_from_file_location('analyze_cad', os.path.join(base, 'analyze_cad.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # 预期路径 or 历史解包路径(msiexec //a 解包)
        candidates = [
            str(mod.ODA_EXE),
            r'C:/Users/32865/AppData/Local/Temp/ODA_extract/ODAFileConverter.exe',
        ]
        for p in candidates:
            if os.path.exists(p):
                return True
        # 都不在 → 触发自动下载/复用
        return bool(mod._ensure_oda())
    except Exception:
        return False


def check_env(verbose=True):
    """逐依赖探测, 返回状态字典"""
    status = {}
    for dep in DEPENDENCIES:
        name = dep['name']
        try:
            if dep['import']:
                mod = importlib.import_module(dep['import'])
                ok = dep['test'](mod)
            else:
                ok = dep['test'](None)
            status[name] = {
                'ok': ok,
                'desc': dep['desc'],
                'version': getattr(mod, '__version__', '?') if dep['import'] and ok else '',
            }
        except Exception as e:
            status[name] = {'ok': False, 'desc': dep['desc'], 'error': str(e)[:80]}
        if verbose:
            mark = '✓' if status[name]['ok'] else '✗'
            ver = status[name].get('version', '')
            print(f'  {mark} {name} {ver}')
    return status


def auto_repair(status, verbose=True):
    """失效依赖自动修复。返回修复结果 {依赖: 修复后是否ok}"""
    results = {}
    for dep in DEPENDENCIES:
        name = dep['name']
        if status.get(name, {}).get('ok'):
            results[name] = True
            continue
        repair = dep.get('repair')
        if not repair:
            results[name] = False
            if verbose:
                print(f'  ⚠ {name} 失效且无自动修复(代码回归, 需人工)')
            continue
        if verbose:
            print(f'  🔧 尝试修复 {name}...')
        try:
            if repair[0] == '_ensure_oda_auto':
                import importlib.util
                spec = importlib.util.spec_from_file_location('analyze_cad', os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), 'analyze_cad.py'))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                ok = bool(mod._ensure_oda())
            else:
                subprocess.run(repair, capture_output=True, text=True, timeout=300)
                # 修复后重测
                if dep['import']:
                    mod = importlib.import_module(dep['import'])
                    ok = dep['test'](mod)
                else:
                    ok = dep['test'](None)
            results[name] = ok
            if verbose:
                print(f'  {"✓" if ok else "✗"} {name} 修复{"成功" if ok else "失败"}')
        except Exception as e:
            results[name] = False
            if verbose:
                print(f'  ✗ {name} 修复异常: {e}')
    return results


def ensure_healthy(verbose=True):
    """识图前调用: 自检 → 自动修复失效项 → 返回是否全部健康"""
    status = check_env(verbose=False)
    bad = [k for k, v in status.items() if not v['ok']]
    if not bad:
        if verbose:
            print('  依赖健康: 全部正常')
        return True, status
    if verbose:
        print(f'  依赖健康: {len(bad)} 项失效 → 自动修复')
    results = auto_repair(status, verbose)
    all_ok = all(results.values())
    return all_ok, results


if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    print('=== 依赖健康自检 ===')
    st = check_env()
    bad = [k for k, v in st.items() if not v['ok']]
    if bad:
        print(f'\n{len(bad)} 项失效, 尝试自动修复...')
        auto_repair(st)
    else:
        print('\n全部健康')
