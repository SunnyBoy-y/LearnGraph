"""Apply narrowly scoped streaming boundary/order fixes, preserving bytes."""
from pathlib import Path
p = Path(__file__).resolve().parents[1] / 'backend/app/services/chat.py'
raw = p.read_bytes()
nl = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8').replace('\r\n', '\n')
patches = [(
    '                data={"kind": "final_answer"},',
    '                # The shared draft may still become pre-tool narration.\n'
    '                # Only maybe_start_answer() establishes the final boundary.\n'
    '                data={},', 2), (
    '                answer_started_at = started\n                thinking_ms = max(',
    '                answer_started_at = started\n'
    '                text_record.data = {**(text_record.data or {}), "kind": "final_answer"}\n'
    '                thinking_ms = max(', 2)]
for mode, counter in [('retry_agent_mode', 'next_ordinal'), ('payload.agent_mode', 'next_stream_part_ordinal')]:
    old = f'                                    if not {mode}:\n                                        yield from maybe_start_answer()'
    new = ('                                    # Reserve visible order at the first text delta,\n'
           '                                    # not when the empty shared draft was created.\n'
           f'                                    if not final_text and text_record.ordinal < {counter} - 1:\n'
           f'                                        text_record.ordinal = {counter}\n'
           f'                                        {counter} += 1\n' + old)
    patches.append((old, new, 1))
for old, new, count in patches:
    if text.count(new) == count:
        continue
    assert text.count(old) == count, f'Unexpected patch anchors: {old[:80]}'
    text = text.replace(old, new)
old_sequence = '"sequence": chunk_sequence,'
if old_sequence in text:
    assert text.count(old_sequence) == 3
    for record in ('text_record', 'reasoning_record', 'text_record'):
        text = text.replace(old_sequence, f'"sequence": {record}.ordinal,', 1)
compile(text, str(p), 'exec')
p.write_bytes(text.replace('\n', nl).encode('utf-8'))
print('Verified/applied 2 draft initializers, 2 final boundaries, 2 first-text ordinal guards; syntax OK.')
