"""Normalize accidental literal newlines inside one-shot patch strings."""
from pathlib import Path
import ast
import sys

def normalize(text):
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == '#':
            end = text.find(chr(10), i)
            end = len(text) if end < 0 else end
            out.append(text[i:end]); i = end; continue
        if c not in (chr(34), chr(39)):
            out.append(c); i += 1; continue
        triple = text.startswith(c * 3, i)
        size = 3 if triple else 1
        end = i + size
        while end < len(text):
            if text[end] == chr(92):
                end += 2; continue
            if text.startswith(c * size, end):
                break
            end += 1
        body = text[i + size:end]
        if not triple and chr(10) in body:
            body = body.replace(chr(10), chr(92) + 'n').replace(chr(13), chr(92) + 'r')
        out.append(c * size + body + c * size)
        i = end + size
    return ''.join(out)

if __name__ == '__main__':
    path = Path(sys.argv[1])
    text = normalize(path.read_text(encoding='utf-8'))
    ast.parse(text)
    path.write_text(text, encoding='utf-8', newline=chr(10))
    print('syntax_ok', path)
