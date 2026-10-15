import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { VoiceOrb } from './voice-orb';

vi.mock('./voice-session-controller', () => ({ voiceSessionController: { interrupt: vi.fn() }, useVoiceSession: vi.fn() }));
vi.mock('@/components/ai-elements/prompt-input', () => ({ PromptInputButton: () => null }));

let reduced: boolean;
let preferenceChanged: (() => void) | undefined;
let left: number, top: number, width: number;
let hit: Element | null;
let rectSpy: ReturnType<typeof vi.spyOn>;
const arc = vi.fn(), clearRect = vi.fn(), disconnect = vi.fn();
let observerCallback: (() => void) | undefined;
const props = { transport: 'connected' as const, state: 'listening' as const };
function pointer(target: Element, type = 'pointerdown') {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: left + 42, clientY: top + 42, button: 0, detail: 1 });
  Object.defineProperties(event, { isPrimary: { value: true }, pointerId: { value: 1 } });
  act(() => { target.dispatchEvent(event); });
  return event;
}

beforeEach(() => {
  vi.useFakeTimers(); reduced = false; left = 100; top = 200; width = 1024; hit = null;
  arc.mockClear(); clearRect.mockClear(); disconnect.mockClear(); preferenceChanged = undefined; observerCallback = undefined;
  vi.spyOn(window, 'matchMedia').mockImplementation((query) => ({
    get matches() { return reduced; }, media: query, onchange: null,
    addEventListener: vi.fn((_type, listener) => { preferenceChanged = listener as () => void; }),
    removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  }) as unknown as MediaQueryList);
  const context = new Proxy<Record<string, unknown>>({
    arc, clearRect, createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  }, { get(target, key: string) { return target[key] ??= vi.fn(); } });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, 'clientWidth', 'get').mockImplementation(() => width);
  vi.spyOn(HTMLCanvasElement.prototype, 'clientHeight', 'get').mockReturnValue(768);
  rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('chat-voice-orb-dock__visual')
      ? { left, top, width: 84, height: 84, right: left + 84, bottom: top + 84, x: left, y: top, toJSON() {} }
      : { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON() {} };
  });
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { observerCallback = callback; } observe() {} unobserve() {} disconnect = disconnect; });
  vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1);
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
  Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => hit });
});
afterEach(() => { cleanup(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('orb interaction respects top layers', () => {
  it.each(['dialog-overlay', 'dialog-content', 'popover-content', 'sheet-overlay'])('does not capture %s presses', (slot) => {
    const view = render(<><VoiceOrb {...props} /><div data-slot={slot} data-testid="top" /></>);
    hit = view.getByTestId('top');
    const listener = vi.fn(); hit.addEventListener('pointerdown', listener);
    expect(pointer(hit).defaultPrevented).toBe(false);
    expect(listener).toHaveBeenCalledTimes(1);
    const click = new MouseEvent('click', { bubbles: true, cancelable: true }); hit.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(false);
  });
  it('yields when a modal is open even if the hit target was retargeted', () => {
    const view = render(<><VoiceOrb {...props} /><div role="dialog" aria-modal="true" /><div data-testid="background" /></>);
    hit = view.getByTestId('background'); expect(pointer(hit).defaultPrevented).toBe(false);
  });
  it('keeps normal orb grabs and respects controls and task summaries', () => {
    const view = render(<><VoiceOrb {...props} /><div data-testid="background" /><button>button</button><details><summary>summary</summary></details></>);
    hit = view.getByTestId('background'); expect(pointer(hit).defaultPrevented).toBe(true); pointer(hit, 'pointerup');
    hit = view.getByRole('button', { name: 'button' }); expect(pointer(hit).defaultPrevented).toBe(false);
    const click = new MouseEvent('click', { bubbles: true, cancelable: true }); hit.dispatchEvent(click); expect(click.defaultPrevented).toBe(false);
    hit = view.getByText('summary'); expect(pointer(hit).defaultPrevented).toBe(false);
  });
  it('does not swallow a modal click opened between orb press and click', () => {
    const view = render(<><VoiceOrb {...props} /><div data-testid="background" /></>);
    hit = view.getByTestId('background'); pointer(hit); pointer(hit, 'pointerup');
    view.rerender(<><VoiceOrb {...props} /><div data-testid="background" /><div role="dialog" aria-modal="true" data-testid="dialog" /></>);
    const click = new MouseEvent('click', { bubbles: true, cancelable: true }); view.getByTestId('dialog').dispatchEvent(click);
    expect(click.defaultPrevented).toBe(false);
  });
});

describe('reduced-motion static canvas', () => {
  it('repaints and tracks the anchor after canvas resize and scroll', () => {
    reduced = true; render(<VoiceOrb {...props} />); expect(arc).toHaveBeenCalled();
    arc.mockClear(); clearRect.mockClear(); left = 300; top = 400; width = 800;
    act(() => { window.dispatchEvent(new Event('resize')); });
    expect(clearRect).toHaveBeenCalled(); expect(arc.mock.calls.some(([x, y]) => x === 342 && y === 442)).toBe(true);
    arc.mockClear(); top = 500; act(() => { window.dispatchEvent(new Event('scroll')); });
    expect(arc.mock.calls.some(([x, y]) => x === 342 && y === 542)).toBe(true);
  });
  it('observes layout changes and detects position-only shifts without continuous repaint', async () => {
    reduced = true; render(<VoiceOrb {...props} />); arc.mockClear();
    left = 250; act(() => { observerCallback?.(); });
    expect(arc.mock.calls.some(([x]) => x === 292)).toBe(true);
    arc.mockClear(); top = 300; await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(arc.mock.calls.some(([, y]) => y === 342)).toBe(true);
    arc.mockClear(); await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(arc).not.toHaveBeenCalled();
  });
  it('responds to preference changes and disposes observers and timers on unmount', async () => {
    const view = render(<VoiceOrb {...props} />);
    reduced = true; act(() => preferenceChanged?.());
    expect(window.cancelAnimationFrame).toHaveBeenCalled(); expect(arc).toHaveBeenCalled();
    view.unmount(); expect(disconnect).toHaveBeenCalled();
    arc.mockClear(); rectSpy.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); window.dispatchEvent(new Event('resize')); });
    expect(arc).not.toHaveBeenCalled(); expect(rectSpy).not.toHaveBeenCalled();
  });
});
