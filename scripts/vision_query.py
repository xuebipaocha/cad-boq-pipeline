# -*- coding: utf-8 -*-
"""视觉识别查询层 — v5.15 V-2 / v6.10 多后端(DeepSeek 优先)

- 后端可切换: DeepSeek(默认) / 千问 qwen3-vl 系列 — 均走 OpenAI 兼容接口
- Key/BaseURL/模型全可配置: 环境变量 > Reasonix 全局 .env > 内置默认
- 主后端不可用(无 Key/超时/报错) → 自动回退备选后端(默认 DeepSeek → 千问)
- 结构化 JSON 输出(工程类型/构件计数/区域/置信度), 便于下游融合
- 独立 try: 全后端都不可用 → 返回 None, 不影响几何主路径
- 结果带 '来源': '视觉识别' + '后端'/'模型'/'推理链'(审计可追)

v6.10 变更(2026-08-19):
- DeepSeek V4.1-Flash 实测 input_modalities 含 image(官方 /models 实查), 且同图对比
  读出图内文字/图元数量多于 qwen3-vl-flash → 定为默认视觉后端
- prompt 强化: 枚举值硬约束 + 禁 markdown 包裹 + 推理链留痕(_meta['推理链'])
- 后端回退链: 主后端失败自动尝试备选(不因单一供应商故障中断视觉路径)

用法:
  python3 vision_query.py 渲染.png --enable-vision [--backend deepseek|qwen]
  python3 vision_query.py renders/ --out vision_results.json --enable-vision
"""
import os
import sys
import json
import base64
import glob

sys.stdout.reconfigure(encoding='utf-8')

# ── 后端定义(v6.10): 均为 OpenAI 兼容 /chat/completions ──
BACKENDS = {
    'deepseek': {
        'base_url': 'https://api.deepseek.com',
        'model': 'deepseek-flash',          # DeepSeek-V4.1-Flash(多模态, 实测支持 image)
        'key_names': ('DEEPSEEK_API_KEY',),
        'env_base': 'DEEPSEEK_BASE_URL',
        'env_model': 'DEEPSEEK_MODEL',
    },
    'qwen': {
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'model': 'qwen3-vl-flash',          # v6.5 起沿用的视觉专用模型
        'key_names': ('QWEN_API_KEY', 'DASHSCOPE_API_KEY'),
        'env_base': 'QWEN_BASE_URL',
        'env_model': 'QWEN_MODEL',
    },
}
# 默认后端: 环境变量 VISION_BACKEND 可覆盖(deepseek|qwen)
DEFAULT_BACKEND = os.environ.get('VISION_BACKEND', 'deepseek')
# 回退顺序: 主后端失败 → 依次尝试链上其余后端
FALLBACK_ORDER = ('deepseek', 'qwen')

DEFAULT_BASE_URL = BACKENDS['deepseek']['base_url']       # 向后兼容旧常量
DEFAULT_MODEL = BACKENDS['deepseek']['model']             # 向后兼容旧常量

# 视觉按需调用开关(v5.15, 用户确认工作方式):
# 默认关闭 — 主力是纯文本语言模型走全链路, 仅显式启用视觉时调用视觉模型。
# 启用方式: 环境变量 VISION_ENABLED=1 或调用方传 enable=True / CLI --enable-vision。
VISION_ENABLED = os.environ.get('VISION_ENABLED', '0') in ('1', 'true', 'True', 'yes')

# 视觉识别默认提示词: 限定任务 + 强制 JSON(与 V-2 规划任务清单一致)
# v6.10 强化: ①枚举值硬约束(不许把图标题/自造词当工程类型) ②明确"仅输出 JSON, 不要 markdown
# 代码块/前后解释" ③补 out_of_enum 字段, 允许"不确定"而不是乱填 ④要求逐项列出看到的文字,
# 供与几何/文字识图交叉验证(视觉永不覆盖几何)。
PROMPT_JSON = (
    '你是 CAD 图纸视觉识别助手。请分析这张图纸渲染图。\n'
    '硬性要求:\n'
    '1) 只输出一个 JSON 对象, 不要 markdown 代码块(不要 ```), 不要任何前后说明文字;\n'
    '2) "工程类型" 只能取以下枚举之一(原样抄写, 不得自造或填图名): '
    '"房屋建筑与装饰工程"、"安装工程"、"市政工程"、"园林绿化工程"、"钢结构工程";'
    '若无法判断, 填 "" 并把 "枚举外判断" 写成你的猜测;\n'
    '3) 数字必须是你在图中真实数出来的; 无法确定填 0, 不要猜测;\n'
    '4) "可见文字" 列出图中能辨认的文字(含图名/图层名/比例尺/规格标注), 看不清的不要编;\n'
    '5) **"房间部位" 逐条列出图中能辨认的房间/部位名称**(如 卫生间、办公室、会议室、走廊、\n'
    '   楼梯间、厨房、淋浴间、储藏间、设备间、门厅、阳台; 或做法表上的"内墙1/楼1/棚1"等部位编号),\n'
    '   这对室内装饰算量至关重要; 看不清/没标注的**不要编造**。\n'
    'JSON 结构:\n'
    '{\n'
    '  "工程类型": "",\n'
    '  "工程类型置信度": 0.0,\n'
    '  "枚举外判断": "",\n'
    '  "构件计数": {"窗户": 0, "门": 0, "柱": 0, "设备符号": 0, "苗木块": 0},\n'
    '  "可见文字": [],\n'
    '  "房间部位": [],\n'
    '  "区域": [{"名称": "主区域", "描述": ""}],\n'
    '  "备注": ""\n'
    '}'
)

SPECIALTY_ENUM = ('房屋建筑与装饰工程', '安装工程', '市政工程', '园林绿化工程', '钢结构工程')

_ENV_PATHS = (lambda: os.path.join(os.environ.get('APPDATA', ''), 'reasonix', '.env'),
              lambda: os.path.expanduser('~/.reasonix/.env'))


def _read_env_key(name):
    """读单个环境变量 > Reasonix 全局 .env。找不到返回 ''。"""
    v = os.environ.get(name, '')
    if v:
        return v.strip()
    for path_fn in _ENV_PATHS:
        try:
            with open(path_fn(), encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(name + '='):
                        return line.split('=', 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ''


def _load_api_key():
    """向后兼容: 取默认后端的 Key(旧调用点仍可用)。"""
    return _read_env_key(BACKENDS[DEFAULT_BACKEND]['key_names'][0])


def resolve_backends(backend=None, model=None, base_url=None, api_key=None):
    """解析可用后端列表(主后端在前, 失败时按序回退)。

    - 显式传 model/base_url/api_key → 只返回该单一后端(向后兼容旧调用);
    - 否则 → 以 backend 参数或 VISION_BACKEND(默认 deepseek) 为主, 其余按
      FALLBACK_ORDER 追加, 仅保留"取到 Key"的后端。
    """
    if model or base_url or api_key:
        return [{'name': backend or 'custom', 'model': model or DEFAULT_MODEL,
                 'base_url': base_url or DEFAULT_BASE_URL, 'api_key': api_key or _load_api_key()}]
    main = (backend or DEFAULT_BACKEND).lower()
    order = [main] + [b for b in FALLBACK_ORDER if b != main]
    out = []
    for name in order:
        cfg = BACKENDS.get(name)
        if not cfg:
            continue
        key = ''
        for kn in cfg['key_names']:
            key = _read_env_key(kn)
            if key:
                break
        if not key:
            continue
        out.append({
            'name': name,
            'model': os.environ.get(cfg['env_model']) or cfg['model'],
            'base_url': os.environ.get(cfg['env_base']) or cfg['base_url'],
            'api_key': key,
        })
    return out


def _image_to_base64(image_path):
    with open(image_path, 'rb') as f:
        return base64.b64encode(f.read()).decode()


def _strip_markdown_json(text):
    """剥掉 ```json ... ``` 包裹(v6.10: prompt 已禁, 但保兼容)。"""
    c = (text or '').strip()
    if '```' in c:
        parts = c.split('```')
        if len(parts) >= 3:
            c = parts[1]
            if c.lstrip().lower().startswith('json'):
                c = c.lstrip()[4:]
        c = c.strip()
    # 容错: 前后可能残留解释文字 → 截取第一个 { 到最后一个 }
    if not c.startswith('{') and '{' in c:
        c = c[c.index('{'):]
    if c.endswith('}') is False and '}' in c:
        c = c[:c.rindex('}') + 1]
    return c


def _call_backend(cfg, image_path, prompt, timeout, max_tokens=None):
    """调单个后端 → (result_dict | None, err_str)。

    v6.10 修复(实测驱动): 分块识别中标注密集块(船体大楼 r2c2)曾出现 tokens=7890 但解析
    结果为空 —— 模型输出被默认 max_tokens 截断, JSON 不完整。现显式设定 max_tokens
    (默认 8192), 并在解析失败时打 '_解析失败' 标记(不再静默降级为空结果)。
    """
    import urllib.request
    try:
        b64 = _image_to_base64(image_path)
    except Exception as e:
        return None, f'读图失败 {image_path}: {e}'
    payload = {
        'model': cfg['model'],
        'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
            {'type': 'text', 'text': prompt},
        ]}],
        'stream': False,
        'max_tokens': int(max_tokens or os.environ.get('VISION_MAX_TOKENS') or 8192),
    }
    req = urllib.request.Request(
        cfg['base_url'].rstrip('/') + '/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': f"Bearer {cfg['api_key']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return None, f"调用失败({e})"
    content = ''
    reasoning = ''
    try:
        msg = d['choices'][0]['message']
        content = msg.get('content') or ''
        reasoning = msg.get('reasoning_content') or ''
        finish = (d['choices'][0].get('finish_reason') or '')
        c = _strip_markdown_json(content)
        result = json.loads(c)
        if not isinstance(result, dict):
            result = {'工程类型': str(result)}
    except Exception as e:
        finish = (d.get('choices') or [{}])[0].get('finish_reason', '') if d.get('choices') else ''
        result = {'工程类型': '', '原始回复': content[:800], '_解析失败': True,
                  '_解析错误': str(e)[:200], '_finish_reason': finish}
    if finish == 'length':
        result['_输出截断'] = True
    # v6.10: 枚举硬校验 — 越界值不丢弃但标记, 供融合层判定"需人工确认"
    gt = str(result.get('工程类型', '') or '')
    if gt and gt not in SPECIALTY_ENUM:
        result.setdefault('枚举外判断', gt)
        result['工程类型'] = ''
        result['枚举越界'] = True
    usage = d.get('usage') or {}
    result['_meta'] = {
        '模型': d.get('model', cfg['model']),
        '后端': cfg['name'],
        '来源': '视觉识别',
        'image_tokens': (usage.get('prompt_tokens_details') or {}).get('image_tokens', 0),
        'total_tokens': usage.get('total_tokens', 0),
        'image': os.path.basename(image_path),
    }
    # 推理链留痕(可审计: 模型"怎么看的") — 截断防爆
    if reasoning:
        result['_meta']['推理链'] = reasoning[:1500]
    return result, ''


def query_vision(image_path, model=None, base_url=None, prompt=None, timeout=120,
                 api_key=None, enable=None, backend=None):
    """单张 PNG → 结构化 JSON(视觉识别)。失败/超时/未启用 → None(不影响主流程)。

    v5.15 按需调用: enable 为 None 时跟随全局 VISION_ENABLED(默认关闭);
    显式 enable=True 时强制启用(调用方按需触发)。
    v6.10 多后端: 主后端(默认 DeepSeek)失败 → 自动回退备选后端(千问)。
    """
    if enable is None:
        enable = VISION_ENABLED
    if not enable:
        return None
    prompt = prompt or PROMPT_JSON
    backends = resolve_backends(backend=backend, model=model, base_url=base_url, api_key=api_key)
    if not backends:
        print('  ⚠ vision_query: 未找到可用的视觉 API Key (DEEPSEEK_API_KEY/QWEN_API_KEY), 跳过视觉识别')
        return None

    errs = []
    for i, cfg in enumerate(backends):
        result, err = _call_backend(cfg, image_path, prompt, timeout)
        if result:
            if i > 0:
                result['_meta']['回退'] = f"主后端不可用, 已回退 {cfg['name']}"
            return result
        errs.append(f"{cfg['name']}: {err}")
    print(f"  ⚠ vision_query: 全部视觉后端失败 — {'; '.join(errs)}")
    return None


def run_batch(image_dir, out_path=None, model=None, limit=None, backend=None):
    """批量识别一个目录下所有 PNG → 结果列表。"""
    results = []
    pngs = sorted(glob.glob(os.path.join(image_dir, '*.png')))
    if limit:
        pngs = pngs[:limit]
    print(f'vision_query: 批量识别 {len(pngs)} 张图 (后端 {backend or DEFAULT_BACKEND})')
    for i, p in enumerate(pngs, 1):
        r = query_vision(p, model=model, backend=backend)
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
    ap = argparse.ArgumentParser(description='视觉识别查询层(V-2): PNG → 结构化 JSON, 多后端')
    ap.add_argument('images', nargs='*', help='PNG 文件或目录(批量)')
    ap.add_argument('--model', default=None, help='模型名(默认按后端: deepseek-flash / qwen3-vl-flash)')
    ap.add_argument('--base-url', default=None, help='OpenAI 兼容 base_url(默认按后端)')
    ap.add_argument('--backend', default=None, choices=('deepseek', 'qwen'),
                    help='视觉后端(默认 deepseek, 失败回退千问)')
    ap.add_argument('--out', default=None, help='批量结果输出 json')
    ap.add_argument('--limit', type=int, default=None, help='批量最多处理张数')
    ap.add_argument('--enable-vision', action='store_true',
                    help='显式启用视觉调用(默认关闭, 按需触发)')
    args = ap.parse_args(argv)

    if not args.images:
        print('用法: python3 vision_query.py 渲染.png --enable-vision 或 vision_query.py renders/ --out r.json')
        return
    # v5.15 按需调用: 默认关闭, CLI 需 --enable-vision 显式触发(或 VISION_ENABLED=1)
    if not (args.enable_vision or VISION_ENABLED):
        print('视觉调用默认关闭(按需调用原则)。如需启用: 加 --enable-vision 或 VISION_ENABLED=1')
        return
    if len(args.images) == 1 and os.path.isdir(args.images[0]):
        run_batch(args.images[0], args.out, model=args.model, limit=args.limit, backend=args.backend)
        return
    for p in args.images:
        r = query_vision(p, model=args.model, base_url=args.base_url, backend=args.backend)
        print(json.dumps(r, ensure_ascii=False, indent=2) if r else 'FAIL')


if __name__ == '__main__':
    main()
