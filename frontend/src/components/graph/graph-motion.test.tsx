import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { graphRelationKey, useGraphRelationFeedback } from './graph-motion'

describe('graph relation feedback', () => {
  it('signals only new source relations, not initial load, polling or selection', () => {
    const nodes = [{ id: 'root', data: { root: true } }, { id: 'a', data: {} }, { id: 'b', data: {} }]
    const edges = [{ id: 'one', source: 'root', target: 'a' }]
    const view = renderHook(({ nodes, edges }) => useGraphRelationFeedback(nodes, edges), { initialProps: { nodes, edges } })
    expect(view.result.current.targets.size).toBe(0)
    view.rerender({ nodes: [...nodes], edges: edges.map(edge => ({ ...edge })) })
    expect(view.result.current.targets.size).toBe(0)
    const nextEdge = { id: 'two', source: 'a', target: 'b' }
    view.rerender({ nodes, edges: [...edges, nextEdge] })
    expect([...view.result.current.targets]).toEqual(['b'])
    expect(view.result.current.relations.has(graphRelationKey(nextEdge))).toBe(true)
    expect(view.result.current.relations.has(graphRelationKey(edges[0]))).toBe(false)
  })

  it('treats another graph as a fresh view rather than newly bound relations', () => {
    const view = renderHook(({ root }) => useGraphRelationFeedback(
      [{ id: root, data: { root: true } }, { id: 'a', data: {} }],
      [{ id: 'edge', source: root, target: 'a' }],
    ), { initialProps: { root: 'one' } })
    view.rerender({ root: 'two' })
    expect(view.result.current.targets.size).toBe(0)
  })
})
