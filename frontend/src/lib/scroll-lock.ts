import type { SyntheticEvent } from "react";

/**
 * Radix Dialog 用 react-remove-scroll 锁页面滚动：document 冒泡阶段会
 * preventDefault 掉所有来自 Dialog 子树之外的 wheel / touchmove。Portal 渲染
 * 的浮层（Popover / Select / DropdownMenu 的 Content）全部落在 Dialog 子树
 * 之外，内部滚动列表因此收不到滚轮——滚动条不随滚轮移动。
 *
 * 浮层 Content 在自身冒泡阶段阻止事件继续上抛即可绕开锁。仅在锁确实生效时
 * （body[data-scroll-locked]，react-remove-scroll-bar 写入）才拦截，避免在
 * 普通页面截断 wheel 冒泡、误伤 Radix 自带的「滚轮落在滚动条上」处理。
 */
function bypassDialogScrollLock(event: SyntheticEvent): void {
  if (!document.body.hasAttribute("data-scroll-locked")) return;
  // 指针落在 ScrollArea 轨道/滑块上时不拦截：Radix 在 document 层会把这类
  // wheel 转发给 viewport（preventDefault 不影响该转发），拦掉反而丢能力。
  const target = event.target;
  if (
    target instanceof Element &&
    target.closest('[data-slot="scroll-area-scrollbar"]')
  ) {
    return;
  }
  event.stopPropagation();
}

/**
 * 先执行调用方自己的 wheel / touchmove 处理器，再做滚动锁旁路。用法：
 * `onWheel={withScrollLockBypass(props.onWheel)}`。
 */
export function withScrollLockBypass<E extends SyntheticEvent>(
  handler?: (event: E) => void,
): (event: E) => void {
  return (event: E) => {
    handler?.(event);
    bypassDialogScrollLock(event);
  };
}
