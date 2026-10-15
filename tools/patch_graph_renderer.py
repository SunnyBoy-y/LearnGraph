from pathlib import Path

def patch(path, changes):
    p=Path(path); text=p.read_text(encoding='utf-8')
    for old,new in changes:
        if text.count(old)!=1: raise RuntimeError(f'{path}: bad anchor {old[:100]} count={text.count(old)}')
        text=text.replace(old,new,1)
    p.write_text(text,encoding='utf-8',newline=''); print(f'Patched {path}: {len(changes)} changes')

p=Path('frontend/src/components/graph/knowledge-graph-layout.ts'); s=p.read_text(encoding='utf-8')
a=s.index('  const wouldCreateCycle ='); b=s.index('  for (const edge of orderedEdges)',a)
cycle='''  // With at most one parent per node, a forest union detects cycles without
  // walking the whole ancestor chain for every edge.
  const components = new Map(nodes.map(node => [node.id, node.id]));
  const componentOf = (id: string): string => {
    let root = id;
    while (components.get(root) !== root) root = components.get(root)!;
    while (id !== root) { const next = components.get(id)!; components.set(id, root); id = next; }
    return root;
  };
  const wouldCreateCycle = (parentId: string, childId: string) =>
    componentOf(parentId) === componentOf(childId);

'''
a2=s.index('  const heightCache ='); b2=s.index('  const items: TreeLayoutItem[]',a2)
height='''  const heightCache = new Map<string, number>();
  const branchHeight = (nodeId: string): number => {
    const stack: [string, boolean][] = [[nodeId, false]];
    while (stack.length) {
      const [id, visited] = stack.pop()!;
      if (heightCache.has(id)) continue;
      const childIds = collapsedIds.has(id) ? [] : children.get(id) ?? [];
      if (!visited && childIds.length) {
        stack.push([id, true]);
        for (const child of childIds) if (!heightCache.has(child)) stack.push([child, false]);
      } else {
        heightCache.set(id, Math.max(TREE_NODE_H, childIds.reduce((sum, child) => sum + (heightCache.get(child) ?? TREE_NODE_H), 0) + Math.max(0, childIds.length - 1) * TREE_ROW_GAP));
      }
    }
    return heightCache.get(nodeId)!;
  };

'''
patch(str(p),[(s[a:b],cycle),('    parentByChild.set(edge.target, edge.source);','    parentByChild.set(edge.target, edge.source);\n    components.set(componentOf(edge.target), componentOf(edge.source));'),(s[a2:b2],height),('''  const layout = buildKnowledgeTreeLayout(nodes, edges);
  return new Map(Object.entries(layout.depths));''','''  const {children, rootId} = buildKnowledgeTreeHierarchy(nodes, edges);
  const depths = new Map<string, number>();
  if (!rootId) return depths;
  const queue = [rootId];
  depths.set(rootId, 0);
  let maximum = 0;
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index]!;
    const depth = depths.get(id)!;
    maximum = Math.max(maximum, depth);
    for (const child of children.get(id) ?? []) {
      if (depths.has(child)) continue;
      depths.set(child, depth + 1);
      queue.push(child);
    }
  }
  // Match the tree renderer's auxiliary column for disconnected nodes.
  for (const node of nodes) if (!depths.has(node.id)) depths.set(node.id, maximum + 1);
  return depths;'''),('''  const ringGap = 210;
  for (const [depth, ids] of byDepth) {
    if (depth === 0) continue;
    const radius = depth * ringGap;
    const count = ids.length;''','''  const ringGap = 210;
  const cardClearance = Math.hypot(FREE_NODE_W, FREE_NODE_H) + 28;
  let outerRadius = 0;
  for (const [depth, ids] of [...byDepth].sort(([a], [b]) => a - b)) {
    if (depth === 0) continue;
    const count = ids.length;
    const packingRadius = count > 1 ? cardClearance / (2 * Math.sin(Math.PI / count)) : 0;
    const radius = Math.max(depth * ringGap, outerRadius + cardClearance + 24, packingRadius);
    outerRadius = radius + 24;'''),('''    const radius = (maxDepth + 1) * ringGap;''','''    const packingRadius = orphans.length > 1 ? cardClearance / (2 * Math.sin(Math.PI / orphans.length)) : 0;
    const radius = Math.max((maxDepth + 1) * ringGap, outerRadius + cardClearance, packingRadius);''')])

p=Path('frontend/src/components/graph/knowledge-graph.tsx'); s=p.read_text(encoding='utf-8')
a=s.index('  const structureSkeleton = useMemo('); b=s.index('  const focusIsRoot',a)
skeleton='''  const structureSkeleton = useMemo(() => {
    const hierarchy = buildKnowledgeTreeHierarchy(nodes, edges);
    const depths = getKnowledgeGraphTreeDepths(nodes, edges);
    return { ...buildKnowledgeTreeLayout([], []),
      depths: Object.fromEntries(depths),
      maxDepth: depths.size ? Math.max(...depths.values()) : 0,
      parentByChild: hierarchy.parentByChild,
      childrenByParent: hierarchy.children,
    };
  }, [edges, nodes]);

'''
a2=s.index('          setZoom((current) => {',s.index('        onMove=')); b2=s.index('          });',a2)+len('          });')
patch(str(p),[('  buildKnowledgeTreeLayout,','  buildKnowledgeTreeLayout,\n  buildKnowledgeTreeHierarchy,\n  getKnowledgeGraphTreeDepths,'),(s[a:b],skeleton),('''      return buildKnowledgeTreeLayout([], []);
    }
    return buildKnowledgeTreeLayout(nodes, edges,''','''      return structureSkeleton;
    }
    return buildKnowledgeTreeLayout(nodes, edges,'''),('  }, [collapsedIds, edges, highlightIds, nodes, view]);','  }, [collapsedIds, edges, highlightIds, nodes, view, structureSkeleton]);'),('  const [zoom, setZoom] = useState(1);','''  const [zoom, setZoom] = useState(1);
  const [revealedDepth, setRevealedDepth] = useState<number>();
  const [pendingFocus, setPendingFocus] = useState<{id: string; neighbors?: boolean} | null>(null);
  useEffect(() => setRevealedDepth(undefined), [maxDepth]);'''),('if (maxDepth !== undefined && depth > maxDepth) return false;','if (maxDepth !== undefined && depth > Math.max(maxDepth, revealedDepth ?? maxDepth)) return false;'),('''    collapsedIds,
    maxDepth,
    nodes,''','''    collapsedIds,
    maxDepth,
    revealedDepth,
    nodes,'''),('''      const depth = structuredTree.depths[node.id] ?? 0;''','''      const depth = structureSkeleton.depths[node.id] ?? 0;'''),('''    structuredTree.depths,
    structuredTree.parentByChild,
    view,''','''    structureSkeleton.depths,
    structuredTree.parentByChild,
    view,'''),('  const laidOutNodes = useMemo(() => {','  const laidOutResult = useMemo(() => {'),('    return visibleSourceNodes.map((node) => {','    const built = visibleSourceNodes.map((node) => {'),('''        return candidate;
      });
    },''','''        return candidate;
      });
      return {nodes: built, cache: next};
    },'''),('''  const [renderedNodes, setRenderedNodes] =''','''  const laidOutNodes = laidOutResult.nodes;
  useEffect(() => { laidOutNodesRef.current = laidOutResult.cache; }, [laidOutResult]);

  const [renderedNodes, setRenderedNodes] ='''),('''            onOpenExplore: (nodeId: string) => {''','''            onOpenExplore: onOpenExplore || node.data.onOpenExplore ? (nodeId: string) => {'''),('''              else node.data.onOpenExplore?.(nodeId);
            },''','''              else node.data.onOpenExplore?.(nodeId);
            } : undefined,'''),('''            onStudy: (nodeId: string) => {''','''            onStudy: onStudy || node.data.onStudy ? (nodeId: string) => {'''),('''              else node.data.onStudy?.(nodeId);
            },''','''              else node.data.onStudy?.(nodeId);
            } : undefined,'''),('{tree && !isRoot ? (\n        <button\n          className="knowledge-node__cta nodrag"','{tree && !isRoot && data.onStudy ? (\n        <button\n          className="knowledge-node__cta nodrag"'),('onOpen={() => data.onOpenExplore?.(id)}','onOpen={data.onOpenExplore ? () => data.onOpenExplore?.(id) : undefined}'),(s[a2:b2],'          setZoom(current => Math.abs(current - viewport.zoom) < 0.001 ? current : viewport.zoom);'),('''        frameNode(nodeId, options);''','''        const depth = structureSkeleton.depths[nodeId];
        if (depth !== undefined) setRevealedDepth(current => Math.max(current ?? 0, depth));
        const next = new Set(collapsedIds);
        let parent = structureSkeleton.parentByChild.get(nodeId);
        while (parent) { next.delete(parent); parent = structureSkeleton.parentByChild.get(parent); }
        if (next.size !== collapsedIds.size) { setInternalCollapsed(next); onCollapsedIdsChange?.([...next]); }
        setPendingFocus({id: nodeId, ...options});'''),('''  }, [fitAll, frameNode, onCanvasApi]);''','''  }, [fitAll, onCanvasApi, structureSkeleton, collapsedIds, onCollapsedIdsChange]);

  useEffect(() => {
    if (!pendingFocus || !visibleIds.has(pendingFocus.id) || !flowInstance) return;
    const frame = requestAnimationFrame(() => {
      frameNode(pendingFocus.id, pendingFocus);
      setPendingFocus(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [pendingFocus, visibleIds, renderedNodes, frameNode, flowInstance]);''')])
