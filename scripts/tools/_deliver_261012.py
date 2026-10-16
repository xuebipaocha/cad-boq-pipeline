# -*- coding: utf-8 -*-
"""P5 交付物生成：汇总清单 Excel + 交叉验证报告（markdown）。一次性工具。"""
import json
import os

BASE = os.path.join(os.environ['LOCALAPPDATA'], 'Temp', '261012_out')
SUMP = os.path.join(os.environ['LOCALAPPDATA'], 'Temp', '261012_汇总.json')
OUTD = os.path.abspath('../output/261012')
os.makedirs(OUTD, exist_ok=True)

d = json.load(open(SUMP, encoding='utf-8'))
rows, per_fig = d['rows'], d['per_fig']

# --- ① 汇总清单 Excel ---
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = '全专业算量汇总'
    hdr = ['图', '专业', '分项名称', '工程量', '单位']
    ws.append(hdr)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='4472C4')
        c.alignment = Alignment(horizontal='center')
    for r in rows:
        ws.append([r['图'], r['专业'], r['分项'], r['量'], r['单位']])
    for col, w in zip('ABCDE', (12, 22, 42, 14, 8)):
        ws.column_dimensions[col].width = w
    ws2 = wb.create_sheet('各图产出总览')
    ws2.append(['图', '专业', '工程性质', '建筑面积_m2', '算量项数'])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for k, v in per_fig.items():
        ws2.append([k, v['专业'], v['性质'], v['面积'], v['算量项']])
    xls = os.path.join(OUTD, '261012_全专业算量汇总.xlsx')
    wb.save(xls)
    print('清单已生成:', xls)
except Exception as e:
    print('Excel 生成失败:', str(e)[:100])

# --- ② 交叉验证报告 ---
vf = {}
p = os.path.join(os.path.abspath('../output/3d'), 'vision_feedback.json')
if os.path.exists(p):
    vf = json.load(open(p, encoding='utf-8'))

dup = d['dup']
ph = d['placeholders']
ma = d['missing_area']
conc = sum(float(r['量'] or 0) for r in rows if r['图'] == '混凝土结构' and '混凝土' in r['分项'])
steel = sum(float(r['量'] or 0) for r in rows if r['图'] == '混凝土结构' and '钢筋' == r['分项'])
stl = sum(float(r['量'] or 0) for r in rows if r['图'] == '钢结构' and '制作安装' in r['分项'])

L = []
L.append('# 261012 项目 · 全专业算量交叉验证报告\n')
L.append('> 生成方式：7 份 DWG → ODA 转 DXF → pipeline（识图/审图/算量/编清单）→ 三维建模 → 视觉模型审图反推。\n')
L.append('## 一、结论（先说结论）\n')
L.append('| 专业部分 | 状态 | 依据 |')
L.append('|---|---|---|')
L.append('| 建筑装饰（1-建筑） | ⚠ 可用但需核 | 算量 15 项；与混凝土结构图存在 11 组分项完全重复，需人工判是否重复计量 |')
L.append('| 安装（给排水/暖通/电气） | ⚠ 部分可用 | 24 项，但含 9 条「未标管径」占位分项（管径未提取 → 量不可用） |')
L.append('| **钢结构（2-1）** | ❌ **不可用** | 制作安装合计 **%.2f t**（厂房应数十~数百 t）；三维模型仅 12 个示意方块；视觉模型两轮均判「无参考价值」 |' % stl)
L.append('| **混凝土结构（2-2）** | ❌ **不可用** | 面积缺失；与建筑图分项完全重复；三维模型梁柱悬浮、独立基础未建 |')
L.append('| 总图（0-总图） | ❌ 无产出 | 算量 0 项 |')
L.append('\n## 二、交叉验证四类发现\n')
L.append('### ① 跨图重复分项 %d 组（重复计量风险）\n' % len(dup))
for k, v in sorted(dup.items(), key=lambda x: -len(x[1]))[:13]:
    L.append('- `%s` ← %s' % (k, '、'.join(sorted(set(v)))))
L.append('\n### ② 占位/未提取分项 %d 条（规格未读到 → 量不可用）\n' % len(ph))
for f, n in ph:
    L.append('- [%s] %s' % (f, n))
L.append('\n### ②-补 跨图重复的去重规则（按图归属）\n')
L.append('- 上表重复分项共 %d 组，**根因**：同一建筑外轮廓在平面/立面/剖面/屋顶多图区反复出现，' % len(dup))
L.append('  使各图独立算量时把同一实体重复计入。')
L.append('- **去重规则（按图归属，避免重复计量）**：')
L.append('  1. **结构性分项**（现浇混凝土/钢筋/模板）→ 只认 **结构图**（2-2 混凝土结构），建筑图内同项不计；')
L.append('  2. **装饰性分项**（涂料/抹灰/门窗/地面）→ 只认 **建筑图**（1-建筑）；')
L.append('  3. **安装分项**（管道/阀门/电缆）→ 按专业图归属，跨专业同项（如"阀门安装"出现在给排水/暖通/电气）'
         '按**各专业各自计量**（口径不同，不算重复）。')
L.append('- 落地方式：在清单生成时给每条分项打"图来源"标记，按上表规则取一次；本条已识别但**尚未接入 pipeline**。\n')
L.append('\n### ③ 量级核查\n')
L.append('- 混凝土结构：混凝土 %.2f m³ / 钢筋 %.2f t → **含钢量 %.0f kg/m³**（经验 100~180，偏高）'
         % (conc, steel, (steel * 1000 / conc) if conc else 0))
L.append('- 钢结构：制作安装 **%.2f t**（厂房常见数十~数百 t，**严重偏少**）' % stl)
L.append('\n### ④ 缺建筑面积 %d 图\n' % len(ma))
L.append('- ' + '、'.join(ma))
L.append('\n## 三、三维模型 + 视觉模型验证（新能力）\n')
L.append('| 模型 | 构件数 | 视觉模型结论（原文摘录） |')
L.append('|---|---|---|')
L.append('| 2-2 混凝土结构 | 138（柱47+梁89推断+板/墙示意） | 「梁柱网格悬浮在空中；独立基础完全缺失；板墙仅 1 个示意方块；**工程量必然严重偏小**」 |')
L.append('| 2-1 钢结构 | 12 | 「仅 12 方块，无柱网轴线与梁柱连接；**完全未建钢梁，梁/节点/檩条/围护系统核心工程量大量漏算，结果无参考价值**」 |')
L.append('\n模型文件：`output/3d/t22.html`（土建）、`output/3d/t21.html`（钢结构）——浏览器打开可拖拽旋转；')
L.append('截图 PNG 与视觉反馈 JSON 同目录。\n')
L.append('\n## 四、下一步（按收益排序）\n')
L.append('1. **钢结构构件提取**（收益最高）：现仅从规格文字识别 12 个构件；应改为**从几何线要素聚类**（图内 LINE 12954 / 拉杆 7402 / 螺栓 525），'
         '按 H 型钢/角钢/钢板分类统计长度与重量 → 钢结构量才能可用')
L.append('2. **建筑图与结构图分项去重**：11 组分项在两图重复计算，需按"图纸归属"归集')
L.append('3. **安装管径提取**：9 条「未标管径」占位 → 需从管径标注（DN/De/Φ）与图例提取')
L.append('4. **建筑面积**：7 图全部缺失 → 需按 GB/T 50353 分幅计算（该项目多图幅混排）')
L.append('5. **基础与筏板建模**：视觉指出"独立基础未建"，基础量需单独提取')
L.append('\n## 五、交付物\n')
L.append('- `261012_全专业算量汇总.xlsx`：88 条分项（图/专业/分项/量/单位）+ 各图产出总览')
L.append('- 本报告（交叉验证结论与待修项）')
L.append('- `output/3d/*.html` + PNG 截图 + `vision_feedback.json`（三维模型与视觉证据）')

rp = os.path.join(OUTD, '261012_交叉验证报告.md')
open(rp, 'w', encoding='utf-8').write('\n'.join(L))
print('报告已生成:', rp)
print('RESULTN 交付物目录:', OUTD)
print('   文件:', sorted(os.listdir(OUTD)))
