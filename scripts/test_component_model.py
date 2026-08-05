# -*- coding: utf-8 -*-
"""构件模型单元测试 — v5.0 P1

模式: 直接构造 pid dict 调 build_component_model 纯函数, 不依赖真实 DXF。
覆盖: 平法柱/梁生成(编号/截面/跨数/钢筋)、板厚/房间面积、缺失证据兜底、
非房建专业空骨架。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from component_model import build_component_model

PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f'  ✅ {name}')
    except AssertionError as e:
        FAIL += 1
        print(f'  ❌ {name}: {e}')
    except Exception as e:
        FAIL += 1
        print(f'  ❌ {name}: 异常 {type(e).__name__}: {e}')


def _make_pid(extra_texts=None, specialty='房屋建筑与装饰工程'):
    """构造一个带平法标注的房建识图结果"""
    texts = [
        'KL1(3) 300×600 Φ8@100/200(2) 2Φ25;4Φ22 G4Φ12',
        'KZ1 500×500 12Φ22 Φ8@100',
        '板厚120 底筋Φ10@200双层双向',
        '层高3.0m',
        '墙厚200mm',
    ]
    if extra_texts:
        texts.extend(extra_texts)
    return {
        '专业类型': specialty,
        '图纸元数据': {'单位': 'mm', 'insunits': 4},
        '面积区域': [{'名称': '主区域', '面积_m2': 360.0, '周长_m': 78.0, '面积来源': '闭合多段线'}],
        '构造层': [{'名称': '楼地面', '厚度_mm': 100, '材料': '水泥砂浆', '厚度来源': '做法表提取'}],
        '施工说明': texts,
        '标高参数': {'层高_m': 3.0, '挖深_m': 1.5},
        '建筑信息': {'构件分类': {'框架柱': 6, '框架梁': 6}, '构件尺寸样本': {}},
        '标注关联': [], '构件尺寸推导': {}, '图块明细': {},
    }


def test_columns_from_flat():
    pid = _make_pid()
    cm = build_component_model(pid)
    cols = cm['柱']
    assert len(cols) == 1, f'期望 1 条柱构件(同编号归并), 实际 {len(cols)}'
    c = cols[0]
    assert c['编号'] == 'KZ1', f'编号 {c["编号"]}'
    assert c['截面宽_mm'] == 500 and c['截面高_mm'] == 500, f'截面 {c["截面宽_mm"]}×{c["截面高_mm"]}'
    assert c['钢筋']['纵筋'] == [{'根数': 12, '直径_mm': 22}], f'纵筋 {c["钢筋"]["纵筋"]}'
    assert c['钢筋']['箍筋']['间距_mm'] == 100, f'箍筋 {c["钢筋"]["箍筋"]}'
    assert c['高度_m'] == 3.0, f'高度 {c["高度_m"]}'
    assert c['截面来源'] == '平法标注'
    assert 0 < c['置信度'] <= 1.0


def test_beams_from_flat():
    pid = _make_pid()
    cm = build_component_model(pid)
    beams = cm['梁']
    assert len(beams) == 1, f'期望 1 条梁构件, 实际 {len(beams)}'
    b = beams[0]
    assert b['编号'] == 'KL1', f'编号 {b["编号"]}'
    assert b['跨数'] == 3, f'跨数 {b["跨数"]}'
    assert b['截面宽_mm'] == 300 and b['截面高_mm'] == 600, f'截面 {b["截面宽_mm"]}×{b["截面高_mm"]}'
    assert b['钢筋']['上部筋'] == [{'根数': 2, '直径_mm': 25}], f'上部筋 {b["钢筋"]["上部筋"]}'
    assert b['钢筋']['箍筋']['非加密间距_mm'] == 200
    assert b['长度来源'] in ('默认', '施工说明', '标注', '几何', '标注推导')


def test_slab_and_room():
    pid = _make_pid()
    cm = build_component_model(pid)
    slabs = cm['板']
    assert len(slabs) == 1
    assert slabs[0]['厚度_mm'] == 120, f'板厚 {slabs[0]["厚度_mm"]}'
    assert slabs[0]['厚度来源'] == '平法标注'
    assert abs(slabs[0]['面积_m2'] - 360.0) < 1e-6
    rooms = cm['房间']
    assert len(rooms) == 1
    assert abs(rooms[0]['面积_m2'] - 360.0) < 1e-6
    walls = cm['墙']
    assert walls[0]['厚度_mm'] == 200
    assert abs(walls[0]['长度_m'] - 78.0) < 1e-6


def test_defaults_when_no_evidence():
    """无平法/无样本/无标注 → 默认截面兜底, 不抛"""
    pid = {
        '专业类型': '房屋建筑与装饰工程',
        '图纸元数据': {'单位': 'mm', 'insunits': 4},
        '面积区域': [{'名称': '主区域', '面积_m2': 360.0, '周长_m': 78.0, '面积来源': '闭合多段线'}],
        '构造层': [], '施工说明': [], '标高参数': {},
        '建筑信息': {'构件分类': {}, '构件尺寸样本': {}},
        '标注关联': [], '构件尺寸推导': {}, '图块明细': {},
    }
    cm = build_component_model(pid)
    assert cm['柱'] == [], f'无证据不应凭空出柱, 实际 {cm["柱"]}'
    assert cm['梁'] == [], f'无证据不应凭空出梁, 实际 {cm["梁"]}'
    assert cm['板'][0]['厚度_mm'] == 120
    assert cm['墙'][0]['厚度_mm'] == 200
    assert cm['房间'][0]['面积_m2'] == 360.0


def test_msp_none_ok():
    """msp=None 降级: 纯文本证据仍可用"""
    pid = _make_pid()
    cm = build_component_model(pid, msp=None)
    assert len(cm['柱']) == 1 and cm['柱'][0]['编号'] == 'KZ1'


def test_merge_same_name():
    """同编号同截面归并: 两条 KZ1 500×500 → 数量 2"""
    pid = _make_pid(extra_texts=['KZ1 500×500 12Φ22 Φ8@100'])
    cm = build_component_model(pid)
    cols = cm['柱']
    assert len(cols) == 1, f'同编号应归并为 1 条, 实际 {len(cols)}'
    assert cols[0]['数量'] == 2, f'数量应累加为 2, 实际 {cols[0]["数量"]}'


def test_non_building_specialty():
    """v5.12: 市政/园林不再空骨架 — 返回各自构件键(空列表), 路基从面积区域构建"""
    pid = _make_pid(specialty='市政工程')
    cm = build_component_model(pid)
    # 市政构件键存在; 无面积证据的键为空, 有面积的键(路基)正常构建
    for k in ('道路面层', '道路基层', '路基', '路缘石', '管网'):
        assert k in cm, f'市政构件缺少键 {k}: {list(cm.keys())}'
    assert cm['道路面层'] == [] and cm['路缘石'] == [], f'无证据不应出构件: {cm["道路面层"]}'
    assert cm['路基'][0]['面积_m2'] == 360.0, f'路基应从面积区域构建: {cm["路基"]}'


def _make_mep_pid():
    return {
        '专业类型': '安装工程',
        '图纸元数据': {'单位': 'mm', 'insunits': 4},
        '图块明细': {
            '阀门': [{'name': '阀门', 'spec': 'DN=DN100', 'count': 4, 'x': 40000.0, 'y': 3000.0}],
            '配电箱柜': [{'name': '配电箱', 'spec': '', 'count': 1, 'x': 50000.0, 'y': 9000.0}],
            '灯具': [{'name': '双管荧光灯', 'spec': '', 'count': 6, 'x': 55000.0, 'y': 12000.0}],
        },
        '线性构件': [{'名称': '给水_DN100', '类型': '管道', '长度_m': 60.0, '管径': 'DN100'}],
        '施工说明': [],
    }


def test_mep_components():
    pid = _make_mep_pid()
    cm = build_component_model(pid)
    assert cm['阀门'][0]['数量'] == 4, f'阀门数量 {cm["阀门"][0]["数量"]}'
    assert cm['阀门'][0]['规格'] == 'DN=DN100'
    assert cm['阀门'][0]['位置'] == {'x_m': 40.0, 'y_m': 3.0}, f'位置 {cm["阀门"][0]["位置"]}'
    assert cm['灯具'][0]['数量'] == 6
    assert cm['配电箱柜'][0]['数量'] == 1
    assert cm['管道'][0]['长度_m'] == 60.0 and cm['管道'][0]['规格'] == 'DN100'
    assert '证据' in cm['阀门'][0] and cm['阀门'][0]['置信度'] > 0


def test_steel_components():
    pid = {
        '专业类型': '钢结构工程',
        '图纸元数据': {'单位': 'mm', 'insunits': 4},
        '钢结构': {'构件': [{'名称': 'H型钢柱', '截面类型': 'H', '截面参数': [500, 200, 10, 16], '长度_m': 12.0}]},
        '施工说明': [],
    }
    cm = build_component_model(pid)
    assert len(cm['钢构件']) == 1
    s = cm['钢构件'][0]
    assert s['规格'] == 'H500×200×10×16', f'规格 {s["规格"]}'
    assert s['长度_m'] == 12.0
    assert '证据' in s and s['置信度'] > 0


def test_civil_components():
    """v5.12: 市政构件 — 构造层→面层/基层, 面积→路基, 线性构件→管网"""
    pid = _make_pid(specialty='市政工程')
    pid['面积区域'] = [{'名称': '主区域', '面积_m2': 960.0, '周长_m': 184.0}]
    pid['构造层'] = [
        {'名称': '4cm细粒式沥青混凝土 AC-13', '厚度_mm': 40.0, '材料': '沥青混凝土'},
        {'名称': '18cm水泥稳定碎石基层', '厚度_mm': 180.0, '材料': '水泥稳定碎石'},
    ]
    pid['线性构件'] = [{'名称': '给水_DN200', '类型': '管道', '长度_m': 80.0, '管径': 'DN200', '系统': '给水'}]
    cm = build_component_model(pid)
    assert len(cm['道路面层']) == 1, f'面层 {cm["道路面层"]}'
    assert cm['道路面层'][0]['厚度_mm'] == 40.0
    assert len(cm['道路基层']) == 1
    assert cm['路基'][0]['面积_m2'] == 960.0
    assert cm['管网'][0]['长度_m'] == 80.0 and cm['管网'][0]['系统'] == '给水'


def test_garden_components():
    """v5.12: 园林构件 — 苗木计数/草坪面积/种植土深度"""
    pid = _make_pid(specialty='园林绿化工程')
    pid['面积区域'] = [{'名称': '主区域', '面积_m2': 1500.0, '周长_m': 160.0}]
    pid['构造层'] = [{'名称': '种植土回填 50cm厚', '厚度_mm': 500.0, '材料': '种植土'}]
    pid['园林信息'] = {'苗木': {'乔木': [{'名称': 'CAD识别', '数量': 6}], '灌木': [{'名称': 'CAD识别', '数量': 4}]},
                       '硬景': {'铺装_m2': 0, '路缘石_m': 0}}
    cm = build_component_model(pid)
    assert cm['乔木'][0]['数量'] == 6, f'乔木 {cm["乔木"]}'
    assert cm['灌木'][0]['数量'] == 4
    assert cm['草坪'][0]['面积_m2'] == 1500.0
    assert cm['种植土'][0]['深度_m'] == 0.5, f'种植土深度 {cm["种植土"]}'


def test_decoration_components():
    """v5.12: 精装构件 — 构造层/说明多组命中(自流平+木地板同层)"""
    pid = _make_pid(specialty='房屋建筑与装饰工程')
    pid['面积区域'] = [{'名称': '主区域', '面积_m2': 120.0, '周长_m': 46.0}]
    pid['构造层'] = [
        {'名称': '1.界面剂 2.自流平 3.实木复合地板', '厚度_mm': 12.0, '材料': '实木复合地板 12mm 柚木色'},
        {'名称': '1.防水 2.粘结层 3.防滑地砖', '厚度_mm': 8.0, '材料': '300×300 防滑地砖'},
    ]
    pid['施工说明'] = ['踢脚线: 实木踢脚 100mm高 全屋']
    cm = build_component_model(pid)
    mats = {f['材料名'] for f in cm['楼地面']}
    assert '木地板' in mats and '自流平找平' in mats and '地砖地面' in mats, f'楼地面材料 {mats}'
    assert any(d['材料名'] == '踢脚线' for d in cm['细部']), f'细部 {cm["细部"]}'


def test_slab_from_construction_layer():
    """无平法板厚时, 构造层钢筋混凝土楼板厚度优先"""
    pid = _make_pid(extra_texts=None)
    pid['施工说明'] = ['层高3.0m', '墙厚200mm']
    pid['构造层'] = [{'名称': '钢筋混凝土楼板', '厚度_mm': 130, '材料': '混凝土', '厚度来源': '做法表提取'}]
    cm = build_component_model(pid)
    assert cm['板'][0]['厚度_mm'] == 130, f'构造层板厚 130, 实际 {cm["板"][0]["厚度_mm"]}'
    assert cm['板'][0]['厚度来源'] == '构造层'


if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    print('构件模型单元测试:')
    test('平法柱生成(KZ1 500×500 钢筋/高度/置信度)', test_columns_from_flat)
    test('平法梁生成(KL1 跨数3 300×600 钢筋)', test_beams_from_flat)
    test('板/墙/房间(板厚120 房间360 墙长78)', test_slab_and_room)
    test('无证据默认值兜底', test_defaults_when_no_evidence)
    test('msp=None 降级', test_msp_none_ok)
    test('同编号归并数量累加', test_merge_same_name)
    test('非房建专业空骨架', test_non_building_specialty)
    test('安装构件(阀门/灯具/管道+位置)', test_mep_components)
    test('钢构构件(规格格式化)', test_steel_components)
    test('构造层板厚优先', test_slab_from_construction_layer)
    test('市政构件(面层/基层/路基/管网)', test_civil_components)
    test('园林构件(苗木/草坪/种植土)', test_garden_components)
    test('精装构件(楼地面/墙面/天棚/细部)', test_decoration_components)
    print(f'\n结果: {PASS}通过, {FAIL}失败, 共{PASS + FAIL}项')
    sys.exit(1 if FAIL else 0)
