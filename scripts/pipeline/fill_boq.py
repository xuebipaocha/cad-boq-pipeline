"""
填充国标清单模板
修复: 1. 分类名从item中读取而非硬编码"道路工程" 2. 综合单价分析表支持多item 3. 处理合并单元格
"""
import sys, os, shutil
from openpyxl import load_workbook
from openpyxl.styles import Font, Border, Side, Alignment

def fill_boq_template(boq_items, template_path, output_path):
    """用清单数据填充国标模板"""
    shutil.copy2(template_path, output_path)
    
    if not os.path.exists(output_path):
        return False
    
    wb = load_workbook(output_path)
    dfont = Font(name='微软雅黑', size=10)
    bfont = Font(name='微软雅黑', bold=True, size=10)
    thin = Side(border_style="thin", color="000000")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    
    # Sheet 1: 清单计价表
    if '表E.2.1 分部分项工程项目清单计价表' in wb.sheetnames:
        ws = wb['表E.2.1 分部分项工程项目清单计价表']
        
        # 清旧数据（R5以下）
        for row in range(5, ws.max_row + 1):
            for col in range(1, 11):
                try:
                    ws.cell(row=row, column=col).value = None
                except:
                    pass  # 合并单元格跳过
        
        # 填数据（按分部归类）
        r = 5
        current_cat = None
        for item in boq_items:
            cat = item.get('section', item.get('category', '一般工程'))
            if current_cat != cat:
                if r > 5:
                    r += 1  # 分部间空行
                try:
                    ws.cell(row=r, column=3, value=cat).font = bfont
                except:
                    pass
                r += 1
                current_cat = cat
            
            for ci, v in [(1,item['seq']),(2,item.get('code','')),(3,item['name']),
                          (4,item.get('features','')),(6,item['unit']),(7,item['qty'])]:
                try:
                    ws.cell(row=r, column=ci, value=v).font = dfont
                except:
                    pass  # 合并单元格跳过
            r += 1
    
    # Sheet 2: 综合单价分析表（支持多item）
    if '表E.2.2-3 分部分项工程项目、技术措施清单综合单价分析表' in wb.sheetnames:
        ws2 = wb['表E.2.2-3 分部分项工程项目、技术措施清单综合单价分析表']
        
        # 清数据
        for row in range(1, ws2.max_row + 1):
            for col in range(1, 15):
                try:
                    ws2.cell(row=row, column=col).value = None
                except:
                    pass
        
        # 每个item填一个分析表块（约21行/块）
        r = 1
        page = 0
        for item in boq_items:
            page += 1
            # 标题
            try:
                ws2.cell(row=r, column=1, value="表E.2.2-3 分部分项工程项目、技术措施清单综合单价分析表").font = Font(name='微软雅黑', bold=True, size=14)
            except: pass
            r += 1
            # 工程名称行
            try:
                ws2.cell(row=r, column=1, value=f"工程名称：{item.get('project','')}").font = dfont
            except: pass
            r += 1
            # 项目信息
            try:
                ws2.cell(row=r, column=1, value="项目编码").font = bfont
                ws2.cell(row=r, column=3, value=item.get('code','')).font = dfont
                ws2.cell(row=r, column=5, value="项目名称").font = bfont
                ws2.cell(row=r, column=8, value=item['name']).font = dfont
                ws2.cell(row=r, column=10, value="计量单位").font = bfont
                ws2.cell(row=r, column=11, value=item['unit']).font = dfont
                ws2.cell(row=r, column=13, value="工程量").font = bfont
                ws2.cell(row=r, column=14, value=item['qty']).font = dfont
            except: pass
            r += 1
            # 项目特征描述
            try:
                ws2.cell(row=r, column=1, value="项目特征描述").font = bfont
                ws2.cell(row=r, column=5, value=item.get('features','')).font = dfont
            except: pass
            r += 1
            # 明细标题
            try:
                ws2.cell(row=r, column=1, value="清单综合单价组成明细").font = bfont
            except: pass
            r += 1
            # 表头
            try:
                ws2.cell(row=r, column=1, value="定额编号").font = bfont
                ws2.cell(row=r, column=2, value="定额项目名称").font = bfont
                ws2.cell(row=r, column=3, value="定额单位").font = bfont
                ws2.cell(row=r, column=4, value="数量").font = bfont
            except: pass
            r += 1
            # 定额明细行
            for quota in item.get('quotas', []):
                try:
                    ws2.cell(row=r, column=1, value=quota.get('code','')).font = dfont
                    ws2.cell(row=r, column=2, value=quota.get('name','')).font = dfont
                    ws2.cell(row=r, column=3, value=quota.get('unit','')).font = dfont
                    ws2.cell(row=r, column=4, value=quota.get('quantity',1)).font = dfont
                except: pass
                r += 1
            # 空行分隔
            r += 2
    
    wb.save(output_path)
    return True
