import { act, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ImageLightbox } from "@/components/chat/image-lightbox";

/**
 * 灯箱缩放的本地验证。
 *
 * jsdom 里元素尺寸恒为 0，所以这里：
 * - 用 `naturalWidth/Height` 桩给出"原图尺寸"，由组件自己算适配尺寸；
 * - 把手势面/舞台的 `getBoundingClientRect`、舞台的 `offsetWidth/Height` 固定成
 *   可控值，再用真实事件驱动滚轮/指针路径。
 * 适配口径：宽 min(vw×0.92, 1500)、高 vh×0.82；jsdom 视口默认 1024×768。
 */
const FRAME_WIDTH = Math.min(1024 * 0.92, 1500); // 942.08
const FRAME_HEIGHT = 768 * 0.82; // 629.76

const NATURAL_WIDTH = 1600;
const NATURAL_HEIGHT = 900;
/** 1600×900 的适配尺寸（宽先受限）。 */
const FIT_WIDTH = FRAME_WIDTH;
const FIT_HEIGHT = (NATURAL_HEIGHT * FRAME_WIDTH) / NATURAL_WIDTH; // 529.92
/** 小图 400×300 的适配尺寸（高先受限 → 填满可用框高度）。 */
const SMALL_NATURAL = { width: 400, height: 300 };
const SMALL_FIT_HEIGHT = FRAME_HEIGHT;
const SMALL_FIT_WIDTH = (SMALL_NATURAL.width * FRAME_HEIGHT) / SMALL_NATURAL.height;

const STAGE_BOX = { left: 100, top: 100, width: FIT_WIDTH, height: FIT_HEIGHT };
const STAGE_CENTER = {
  x: STAGE_BOX.left + STAGE_BOX.width / 2,
  y: STAGE_BOX.top + STAGE_BOX.height / 2,
};

class TestPointerEvent extends MouseEvent {
  pointerId: number;
  pointerType: string;

  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
    this.pointerType = init.pointerType ?? "mouse";
  }
}

const PointerEventCtor = (window.PointerEvent ??
  TestPointerEvent) as typeof PointerEvent;

function rect(box: { left: number; top: number; width: number; height: number }) {
  return {
    ...box,
    right: box.left + box.width,
    bottom: box.top + box.height,
    x: box.left,
    y: box.top,
    toJSON: () => ({}),
  } as DOMRect;
}

function stubNaturalSize(width: number, height: number) {
  Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", {
    configurable: true,
    get: () => width,
  });
  Object.defineProperty(HTMLImageElement.prototype, "naturalHeight", {
    configurable: true,
    get: () => height,
  });
}

beforeEach(() => {
  stubNaturalSize(NATURAL_WIDTH, NATURAL_HEIGHT);
});

afterEach(() => {
  delete (HTMLImageElement.prototype as unknown as Record<string, unknown>)
    .naturalWidth;
  delete (HTMLImageElement.prototype as unknown as Record<string, unknown>)
    .naturalHeight;
});

/** 当前这个灯箱（同一次测试里可能渲染多次，取最后一个）。 */
function currentRoot() {
  const nodes = document.querySelectorAll<HTMLElement>(".chat-image-lightbox");
  const node = nodes[nodes.length - 1];
  if (!node) throw new Error("lightbox not rendered");
  return node;
}

function query<T extends Element>(selector: string) {
  const node = currentRoot().querySelector<T>(selector);
  if (!node) throw new Error(`${selector} not rendered`);
  return node;
}

function surface() {
  return query<HTMLElement>(".chat-image-lightbox__surface");
}

function stage() {
  return query<HTMLElement>(".chat-image-lightbox__stage");
}

function image() {
  return query<HTMLImageElement>(".chat-image-lightbox__image");
}

/** 渲染并装好尺寸桩；同一测试里重复调用时，查询会取最后一个灯箱。 */
function setupLightbox(open = true) {
  const tree = (nextOpen: boolean) => (
    <ImageLightbox
      alt="测试图片"
      dialogTitle="预览图片 测试图片"
      onOpenChange={() => undefined}
      open={nextOpen}
      src="data:image/png;base64,iVBORw0KGgo="
    />
  );
  const view = render(tree(open));
  const stageNode = stage();
  stageNode.getBoundingClientRect = () => {
    const current = transform();
    return rect({
      left: STAGE_CENTER.x + current.x - FIT_WIDTH * current.scale / 2,
      top: STAGE_CENTER.y + current.y - FIT_HEIGHT * current.scale / 2,
      width: FIT_WIDTH * current.scale,
      height: FIT_HEIGHT * current.scale,
    });
  };
  Object.defineProperty(stageNode, "offsetWidth", {
    configurable: true,
    get: () => parseFloat(image().style.width || "0"),
  });
  Object.defineProperty(stageNode, "offsetHeight", {
    configurable: true,
    get: () => parseFloat(image().style.height || "0"),
  });
  // 图片加载完成 → 组件量出适配尺寸。
  act(() => {
    fireEvent.load(image());
  });
  return {
    rerender: (nextOpen: boolean) => view.rerender(tree(nextOpen)),
    zoomIn: () => fireEvent.click(query<HTMLButtonElement>(
      'button[aria-label="放大图片"]',
    )),
    zoomOutButton: () => query<HTMLButtonElement>('button[aria-label="缩小图片"]'),
    resetButton: () => query<HTMLButtonElement>('button[aria-label="重置缩放"]'),
  };
}

/** 舞台上的 transform（缩放/平移都打在舞台，外框与图才是一起动的）。 */
function transform() {
  const matched =
    /translate\((-?[\d.]+)px, (-?[\d.]+)px\) scale\(([\d.e+-]+)\)/.exec(
      stage().style.transform,
    );
  if (!matched) throw new Error(`unexpected transform: ${stage().style.transform}`);
  return {
    x: Number(matched[1]),
    y: Number(matched[2]),
    scale: Number(matched[3]),
  };
}

function percent() {
  const label = currentRoot().querySelector(".chat-image-lightbox__percent");
  if (!label) throw new Error("percent label not rendered");
  return label.textContent;
}

function wheel(target: HTMLElement, deltaY: number, x: number, y: number) {
  const event = new WheelEvent("wheel", {
    bubbles: true,
    cancelable: true,
    clientX: x,
    clientY: y,
    deltaY,
  });
  let notCancelled = true;
  act(() => {
    notCancelled = target.dispatchEvent(event);
  });
  return notCancelled;
}

function pointer(
  target: HTMLElement,
  type: "pointerdown" | "pointermove" | "pointerup",
  x: number,
  y: number,
  pointerId = 1,
) {
  act(() => {
    target.dispatchEvent(
      new PointerEventCtor(type, {
        bubbles: true,
        cancelable: true,
        clientX: x,
        clientY: y,
        pointerId,
        pointerType: "mouse",
      }),
    );
  });
}

describe("ImageLightbox 适配与缩放", () => {
  it("100% = 适配可用框：大图缩到放得下，小图拉大填满", () => {
    setupLightbox();
    expect(percent()).toBe("100%");

    const big = image();
    expect(parseFloat(big.style.width)).toBeCloseTo(FIT_WIDTH, 4);
    expect(parseFloat(big.style.height)).toBeCloseTo(FIT_HEIGHT, 4);

    // 小图：高先受限，应该被拉到可用框高度（修掉"100% 还太小"）。
    stubNaturalSize(SMALL_NATURAL.width, SMALL_NATURAL.height);
    setupLightbox();
    const small = image();
    expect(parseFloat(small.style.height)).toBeCloseTo(SMALL_FIT_HEIGHT, 4);
    expect(parseFloat(small.style.width)).toBeCloseTo(SMALL_FIT_WIDTH, 4);
    expect(parseFloat(small.style.height)).toBeGreaterThan(SMALL_NATURAL.height);

    // 极小图：放大到 4× 封顶，不做像素糊化。
    stubNaturalSize(80, 60);
    setupLightbox();
    const tiny = image();
    expect(parseFloat(tiny.style.width)).toBeCloseTo(80 * 4, 4);
    expect(parseFloat(tiny.style.height)).toBeCloseTo(60 * 4, 4);
  });

  it("缩放打在舞台上：外框与图一起缩放", () => {
    const view = setupLightbox();
    // transform 只在舞台（卡片）上，图片自身不带 transform。
    expect(stage().style.transform).toContain("scale(1)");
    expect(image().style.transform).toBe("");

    view.zoomIn();
    expect(transform().scale).toBeCloseTo(1.25, 6);
    expect(percent()).toBe("125%");
    expect(image().style.transform).toBe("");
  });

  it("工具条倍率与重置、0.5× 下限禁用", () => {
    const view = setupLightbox();
    view.zoomIn();
    view.zoomIn();
    expect(percent()).toBe("156%");

    fireEvent.click(view.resetButton());
    expect(percent()).toBe("100%");
    expect(transform()).toEqual({ x: 0, y: 0, scale: 1 });

    const zoomOut = view.zoomOutButton();
    for (let index = 0; index < 8; index += 1) {
      fireEvent.click(zoomOut);
    }
    expect(percent()).toBe("50%");
    expect(zoomOut).toBeDisabled();
  });

  it("滚轮在整屏手势面的任意位置都能缩放（不要求光标压在图上）", () => {
    setupLightbox();
    const surfaceNode = surface();
    // 左上角空白处：旧实现（监听挂在小图上）这里完全没反应。
    const notCancelled = wheel(surfaceNode, -100, 8, 8);
    expect(notCancelled).toBe(false);
    expect(transform().scale).toBeCloseTo(Math.exp(0.22), 6);
    expect(percent()).toBe("125%");

    // 下滚缩小。
    wheel(surfaceNode, 100, 8, 8);
    expect(percent()).toBe("100%");
  });

  it("滚轮以光标为锚点，锚点下的像素不动", () => {
    setupLightbox();
    const clientX = STAGE_CENTER.x + 200;
    const clientY = STAGE_CENTER.y;
    wheel(surface(), -100, clientX, clientY);

    const expectedScale = Math.exp(0.22);
    expect(transform().scale).toBeCloseTo(expectedScale, 6);
    expect(transform().x).toBeCloseTo(200 - 200 * expectedScale, 4);
    expect(transform().y).toBeCloseTo(0, 4);
  });

  it("拖动平移按边界夹取，适配状态（100%）不动", () => {
    setupLightbox();

    pointer(surface(), "pointerdown", STAGE_CENTER.x, STAGE_CENTER.y);
    pointer(surface(), "pointermove", STAGE_CENTER.x + 120, STAGE_CENTER.y + 90);
    pointer(surface(), "pointerup", STAGE_CENTER.x + 120, STAGE_CENTER.y + 90);
    expect(transform()).toEqual({ x: 0, y: 0, scale: 1 });

    wheel(surface(), -100, STAGE_CENTER.x, STAGE_CENTER.y);
    wheel(surface(), -100, STAGE_CENTER.x, STAGE_CENTER.y);
    const zoomed = transform();

    pointer(surface(), "pointerdown", STAGE_CENTER.x, STAGE_CENTER.y);
    pointer(surface(), "pointermove", STAGE_CENTER.x + 5000, STAGE_CENTER.y + 5000);
    pointer(surface(), "pointerup", STAGE_CENTER.x + 5000, STAGE_CENTER.y + 5000);

    const maxX = (FIT_WIDTH * zoomed.scale - FIT_WIDTH) / 2;
    const maxY = (FIT_HEIGHT * zoomed.scale - FIT_HEIGHT) / 2;
    expect(transform().scale).toBeCloseTo(zoomed.scale, 6);
    expect(transform().x).toBeCloseTo(maxX, 4);
    expect(transform().y).toBeCloseTo(maxY, 4);
  });

  it("连续滚轮缩放时，已平移的图片仍以光标为锚点", () => {
    setupLightbox();
    // 尺寸桩跟随真实 transform，验证已有位移时的连续缩放。
    const clientX = STAGE_CENTER.x + 100;
    wheel(surface(), -100, clientX, STAGE_CENTER.y);
    wheel(surface(), -100, clientX, STAGE_CENTER.y);
    const current = transform();
    expect(current.x + 100 * current.scale).toBeCloseTo(100, 4);

    wheel(surface(), 100, clientX, STAGE_CENTER.y);
    wheel(surface(), 100, clientX, STAGE_CENTER.y);
    expect(transform().x).toBeCloseTo(0, 4);
    expect(transform().scale).toBeCloseTo(1, 6);
  });

  it("双指张开放大，并把双指中点当作锚点", () => {
    setupLightbox();
    const node = surface();
    const left = STAGE_CENTER.x - 100;
    const right = STAGE_CENTER.x + 100;
    pointer(node, "pointerdown", left, STAGE_CENTER.y, 1);
    pointer(node, "pointerdown", right, STAGE_CENTER.y, 2);
    // 间距 200 → 400，倍率 2×；中点没动 → 不产生平移。
    pointer(node, "pointermove", left - 100, STAGE_CENTER.y, 1);
    pointer(node, "pointermove", right + 100, STAGE_CENTER.y, 2);
    expect(transform().scale).toBeCloseTo(2, 4);
    expect(transform().x).toBeCloseTo(0, 4);
    expect(transform().y).toBeCloseTo(0, 4);
  });

  it("双击还原到适配状态", () => {
    setupLightbox();
    const node = surface();
    wheel(node, -100, STAGE_CENTER.x + 150, STAGE_CENTER.y);
    expect(transform().scale).toBeGreaterThan(1);

    pointer(node, "pointerdown", STAGE_CENTER.x, STAGE_CENTER.y);
    pointer(node, "pointerup", STAGE_CENTER.x, STAGE_CENTER.y);
    expect(transform().scale).toBeGreaterThan(1);

    pointer(node, "pointerdown", STAGE_CENTER.x, STAGE_CENTER.y);
    pointer(node, "pointerup", STAGE_CENTER.x, STAGE_CENTER.y);
    expect(percent()).toBe("100%");
    expect(transform()).toEqual({ x: 0, y: 0, scale: 1 });
  });

  it("点工具条按钮不会被当成双击还原", () => {
    setupLightbox();
    const zoomIn = query<HTMLButtonElement>('button[aria-label="放大图片"]');
    fireEvent.pointerDown(zoomIn, { button: 0, pointerId: 1 });
    fireEvent.pointerUp(zoomIn, { button: 0, pointerId: 1 });
    fireEvent.pointerDown(zoomIn, { button: 0, pointerId: 1 });
    fireEvent.pointerUp(zoomIn, { button: 0, pointerId: 1 });
    fireEvent.click(zoomIn);
    expect(transform().scale).toBeCloseTo(1.25, 6);
  });

  it("关闭再打开不残留上一张的缩放", () => {
    const { rerender } = setupLightbox();
    wheel(surface(), -100, STAGE_CENTER.x, STAGE_CENTER.y);
    expect(transform().scale).toBeGreaterThan(1);

    rerender(false);
    rerender(true);
    expect(percent()).toBe("100%");
    expect(transform()).toEqual({ x: 0, y: 0, scale: 1 });
  });
});
