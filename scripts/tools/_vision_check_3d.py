# -*- coding: utf-8 -*-
"""P4 视觉验证闭环：三维模型 HTML → PNG 截图 → **调用视觉模型审图** → 反推算量合理性。

流程（用户 2026-10-16 要求）：
  1. playwright 无头浏览器截取 export_3d_html.py 生成的模型（多视角）
  2. 把 PNG 交给视觉模型（DeepSeek/qwen，经 vision_query），要求它做**算量合理性审查**：
     构件数量/布置是否像真实建筑、有无悬空重叠/比例失真/构件缺失/只有示意方块
  3. 输出"工程量可能偏小还是偏大"的判断，作为反推算量的人工复核线索
结果落盘 output/3d/vision_feedback.json。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, 'output', '3d')
HTMLS = [
    ('2-1钢结构（几何重建后，608 构件）', os.path.join(OUT, 't21_steel.html')),
]

PROMPT = (
    '这是建筑结构三维模型的截图（{name}，视角 {view}）。请做**算量合理性审查**，分条回答：\n'
    '① 构件数量与布置是否像真实建筑？（钢结构厂房应有成百上千构件、跨度几十米；'
    '混凝土框架应有柱/梁/板/墙配套，柱应成规则柱网）\n'
    '② 有无明显不合理：构件悬空/重叠、比例失真、构件明显缺失、大量只有示意方块？\n'
    '③ 据此反推工程量可能**偏小还是偏大**，指出最可疑的点。\n'
    '用简洁中文，不超过 200 字。'
)


def shot(html_path, view, png_path):
    """playwright 无头截图（含视角参数）。"""
    from playwright.sync_api import sync_playwright
    url = 'file:///' + os.path.abspath(html_path).replace('\\', '/') + '?view=' + view
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1400, 'height': 900})
        pg.goto(url)
        pg.wait_for_timeout(1000)
        pg.screenshot(path=png_path)
        b.close()
    return png_path


def main():
    os.makedirs(OUT, exist_ok=True)
    from vision_query import query_vision
    all_r = {}
    for name, html in HTMLS:
        if not os.path.exists(html):
            print('缺 HTML:', html)
            continue
        stem = os.path.splitext(os.path.basename(html))[0]
        for view in ('iso', 'front'):
            png = os.path.join(OUT, '%s_%s.png' % (stem, view))
            try:
                shot(html, view, png)
                print('截图 OK  %s (%.0f KB)' % (os.path.basename(png),
                                              os.path.getsize(png) / 1024.0))
            except Exception as e:
                print('截图 FAIL %s — %s' % (os.path.basename(png), str(e)[:100]))
                continue
            r = query_vision(png, prompt=PROMPT.format(name=name, view=view), enable=True)
            all_r['%s/%s' % (name, view)] = r
            print('--- 视觉反馈 [%s · %s] ---' % (name, view))
            if not r:
                print('   (视觉未返回：可能无 Key/超时/未启用)')
                continue
            txt = r.get('原始回复') or r.get('工程类型') or json.dumps(r, ensure_ascii=False)
            print('   ' + str(txt)[:900].replace('\n', '\n   '))
    with open(os.path.join(OUT, 'vision_feedback.json'), 'w', encoding='utf-8') as f:
        json.dump(all_r, f, ensure_ascii=False, indent=1)
    print('RESULTN 视觉反馈已落盘 output/3d/vision_feedback.json（%d 组）' % len(all_r))


if __name__ == '__main__':
    main()
