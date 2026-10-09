"""Expose the exact JSON in static HTML for tools that cannot open JSON URLs."""
import argparse
from html import escape
import json
from pathlib import Path


def render_snapshot(document):
    body = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>14:30指数行情与风险快照</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:20px;border-radius:8px}</style></head>
<body><h1>14:30指数行情与风险快照</h1>
<p>下面是本次发布的完整原始JSON。它是定时快照，读取时必须重新核验来源时间、交易日、指数身份和300秒新鲜度；不能只看采集时的成功状态。</p>
<p><a href="latest.json">JSON接口</a> · <a href="openapi.json">只读接口说明</a></p>
<pre id="snapshot-json">''' + escape(body) + '''</pre>
</body></html>
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='public/latest.json')
    parser.add_argument('--output', default='public/snapshot.html')
    args = parser.parse_args()
    document = json.loads(Path(args.input).read_text(encoding='utf-8'))
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_snapshot(document), encoding='utf-8')


if __name__ == '__main__':
    main()
