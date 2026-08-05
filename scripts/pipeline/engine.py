"""
规则引擎 - 自动加载并运行所有规则
"""
import os, importlib, sys

def load_rules():
    """加载 rules/ 目录下所有规则"""
    rule_list = []
    rules_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rules')
    sys.path.insert(0, rules_dir)
    
    for f in sorted(os.listdir(rules_dir)):
        if f.startswith('rule_') and f.endswith('.py'):
            modname = f[:-3]
            try:
                module = importlib.import_module(modname)
                from pipeline.rules.base import RuleBase
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, RuleBase) and attr is not RuleBase:
                        rule_list.append(attr())
            except Exception as e:
                print(f'  加载规则{f}失败: {e}')
    
    return rule_list

def run_review(drawing_data):
    """运行所有审图规则（含前置条件检查）"""
    rules = load_rules()
    all_problems = []
    has_areas = len(drawing_data.get('面积区域',[])) > 0
    has_layers = len(drawing_data.get('构造层',[])) > 0
    has_texts = len(drawing_data.get('施工说明',[])) > 0
    
    for rule in rules:
        # 前置条件
        prereq = getattr(rule, 'prerequisites', None)
        if prereq:
            skip = False
            if '面积区域' in prereq and not has_areas: skip = True
            if '构造层' in prereq and not has_layers: skip = True
            if '施工说明' in prereq and not has_texts: skip = True
            if skip:
                print(f'  规则{rule.name}跳过：缺{prereq}')
                all_problems.append({
                    '问题': f'审图规则{rule.description}跳过：缺少{prereq}数据',
                    '位置': '系统提示',
                    '类别': '数据不足',
                    '影响造价': False,
                    '严重程度': '低',
                    '建议': '请确认图纸已完整识别'
                })
                continue
        try:
            problems = rule.check(drawing_data)
            all_problems.extend(problems)
        except Exception as e:
            print(f'  规则{rule.name}执行出错: {e}')
    return all_problems
