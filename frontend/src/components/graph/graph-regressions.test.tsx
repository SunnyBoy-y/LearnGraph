import { act, render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { buildKnowledgeTreeHierarchy, buildKnowledgeTreeLayout, buildSpatialLayout, getKnowledgeGraphTreeDepths, FREE_NODE_W, FREE_NODE_H } from './knowledge-graph-layout'
import { KnowledgeGraph, type KnowledgeNode } from './knowledge-graph'
import type { Edge } from '@xyflow/react'

const capture = vi.hoisted(() => ({props: null as any}))
vi.mock('@xyflow/react', () => ({
  ReactFlow: (props: any) => {capture.props = props; return <div data-testid="flow" />},
  Background: () => null, Controls: () => null, MiniMap: () => null, Handle: () => null, BaseEdge: () => null,
  Position: {Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right'},
  BackgroundVariant: {Dots: 'dots'}, applyNodeChanges: (_changes: unknown, nodes: unknown) => nodes,
}))
function graph(count: number, chain = false) {
  const nodes = Array.from({length: count}, (_, index) => ({id: String(index), type: 'knowledge', position: {x: 0, y: 0}, data: {label: `节点${index}`, root: index === 0}})) as KnowledgeNode[]
  const edges: Edge[] = nodes.slice(1).map((node, index) => ({id: `edge-${node.id}`, source: chain ? String(index) : '0', target: node.id, data: {relation: 'contains'}}))
  return {nodes, edges}
}

describe('graph layout regression', () => {
  it.each([12, 24, 60])('keeps %i sibling cards non-overlapping', count => {
    const g = graph(count + 1); const positions = buildSpatialLayout(g.nodes, g.edges)
    for (let i = 0; i <= count; i++) for (let j = i + 1; j <= count; j++) {
      const a = positions[String(i)]!, b = positions[String(j)]!
      expect(Math.abs(a.x - b.x) >= FREE_NODE_W || Math.abs(a.y - b.y) >= FREE_NODE_H).toBe(true)
    }
  })
  it('keeps adjacent dense depth rings separated', () => {
    const g = graph(61); g.edges.forEach((edge, index) => {if (index >= 30) edge.source = '1'})
    const positions = buildSpatialLayout(g.nodes, g.edges)
    for (let i = 0; i < 61; i++) for (let j = i + 1; j < 61; j++) {
      const a = positions[String(i)]!, b = positions[String(j)]!
      expect(Math.abs(a.x - b.x) >= FREE_NODE_W || Math.abs(a.y - b.y) >= FREE_NODE_H).toBe(true)
    }
  })
  it('computes a deep chain without calling recursive geometry', () => {
    const g = graph(10_000, true); const depths = getKnowledgeGraphTreeDepths(g.nodes, g.edges)
    expect(depths.size).toBe(10_000); expect(depths.get('9999')).toBe(9999)
  })
  it('matches existing tree depths including disconnected nodes', () => {
    const g = graph(20, true); g.edges = g.edges.filter(edge => edge.target !== '15')
    expect(Object.fromEntries(getKnowledgeGraphTreeDepths(g.nodes, g.edges))).toEqual(buildKnowledgeTreeLayout(g.nodes, g.edges).depths)
  })
  it('still rejects cycles and multiple parents after hierarchy optimization', () => {
    const g = graph(4, true); g.nodes[0]!.data.root = false
    g.edges.push({id: 'cycle', source: '3', target: '0'}, {id: 'duplicate', source: '0', target: '2'})
    const h = buildKnowledgeTreeHierarchy(g.nodes, g.edges)
    for (const node of g.nodes) {const seen = new Set<string>(); let id: string | undefined = node.id; while (id) {expect(seen.has(id)).toBe(false); seen.add(id); id = h.parentByChild.get(id)}}
  })
})

describe('graph rendering regression', () => {
  it('preserves non-root topology when switching to spatial layout', async () => {
    const g = graph(4, true)
    render(<KnowledgeGraph {...g} layout="spatial" interactive initialFit="focus" />)
    await waitFor(() => expect(capture.props.nodes.find((node: KnowledgeNode) => node.id === '3')?.data.depth).toBe(3))
  })
  it('updates actual zoom percentage within the same LOD tier', async () => {
    const g = graph(4); const ui = render(<KnowledgeGraph {...g} layout="tree" interactive initialFit="focus" showZoomControls />)
    act(() => capture.props.onMove({}, {zoom: 1.8}))
    act(() => capture.props.onMove({}, {zoom: 1.2}))
    await waitFor(() => expect(ui.container.textContent).toContain('120%'))
  })
  it('keeps unchanged node references when selection moves between siblings', async () => {
    const g = graph(5); const ui = render(<KnowledgeGraph {...g} layout="tree" interactive initialFit="focus" selectedId="1" />)
    await waitFor(() => expect(capture.props.nodes.find((node: KnowledgeNode) => node.id === '1')?.selected).toBe(true))
    const previous = capture.props.nodes.find((node: KnowledgeNode) => node.id === '4')
    ui.rerender(<KnowledgeGraph {...g} layout="tree" interactive initialFit="focus" selectedId="2" />)
    await waitFor(() => expect(capture.props.nodes.find((node: KnowledgeNode) => node.id === '2')?.selected).toBe(true))
    expect(capture.props.nodes.find((node: KnowledgeNode) => node.id === '4')).toBe(previous)
  })
  it('keeps the camera still when a learner selects another node', async () => {
    const g = graph(4)
    const flow = {
      getZoom: () => 1,
      getNode: (id: string) => capture.props.nodes.find((node: KnowledgeNode) => node.id === id),
      getNodes: () => capture.props.nodes,
      setCenter: vi.fn(),
      fitView: vi.fn(),
      fitBounds: vi.fn(),
    }
    render(<KnowledgeGraph {...g} layout="tree" interactive initialFit="focus" selectedId="1" />)
    act(() => capture.props.onInit(flow))
    await waitFor(() => expect(flow.setCenter).toHaveBeenCalledTimes(1))
    act(() => capture.props.onNodeClick({}, capture.props.nodes.find((node: KnowledgeNode) => node.id === '2')))
    await waitFor(() => expect(capture.props.nodes.find((node: KnowledgeNode) => node.id === '2')?.selected).toBe(true))
    expect(flow.setCenter).toHaveBeenCalledTimes(1)
  })
  it('reveals a collapsed descendant above the current depth limit before focusing', async () => {
    const g = graph(4, true); let api: any; const onCanvasApi = (value: unknown) => {api = value}
    const flow = {getZoom: () => 1, getViewport: () => ({x: 0, y: 0, zoom: 1}), getNode: (id: string) => capture.props.nodes.find((node: KnowledgeNode) => node.id === id), getNodes: () => capture.props.nodes, setCenter: vi.fn(), fitView: vi.fn(), fitBounds: vi.fn()}
    render(<KnowledgeGraph {...g} layout="tree" interactive initialFit="focus" collapsedIds={['0']} maxDepth={0} onCanvasApi={onCanvasApi} />)
    act(() => capture.props.onInit(flow))
    act(() => api.focusNode('3'))
    await waitFor(() => expect(capture.props.nodes.some((node: KnowledgeNode) => node.id === '3')).toBe(true))
    await waitFor(() => expect(flow.setCenter).toHaveBeenCalled())
  })
})
