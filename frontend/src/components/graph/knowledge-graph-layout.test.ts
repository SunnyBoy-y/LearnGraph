// 图谱布局纯函数补测（git 旁路：*.test.* 被 .gitignore 忽略，永不入库）
// 覆盖审计发现 F5.1/F5.3 相关：层级构建、环检测、祖先链、深度计算
import { describe, it, expect } from 'vitest'
import {
  buildKnowledgeTreeHierarchy,
  getKnowledgeGraphTreeDepths,
  getTreeActivePath,
  countTreeDescendants,
} from './knowledge-graph-layout'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function node(id: string, opts: { root?: boolean } = {}): any {
  return { id, data: { label: id, root: opts.root ?? false } }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function edge(source: string, target: string, relation = 'relates'): any {
  return { id: `${source}-${target}`, source, target, data: { relation } }
}

describe('buildKnowledgeTreeHierarchy', () => {
  it('过滤无效边（不存在的节点/自环）', () => {
    const nodes = [node('a'), node('b')]
    const edges = [edge('a', 'b'), edge('a', 'missing'), edge('a', 'a')]
    const h = buildKnowledgeTreeHierarchy(nodes, edges)
    expect(h.parentByChild.get('b')).toBe('a')
    expect(h.parentByChild.get('missing')).toBeUndefined()
  })

  it('单节点单父：多边时第一个有效父胜出，不重复挂载', () => {
    const nodes = [node('a'), node('b'), node('c')]
    const edges = [edge('a', 'b'), edge('c', 'b')]
    const h = buildKnowledgeTreeHierarchy(nodes, edges)
    expect(h.parentByChild.get('b')).toBe('a')
  })

  it('环检测：父子反向边不会造成环', () => {
    const nodes = [node('a'), node('b')]
    const edges = [edge('a', 'b'), edge('b', 'a')]
    const h = buildKnowledgeTreeHierarchy(nodes, edges)
    // b 只能有一个父；反向边 a<-b 若再挂 a 会成环，应被跳过
    expect(h.parentByChild.get('b')).toBe('a')
    expect(h.parentByChild.get('a')).toBeUndefined()
  })

  it('contains 关系优先于普通关系构成层级', () => {
    const nodes = [node('root'), node('a'), node('b')]
    const edges = [edge('a', 'b'), edge('root', 'a', 'contains')]
    const h = buildKnowledgeTreeHierarchy(nodes, edges)
    expect(h.parentByChild.get('a')).toBe('root')
  })

  it('显式 root 节点不被挂为子节点', () => {
    const nodes = [node('r', { root: true }), node('a')]
    const edges = [edge('a', 'r')]
    const h = buildKnowledgeTreeHierarchy(nodes, edges)
    expect(h.parentByChild.get('r')).toBeUndefined()
  })
})

describe('getKnowledgeGraphTreeDepths / getTreeActivePath / countTreeDescendants', () => {
  it('深度计算：树层级正确', () => {
    const nodes = [node('a'), node('b'), node('c')]
    const edges = [edge('a', 'b'), edge('b', 'c')]
    const depths = getKnowledgeGraphTreeDepths(nodes, edges)
    expect(depths.get('a')).toBe(0)
    expect(depths.get('b')).toBe(1)
    expect(depths.get('c')).toBe(2)
  })

  it('active path：沿父链上溯', () => {
    const nodes = [node('a'), node('b'), node('c')]
    const edges = [edge('a', 'b'), edge('b', 'c')]
    const h = buildKnowledgeTreeHierarchy(nodes, edges)
    const path = getTreeActivePath('c', h.parentByChild)
    expect(path).toContain('a')
    expect(path).toContain('b')
    expect(path).toContain('c')
  })

  it('descendants 计数：子树规模', () => {
    const nodes = [node('a'), node('b'), node('c'), node('d')]
    const edges = [edge('a', 'b'), edge('b', 'c'), edge('b', 'd')]
    const h = buildKnowledgeTreeHierarchy(nodes, edges)
    expect(countTreeDescendants('b', h.children)).toBe(2)
    expect(countTreeDescendants('c', h.children)).toBe(0)
  })
})
