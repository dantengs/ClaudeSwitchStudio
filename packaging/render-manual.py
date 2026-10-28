"""Render the project's small Markdown manual to a self-contained offline page."""
from pathlib import Path
import html
import re

root = Path(__file__).resolve().parents[1]
source = (root / 'docs/USER_GUIDE.md').read_text(encoding='utf-8-sig')

def inline(value):
    value = html.escape(value)
    value = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', value)
    return re.sub(r'`([^`]+)`', r'<code>\1</code>', value)

parts = []
table = []
paragraph = []

def flush():
    if paragraph:
        parts.append('<p>' + inline(' '.join(paragraph)) + '</p>')
        paragraph.clear()
    if table:
        rows = [row for row in table if not re.match(r'^\|[\s:|\-]+\|$', row)]
        rendered = []
        for i, row in enumerate(rows):
            tag = 'th' if i == 0 else 'td'
            rendered.append('<tr>' + ''.join(f'<{tag}>{inline(cell.strip())}</{tag}>' for cell in row.strip('|').split('|')) + '</tr>')
        parts.append('<table>' + ''.join(rendered) + '</table>')
        table.clear()

for line in source.splitlines():
    if line.startswith('|'):
        if paragraph: flush()
        table.append(line)
    elif not line.strip():
        flush()
    elif line.startswith('#'):
        flush()
        level = len(line) - len(line.lstrip('#'))
        parts.append(f'<h{level}>{inline(line[level:].strip())}</h{level}>')
    else:
        if table: flush()
        paragraph.append(line)
flush()
document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Switch Studio · 使用手册</title><style>
:root{color-scheme:light dark;font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;line-height:1.9;font-size:16px}
body{max-width:850px;margin:48px auto;padding:0 28px 64px}h1{font-size:32px}h2{font-size:23px;margin-top:42px}
p{margin:18px 0}strong{color:#398edb}code{font-family:Consolas,monospace;font-size:.92em}table{border-collapse:collapse;width:100%}
td,th{text-align:left;padding:12px;border-bottom:1px solid #76829555}th{font-weight:600}
</style><article>''' + '\n'.join(parts) + '</article></html>'
(root / 'docs/USER_GUIDE.html').write_text(document, encoding='utf-8')
