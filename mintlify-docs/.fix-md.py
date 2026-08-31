#!/usr/bin/env python3
"""Fix MDX 3 parsing errors by escaping `<word>` to `&lt;word&gt;`.

Strategy: replace `<[a-zA-Z][a-zA-Z0-9_-]*>` with `&lt;name&gt;`
- Works inside AND outside code blocks
- HTML entities render as `<name>` in output
- Doesn't affect real JSX tags like `<Card>`, `<Step>` (these start with capital)
"""
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r'<([a-z][a-zA-Z0-9_-]*)>')

def fix_file(p: Path) -> int:
    text = p.read_text(encoding='utf-8')
    new = PLACEHOLDER.sub(r'&lt;\1&gt;', text)
    if new != text:
        p.write_text(new, encoding='utf-8')
        return len(PLACEHOLDER.findall(text))
    return 0

if __name__ == '__main__':
    base = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
    total = 0
    files_changed = 0
    for f in sorted(base.rglob('*.mdx')):
        n = fix_file(f)
        if n > 0:
            print(f"  {f.relative_to(base)}: {n} replacements")
            total += n
            files_changed += 1
    print(f"Total: {total} replacements across {files_changed} files")
