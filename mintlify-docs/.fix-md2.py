#!/usr/bin/env python3
"""Fix MDX 3 issues (round 2):
- `<http(s)://...>` autolinks → escape < and >
- Stray closing tags like `</em>`, `</svg>` in code blocks → escape
"""
import re
import sys
from pathlib import Path

# Match <http://...> or <https://...> (autolink syntax that MDX chokes on)
AUTOLINK = re.compile(r'<(https?://[^>\s]+)>')

# Match closing HTML tags in code blocks where opening wasn't also escaped:
# Pattern: </word> not preceded by `&lt;` on same line context
def fix_file(p: Path) -> int:
    text = p.read_text(encoding='utf-8')
    new = text
    n_autolink = 0

    # Fix autolinks
    def repl_autolink(m):
        return f'&lt;{m.group(1)}&gt;'
    new, n_autolink = AUTOLINK.subn(repl_autolink, new)

    # For each remaining `</word>` where the matching `<word` wasn't escaped:
    # Simplest: if line contains both `</word>` AND `<word` not escaped, escape the closing
    # Use line-by-line processing to handle code block context properly
    out_lines = []
    in_code = False
    stray_close_fixed = 0
    for line in new.split('\n'):
        # Toggle code fence
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            out_lines.append(line)
            continue
        if not in_code:
            # Outside code: leave as-is (real JSX uses these)
            out_lines.append(line)
            continue
        # Inside code block: escape stray </word> where opening wasn't escaped
        # Find </word> not preceded by `&lt;` in same line
        def fix_stray_close(m):
            # Check if opening <word was also unescaped on this line
            tag = m.group(1)
            # If we have both `&lt;tag` (escaped opening) and `</tag>` (raw closing), fix closing
            if f'&lt;{tag}' in line:
                return f'&lt;/{tag}&gt;'
            return m.group(0)
        new_line, n = re.subn(r'</([a-zA-Z][a-zA-Z0-9_-]*)>', fix_stray_close, line)
        stray_close_fixed += n
        out_lines.append(new_line)
    new = '\n'.join(out_lines)

    total = n_autolink + stray_close_fixed
    if new != text:
        p.write_text(new, encoding='utf-8')
    return total

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
