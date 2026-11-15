import { describe, expect, it } from 'vitest'
import { graphNeighbourhood, graphRailPositions } from './graph-rail-model'
import type { Graph } from '@/types/graphs'

const graph = {
  id: 'g', nodes: ['parent', 'selected', 'child', 'unrelated'].map(id => ({ id, label: id })),
  edges: [{ source_node_id: 'parent', target_node_id: 'selected' }, { source_node_id: 'selected', target_node_id: 'child' }],
} as Graph

describe('readable graph sidebar neighbourhood', () => {
  it('places parent above focus and children below, within readable distance', () => {
    const positions = graphRailPositions(graph, 'selected')!
    expect(positions.selected.y).toBe(0)
    expect(positions.parent.y).toBe(-200)
    expect(positions.child.y).toBe(215)
  })
  it('shows parents/current node/children rather than the entire map', () => {
    const result = graphNeighbourhood(graph, 'selected')
    expect(result.graph.nodes.map(node => node.id)).toEqual(['parent', 'selected', 'child'])
    expect(result.graph.edges).toHaveLength(2)
    expect(graph.nodes).toHaveLength(4)
  })
  it('keeps a hundred-node graph bounded and exposes remaining neighbours', () => {
    const large = { ...graph, nodes: ['root', ...Array.from({ length: 100 }, (_, i) => `n${i}`)].map(id => ({ id, label: id })),
      edges: Array.from({ length: 100 }, (_, i) => ({ source_node_id: 'root', target_node_id: `n${i}` })) } as Graph
    expect(graphNeighbourhood(large, 'root').graph.nodes).toHaveLength(13)
    expect(graphNeighbourhood(large, 'root').hiddenCount).toBe(88)
    expect(graphNeighbourhood(large, 'root', true).graph.nodes).toHaveLength(101)
    expect(graphNeighbourhood(large, 'n99').graph.nodes.map(node => node.id)).toEqual(['root', 'n99'])
  })
  it('retains an isolated searched node', () => {
    expect(graphNeighbourhood(graph, 'unrelated').graph.nodes.map(node => node.id)).toEqual(['unrelated'])
  })
})
