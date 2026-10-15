import { describe, expect, it } from 'vitest'

import { collectAsync, sseResponse } from '@/test/async'
import { parseSseResponse } from './sse'

describe('parseSseResponse', () => {
  it('parses chunked SSE frames and deduplicates event identifiers', async () => {
    const response = sseResponse([
      'id: event-1\nevent: delta\ndata: {"event_',
      'id":"event-1","value":"one"}\n\n',
      'id: event-1\ndata: {"value":"duplicate"}\n\n',
      'data: {"event_id":"event-2","value":"two"}\n\n',
    ])

    const events = await collectAsync(parseSseResponse<{ event_id?: string; value: string }>(response))

    expect(events).toEqual([
      {
        id: 'event-1',
        event: 'delta',
        data: { event_id: 'event-1', value: 'one' },
        rawData: '{"event_id":"event-1","value":"one"}',
      },
      {
        id: 'event-2',
        event: 'message',
        data: { event_id: 'event-2', value: 'two' },
        rawData: '{"event_id":"event-2","value":"two"}',
      },
    ])
  })

  it('shares de-duplication state across reconnects', async () => {
    const seen = new Set<string>(['already-seen'])
    const events = await collectAsync(
      parseSseResponse<{ event_id: string }>(
        sseResponse(['data: {"event_id":"already-seen"}\n\ndata: {"event_id":"new"}\n\n']),
        { seenEventIds: seen },
      ),
    )

    expect(events.map((event) => event.id)).toEqual(['new'])
    expect(seen).toEqual(new Set(['already-seen', 'new']))
  })
})
