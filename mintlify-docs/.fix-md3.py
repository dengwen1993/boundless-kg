#!/usr/bin/env python3
"""Fix MDX 3 round 3: escape < followed by digit (e.g. '<30 ms' in tables)."""
import re
import sys
from pathlib import Path

DIGIT_PREFIX = re.compile(r'<([0-9])')

def fix_file(p: Path) -> int:
    text = p.read_text(encoding='utf-8')
    new, n = DIGIT_PREFIX.subn(r'&lt;\1', text)
    if new != text:
        p.write_text(new, encoding='utf-8')
    return n

if __name__ == '__main__':
    base = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
    total = 0
    files = 0
    for f in sorted(base.rglob('*.mdx')):
        n = fix_file(f)
        if n > 0:
            print(f"  {f.relative_to(base)}: {n}")
            total += n
            files += 1
    print(f"Total: {total} replacements across {files} files")
