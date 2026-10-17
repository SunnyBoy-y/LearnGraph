import { describe, expect, it } from "vitest";
import { createOrbRhythm, orbBodyScale, orbHaloFrame, stepOrbRhythm } from "./voice-orb-motion";

const TAU = Math.PI * 2;
const forward = (next: number, previous: number, period: number) => (next - previous + period) % period;

describe("voice orb continuous rhythm", () => {
  it.each([30, 60, 120])("keeps breathing and halo speed bounded for ten minutes at %i fps", (fps) => {
    const rhythm = createOrbRhythm();
    let maxBreathStep = 0;
    let maxHaloStep = 0;
    for (let frame = 0; frame < fps * 600; frame += 1) {
      const energy = 0.5 + 0.5 * Math.sin(frame / fps * 4);
      const previous = { ...rhythm };
      stepOrbRhythm(rhythm, 1 / fps, energy, Math.floor(frame / fps) % 3 !== 0);
      maxBreathStep = Math.max(maxBreathStep, forward(rhythm.breathPhase, previous.breathPhase, TAU));
      maxHaloStep = Math.max(maxHaloStep, forward(rhythm.haloPhase, previous.haloPhase, 1));
    }
    expect(maxBreathStep).toBeLessThanOrEqual(4.59 / fps + 1e-12);
    expect(maxHaloStep).toBeLessThanOrEqual(1 / 2.4 / fps + 1e-12);
  });

  it("does not jump phase when the voice starts, stops, or changes volume", () => {
    const rhythm = createOrbRhythm();
    rhythm.breathPhase = 2.5;
    rhythm.haloPhase = 0.4;
    for (const active of [true, false, true]) {
      const previous = { ...rhythm };
      stepOrbRhythm(rhythm, 1 / 60, active ? 1 : 0, active);
      expect(forward(rhythm.breathPhase, previous.breathPhase, TAU)).toBeLessThan(0.0765);
      expect(forward(rhythm.haloPhase, previous.haloPhase, 1)).toBeLessThan(0.007);
      expect(Math.abs(rhythm.breathSpeed - previous.breathSpeed)).toBeLessThan(0.051);
    }
  });

  it.each([0, 1])("fades ring %i to transparency on both sides of its wrap", (index) => {
    const rhythm = createOrbRhythm();
    const boundary = (1 - index * 1.2 / 3.8) % 1;
    rhythm.haloPhase = (boundary - 1e-6 + 1) % 1;
    const before = orbHaloFrame(rhythm, index, true);
    rhythm.haloPhase = boundary + 1e-6;
    const after = orbHaloFrame(rhythm, index, true);
    expect(before.alpha).toBeLessThan(1e-10);
    expect(after.alpha).toBeLessThan(1e-10);
    expect(Math.abs(before.alpha - after.alpha)).toBeLessThan(1e-10);
  });

  it("keeps a static readout under reduced motion, including after a tap", () => {
    const rhythm = createOrbRhythm();
    const scale = orbBodyScale(rhythm, 0.5, 0, false);
    stepOrbRhythm(rhythm, 10, 1, true);
    expect(orbBodyScale(rhythm, 0.5, 1, false)).toBe(scale);
    expect(orbHaloFrame(rhythm, 0, false)).toEqual(orbHaloFrame(rhythm, 1, false));
  });
});
