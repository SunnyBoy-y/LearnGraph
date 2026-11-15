import type { Graph } from '@/types/graphs'

/** A sidebar is a readable neighbourhood, not a miniature whole-graph poster. */
export function graphNeighbourhood(graph: Graph, nodeId?: string, showAll = false, limit = 12) {
  if (!nodeId) return { graph, hiddenCount: 0 }
  const adjacent = new Set<string>()
  for (const edge of graph.edges) {
    if (edge.source_node_id === nodeId) adjacent.add(edge.target_node_id)
    if (edge.target_node_id === nodeId) adjacent.add(edge.source_node_id)
  }
  const all = graph.nodes.filter(node => adjacent.has(node.id))
  // Keep prerequisites/parents visible before wide lists of children.
  const parents = new Set(graph.edges.filter(edge => edge.target_node_id === nodeId).map(edge => edge.source_node_id))
  all.sort((a, b) => Number(parents.has(b.id)) - Number(parents.has(a.id)))
  const visible = showAll ? all : all.slice(0, limit)
  const ids = new Set([nodeId, ...visible.map(node => node.id)])
  return {
    graph: { ...graph, nodes: graph.nodes.filter(node => ids.has(node.id)),
      edges: graph.edges.filter(edge => ids.has(edge.source_node_id) && ids.has(edge.target_node_id)) },
    hiddenCount: all.length - visible.length,
  }
}

/** Keep a sidebar's parent, selected node and children on one readable spine. */
export function graphRailPositions(graph: Graph, focusId?: string) {
  if (!focusId) return undefined
  const parents = new Set(graph.edges.filter(edge => edge.target_node_id === focusId).map(edge => edge.source_node_id))
  const children = graph.nodes.filter(node => node.id !== focusId && !parents.has(node.id))
  const parentNodes = graph.nodes.filter(node => parents.has(node.id))
  return Object.fromEntries(graph.nodes.map(node => {
    const isRoot = node.node_type === 'root' || !graph.edges.some(edge => edge.target_node_id === node.id)
    const y = node.id === focusId ? 0 : parents.has(node.id)
      ? -(parentNodes.indexOf(node) + 1) * 200 : (children.indexOf(node) + 1) * 215
    return [node.id, { x: isRoot ? -32 : -118, y }]
  }))
}
