# -*- coding: utf-8 -*-
"""视觉识别查询层 — v5.15 视觉化 V-2

把"渲染 PNG → 多模态模型视觉识别"固化为可复用薄封装:
- 模型/API Key/BaseURL 全部可配置(默认读 Reasonix 全局 .env 的 QWEN_API_KEY)
- 结构化 JSON 输出(工程类型/构件计数/区域/置信度), 便于下游融合
- 独立 try: 模型不可用/超时 → 返回 None, 不影响几何主路径
- 视觉结果一律带 '来源': '视觉识别', 由融合层(V-3)标"需交叉验证"

用法:
  python3 vision_query.py 渲染.png [--model qwen3-vl-flash] [--task 工程类型]
  python3 vision_query.py --batch renders/ --out vision_results.json
"""
import os
import sys
import json
import base64
import glob

sys.stdout.reconfigure(encoding='utf-8')

DEFAULT_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
DEFAULT_MODEL = 'qwen3-vl-flash'  # v6.5: 视觉专用模型(原 qwen3.7-flash 通用推理型, 视觉识别弱)

# 视觉按需调用开关(v5.15, 用户确认工作方式):
# 默认关闭 — 主力是纯文本语言模型(deepseek 等)走全链路, 仅显式启用视觉时调用视觉模型。
# 启用方式: 环境变量 VISION_ENABLED=1 或调用方传 enable=True / CLI --enable-vision。
VISION_ENABLED = os.environ.get('VISION_ENABLED', '0') in ('1', 'true', 'True', 'yes')

# 视觉识别默认提示词: 限定任务 + 强制 JSON(与 V-2 规划任务清单一致)
PROMPT_JSON = (
    '你是 CAD 图纸视觉识别助手。请分析这张图纸渲染图, 输出 JSON(不要输出其他文字):\n'
    '{\n'
    '  "工程类型": "房屋建筑与装饰工程|安装工程|市政工程|园林绿化工程|钢结构工程",\n'
    '  "工程类型置信度": 0.0-1.0,\n'
    '  "构件计数": {"窗户": 0, "门": 0, "柱": 0, "设备符号": 0, "苗木块": 0},\n'
    '  "区域": [{"名称": "主区域", "描述": ""}],\n'
    '  "备注": ""\n'
    '}\n'
    '无法确定的项目填 0 或空, 不要猜测。'
)


def _load_api_key():
    """读取千问 API Key: 环境变量 QWEN_API_KEY > Reasonix 全局 .env。"""
    key = os.environ.get('QWEN_API_KEY', '') or os.environ.get('DASHSCOPE_API_KEY', '')
    if key:
        return key.strip()
    for p in (os.path.join(os.environ.get('APPDATA', ''), 'reasonix', '.env'),
              os.path.expanduser('~/.reasonix/.env')):
        try:
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('QWEN_API_KEY='):
                        return line.split('=', 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ''


def _image_to_base64(image_path):
    with open(image_path, 'rb') as f:
        return base64.b64encode(f.read()).decode()


def query_vision(image_path, model=None, base_url=None, prompt=None, timeout=120,
                 api_key=None, enable=None):
    """单张 PNG → 结构化 JSON(视觉识别)。失败/超时/未启用 → None(不影响主流程)。

    v5.15 按需调用: enable 为 None 时跟随全局 VISION_ENABLED(默认关闭);
    显式 enable=True 时强制启用(调用方按需触发)。
    """
    if enable is None:
        enable = VISION_ENABLED
    if not enable:
        return None
    # v6.5: 模型可配置 — 环境变量 VISION_MODEL 覆盖默认(qwen3-vl-flash)
    model = model or os.environ.get('VISION_MODEL') or DEFAULT_MODEL
    base_url = base_url or DEFAULT_BASE_URL
    prompt = prompt or PROMPT_JSON
    key = api_key or _load_api_key()
    if not key:
        print(f'  ⚠ vision_query: 未找到 QWEN_API_KEY, 跳过视觉识别')
        return None

    import urllib.request
    try:
        b64 = _image_to_base64(image_path)
    except Exception as e:
        print(f'  ⚠ vision_query: 读图失败 {image_path}: {e}')
        return None

    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
            {'type': 'text', 'text': prompt},
        ]}],
        'stream': False,
    }
    req = urllib.request.Request(
        base_url.rstrip('/') + '/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f'  ⚠ vision_query: 调用失败({e})')
        return None

    try:
        content = d['choices'][0]['message']['content']
        # 提取 JSON(模型可能包裹 ```json ... ```)
        c = content.strip()
        if '```' in c:
            c = c.split('```')[1]
            if c.startswith('json'):
                c = c[4:]
        result = json.loads(c)
        if not isinstance(result, dict):
            result = {'工程类型': str(result)}
    except Exception:
        result = {'工程类型': content[:100], '原始回复': content[:500]}

    result['_meta'] = {
        '模型': d.get('model', model),
        '来源': '视觉识别',
        'image_tokens': ((d.get('usage') or {}).get('prompt_tokens_details') or {}).get('image_tokens', 0),
        'total_tokens': (d.get('usage') or {}).get('total_tokens', 0),
        'image': os.path.basename(image_path),
    }
    return result


def run_batch(image_dir, out_path=None, model=None, limit=None):
    """批量识别一个目录下所有 PNG → 结果列表。"""
    results = []
    pngs = sorted(glob.glob(os.path.join(image_dir, '*.png')))
    if limit:
        pngs = pngs[:limit]
    print(f'vision_query: 批量识别 {len(pngs)} 张图 (模型 {model or DEFAULT_MODEL})')
    for i, p in enumerate(pngs, 1):
        r = query_vision(p, model=model)
        status = 'OK' if r else 'FAIL'
        print(f'  [{i}/{len(pngs)}] {status} {os.path.basename(p)}')
        if r:
            results.append(r)
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f'  输出: {out_path}')
    return results


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='视觉识别查询层(V-2): PNG → 结构化 JSON')
    ap.add_argument('images', nargs='*', help='PNG 文件或目录(批量)')
    ap.add_argument('--model', default=DEFAULT_MODEL, help=f'模型名(默认 {DEFAULT_MODEL})')
    ap.add_argument('--base-url', default=DEFAULT_BASE_URL, help='OpenAI 兼容 base_url')
    ap.add_argument('--out', default=None, help='批量结果输出 json')
    ap.add_argument('--limit', type=int, default=None, help='批量最多处理张数')
    ap.add_argument('--enable-vision', action='store_true',
                    help='显式启用视觉调用(默认关闭, 按需触发)')
    args = ap.parse_args(argv)

    if not args.images:
        print('用法: python3 vision_query.py 渲染.png 或 vision_query.py renders/ --out r.json')
        return
    # v5.15 按需调用: 默认关闭, CLI 需 --enable-vision 显式触发(或 VISION_ENABLED=1)
    if not (args.enable_vision or VISION_ENABLED):
        print('视觉调用默认关闭(按需调用原则)。如需启用: 加 --enable-vision 或 VISION_ENABLED=1')
        return
    if len(args.images) == 1 and os.path.isdir(args.images[0]):
        run_batch(args.images[0], args.out, model=args.model, limit=args.limit)
        return
    for p in args.images:
        r = query_vision(p, model=args.model, base_url=args.base_url)
        print(json.dumps(r, ensure_ascii=False, indent=2) if r else 'FAIL')


if __name__ == '__main__':
    main()
