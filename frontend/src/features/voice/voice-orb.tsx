import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Mic, MicOff, LoaderCircle, PhoneOff } from "lucide-react";
import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { cn } from "@/lib/utils";
import {
  useVoiceSession,
  type VoiceSessionState,
  type VoiceTransportState,
} from "./voice-session-controller";
import { voiceStatusText } from "./voice-status";
import { readVoiceSessionResumable } from "./voice-session-markers";

/**
 * The composer's voice controls -- and the only place a call's connection state
 * is presented (2026-09-14 revision).
 *
 * The voice-tutor button is the dial key: entering voice mode starts the call,
 * so the control has to answer "is this thing actually connected?" by itself.
 * Right-hand slot:
 *
 *   connecting / reconnecting -> grey + spinner
 *   connected                 -> coloured, and the connect cue plays right then
 *   error                     -> grey
 *
 * There is deliberately no floating caption card: live transcript and the
 * tutor's answer belong to the conversation itself, and the call's incidental
 * status (model pin, ICE path, degradation) is a single in-flow line above the
 * composer -- see `VoiceStatusLine`'s host in the chat page. A floating caption
 * layer used to sit on top of the messages it was describing.
 *
 * The orb is back (2026-09-15) as a *state indicator only*: one coloured sphere
 * that shows whether the call is hearing, thinking, or speaking, sitting inline
 * above that status line so it never covers the conversation. Restoring it did
 * not restore the auto-dial effect that used to live in `VoiceOrbDock` -- the
 * chat page owns dialing now, and a second dialer would make entering voice mode
 * open two calls.
 *
 * 2026-09-18: the sphere itself is a `<canvas>` now, ported from demo-voice2o2's
 * 呼吸彩球 -- a three-stop radial body, a real specular highlight, the light the
 * floor bounces back, a breathing glow, and a tap that sets off a burst (flash,
 * staggered shockwave rings, sparks) -- and it is a physical object rather than a
 * decoration: drag it anywhere on screen, throw it, and it
 * bounces off the viewport before settling back home. The layer it is painted on
 * is fixed and portal'd onto `<body>` (see `VoiceOrb`), so none of that can move
 * the text around it, nor be clipped by the conversation's own overflow.
 *
 * One departure from the demo worth knowing: there is no per-side hue. The hue
 * drifts continuously and winds up on taps, exactly as in the demo, so colour no
 * longer says *who* is talking -- that was the CSS orb's `is-user-voice` /
 * `is-assistant-voice`. `state` still drives the motion, so "listening" and
 * "speaking" stay readable from the way the sphere moves.
 */

export interface VoiceOrbProps {
  transport: VoiceTransportState;
  state: VoiceSessionState;
  error?: string | null;
  muted?: boolean;
  audioLevel?: number;
}

/**
 * The orb's motion engine.
 *
 * Three things separate "a coloured ball" from "a ball breathing with the
 * conversation":
 *
 * 1. Loudness is low-passed before it drives anything. Instantaneous RMS is a
 *    per-frame number, so wiring it straight into a scale makes the sphere
 *    flicker on every syllable; a ~0.35s envelope followed by a fast-attack /
 *    slow-release smoother turns it into a loudness contour instead.
 * 2. While the tutor speaks the orb breathes on a human rhythm -- randomised
 *    syllables (~0.22-0.42s) separated by short pauses, with an occasional
 *    longer breath -- rather than following the real syllables.
 * 3. The sphere is a physical object (2026-09-18). It lags behind the finger on a
 *    spring, stretches along whatever direction it is being pulled, is thrown
 *    when released, bounces off the viewport edges and is then reeled back to its
 *    home above the composer. The demo's own spring is far too soft for that --
 *    its ball is meant to drift across a screen, not to be played with -- so the
 *    stiffness, damping and bounce here are its own.
 *
 * Everything it produces is consumed by drawing to the canvas, so the loop still
 * never triggers a React render.
 */
interface OrbEngine {
  /** Frame clock in seconds: drives the breath, the hue drift and the halos. */
  time: number;
  /** Low-passed raw loudness 0..1 (the envelope). */
  level: number;
  /** Smoothed output level that drives the visuals. */
  energy: number;
  /** Current syllable envelope 0..1 of the speaking rhythm. */
  syllable: number;
  syllableTimer: number;
  inSyllable: boolean;
  syllableLen: number;
  pauseLen: number;
  /** Tap pulse, decaying to 0. */
  pop: number;
  /** Tap shockwave age, 1 -> 0; the staggered rings are read off it. */
  ring: number;
  /** Tap flash, decaying to 0: the wide halo a tap throws around the sphere. */
  flash: number;
  /** Tap sparks, decaying to 0, thrown along the angles `sparkSeed` fixes. */
  spark: number;
  sparkSeed: number;
  /** Tap-accumulated hue speed (deg/s), decaying back to the base rotation. */
  hueBoost: number;
  /** Drifting hue in degrees. */
  hue: number;
  /** Sphere centre in viewport CSS px, and its velocity in px/s. */
  x: number;
  y: number;
  vx: number;
  vy: number;
  /** Where the sphere belongs: the centre of its anchor above the composer. */
  homeX: number;
  homeY: number;
  /** Viewport size in CSS px, and the bitmap ratio backing the canvas. */
  viewportW: number;
  viewportH: number;
  dpr: number;
  /** False until the anchor has been measured; nothing is drawn before that. */
  ready: boolean;
  /** True while a pointer is holding the sphere. */
  dragging: boolean;
  /** Where that pointer is (viewport CSS px) and how fast it is moving (px/s). */
  pointerX: number;
  pointerY: number;
  pointerVX: number;
  pointerVY: number;
  /** Jelly deformation: how far it is stretched, and along which angle. */
  stretch: number;
  stretchAngle: number;
  /** Its lean, in radians (a tilt into the travel, not a spin). */
  rot: number;
  /** What the previous frame painted, so only that part of the layer is cleared. */
  lastBounds: { x0: number; y0: number; x1: number; y1: number } | null;
  /** Timestamp of the previous frame. */
  last: number;
}

interface OrbSignal {
  audioLevel: number;
  listening: boolean;
  speaking: boolean;
}

/** Sphere radius in CSS px. The anchor in index.css is sized to hold it. */
const ORB_RADIUS = 34;
/** Where the hue starts before it drifts away (the demo's 210). */
const ORB_BASE_HUE = 210;
/** Hue drift in deg/s, before whatever taps have wound up on top of it. */
const ORB_HUE_DRIFT = 14;
/** Spring pulling the sphere towards the finger, and the damping that follows it
    (a little under-damped, so it trails the finger with a slight wobble). */
const ORB_GRAB_STIFFNESS = 190;
const ORB_GRAB_DAMPING = 16;
/** Reeling it home afterwards. Deliberately soft: a thrown sphere should coast
    and bounce around for a moment instead of being yanked straight back. */
const ORB_HOME_STIFFNESS = 70;
const ORB_HOME_DAMPING = 7.5;
/** How much of a hit a viewport edge gives back, and the speed ceiling (px/s). */
const ORB_EDGE_BOUNCE = 0.62;
const ORB_MAX_SPEED = 2600;
/** Speed (px/s) and lag (px) that stretch the sphere as far as it goes. */
const ORB_STRETCH_SPEED = 2200;
const ORB_STRETCH_REACH = 260;
const ORB_MAX_STRETCH = 0.5;
/** Kept clear of the viewport edge (× radius) so a bouncing sphere stays whole. */
const ORB_EDGE_MARGIN = 1.4;
/** The two slow rings the CSS orb ran at 3.8s, the second offset by 1.2s. */
const ORB_HALO_SECONDS = 3.8;
const ORB_HALO_DELAY_SECONDS = 1.2;
/** Ring radii as a fraction of the sphere radius (the old 37% / 50% of an
    84px-wide orb box). */
const ORB_HALO_RADIUS_RATIOS = [0.62, 0.83] as const;
/** Tap burst: three staggered rings, a dozen sparks, and how far they spread. */
const ORB_TAP_RING_COUNT = 3;
const ORB_TAP_RING_STAGGER = 0.18;
const ORB_SPARK_COUNT = 12;
/** Tap-ring growth per unit of shockwave age: the demo's 2.6x, pushed to 3.2x
    because the rings have to stay legible while they travel. */
const ORB_TAP_RING_GROWTH = 2.2;

const ORB_ENVELOPE_SECONDS = 0.35;
/** Rise is quicker than fall: the ball reacts to speech but settles gently. */
const ORB_ATTACK_SECONDS = 0.14;
const ORB_RELEASE_SECONDS = 0.42;

function createOrbEngine(): OrbEngine {
  return {
    time: 0,
    level: 0,
    energy: 0,
    syllable: 0,
    syllableTimer: 0,
    inSyllable: false,
    syllableLen: 0.16,
    pauseLen: 0.12,
    pop: 0,
    ring: 0,
    flash: 0,
    spark: 0,
    sparkSeed: 0,
    hueBoost: 0,
    hue: ORB_BASE_HUE,
    x: 0,
    y: 0,
    vx: 0,
    vy: 0,
    homeX: 0,
    homeY: 0,
    viewportW: 0,
    viewportH: 0,
    dpr: 1,
    ready: false,
    dragging: false,
    pointerX: 0,
    pointerY: 0,
    pointerVX: 0,
    pointerVY: 0,
    stretch: 0,
    stretchAngle: 0,
    rot: 0,
    lastBounds: null,
    last: 0,
  };
}

/** The level the sphere is being asked to show, before any smoothing. */
function orbTargetLevel(level: number, signal: OrbSignal, syllable: number): number {
  const botPulse = signal.speaking ? 0.32 + 0.22 * syllable : 0;
  const userPulse = signal.listening ? 0.2 : 0;
  return Math.min(1, Math.max(level, botPulse, userPulse));
}

function stepOrbEngine(engine: OrbEngine, dt: number, signal: OrbSignal): void {
  engine.time += dt;

  // Hue: the demo's endless rotation, wound up by taps and easing back to it.
  engine.hue = (engine.hue + dt * (ORB_HUE_DRIFT + engine.hueBoost)) % 360;

  // Speaking rhythm: a cosine window per syllable (0 -> 1 -> 0) reads as a slow
  // nod rather than a flicker; most pauses are short, some are a breath.
  if (signal.speaking) {
    engine.syllableTimer += dt;
    if (engine.inSyllable) {
      const phase = Math.min(1, engine.syllableTimer / engine.syllableLen);
      engine.syllable = 0.5 - 0.5 * Math.cos(2 * Math.PI * phase);
      if (phase >= 1) {
        engine.inSyllable = false;
        engine.syllableTimer = 0;
        engine.pauseLen =
          Math.random() < 0.2 ? 0.3 + Math.random() * 0.3 : 0.1 + Math.random() * 0.14;
      }
    } else {
      engine.syllable = 0;
      if (engine.syllableTimer >= engine.pauseLen) {
        engine.inSyllable = true;
        engine.syllableTimer = 0;
        engine.syllableLen = 0.22 + Math.random() * 0.2;
      }
    }
  } else {
    engine.syllable = 0;
    engine.syllableTimer = 0;
    engine.inSyllable = false;
  }

  // Loudness contour: envelope first (kills per-frame jitter), then a fast
  // attack / slow release so the sphere leans into speech and settles gently.
  const raw = Math.max(0, Math.min(1, signal.audioLevel));
  const envelope = 1 - Math.exp(-dt / ORB_ENVELOPE_SECONDS);
  engine.level += (raw - engine.level) * envelope;
  const target = orbTargetLevel(engine.level, signal, engine.syllable);
  const tau = target > engine.energy ? ORB_ATTACK_SECONDS : ORB_RELEASE_SECONDS;
  engine.energy += (target - engine.energy) * (1 - Math.exp(-dt / tau));

  // Tap envelopes. Three different decay speeds on purpose: a flash that snaps,
  // sparks that streak away, and a shockwave that lingers just long enough for the
  // staggered rings to read as leaving the sphere one after another.
  engine.pop *= Math.pow(0.002, dt);
  engine.ring = Math.max(0, engine.ring - dt * 1.25);
  engine.flash *= Math.pow(0.01, dt);
  engine.spark *= Math.pow(0.03, dt);
  engine.hueBoost *= Math.pow(0.1, dt);

  stepOrbMotion(engine, dt);
}

/**
 * The physical half of the engine: springs, walls, bounce and the jelly.
 *
 * Split out because it has no opinion about the conversation -- the sphere behaves
 * the same way whether it is listening, thinking or idle.
 */
function stepOrbMotion(engine: OrbEngine, dt: number): void {
  if (!engine.ready || dt <= 0) return;

  const margin = ORB_RADIUS * ORB_EDGE_MARGIN;
  // The walls are pushed out rather than allowed to fight the home spring, so a
  // composer sitting close to an edge still leaves the sphere somewhere to rest.
  const minX = Math.min(margin, engine.homeX);
  const maxX = Math.max(engine.viewportW - margin, engine.homeX);
  const minY = Math.min(margin, engine.homeY);
  const maxY = Math.max(engine.viewportH - margin, engine.homeY);

  // What it is being pulled towards, and how hard. Held: the finger. Let go: home.
  const targetX = engine.dragging ? engine.pointerX : engine.homeX;
  const targetY = engine.dragging ? engine.pointerY : engine.homeY;
  const stiffness = engine.dragging ? ORB_GRAB_STIFFNESS : ORB_HOME_STIFFNESS;
  const damping = engine.dragging ? ORB_GRAB_DAMPING : ORB_HOME_DAMPING;
  engine.vx += ((targetX - engine.x) * stiffness - engine.vx * damping) * dt;
  engine.vy += ((targetY - engine.y) * stiffness - engine.vy * damping) * dt;

  let speed = Math.hypot(engine.vx, engine.vy);
  if (speed > ORB_MAX_SPEED) {
    engine.vx = (engine.vx / speed) * ORB_MAX_SPEED;
    engine.vy = (engine.vy / speed) * ORB_MAX_SPEED;
    speed = ORB_MAX_SPEED;
  }

  engine.x += engine.vx * dt;
  engine.y += engine.vy * dt;

  // Walls: a thrown sphere bounces off the viewport instead of leaving it.
  if (engine.x < minX) {
    engine.x = minX;
    engine.vx = Math.abs(engine.vx) * ORB_EDGE_BOUNCE;
  } else if (engine.x > maxX) {
    engine.x = maxX;
    engine.vx = -Math.abs(engine.vx) * ORB_EDGE_BOUNCE;
  }
  if (engine.y < minY) {
    engine.y = minY;
    engine.vy = Math.abs(engine.vy) * ORB_EDGE_BOUNCE;
  } else if (engine.y > maxY) {
    engine.y = maxY;
    engine.vy = -Math.abs(engine.vy) * ORB_EDGE_BOUNCE;
  }

  // Parked: without this the springs keep nudging a sub-pixel wobble into the
  // layer forever and the sphere never quite looks at rest.
  if (!engine.dragging && speed < 2 && Math.hypot(engine.homeX - engine.x, engine.homeY - engine.y) < 0.6) {
    engine.x = engine.homeX;
    engine.y = engine.homeY;
    engine.vx = 0;
    engine.vy = 0;
    speed = 0;
  }

  // Jelly: stretched along whatever is pulling it -- the finger's lead while it is
  // held, the direction of travel once thrown -- and squashed across it.
  const lagX = engine.pointerX - engine.x;
  const lagY = engine.pointerY - engine.y;
  const lag = Math.hypot(lagX, lagY);
  const pull = Math.min(1, (engine.dragging ? lag / ORB_STRETCH_REACH : 0) + speed / ORB_STRETCH_SPEED);
  engine.stretch += (pull * ORB_MAX_STRETCH - engine.stretch) * (1 - Math.exp(-dt / 0.07));
  if (lag > 4 && engine.dragging) {
    engine.stretchAngle = Math.atan2(lagY, lagX);
  } else if (speed > 12) {
    engine.stretchAngle = Math.atan2(engine.vy, engine.vx);
  }

  // It leans into its travel rather than spinning: a spinning sphere would drag
  // its specular highlight around the surface with it and stop reading as a ball.
  const tilt = Math.max(-0.45, Math.min(0.45, engine.vx / 1100));
  engine.rot += (tilt - engine.rot) * (1 - Math.exp(-dt / 0.1));
}

/**
 * One frame of the sphere, drawn in CSS px (the caller has already scaled the
 * context for the device pixel ratio).
 *
 * `animate` is false under reduced motion: the sphere still reports loudness,
 * but nothing moves.
 */
function drawOrb(
  context: CanvasRenderingContext2D,
  engine: OrbEngine,
  signal: OrbSignal,
  animate: boolean,
): void {
  const { x, y } = engine;
  const hue = engine.hue;

  // Only what the previous frame painted is cleared: this layer spans the whole
  // viewport, and repainting all of it every frame would be pure waste. `reach`
  // must therefore cover everything drawn below -- including the tap burst, which
  // reaches much further than the sphere does -- or the parts sticking out of it
  // would be smeared across the screen and never wiped.
  const reach = ORB_RADIUS * (3.4 + engine.flash * 2.2);
  const bounds = { x0: x - reach, y0: y - reach, x1: x + reach, y1: y + reach };
  const dirty = engine.lastBounds
    ? {
        x0: Math.min(engine.lastBounds.x0, bounds.x0),
        y0: Math.min(engine.lastBounds.y0, bounds.y0),
        x1: Math.max(engine.lastBounds.x1, bounds.x1),
        y1: Math.max(engine.lastBounds.y1, bounds.y1),
      }
    : { x0: 0, y0: 0, x1: engine.viewportW, y1: engine.viewportH };
  context.clearRect(dirty.x0, dirty.y0, dirty.x1 - dirty.x0, dirty.y1 - dirty.y0);
  engine.lastBounds = bounds;

  // The two slow rings the CSS orb used to animate. They were a fixed blue-grey
  // whatever the sphere was doing; on a drifting hue they follow it instead.
  const haloPeriod =
    signal.listening || signal.speaking ? 2.2 - engine.energy * 1.1 : ORB_HALO_SECONDS;
  ORB_HALO_RADIUS_RATIOS.forEach((ratio, index) => {
    const phase = animate
      ? ((engine.time + index * ORB_HALO_DELAY_SECONDS) % haloPeriod) / haloPeriod
      : 0;
    const eased = animate ? 1 - Math.pow(1 - phase, 3) : 0;
    // `animation: none` under reduced motion leaves a ring at its static style
    // (scale .7, full opacity) rather than at the keyframe's start.
    const ringRadius = ORB_RADIUS * ratio * (animate ? 0.58 + 0.42 * eased : 0.7);
    const alpha = animate ? 0.18 * 0.65 * (1 - eased) : 0.18;
    context.strokeStyle = `hsla(${hue}, 80%, 68%, ${alpha})`;
    context.lineWidth = 1;
    context.beginPath();
    context.arc(x, y, ringRadius, 0, Math.PI * 2);
    context.stroke();
  });

  // Tap flash: a wide, bright halo thrown around the sphere -- the "hit" that the
  // rest of the burst is timed around, and the reason a tap needs a gradient of
  // its own rather than the quiet glow below. Clamped to the distance to the
  // viewport edge, because a bright haze sliced off by the screen is exactly what
  // makes an effect look cheap: it has to reach zero before it gets there.
  if (animate && engine.flash > 0.01) {
    const flashRadius = Math.max(
      0,
      Math.min(
        ORB_RADIUS * (1.6 + engine.flash * 3),
        Math.min(x, y, engine.viewportW - x, engine.viewportH - y),
      ),
    );
    const flashAlpha = Math.pow(engine.flash, 1.4) * 0.55;
    const flash = context.createRadialGradient(x, y, 0, x, y, flashRadius);
    flash.addColorStop(0, `hsla(${hue}, 100%, 94%, ${flashAlpha})`);
    flash.addColorStop(0.28, `hsla(${hue + 20}, 98%, 72%, ${flashAlpha * 0.55})`);
    flash.addColorStop(1, `hsla(${hue + 45}, 96%, 62%, 0)`);
    context.fillStyle = flash;
    context.beginPath();
    context.arc(x, y, flashRadius, 0, Math.PI * 2);
    context.fill();
  }

  // Breathing glow, widened and brightened by a tap. Its outer stop is transparent,
  // so clamping the radius costs nothing visually -- it just keeps the falloff
  // inside the viewport, wherever the sphere has been dragged to. The clamp is also
  // a guard: a negative radius throws, and that would take the loop down.
  if (engine.energy > 0.05 || engine.pop > 0.02) {
    const glowRadius = Math.max(
      0,
      Math.min(
        ORB_RADIUS *
          (1.5 + Math.sin(engine.time * 1.5) * 0.04 + engine.energy * 0.15 + engine.pop * 0.6),
        Math.min(x, y, engine.viewportW - x, engine.viewportH - y),
      ),
    );
    const glowAlpha = 0.08 + engine.energy * 0.15 + engine.pop * 0.2;
    const glow = context.createRadialGradient(x, y, ORB_RADIUS * 0.5, x, y, glowRadius);
    glow.addColorStop(0, `hsla(${hue}, 92%, 68%, ${glowAlpha})`);
    glow.addColorStop(1, `hsla(${hue}, 92%, 68%, 0)`);
    context.fillStyle = glow;
    context.beginPath();
    context.arc(x, y, glowRadius, 0, Math.PI * 2);
    context.fill();
  }

  // Tap shockwave: three rings leaving the sphere at once, each starting a little
  // later than the last, so a tap reads as a burst rather than one polite circle.
  // Their hue fans out slightly, which is most of what makes it look like energy.
  // Each ring holds its brightness for most of the travel and only fades near the
  // end: an even fade would have it invisible long before it got anywhere.
  if (animate && engine.ring > 0) {
    const age = 1 - engine.ring;
    context.lineCap = "round";
    for (let index = 0; index < ORB_TAP_RING_COUNT; index += 1) {
      const stage = Math.min(1, age - index * ORB_TAP_RING_STAGGER);
      if (stage <= 0) continue;
      const fade = Math.min(1, (1 - stage) / 0.55);
      if (fade <= 0.02) continue;
      context.strokeStyle = `hsla(${hue + index * 18}, 96%, ${74 - index * 6}%, ${0.85 * fade})`;
      context.lineWidth = 1.5 + fade * 3.5;
      context.beginPath();
      context.arc(x, y, ORB_RADIUS * (1 + stage * ORB_TAP_RING_GROWTH), 0, Math.PI * 2);
      context.stroke();
    }
  }

  const amplitude = 0.04 + engine.energy * 0.14;
  const frequency = 0.9 + engine.energy;
  const scale =
    1 +
    (animate ? amplitude * Math.sin(engine.time * frequency) : 0) +
    engine.energy * 0.24 +
    (animate ? engine.pop * 0.45 : 0);
  // The bounce keeps the sphere off the walls, but a breathing, stretched sphere
  // is larger than its resting size, so the drawn radius is capped by the room it
  // actually has. Guarded: a negative gradient radius throws, and an exception in
  // this loop would take the whole orb down with it.
  const bodyRadius = Math.max(
    0,
    Math.min(
      ORB_RADIUS * scale,
      (Math.min(x, engine.viewportW - x) - 1) / (1 + engine.stretch),
      (Math.min(y, engine.viewportH - y) - 1) / (1 + engine.stretch),
    ),
  );

  context.save();
  context.translate(x, y);
  // Deformation happens in the world frame, aligned with the pull, and the
  // sphere's own lean is applied inside it -- so a ball pulled sideways is wider,
  // not taller.
  context.rotate(engine.stretchAngle);
  context.scale(1 + engine.stretch, 1 - engine.stretch * 0.5);
  context.rotate(engine.rot - engine.stretchAngle);

  // Body: the demo's three-stop radial gradient.
  const litHue = (hue + 55) % 360;
  const deepHue = (hue + 330) % 360;
  const body = context.createRadialGradient(
    -bodyRadius * 0.35,
    -bodyRadius * 0.42,
    bodyRadius * 0.08,
    0,
    0,
    bodyRadius * 1.15,
  );
  body.addColorStop(0, `hsla(${litHue}, 96%, 80%, 1)`);
  body.addColorStop(0.45, `hsla(${hue}, 88%, 62%, 1)`);
  body.addColorStop(1, `hsla(${deepHue}, 82%, 46%, 1)`);
  context.fillStyle = body;
  context.beginPath();
  context.arc(0, 0, bodyRadius, 0, Math.PI * 2);
  context.fill();

  // Specular highlight, a small hot dot, and the light the floor bounces back.
  context.fillStyle = "rgba(255,255,255,0.6)";
  context.beginPath();
  context.ellipse(
    -bodyRadius * 0.34,
    -bodyRadius * 0.42,
    bodyRadius * 0.22,
    bodyRadius * 0.13,
    -0.6,
    0,
    Math.PI * 2,
  );
  context.fill();
  context.fillStyle = "rgba(255,255,255,0.28)";
  context.beginPath();
  context.arc(-bodyRadius * 0.1, -bodyRadius * 0.55, bodyRadius * 0.06, 0, Math.PI * 2);
  context.fill();
  context.fillStyle = "rgba(255,255,255,0.14)";
  context.beginPath();
  context.ellipse(
    bodyRadius * 0.3,
    bodyRadius * 0.62,
    bodyRadius * 0.34,
    bodyRadius * 0.12,
    0.4,
    0,
    Math.PI * 2,
  );
  context.fill();

  context.restore();

  // Sparks: a dozen streaks thrown outward at angles fixed when the tap happened
  // (off `sparkSeed`), so they fly in straight lines instead of jittering from
  // frame to frame the way per-frame randomness would. Their reach is kept inside
  // `reach` above, which is what keeps them from being left behind on the layer.
  if (animate && engine.spark > 0.02) {
    const travel = 1 - engine.spark;
    const fade = Math.pow(engine.spark, 1.1);
    context.lineCap = "round";
    for (let index = 0; index < ORB_SPARK_COUNT; index += 1) {
      const angle =
        engine.sparkSeed +
        (index / ORB_SPARK_COUNT) * Math.PI * 2 +
        Math.sin(index * 12.9898) * 0.12;
      const from = ORB_RADIUS * (1.02 + travel * 1.3);
      const to = from + ORB_RADIUS * (0.12 + engine.spark * 0.7);
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      context.strokeStyle = `hsla(${hue + 30}, 98%, 78%, ${0.9 * fade})`;
      context.lineWidth = 1.2 + 2.4 * engine.spark;
      context.beginPath();
      context.moveTo(x + cos * from, y + sin * from);
      context.lineTo(x + cos * to, y + sin * to);
      context.stroke();
    }
  }
}

function orbPrefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/**
 * The orb: a visual meter for both sides of the conversation -- and, since
 * 2026-09-18, a small toy.
 *
 * It is still not a control: mute and hang-up live on the composer buttons. What
 * it carries is its own body -- drag it anywhere, throw it, tap it to set off a
 * flash and a shockwave, and it comes back on its own.
 *
 * All of its geometry is layout-neutral by construction:
 *
 * - The sphere is painted on a fixed, viewport-sized `<canvas>` portal'd onto
 *   `<body>`. Out of flow, nothing it does can move the text around it, and no
 *   ancestor's `overflow` can clip it -- neither of which an in-place canvas here
 *   could promise (`.chat-canvas-page` hides its overflow).
 * - The only part left in the layout is an 84px anchor: the sphere's home, and the
 *   one place it can always be grabbed from whatever the page is doing underneath.
 * - It is measured, never forced: the anchor's rect is read twice a second rather
 *   than every frame, because this app re-lays out constantly while it streams.
 *
 * Nothing here re-renders React: the drag, the springs, the bounce and the jelly
 * all live in the animation loop. State comes from `voice-session-controller`;
 * nothing here touches the transcript.
 */
export function VoiceOrb({
  transport,
  state,
  error,
  muted = false,
  audioLevel = 0,
}: VoiceOrbProps) {
  const isListening = state === "listening";
  const isSpeaking = state === "speaking";
  const status = voiceStatusText(transport, state, error);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const anchorRef = useRef<HTMLSpanElement | null>(null);
  const engineRef = useRef<OrbEngine | null>(null);
  /** Repaints a single still frame; only wired up under reduced motion. */
  const paintRef = useRef<(() => void) | null>(null);
  /** Pointer bookkeeping, in a ref so that dragging never re-renders. */
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    at: number;
    moved: boolean;
  } | null>(null);
  /** Set while a grab is in flight, so it cannot become a click underneath. */
  const swallowClickRef = useRef(false);
  const reducedMotion = orbPrefersReducedMotion();
  // The loop reads the latest state through a ref so it never has to restart.
  const signalRef = useRef<OrbSignal>({ audioLevel, listening: isListening, speaking: isSpeaking });
  signalRef.current = { audioLevel, listening: isListening, speaking: isSpeaking };

  useEffect(() => {
    const canvas = canvasRef.current;
    const anchor = anchorRef.current;
    if (!canvas || !anchor) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const engine = (engineRef.current ??= createOrbEngine());

    // Measure the layer and the sphere's home. Both reads force layout, so this
    // runs on a slow timer plus the events that can actually move the composer.
    const sync = () => {
      const width = canvas.clientWidth || window.innerWidth;
      const height = canvas.clientHeight || window.innerHeight;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const pixelsW = Math.max(1, Math.round(width * dpr));
      const pixelsH = Math.max(1, Math.round(height * dpr));
      if (canvas.width !== pixelsW || canvas.height !== pixelsH) {
        canvas.width = pixelsW;
        canvas.height = pixelsH;
        // A resized canvas is a blank one: forget what was painted on it.
        engine.lastBounds = null;
      }
      engine.viewportW = width;
      engine.viewportH = height;
      engine.dpr = canvas.width / width;
      const rect = anchor.getBoundingClientRect();
      engine.homeX = rect.left + rect.width / 2;
      engine.homeY = rect.top + rect.height / 2;
      if (!engine.ready) {
        engine.ready = true;
        engine.x = engine.homeX;
        engine.y = engine.homeY;
      }
    };
    sync();

    const onViewportChange = () => sync();
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, { capture: true, passive: true });
    const stopListening = () => {
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, { capture: true });
    };

    if (reducedMotion) {
      // No motion: nothing moves and the sphere cannot be dragged, so scrolling
      // passes straight through its anchor (see index.css). It still reports
      // loudness, read straight from the signal rather than integrated, because
      // the loop below is what would normally advance it.
      const paint = () => {
        const signal = signalRef.current;
        sync();
        engine.energy = orbTargetLevel(Math.max(0, Math.min(1, signal.audioLevel)), signal, 0);
        context.setTransform(engine.dpr, 0, 0, engine.dpr, 0, 0);
        drawOrb(context, engine, signal, false);
      };
      paintRef.current = paint;
      paint();
      return () => {
        paintRef.current = null;
        stopListening();
      };
    }

    let sinceSync = 0;
    let frame = window.requestAnimationFrame(function tick(now: number) {
      const dt = engine.last ? Math.min(1 / 30, (now - engine.last) / 1000) : 0;
      engine.last = now;
      // Re-measure now and then: the composer moves without a resize too, since it
      // grows as the draft gets longer.
      sinceSync += dt;
      if (sinceSync > 0.5) {
        sinceSync = 0;
        sync();
      }
      context.setTransform(engine.dpr, 0, 0, engine.dpr, 0, 0);
      stepOrbEngine(engine, dt, signalRef.current);
      drawOrb(context, engine, signalRef.current, true);
      frame = window.requestAnimationFrame(tick);
    });
    return () => {
      window.cancelAnimationFrame(frame);
      stopListening();
    };
  }, [reducedMotion]);

  useEffect(() => {
    if (reducedMotion) return;
    const engine = engineRef.current;
    if (!engine) return;

    // Grab the sphere wherever it currently is, not only over its anchor: half the
    // point of the toy is catching it while it is still moving.
    const onPointerDown = (event: PointerEvent) => {
      if (!event.isPrimary || engine.dragging) return;
      if (Math.hypot(event.clientX - engine.x, event.clientY - engine.y) > ORB_RADIUS * 1.6) {
        return;
      }
      // The sphere wins the press: no text selection, and no click on whatever it
      // happens to be floating over.
      event.preventDefault();
      event.stopPropagation();
      swallowClickRef.current = true;
      engine.dragging = true;
      engine.pointerX = event.clientX;
      engine.pointerY = event.clientY;
      engine.pointerVX = 0;
      engine.pointerVY = 0;
      dragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        at: event.timeStamp,
        moved: false,
      };
    };

    const onPointerMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const elapsed = Math.max(1, event.timeStamp - drag.at) / 1000;
      engine.pointerVX = (event.clientX - engine.pointerX) / elapsed;
      engine.pointerVY = (event.clientY - engine.pointerY) / elapsed;
      engine.pointerX = event.clientX;
      engine.pointerY = event.clientY;
      drag.at = event.timeStamp;
      if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 4) {
        drag.moved = true;
      }
    };

    const onPointerUp = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      dragRef.current = null;
      engine.dragging = false;
      // Let go with the finger's speed as well as the sphere's own: the spring
      // lags behind, so a flick would otherwise feel half-hearted.
      engine.vx = engine.vx * 0.55 + engine.pointerVX * 0.45;
      engine.vy = engine.vy * 0.55 + engine.pointerVY * 0.45;
      // The click that follows a press arrives late; drop the flag after it would
      // have done.
      window.setTimeout(() => {
        swallowClickRef.current = false;
      }, 350);
      if (drag.moved) return;
      // A tap, not a drag: detonate the burst -- impact pulse, flash, shockwave and
      // sparks -- and spin the colour up. Repeated taps stack, so the hue can be
      // wound noticeably faster.
      engine.pop = 1;
      engine.ring = 1;
      engine.flash = 1;
      engine.spark = 1;
      engine.sparkSeed = Math.random() * Math.PI * 2;
      engine.hueBoost = Math.min(engine.hueBoost + 40, 260);
      if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
        navigator.vibrate(18);
      }
    };

    const onClick = (event: MouseEvent) => {
      if (!swallowClickRef.current) return;
      swallowClickRef.current = false;
      event.preventDefault();
      event.stopPropagation();
    };

    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    window.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      window.removeEventListener("click", onClick, true);
    };
  }, [reducedMotion]);

  // A still frame has to be repainted whenever what it shows changes.
  useEffect(() => {
    if (reducedMotion) paintRef.current?.();
  }, [reducedMotion, audioLevel, isListening, isSpeaking]);

  return (
    <>
      <div className="chat-voice-orb-dock">
        {/* The anchor: where the sphere lives when nothing is happening to it, and
            where it can always be grabbed no matter what is underneath it. */}
        <span className="chat-voice-orb-dock__visual" ref={anchorRef} />
        <span aria-live="polite" className="sr-only-voice-status" role="status">
          {status}
        </span>
      </div>
      {typeof document === "undefined"
        ? null
        : createPortal(
            <canvas
              aria-label={`语音球：${status}`}
              className={cn(
                "chat-voice-orb-ball",
                muted && "is-muted",
                transport === "error" && "is-error",
              )}
              ref={canvasRef}
              role="img"
            />,
            document.body,
          )}
    </>
  );
}

interface VoiceScopeProps {
  workspaceId: string;
  sessionId: string;
  modelId?: string | null;
  providerId?: string | null;
}

export interface VoiceComposerActionsProps extends VoiceScopeProps {
  /** Mirrors the mode toggle in the composer so the orb and mode stay in sync. */
  onExit?: () => void;
}

/**
 * The single left voice control. Before a call the chat page renders the ASR
 * microphone here; while connected this slot becomes the local mute toggle.
 */
export function VoiceComposerActions({
  workspaceId,
  sessionId,
  modelId,
  providerId,
}: VoiceComposerActionsProps) {
  const voice = useVoiceSession(workspaceId, sessionId, modelId, providerId);
  const canControl =
    voice.transport === "connected" || voice.transport === "connecting";

  return (
    <>
      <PromptInputButton
        aria-label={voice.muted ? "取消静音" : "静音"}
        aria-pressed={voice.muted}
        className={cn("chat-composer__mute", voice.muted && "is-active")}
        disabled={!canControl}
        onClick={() => voice.setMuted(!voice.muted)}
        tooltip={voice.muted ? "取消静音" : "静音"}
      >
        {voice.muted ? <MicOff className="size-4" /> : <Mic className="size-4" />}
      </PromptInputButton>
    </>
  );
}

/**
 * 全双工语音入口字形：五根圆头实心竖条（中间最高，向两侧对称递减）。
 *
 * 用代码绘制，不引位图。几何取自设计稿：条宽 : 条间距 = 1 : 1，
 * 三档高度比（中 : 次 : 外）= 4.45 : 2.90 : 1.45；
 * 五根条共宽 19.8，在 24×24 里左右各留 2.1 边距，并共用同一条水平中线（y = 12）。
 */
const VOICE_WAVE_BAR_WIDTH = 2.2;
const VOICE_WAVE_CENTER = 12;
const VOICE_WAVE_BARS = [
  { height: 6.4, x: 2.1 },
  { height: 12.8, x: 6.5 },
  { height: 19.8, x: 10.9 },
  { height: 12.8, x: 15.3 },
  { height: 6.4, x: 19.7 },
] as const;

function VoiceWaveGlyph({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={cn("size-4", className)}
      viewBox="0 0 24 24"
    >
      {VOICE_WAVE_BARS.map((bar) => (
        <rect
          fill="currentColor"
          height={bar.height}
          key={bar.x}
          rx={VOICE_WAVE_BAR_WIDTH / 2}
          width={VOICE_WAVE_BAR_WIDTH}
          x={bar.x}
          y={VOICE_WAVE_CENTER - bar.height / 2}
        />
      ))}
    </svg>
  );
}

export interface VoiceCallControlProps extends VoiceScopeProps {
  active: boolean;
  onStart: () => void;
  onExit: () => void;
  /**
   * 宿主追加的禁用条件。可用性由聊天页判定：全双工语音只在「输入框没有文字
   * 且没有正在回答的内容」时可用，而通话自身的状态（connecting / error /
   * 缺 workspace 或 session）仍旧由本组件判定，两者是「或」的关系。
   */
  disabled?: boolean;
}

/**
 * The single right-hand slot: dial while idle, hang up after WebRTC connects.
 *
 * It is also the connection indicator: nothing about the call may look "live"
 * before the transport is up, and `reconnecting` must not look identical to
 * idle (it used to, which is what made a stalled call indistinguishable from a
 * call that had not started).
 */
export function VoiceCallControl({
  workspaceId,
  sessionId,
  modelId,
  providerId,
  active,
  disabled,
  onStart,
  onExit,
}: VoiceCallControlProps) {
  const voice = useVoiceSession(workspaceId, sessionId, modelId, providerId);
  const connected = voice.transport === "connected";
  const connecting =
    voice.transport === "connecting" || voice.transport === "reconnecting";
  // A reload drops the peer connection but not the call: the durable session, its
  // turns and its task shelf all survive on the server. The button therefore
  // offers a way back in rather than presenting a call that never started --
  // clicking it re-opens voice mode, which dials again and recovers state.
  const resumable = !active && readVoiceSessionResumable(workspaceId, sessionId);
  const label = active
    ? connected
      ? "挂断全双工语音"
      : connecting
        ? "取消语音连接"
        : "结束语音导师通话"
    : resumable
      ? "回到通话中"
      : "开始全双工语音";
  return (
    <PromptInputButton
      aria-label={label}
      aria-pressed={active}
      className={cn(
        "chat-composer__voice-mode",
        active && "is-active",
        resumable && "is-resumable",
        // Grey + spinner until the peer connection is actually up: "connected"
        // is a fact about the transport, not an intention.
        active && connecting && "is-connecting",
        active && connected && "is-live",
        active && voice.transport === "error" && "is-error",
      )}
      disabled={Boolean(disabled) || (!active && (!workspaceId || !sessionId))}
      onClick={() => {
        if (!active) return onStart();
        voice.disconnect();
        onExit();
      }}
      tooltip={active && !connected && !connecting ? "结束语音导师通话" : label}
    >
      {active && connected ? (
        <PhoneOff className="size-4" />
      ) : active && connecting ? (
        <LoaderCircle className="size-4 animate-spin" />
      ) : (
        <VoiceWaveGlyph />
      )}
    </PromptInputButton>
  );
}
