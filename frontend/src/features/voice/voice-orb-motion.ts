const TAU = Math.PI * 2;
const HALO_SECONDS = 3.8;
const HALO_OFFSET = 1.2 / HALO_SECONDS;

export interface OrbRhythm {
  breathPhase: number;
  breathSpeed: number;
  haloPhase: number;
  haloSpeed: number;
}

export function createOrbRhythm(): OrbRhythm {
  return { breathPhase: 0, breathSpeed: 2.754, haloPhase: 0, haloSpeed: 1 / HALO_SECONDS };
}

/** Integrate velocity, never multiply elapsed time by a changing frequency. */
export function stepOrbRhythm(rhythm: OrbRhythm, dt: number, energy: number, active: boolean): void {
  const level = Math.max(0, Math.min(1, energy));
  const blend = 1 - Math.exp(-dt / 0.6);
  rhythm.breathSpeed += (2.754 + level * 1.836 - rhythm.breathSpeed) * blend;
  rhythm.haloSpeed += (1 / (active ? 3.2 - level * 0.8 : HALO_SECONDS) - rhythm.haloSpeed) * blend;
  rhythm.breathPhase = (rhythm.breathPhase + rhythm.breathSpeed * dt) % TAU;
  rhythm.haloPhase = (rhythm.haloPhase + rhythm.haloSpeed * dt) % 1;
}

export function orbBodyScale(rhythm: OrbRhythm, energy: number, pop: number, animate: boolean): number {
  return 1 + energy * 0.16 + (animate ? (0.068 + energy * 0.17) * Math.sin(rhythm.breathPhase) + pop * 0.45 : 0);
}

export function orbHaloFrame(rhythm: OrbRhythm, index: number, animate: boolean): { radius: number; alpha: number } {
  if (!animate) return { radius: 0.7, alpha: 0.18 };
  // Fixed phase spacing survives speed changes; both ends are fully transparent.
  const phase = (rhythm.haloPhase + index * HALO_OFFSET) % 1;
  return {
    radius: 0.58 + 0.42 * (1 - Math.pow(1 - phase, 3)),
    alpha: 0.12 * Math.pow(Math.sin(Math.PI * phase), 2),
  };
}
