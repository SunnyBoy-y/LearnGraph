import { describe, expect, it } from 'vitest'

import { voiceSessionController } from '@/features/voice/voice-session-controller'
import {
  emptyTranscriptState,
  reduceVoiceEvent,
  seedFromServerTurns,
} from '@/features/voice/voice-transcript'
import {
  emptyVoiceTaskState,
  mergeVoiceTaskResults,
  reduceVoiceTaskEvent,
  voiceTaskPlaybackLabel,
} from '@/features/voice/voice-tasks'

describe('voice agent upgrade acceptance contract', () => {
  it('keeps direct conversation turns while replayed durable events do not duplicate', () => {
    const userFinal = {
      event_id: 'evt-user-1',
      event_seq: 1,
      type: 'user.final',
      turn_id: 'turn-1',
      payload: { text: 'Explain open addressing first.' },
    }
    const assistantFinal = {
      event_id: 'evt-assistant-1',
      event_seq: 2,
      type: 'turn.finalized',
      turn_id: 'turn-1',
      payload: { role: 'assistant', text: 'It places collisions in the next empty slot.' },
    }

    let state = reduceVoiceEvent(emptyTranscriptState(), userFinal)
    state = reduceVoiceEvent(state, assistantFinal)
    state = reduceVoiceEvent(state, userFinal)
    state = reduceVoiceEvent(state, assistantFinal)

    expect(state.entries.filter((entry) => entry.role === 'user')).toHaveLength(1)
    expect(state.entries.filter((entry) => entry.role === 'assistant')).toHaveLength(1)
    expect(state.entries.map((entry) => entry.text)).toEqual([
      'Explain open addressing first.',
      'It places collisions in the next empty slot.',
    ])
  })

  it('reconnect transcript seeding is idempotent and does not replay speculative text', () => {
    const turns = [
      {
        turn_id: 'turn-reconnect',
        user_text: 'User question',
        assistant_text: 'Tutor answer',
        finalized_at: '2026-09-13T00:00:00Z',
      },
    ]
    let state = seedFromServerTurns(emptyTranscriptState(), turns)
    state = seedFromServerTurns(state, turns)

    expect(state.entries).toHaveLength(2)
    expect(state.entries.every((entry) => entry.phase === 'authoritative')).toBe(true)
  })

  it('restores ready result text and sources after reconnect', () => {
    const state = mergeVoiceTaskResults(emptyVoiceTaskState(), [
      {
        id: 'result-1',
        voice_session_id: 'voice-1',
        subagent_id: 'task-1',
        requirement_version: 2,
        result_version: 1,
        status: 'ready',
        summary: 'Recovered answer',
        source_count: 1,
        available_at: '2026-09-14T00:00:00Z',
        payload: {
          agent_result: {
            short_answer: 'Recovered answer',
            sources: [{ title: 'Official docs', url: 'https://example.com/docs' }],
          },
        },
      },
    ])

    expect(state.tasks).toEqual([
      expect.objectContaining({
        taskId: 'task-1',
        status: 'ready',
        summary: 'Recovered answer',
        requirementVersion: 2,
        resultAvailable: true,
      }),
    ])
    expect(state.tasks[0].sources).toEqual([
      expect.objectContaining({ title: 'Official docs', domain: 'example.com' }),
    ])
  })

  it('interrupt finalizes only playback-authorized speech and ignores an unplayed draft', () => {
    const turnId = 'turn-interrupt'
    let state = reduceVoiceEvent(emptyTranscriptState(), {
      event_id: 'evt-draft',
      event_seq: 1,
      type: 'assistant.llm.delta',
      turn_id: turnId,
      payload: { text: 'Only half of this answer was spoken' },
    })
    expect(state.entries).toEqual([])

    state = reduceVoiceEvent(state, {
      event_id: 'evt-playback-started',
      event_seq: 2,
      type: 'assistant.sentence.playback_started',
      turn_id: turnId,
      payload: {
        text: 'Only half of this answer was spoken',
        sentence_seq: 1,
      },
    })
    state = reduceVoiceEvent(state, {
      event_id: 'evt-interrupt',
      event_seq: 3,
      type: 'turn.interrupted',
      turn_id: turnId,
      payload: { reason: 'barge_in' },
    })

    const partial = state.entries.find((entry) => entry.role === 'assistant')
    expect(partial).toMatchObject({
      text: 'Only half of this answer was spoken',
      interrupted: true,
      final: true,
      phase: 'speculative',
    })
  })

  it('keeps one latest task state per task id and does not speak the raw result', () => {
    const scope = `task-test-${crypto.randomUUID()}`
    voiceSessionController.open('workspace-task', scope)
    voiceSessionController.upsertTask({
      taskId: 'task-1',
      status: 'queued',
      title: 'Hash table research',
      updatedAt: '2026-09-13T00:00:00Z',
    })
    voiceSessionController.upsertTask({
      taskId: 'task-1',
      status: 'ready',
      title: 'Hash table research',
      summary: 'Raw structured result must not become tutor speech.',
      updatedAt: '2026-09-13T00:00:01Z',
    })

    const snapshot = voiceSessionController.getSnapshot()
    expect(snapshot.tasks.filter((task) => task.taskId === 'task-1')).toEqual([
      expect.objectContaining({ status: 'ready' }),
    ])
    expect(snapshot.transcript).toEqual([])
  })

  it('keeps task state isolated by workspace and restores it on reconnect', () => {
    const sessionScope = `workspace-scope-${crypto.randomUUID()}`
    voiceSessionController.open('workspace-a', sessionScope)
    voiceSessionController.upsertTask({
      taskId: 'task-a',
      status: 'running',
      title: 'A task',
      updatedAt: '2026-09-13T00:00:00Z',
    })

    voiceSessionController.open('workspace-b', sessionScope)
    expect(voiceSessionController.getSnapshot().tasks).toEqual([])

    voiceSessionController.open('workspace-a', sessionScope)
    expect(voiceSessionController.getSnapshot().tasks).toEqual([
      expect.objectContaining({ taskId: 'task-a', status: 'running' }),
    ])
  })

  it('keeps playback delivery separate from task completion and accepts speech lifecycle events', () => {
    let state = emptyVoiceTaskState()
    state = reduceVoiceTaskEvent(state, {
      event_id: 'result-claimed', event_seq: 1, type: 'result.claimed',
      task_id: 'task-playback', result_id: 'result-1', speech_id: 'speech-1',
      status: 'ready', playback_status: 'claimed',
    }).state
    expect(state.tasks[0]).toMatchObject({ status: 'ready', playbackStatus: 'claimed' })
    expect(voiceTaskPlaybackLabel(state.tasks[0])).toBe('已领取，等待播报')

    state = reduceVoiceTaskEvent(state, {
      event_id: 'speech-started', event_seq: 2, type: 'speech.started',
      task_id: 'task-playback', speech_id: 'speech-1',
    }).state
    expect(state.tasks[0].playbackStatus).toBe('speaking')

    state = reduceVoiceTaskEvent(state, {
      event_id: 'speech-interrupted', event_seq: 3, type: 'speech.interrupted',
      task_id: 'task-playback', speech_id: 'speech-1',
    }).state
    expect(state.tasks[0]).toMatchObject({ status: 'ready', playbackStatus: 'interrupted' })
  })
})
