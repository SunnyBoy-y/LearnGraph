/**
 * 移动端外壳跨组件事件总线（纯 window CustomEvent，桌面无副作用）。
 *
 * 聊天画布的左右滑手势在 ChatCanvasPage 内识别，但左侧栏（MobileNavigation
 * 的 Sheet）与右侧图谱抽屉（WorkspaceShell 的 graphDrawerOpen）的状态分属
 * 不同组件，因此用自定义事件解耦：
 *  - 右滑 → 打开左侧栏
 *  - 左滑 → 打开右侧图谱
 *  - 左侧栏已打开时左滑 → 关闭左侧栏（由 MobileNavigation 自身处理）
 */

export const OPEN_SIDEBAR_EVENT = "learngraph:open-sidebar";
export const OPEN_GRAPH_EVENT = "learngraph:open-graph";

export function dispatchOpenSidebar(): void {
  window.dispatchEvent(new CustomEvent(OPEN_SIDEBAR_EVENT));
}

export function dispatchOpenGraph(): void {
  window.dispatchEvent(new CustomEvent(OPEN_GRAPH_EVENT));
}
