"""Dependency-free behavioral checks of the actual chat boundary AST.
Runs the real initializers/boundary functions with lightweight repository/event doubles.
Does not connect to a database or model provider.
"""
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

source = Path(__file__).resolve().parents[1] / 'backend/app/services/chat.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
initializers = []
boundaries = []
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'MessagePartRecord':
        fields = {kw.arg: kw.value for kw in node.keywords}
        if (isinstance(fields.get('part_type'), ast.Constant) and fields['part_type'].value == 'text'
                and isinstance(fields.get('status'), ast.Constant) and fields['status'].value == 'pending'
                and isinstance(fields.get('content'), ast.Constant) and fields['content'].value == ''):
            initializers.append(fields)
    if isinstance(node, ast.FunctionDef) and node.name == 'maybe_start_answer':
        boundaries.append(node)

assert len(initializers) == 2, len(initializers)
assert len(boundaries) == 2, len(boundaries)
for fields in initializers:
    data = eval(compile(ast.Expression(fields['data']), str(source), 'eval'), {})
    assert data.get('kind') != 'final_answer', data
print('PASS: both empty shared drafts start without a final-answer mark')

class Events:
    def __init__(self):
        self.events = []
    def _append_event(self, **kwargs):
        self.events.append(kwargs)
        return kwargs
    def _encode_event(self, event):
        return event

for index, boundary in enumerate(boundaries, 1):
    harness = ast.parse('''def run_boundary():
    answer_started = False
    answer_started_at = None
    provider_trace = {}
    sequence = 1
''').body[0]
    harness.body.append(boundary)
    harness.body.extend(ast.parse('''first = list(maybe_start_answer())
second = list(maybe_start_answer())
return first, second, provider_trace, sequence
''').body)
    module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
    record = SimpleNamespace(id='draft', ordinal=7, data={'retained': 'metadata'})
    events = Events()
    now = datetime.now(timezone.utc)
    ns = {'Iterable': list, 'self': events, 'text_record': record, 'utc_now': lambda: now,
          'generation_started_at': now, 'session_id': 'session',
          'message': SimpleNamespace(id='message'), 'assistant_message': SimpleNamespace(id='message'),
          'version': SimpleNamespace(id='version'), 'assistant_version': SimpleNamespace(id='version')}
    exec(compile(module, str(source), 'exec'), ns)
    first, second, trace, sequence = ns['run_boundary']()
    assert record.data == {'retained': 'metadata', 'kind': 'final_answer'}, record.data
    assert len(first) == 1 and not second and len(events.events) == 1
    assert first[0]['event_type'] == 'answer.started'
    assert first[0]['payload']['final_part_id'] == 'draft'
    assert first[0]['payload']['boundary_sequence'] == 7
    assert sequence == 2 and trace['thinking_duration_ms'] == 0
    print(f'PASS: boundary {index} marks actual record, keeps metadata/ordinal, emits exactly once')
guards = [node for node in ast.walk(tree) if isinstance(node, ast.If)
          and ast.unparse(node.test).startswith('not final_text and text_record.ordinal < ')]
assert len(guards) == 2, len(guards)
for guard in guards:
    counter = guard.body[0].value.id
    for content, ordinal, next_value, expected in [('', 4, 6, 6), ('', 1, 2, 1), ('already visible', 1, 5, 1)]:
        record = SimpleNamespace(ordinal=ordinal)
        ns = {'final_text': content, 'text_record': record, counter: next_value}
        module = ast.fix_missing_locations(ast.Module(body=[guard], type_ignores=[]))
        exec(compile(module, str(source), 'exec'), ns)
        assert record.ordinal == expected, (counter, content, record.ordinal)
        assert ns[counter] == next_value + int(expected != ordinal)
    print(f'PASS: {counter} preserves text-first, reasoning-first and already-visible order')
checked = 0
for node in ast.walk(tree):
    if not isinstance(node, ast.Dict):
        continue
    fields = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
    identity = fields.get('id')
    if 'content_delta' not in fields or not isinstance(identity, ast.Attribute):
        continue
    if not isinstance(identity.value, ast.Name) or identity.value.id not in {'text_record', 'reasoning_record', 'invocation_reasoning_record'}:
        continue
    ordinal = fields.get('sequence')
    assert isinstance(ordinal, ast.Attribute) and ordinal.attr == 'ordinal'
    assert ast.unparse(ordinal.value) == ast.unparse(identity.value)
    checked += 1
assert checked >= 6, checked
print(f'PASS: all {checked} text/reasoning delta payloads use stable record ordinals')
print('All backend boundary/order checks passed (isolated actual-code execution).')
