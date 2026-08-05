"""
Step 2: 审图 — 规则引擎
"""
import sys, os, json
sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

def run(input_path, output_dir):
    print('='*50)
    print('Step 2: 审图')
    print('='*50)
    with open(input_path, 'r', encoding='utf-8') as f:
        drawing_data = json.load(f)
    print(f'  读取识图结果: {len(drawing_data.get("构造层",[]))}个构造层')
    from pipeline.engine import run_review
    from pipeline.reporter import export_review_excel
    problems = run_review(drawing_data)
    print(f'  发现 {len(problems)} 个图纸问题')
    xlsx_path = os.path.join(output_dir, '图纸问题清单.xlsx')
    export_review_excel(problems, xlsx_path)
    print(f'  输出: {xlsx_path}')
    json_path = os.path.join(output_dir, '审图记录.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({'problems': problems, 'total': len(problems)}, f, ensure_ascii=False, indent=2)
    return {'problems': problems, 'total': len(problems)}

if __name__ == '__main__':
    out = os.path.join(os.path.dirname(BASE_DIR), 'output')
    os.makedirs(out, exist_ok=True)
    sample = {'构造层': [{'名称':'细粒式沥青混凝土','厚度_mm':40},{'名称':'水泥稳定碎石基层','厚度_mm':None}]}
    with open(os.path.join(out, '识图结果.json'), 'w', encoding='utf-8') as f:
        json.dump(sample, f)
    run(os.path.join(out, '识图结果.json'), out)
