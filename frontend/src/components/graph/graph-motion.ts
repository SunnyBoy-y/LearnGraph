import { useMemo } from 'react'
import { useContentHighlights } from '@/hooks/use-content-highlights'

type Relation = { id: string; source: string; target: string }
type GraphNode = { id: string; data: { root?: boolean } }

export function graphRelationKey(edge: Relation) {
  return `${edge.source}\u0000${edge.target}`
}

export function useGraphRelationFeedback(nodes: GraphNode[], edges: Relation[]) {
  // Layout/selection/collapse operate on derived nodes. Only source relations
  // enter this snapshot, so those interactions never masquerade as new facts.
  const scope = nodes.filter(node => node.data.root).map(node => node.id).sort().join(',') || nodes[0]?.id || ''
  const content = useMemo(() => new Map(edges.map(edge => [graphRelationKey(edge), edge.target])), [edges])
  const changed = useContentHighlights(scope, content, nodes.length > 0)
  const targets = useMemo(() => new Set(edges.filter(edge => changed.has(graphRelationKey(edge))).map(edge => edge.target)), [edges, changed])
  return { relations: changed, targets }
}
