from pathlib import Path
import ast
from normalize_patch_literals import normalize

p = Path(__file__).with_name('apply_memory_background_fixes.py')
s = p.read_text(encoding='utf-8')
old = '    ast.parse(text)'
new = chr(10).join(['    from normalize_patch_literals import normalize', '    text = normalize(text)', old])
assert s.count(old) == 1
s = s.replace(old, new, 1)
ast.parse(s)
p.write_text(s, encoding='utf-8', newline=chr(10))
exec(compile(s, str(p), 'exec'), {'__file__': str(p), '__name__': '__main__'})
