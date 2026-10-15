from pathlib import Path
import ast
from normalize_patch_literals import normalize
ROOT=Path(__file__).resolve().parents[1]/'app'
changes={}
def rep(s,a,b):
    assert s.count(a)==1,(a[:90],s.count(a));return s.replace(a,b,1)
def load(n):
    p=ROOT/n;raw=p.read_bytes();changes[n]=(p,raw,'');return raw.decode().replace(chr(13)+chr(10),chr(10))
def put(n,s):
    p,raw,_=changes[n];s=normalize(s);ast.parse(s);changes[n]=(p,raw,s)

s=load('services/chat.py')
a=s.index('    def _build_model_prompt(');b=s.index('    def ',a+8)
block=s[a:b]
block=rep(block,'        include_history: bool = True,','        include_history: bool = True,\n        prepared_context: str | None = None,')
x=block.index('        context_sections = [');y=block.index('        style_instructions = (',x)
context=block[x:y]
block=block[:x]+'''        if prepared_context is None:
'''+''.join('    '+line if line.strip() else line for line in context.splitlines(True))+'''        else:
            authorized_context = prepared_context
'''+block[y:]
s=s[:a]+block+s[b:]
a=s.index('        compacted, summary = self._build_model_prompt(');b=s.index('        return [ProviderChatMessage',a)
block=s[a:b]
block=rep(block,'            agent_mode=agent_mode_enabled,','            agent_mode=agent_mode_enabled,\n            prepared_context=authorized_context,\n            web_search_results_present=web_search_results_present,\n            audio_transcripts=audio_transcripts,')
s=s[:a]+block+s[b:];put('services/chat.py',s)

s=load('providers/remote/openai.py')
s=rep(s,'        response = self._post_json("responses", payload)\n        texts =', '        response = self._post_json("responses", payload)\n        self.last_usage = self._usage_from_response(response)\n        texts =')
s=rep(s,'        result = _parse_json_object_text(texts[0])\n        self.last_usage = self._usage_from_response(response)', '        result = _parse_json_object_text(texts[0])')
for fn in ['    def _generate_prompted_json(', '    def generate_json(']:
    a=s.index(fn,s.index('class OpenAICompatibleChatProvider'));end=s.find('    def ',a+len(fn));b=end if end>=0 else len(s)
    block=s[a:b]
    block=rep(block,'        response = self._post_json("chat/completions", payload)', '        response = self._post_json("chat/completions", payload)\n        self.last_usage = self._usage_from_chat_chunk(response.get("usage")) or {}')
    if fn.startswith('    def _generate_prompted'):
        block=rep(block,'        result = _parse_json_object_text(content)\n        self.last_usage = self._usage_from_chat_chunk(response.get("usage")) or {}','        result = _parse_json_object_text(content)')
    s=s[:a]+block+s[b:]
put('providers/remote/openai.py',s)

s=load('services/memory_enhancement.py')
tree=ast.parse(s);lines=s.splitlines(True)
patches=[]
for node in ast.walk(tree):
    if isinstance(node,ast.ExceptHandler) and len(node.body)>1 and isinstance(node.body[0],ast.Raise):
        patches.append((node.lineno-1,node.end_lineno,' '*node.col_offset+'except AppError:'+chr(10)+' '*(node.col_offset+4)+'raise'+chr(10)))
for a,b,replacement in sorted(patches,reverse=True):lines[a:b]=[replacement]
s=''.join(lines);put('services/memory_enhancement.py',s)
for p,raw,s in changes.values():assert p.read_bytes()==raw
for p,raw,s in changes.values():
    nl=chr(13)+chr(10) if bytes([13,10]) in raw else chr(10)
    p.write_bytes(s.replace(chr(10),nl).encode());print('updated',p.relative_to(ROOT))
