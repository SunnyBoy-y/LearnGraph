import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  readDrawerProgress,
  setDrawerLayerOpen,
  setDrawerProgress,
  shouldIgnoreDrawerSwipe,
  useMobileDrawerDrag,
  type DrawerSide,
} from "./mobile-shell";

type TouchLike = { identifier: number; clientX: number; clientY: number };

function touch(
  type: "touchstart" | "touchmove" | "touchend" | "touchcancel",
  target: EventTarget,
  touches: TouchLike[],
  changedTouches: TouchLike[] = touches,
) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "touches", { value: touches });
  Object.defineProperty(event, "changedTouches", { value: changedTouches });
  target.dispatchEvent(event);
  return event;
}

function makeElement(
  tag: string,
  parent: Element,
  props: Record<string, string> = {},
) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(props)) {
    element.setAttribute(name, value);
  }
  parent.appendChild(element);
  return element;
}

function defineBox(
  element: HTMLElement,
  geometry: { clientWidth: number; scrollWidth: number; width?: number },
) {
  Object.defineProperty(element, "clientWidth", {
    configurable: true,
    value: geometry.clientWidth,
  });
  Object.defineProperty(element, "scrollWidth", {
    configurable: true,
    value: geometry.scrollWidth,
  });
  Object.defineProperty(element, "getBoundingClientRect", {
    configurable: true,
    value: () => ({ width: geometry.width ?? geometry.clientWidth }) as DOMRect,
  });
}

function resetShellState() {
  document.documentElement.removeAttribute("data-drawer-dragging");
  document.documentElement.classList.remove("has-mobile-drawer");
  setDrawerLayerOpen("left", false);
  setDrawerLayerOpen("right", false);
  document.documentElement.style.removeProperty("--lg-left-drawer-progress");
  document.documentElement.style.removeProperty("--lg-right-drawer-progress");
}

/**
 * 松手时的状态提交被刻意推到下一个宏任务（整棵工作区重渲染不能顶住松手那一
 * 帧），断言提交结果前先把它放行。
 */
async function flushDeferredCommit() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

afterEach(() => {
  resetShellState();
  document.body.innerHTML = "";
});

describe("shouldIgnoreDrawerSwipe 不判定条件", () => {
  it("普通文本区域允许抽屉手势", () => {
    const host = makeElement("div", document.body);
    const paragraph = makeElement("p", host, { "data-drawer-swipe": "allow" });
    expect(shouldIgnoreDrawerSwipe(paragraph)).toBe(false);
  });

  it("表格内不唤起侧栏（即使没有横向溢出）", () => {
    const wrapper = makeElement("div", document.body, {
      "data-streamdown": "table-wrapper",
    });
    const table = makeElement("table", wrapper);
    const tbody = makeElement("tbody", table);
    const row = makeElement("tr", tbody);
    const cell = makeElement("td", row);
    expect(shouldIgnoreDrawerSwipe(cell)).toBe(true);
  });

  it("横向可滚动容器内不唤起侧栏", () => {
    const host = makeElement("div", document.body);
    const scroller = makeElement("div", host, { style: "overflow-x: auto" });
    defineBox(scroller, { clientWidth: 200, scrollWidth: 640 });
    const child = makeElement("span", scroller);
    expect(shouldIgnoreDrawerSwipe(child)).toBe(true);
  });

  it("纵向滚动容器不拦手势", () => {
    const host = makeElement("div", document.body);
    const scroller = makeElement("div", host, { style: "overflow-y: auto" });
    defineBox(scroller, { clientWidth: 200, scrollWidth: 200 });
    expect(shouldIgnoreDrawerSwipe(makeElement("span", scroller))).toBe(false);
  });

  it("标记了 data-drawer-swipe=ignore 的控件被豁免", () => {
    const host = makeElement("div", document.body);
    const rail = makeElement("aside", host, { "data-drawer-swipe": "ignore" });
    expect(shouldIgnoreDrawerSwipe(makeElement("button", rail))).toBe(true);
  });

  it("allow 标记优先于祖先的横向滚动判定", () => {
    const host = makeElement("div", document.body);
    const scroller = makeElement("div", host, {
      "data-drawer-swipe": "allow",
      style: "overflow-x: auto",
    });
    defineBox(scroller, { clientWidth: 390, scrollWidth: 1200 });
    expect(shouldIgnoreDrawerSwipe(scroller)).toBe(false);
  });

  it("表单控件内不唤起侧栏", () => {
    const host = makeElement("div", document.body);
    const input = makeElement("input", host);
    expect(shouldIgnoreDrawerSwipe(input)).toBe(true);
  });
});

describe("抽屉跟手拖拽", () => {
  function renderHarness() {
    const commits: Array<{ side: DrawerSide; open: boolean }> = [];
    function Harness() {
      useMobileDrawerDrag({
        leftAvailable: true,
        onCommit: (side, open) => commits.push({ side, open }),
        rightAvailable: true,
      });
      return (
        <>
          <div className="mobile-nav-drawer" data-drawer-surface="nav" />
          <div className="context-rail" data-drawer-swipe="ignore" />
          <div
            data-conversation-scroll
            data-drawer-swipe="allow"
            data-testid="canvas"
          >
            <p data-testid="text">正文</p>
          </div>
        </>
      );
    }
    const view = render(<Harness />);
    const leftDrawer = view.container.querySelector<HTMLElement>(".mobile-nav-drawer");
    const rightDrawer = view.container.querySelector<HTMLElement>(".context-rail");
    if (!leftDrawer || !rightDrawer) throw new Error("drawer harness missing");
    defineBox(leftDrawer, { clientWidth: 300, scrollWidth: 300 });
    defineBox(rightDrawer, { clientWidth: 400, scrollWidth: 400 });
    return { commits, view };
  }

  it("手指滑多少就拉出多少，正好一半不算超过", async () => {
    const { commits, view } = renderHarness();
    const text = view.getByTestId("text");

    act(() => {
      touch("touchstart", text, [{ identifier: 1, clientX: 20, clientY: 300 }]);
      touch("touchmove", text, [{ identifier: 1, clientX: 170, clientY: 302 }]);
    });
    // 150 / 300 = 0.5：跟手进度就是手指位置
    expect(readDrawerProgress("left")).toBeCloseTo(0.5, 5);
    expect(document.documentElement.getAttribute("data-drawer-dragging")).toBe("left");

    act(() => {
      touch("touchend", text, [], [{ identifier: 1, clientX: 170, clientY: 302 }]);
    });
    expect(readDrawerProgress("left")).toBe(0);
    expect(document.documentElement.hasAttribute("data-drawer-dragging")).toBe(false);
    // 提交刻意不同步：松手这一帧不做整棵工作区的重渲染。
    expect(commits).toEqual([]);
    await flushDeferredCommit();
    expect(commits).toEqual([{ side: "left", open: false }]);
  });

  it("进度变量写在抽屉元素自身而非根节点", () => {
    const { commits, view } = renderHarness();
    const text = view.getByTestId("text");
    const drawer = view.container.querySelector<HTMLElement>(".mobile-nav-drawer");
    if (!drawer) throw new Error("drawer missing");

    act(() => {
      touch("touchstart", text, [{ identifier: 9, clientX: 20, clientY: 300 }]);
      touch("touchmove", text, [{ identifier: 9, clientX: 120, clientY: 300 }]);
    });
    expect(drawer.style.getPropertyValue("--lg-left-drawer-progress")).toBe(
      String(100 / 300),
    );
    // 根节点上不再有进度变量：继承型自定义属性写在根上会让整篇文档每帧失效。
    expect(
      document.documentElement.style.getPropertyValue("--lg-left-drawer-progress"),
    ).toBe("");
    act(() => {
      touch("touchend", text, [], [{ identifier: 9, clientX: 120, clientY: 300 }]);
    });
    expect(drawer.style.getPropertyValue("--lg-left-drawer-progress")).toBe("0");
    expect(commits).toEqual([]);
  });

  it("超过一半松手即展开；左滑拉出右侧图谱抽屉", async () => {
    const { commits, view } = renderHarness();
    const text = view.getByTestId("text");

    act(() => {
      touch("touchstart", text, [{ identifier: 2, clientX: 40, clientY: 200 }]);
      touch("touchmove", text, [{ identifier: 2, clientX: 240, clientY: 201 }]);
    });
    expect(readDrawerProgress("left")).toBeCloseTo(0.6667, 3);
    act(() => {
      touch("touchend", text, [], [{ identifier: 2, clientX: 240, clientY: 201 }]);
    });
    expect(readDrawerProgress("left")).toBe(1);
    await flushDeferredCommit();
    expect(commits).toEqual([{ side: "left", open: true }]);

    act(() => {
      touch("touchstart", text, [{ identifier: 3, clientX: 380, clientY: 400 }]);
      touch("touchmove", text, [{ identifier: 3, clientX: 200, clientY: 399 }]);
    });
    // 180 / 400 = 0.45
    expect(readDrawerProgress("right")).toBeCloseTo(0.45, 5);
    act(() => {
      touch("touchend", text, [], [{ identifier: 3, clientX: 200, clientY: 399 }]);
    });
    expect(readDrawerProgress("right")).toBe(0);
    await flushDeferredCommit();
    expect(commits[1]).toEqual({ side: "right", open: false });
  });

  it("纵向滑动与表格内横滑都不触发抽屉", () => {
    const { commits, view } = renderHarness();
    const text = view.getByTestId("text");
    const wrapper = makeElement("div", view.container, {
      "data-streamdown": "table-wrapper",
    });
    const tableCell = makeElement("td", makeElement("tr", makeElement("tbody", makeElement("table", wrapper))));

    act(() => {
      touch("touchstart", text, [{ identifier: 4, clientX: 100, clientY: 100 }]);
      touch("touchmove", text, [{ identifier: 4, clientX: 130, clientY: 260 }]);
      touch("touchend", text, [], [{ identifier: 4, clientX: 130, clientY: 260 }]);
    });
    expect(commits).toEqual([]);
    expect(readDrawerProgress("left")).toBe(0);

    act(() => {
      touch("touchstart", tableCell, [{ identifier: 5, clientX: 60, clientY: 300 }]);
      touch("touchmove", tableCell, [{ identifier: 5, clientX: 260, clientY: 300 }]);
      touch("touchend", tableCell, [], [{ identifier: 5, clientX: 260, clientY: 300 }]);
    });
    expect(commits).toEqual([]);
    expect(document.documentElement.hasAttribute("data-drawer-dragging")).toBe(false);
  });

  it("已展开的面板可跟手推回", async () => {
    const { commits, view } = renderHarness();
    const drawer = view.container.querySelector<HTMLElement>(".mobile-nav-drawer");
    if (!drawer) throw new Error("drawer missing");
    setDrawerProgress("left", 1);
    const inner = makeElement("button", drawer);

    act(() => {
      touch("touchstart", inner, [{ identifier: 6, clientX: 260, clientY: 200 }]);
      touch("touchmove", inner, [{ identifier: 6, clientX: 60, clientY: 200 }]);
    });
    // 1 - 200 / 300 = 0.333
    expect(readDrawerProgress("left")).toBeCloseTo(0.3333, 3);
    act(() => {
      touch("touchend", inner, [], [{ identifier: 6, clientX: 60, clientY: 200 }]);
    });
    expect(readDrawerProgress("left")).toBe(0);
    await flushDeferredCommit();
    expect(commits).toEqual([{ side: "left", open: false }]);
  });

  it("图谱 rail 自身不参与手势（只有遮罩可以拖回）", () => {
    const { commits, view } = renderHarness();
    const rail = view.container.querySelector<HTMLElement>(".context-rail");
    if (!rail) throw new Error("rail missing");

    act(() => {
      touch("touchstart", rail, [{ identifier: 8, clientX: 200, clientY: 300 }]);
      touch("touchmove", rail, [{ identifier: 8, clientX: 380, clientY: 300 }]);
      touch("touchend", rail, [], [{ identifier: 8, clientX: 380, clientY: 300 }]);
    });
    expect(commits).toEqual([]);
    expect(document.documentElement.hasAttribute("data-drawer-dragging")).toBe(false);
  });

  it("Radix 弹层打开时不抢手势", () => {
    const { commits, view } = renderHarness();
    const text = view.getByTestId("text");
    document.body.setAttribute("data-scroll-locked", "");
    act(() => {
      touch("touchstart", text, [{ identifier: 7, clientX: 20, clientY: 300 }]);
      touch("touchmove", text, [{ identifier: 7, clientX: 260, clientY: 300 }]);
      touch("touchend", text, [], [{ identifier: 7, clientX: 260, clientY: 300 }]);
    });
    document.body.removeAttribute("data-scroll-locked");
    expect(commits).toEqual([]);
    expect(readDrawerProgress("left")).toBe(0);
  });
});

describe("抽屉展开标记（portal 弹层的 CSS 层级输入）", () => {
  it("登记展开的抽屉，两侧可同时存在，全部注销后移除标记", () => {
    expect(document.documentElement.hasAttribute("data-drawer-open")).toBe(false);

    act(() => setDrawerLayerOpen("left", true));
    expect(document.documentElement.getAttribute("data-drawer-open")).toBe("left");

    act(() => setDrawerLayerOpen("right", true));
    expect(document.documentElement.getAttribute("data-drawer-open")).toBe(
      "left right",
    );

    act(() => setDrawerLayerOpen("left", false));
    expect(document.documentElement.getAttribute("data-drawer-open")).toBe(
      "right",
    );

    act(() => setDrawerLayerOpen("right", false));
    expect(document.documentElement.hasAttribute("data-drawer-open")).toBe(false);
  });

  it("重复登记同一侧不会写重复值", () => {
    act(() => setDrawerLayerOpen("left", true));
    act(() => setDrawerLayerOpen("left", true));
    expect(document.documentElement.getAttribute("data-drawer-open")).toBe("left");
    act(() => setDrawerLayerOpen("left", false));
    expect(document.documentElement.hasAttribute("data-drawer-open")).toBe(false);
  });
});
