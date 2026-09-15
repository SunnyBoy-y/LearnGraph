/**
 * 移动端抽屉手势控制器（左：导航抽屉；右：图谱抽屉）。
 *
 * 现状与职责：
 *  - 左侧导航抽屉与右侧图谱抽屉都是常驻 DOM，位移由 CSS 变量
 *    `--lg-left-drawer-progress` / `--lg-right-drawer-progress`（0 = 收起，
 *    1 = 展开，写在使用者元素自身而非根节点）驱动；抽屉的开合状态仍由 React
 *    持有，本模块只搬运「手指进度」。
 *  - 全屏任意位置起手都能拖拽：监听装在 window 上（capture 阶段），
 *    手指滑多少就拉出多少，松手时进度 > 0.5 视为展开，否则收起。
 *  - 不做逐帧 React 更新：touchmove 只写 CSS 变量，React 仅在拖拽开始/结束时
 *    被唤醒一次（用于按需挂载抽屉内容、保持可见性）。松手时的状态提交会推到
 *    下一个宏任务，避免整棵工作区的重渲染顶住松手那一帧。
 *  - touchstart 只登记坐标，方向锁定之后才做排除判定（祖先样式查询会强制布局，
 *    放在起手阶段会让手指动起来的前几帧变慢）。
 *
 * 不判定条件（方向锁定、确认是横向抽屉手势后，起手点满足任一条即放弃）：
 *  - 祖先带 `data-drawer-swipe="ignore"`（显式豁免；`"allow"` 则显式放行并终止向上查找）；
 *  - 祖先横向可滚动（overflow-x: auto/scroll 且确实溢出）：表格、代码块、Tab 条、
 *    热力图、概念分支条等控件内左右滑动不该唤起侧栏；
 *  - 祖先 touch-action 为 none / pan-x：画布拖拽、滑块这类控件自己处置横向手势；
 *  - 表单控件与可编辑区域（input/textarea/select/contenteditable 等）。
 */

import { useEffect, useRef, useSyncExternalStore } from "react";

export type DrawerSide = "left" | "right";

/** 起手点所在表面：两侧抽屉面板/遮罩，或默认的页面画布。 */
export type DrawerSurface = "nav" | "graph" | "canvas";

const PROGRESS_VAR: Record<DrawerSide, string> = {
  left: "--lg-left-drawer-progress",
  right: "--lg-right-drawer-progress",
};

/**
 * 进度变量写在使用者自己身上（抽屉 + 它的遮罩），不写 `<html>`：
 * 自定义属性是继承属性，写在根节点上意味着每一帧都让整篇文档的样式失效，
 * 手机上的重页面（几百上千个节点）会因此掉帧；写在这两个盒子上只影响它们。
 */
const PROGRESS_TARGETS: Record<DrawerSide, string[]> = {
  left: [".mobile-nav-drawer", ".mobile-nav-drawer-overlay"],
  right: [".context-rail", ".graph-drawer-backdrop"],
};

const progressTargetCache: Record<DrawerSide, HTMLElement[]> = {
  left: [],
  right: [],
};

function progressTargets(side: DrawerSide): HTMLElement[] {
  const cached = progressTargetCache[side];
  if (cached.length && cached.every((node) => node.isConnected)) return cached;
  const found: HTMLElement[] = [];
  for (const selector of PROGRESS_TARGETS[side]) {
    const node = document.querySelector(selector);
    if (node instanceof HTMLElement) found.push(node);
  }
  progressTargetCache[side] = found;
  return found;
}

/** 量不到抽屉实际宽度时的兜底（左：导航 286px；右：min(420px, 94vw)）。 */
function fallbackDrawerWidth(side: DrawerSide): number {
  return side === "left"
    ? 286
    : Math.min(420, Math.max(240, Math.round(window.innerWidth * 0.94)));
}

/** 抽屉元素选择器，用于量取跟手行程。 */
const DRAWER_SELECTOR: Record<DrawerSide, string> = {
  left: ".mobile-nav-drawer",
  right: ".context-rail",
};

/** 起手后横向位移超过该值才认定是抽屉拖拽（px）。 */
const LOCK_DISTANCE = 8;
/** 方向锁：|dx| 必须明显大于 |dy|，否则让位给纵向滚动。 */
const LOCK_SLOPE = 1.2;
/** 松手判定：拉出超过一半即展开。 */
const COMMIT_RATIO = 0.5;
/** 抽屉打开时锁页面滚动用的根节点类名（区别于 Radix 的 data-scroll-locked）。 */
const SCROLL_LOCK_CLASS = "has-mobile-drawer";
const DRAGGING_ATTR = "data-drawer-dragging";
const SURFACE_ATTR = "data-drawer-surface";
const SWIPE_HOOK_ATTR = "data-drawer-swipe";

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/** 写入抽屉进度（0 = 完全收起，1 = 完全展开）。 */
export function setDrawerProgress(side: DrawerSide, progress: number): void {
  const value = String(clamp01(progress));
  const targets = progressTargets(side);
  if (!targets.length) {
    // 抽屉还没挂载（例如别的路由）：退回根节点，保证读得到同一个值。
    document.documentElement.style.setProperty(PROGRESS_VAR[side], value);
    return;
  }
  for (const node of targets) node.style.setProperty(PROGRESS_VAR[side], value);
}

/** 读取当前抽屉进度（跟手过程中即为手指位置）。 */
export function readDrawerProgress(side: DrawerSide): number {
  const [node] = progressTargets(side);
  const raw = (
    node
      ? node.style.getPropertyValue(PROGRESS_VAR[side])
      : document.documentElement.style.getPropertyValue(PROGRESS_VAR[side])
  ).trim();
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? clamp01(value) : 0;
}

/** 抽屉打开时锁住页面滚动（遮罩已挡住交互，避免背景跟着滚）。 */
export function setDrawerScrollLock(locked: boolean): void {
  document.documentElement.classList.toggle(SCROLL_LOCK_CLASS, locked);
}

/* -------------------------------------------------------------------------- *
 * 拖拽阶段：React 只需要知道「哪一侧正被手指拖着」——用于按需挂载抽屉内容、
 * 在收起动画播完前保持内容挂载。逐帧进度不进 React，只走 CSS 变量。
 * -------------------------------------------------------------------------- */

let draggingSide: DrawerSide | null = null;
const dragListeners = new Set<() => void>();

function emitDragChange(): void {
  for (const listener of dragListeners) listener();
}

function subscribeDrawerDrag(listener: () => void): () => void {
  dragListeners.add(listener);
  return () => {
    dragListeners.delete(listener);
  };
}

function readDraggingDrawer(): DrawerSide | null {
  return draggingSide;
}

/** 该侧抽屉是否正被手指拖拽。 */
export function useDraggingDrawer(side: DrawerSide): boolean {
  return (
    useSyncExternalStore(
      subscribeDrawerDrag,
      readDraggingDrawer,
      readDraggingDrawer,
    ) === side
  );
}

function beginDrawerDrag(side: DrawerSide): void {
  if (draggingSide === side) return;
  draggingSide = side;
  document.documentElement.setAttribute(DRAGGING_ATTR, side);
  emitDragChange();
}

function endDrawerDrag(): void {
  if (!draggingSide) return;
  draggingSide = null;
  document.documentElement.removeAttribute(DRAGGING_ATTR);
  emitDragChange();
}

/* -------------------------------------------------------------------------- *
 * 不判定条件
 * -------------------------------------------------------------------------- */

const INTERACTIVE_TAGS = new Set([
  "INPUT",
  "TEXTAREA",
  "SELECT",
  "OPTION",
  "VIDEO",
  "AUDIO",
]);

const INTERACTIVE_ROLES = new Set([
  "slider",
  "spinbutton",
  "textbox",
  "searchbox",
]);

/**
 * 永远不识别抽屉手势的区域：表格（含 streamdown 的表格外框）内部左右滑动
 * 另有含义，即使当前没有实际横向溢出也不唤起侧栏。
 */
const BLOCKING_SELECTOR =
  'table,[role="table"],[role="grid"],[data-streamdown="table-wrapper"]';

const HORIZONTAL_SCROLL_VALUES = new Set(["auto", "scroll", "overlay"]);

/** 横向溢出的阈值：小于 8px 认为是布局取整噪声。 */
const HORIZONTAL_OVERFLOW_SLOP = 8;

function blocksDrawerSwipe(element: Element): boolean {
  if (INTERACTIVE_TAGS.has(element.tagName)) return true;
  if (element.matches(BLOCKING_SELECTOR)) return true;
  const editable = element.getAttribute("contenteditable");
  if (editable !== null && editable !== "false") return true;
  const role = element.getAttribute("role");
  if (role && INTERACTIVE_ROLES.has(role)) return true;
  const style = window.getComputedStyle(element);
  const touchAction = style.touchAction ?? "";
  if (touchAction === "none" || touchAction.includes("pan-x")) return true;
  // 先看 overflow-x 再看是否真的溢出：读 scrollWidth/clientWidth 会强制布局，
  // 放在后面可以让绝大多数（overflow-x: visible）祖先完全不触发重排。
  if (!HORIZONTAL_SCROLL_VALUES.has(style.overflowX)) return false;
  return element.scrollWidth - element.clientWidth > HORIZONTAL_OVERFLOW_SLOP;
}

/** 起手点是否落在「不该唤起侧栏」的区域里。 */
export function shouldIgnoreDrawerSwipe(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  let node: Element | null = target;
  while (node && node !== document.documentElement) {
    const hook = node.getAttribute(SWIPE_HOOK_ATTR);
    if (hook === "allow") return false;
    if (hook === "ignore") return true;
    if (blocksDrawerSwipe(node)) return true;
    node = node.parentElement;
  }
  return false;
}

/* -------------------------------------------------------------------------- *
 * 全屏手势
 * -------------------------------------------------------------------------- */

type DrawerDragResolution = { side: DrawerSide; base: number };

type GestureTrack = {
  identifier: number;
  startX: number;
  startY: number;
  /** 起手元素：方向锁定后才用它判排除（touchstart 阶段一律不做样式查询）。 */
  target: Element;
  axis: "pending" | "horizontal" | "dead";
  side: DrawerSide;
  base: number;
  width: number;
  progress: number;
};

export type MobileDrawerDragOptions = {
  /** 左侧导航抽屉是否可用（当前恒为 true，保留给后续按页面收敛）。 */
  leftAvailable: boolean;
  /** 右侧图谱抽屉是否可用（只有存在 context rail 的页面才可拖出）。 */
  rightAvailable: boolean;
  /** 松手吸附结果：open=true 展开，false 收起。 */
  onCommit: (side: DrawerSide, open: boolean) => void;
};

/* -------------------------------------------------------------------------- *
 * 松手提交：React 的开合状态变化会重渲染整棵工作区（含当前页面），首页图表 /
 * 会话列表这一下要 100~150ms。吸附补间跑在合成器上，把这笔开销推到补间开始
 * 之后（下一个宏任务）就看不见了；同步做则会顶住松手那一帧 —— 表现就是
 * 「手指抬起来后抽屉先僵住，再补间」。
 * -------------------------------------------------------------------------- */

type PendingCommit = {
  side: DrawerSide;
  open: boolean;
  timer: number;
  commit: (side: DrawerSide, open: boolean) => void;
};

let pendingCommit: PendingCommit | null = null;

function takePendingCommit(): PendingCommit | null {
  const pending = pendingCommit;
  pendingCommit = null;
  if (pending) window.clearTimeout(pending.timer);
  return pending;
}

/** 立刻执行尚未落地的提交（新手势起手时保证状态顺序）。 */
function flushPendingCommit(): void {
  const pending = takePendingCommit();
  if (pending) pending.commit(pending.side, pending.open);
}

function cancelPendingCommit(): void {
  void takePendingCommit();
}

/** 把提交排到下一个宏任务，并保证同一时刻只有一个待执行提交。 */
function scheduleCommit(
  side: DrawerSide,
  open: boolean,
  commit: (side: DrawerSide, open: boolean) => void,
): void {
  flushPendingCommit();
  const timer = window.setTimeout(() => {
    const pending = takePendingCommit();
    if (pending) pending.commit(pending.side, pending.open);
  }, 0);
  pendingCommit = { side, open, timer, commit };
}

/**
 * 在 window 上装一次抽屉拖拽手势。
 *
 * 手势期间只在同一帧里改 CSS 变量；锁定横向后对可取消的 touchmove 调
 * preventDefault，避免页面跟着一起纵向滚。
 */
export function useMobileDrawerDrag(options: MobileDrawerDragOptions): void {
  const latest = useRef(options);
  useEffect(() => {
    latest.current = options;
  });

  useEffect(() => {
    let track: GestureTrack | null = null;

    const findTouch = (list: TouchList, identifier: number) => {
      for (let index = 0; index < list.length; index += 1) {
        const item = list[index];
        if (item && item.identifier === identifier) return item;
      }
      return null;
    };

    const measureWidth = (side: DrawerSide) => {
      const element = document.querySelector(DRAWER_SELECTOR[side]);
      const width = element ? element.getBoundingClientRect().width : 0;
      return width > 40 ? width : fallbackDrawerWidth(side);
    };

    const resolveDrag = (
      surface: DrawerSurface,
      dx: number,
    ): DrawerDragResolution | null => {
      const { leftAvailable, rightAvailable } = latest.current;
      // 面板 / 遮罩上的拖拽只用于把已拉出的抽屉推回去（base = 1）。
      if (surface === "nav") return leftAvailable ? { side: "left", base: 1 } : null;
      if (surface === "graph") {
        return rightAvailable ? { side: "right", base: 1 } : null;
      }
      // 画布上按方向决定唤起哪一侧，且只负责「拉出」。
      if (dx > 0) return leftAvailable ? { side: "left", base: 0 } : null;
      return rightAvailable ? { side: "right", base: 0 } : null;
    };

    const onTouchStart = (event: TouchEvent) => {
      // 双指（缩放）不参与抽屉手势。
      if (event.touches.length !== 1) return;
      if (track) {
        // 同一手势里的额外手指：沿用原手势；否则是上一次没收到的结束事件，重置。
        if (findTouch(event.touches, track.identifier)) return;
        track = null;
      }
      const target = event.target;
      const touch = event.touches[0];
      if (!touch || !(target instanceof Element)) return;
      // 起手只登记坐标，不做任何样式/排除查询：touchstart 阶段越干净，手指动起来
      // 的第一帧就越顺。排除判定放到方向锁定那一刻（见 onTouchMove）。
      flushPendingCommit();
      track = {
        identifier: touch.identifier,
        startX: touch.clientX,
        startY: touch.clientY,
        target,
        axis: "pending",
        side: "left",
        base: 0,
        width: 0,
        progress: 0,
      };
    };

    /** 方向锁定那一刻才做的排除判定（只在真的开始拖时才付这笔查询成本）。 */
    const shouldSkipGesture = (target: Element): boolean => {
      // 其它 Radix 弹层（设置、活动抽屉、附件诊断…）打开时让位。
      if (document.body.hasAttribute("data-scroll-locked")) return true;
      // 正在调整已选中的文本（拖选 / 拖拽选择手柄）时不抢手势。
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed) return true;
      return shouldIgnoreDrawerSwipe(target);
    };

    const onTouchMove = (event: TouchEvent) => {
      if (!track) return;
      const touch = findTouch(event.touches, track.identifier);
      if (!touch) return;
      const dx = touch.clientX - track.startX;
      const dy = touch.clientY - track.startY;

      if (track.axis === "pending") {
        // 纵向先动：让位给页面滚动，本次手势作废。
        if (
          Math.abs(dy) >= LOCK_DISTANCE &&
          Math.abs(dy) >= Math.abs(dx)
        ) {
          track.axis = "dead";
          return;
        }
        if (Math.abs(dx) < LOCK_DISTANCE) return;
        if (Math.abs(dx) < Math.abs(dy) * LOCK_SLOPE) return;
        if (shouldSkipGesture(track.target)) {
          // 判定为不抢手势：直接作废，也不 preventDefault，让原生滚动/拖选照常。
          track.axis = "dead";
          return;
        }
        const surfaceElement = track.target.closest(`[${SURFACE_ATTR}]`);
        const surface = (surfaceElement?.getAttribute(SURFACE_ATTR) ??
          "canvas") as DrawerSurface;
        const resolved = resolveDrag(surface, dx);
        if (!resolved) {
          track.axis = "dead";
          return;
        }
        track.axis = "horizontal";
        track.side = resolved.side;
        track.base = resolved.base;
        track.width = measureWidth(resolved.side);
        track.progress = resolved.base;
        setDrawerProgress(track.side, resolved.base);
        beginDrawerDrag(track.side);
      }
      if (track.axis !== "horizontal") return;

      // 手指滑多少就拉出多少：左抽屉以 +dx 拉开，右抽屉以 -dx 拉开。
      const travel = track.side === "left" ? dx : -dx;
      const progress = clamp01(track.base + travel / track.width);
      track.progress = progress;
      setDrawerProgress(track.side, progress);
      if (event.cancelable) event.preventDefault();
    };

    const onTouchFinish = (event: TouchEvent) => {
      if (!track) return;
      // 结束的不是起手那根手指：手势继续。
      if (findTouch(event.touches, track.identifier)) return;
      const gesture = track;
      track = null;
      if (gesture.axis !== "horizontal") return;
      const open = gesture.progress > COMMIT_RATIO;
      // 先解除「跟手免过渡」，再落到吸附位置：CSS 会从当前位置补间过去。
      endDrawerDrag();
      setDrawerProgress(gesture.side, open ? 1 : 0);
      // 状态提交推到下一个宏任务：补间期间做重渲染，松手那一帧才不卡。
      scheduleCommit(gesture.side, open, (side, nextOpen) =>
        latest.current.onCommit(side, nextOpen),
      );
    };

    window.addEventListener("touchstart", onTouchStart, {
      capture: true,
      passive: true,
    });
    window.addEventListener("touchmove", onTouchMove, {
      capture: true,
      passive: false,
    });
    window.addEventListener("touchend", onTouchFinish, {
      capture: true,
      passive: true,
    });
    window.addEventListener("touchcancel", onTouchFinish, {
      capture: true,
      passive: true,
    });
    return () => {
      window.removeEventListener("touchstart", onTouchStart, {
        capture: true,
      });
      window.removeEventListener("touchmove", onTouchMove, {
        capture: true,
      });
      window.removeEventListener("touchend", onTouchFinish, {
        capture: true,
      });
      window.removeEventListener("touchcancel", onTouchFinish, {
        capture: true,
      });
      endDrawerDrag();
      cancelPendingCommit();
    };
  }, []);
}
