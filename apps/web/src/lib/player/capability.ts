/**
 * How many stems this device should run through the vocoder.
 *
 * Chapter 8's third hard requirement is a fallback to two stems on mobile "if
 * four plus pitch shifting is too heavy". The mechanism is here. The honest
 * part of this file is what it does *not* claim.
 *
 * ## Why this is not a pure auto-detect
 *
 * The obvious approach - and the first one implemented here - was to render the
 * real worklet in an OfflineAudioContext at the worst case (four stems, +6
 * semitones) and compare the render time to the audio duration. Phase 0 used
 * that instrument and recorded, on an 8-core desktop:
 *
 *     stems   pitch 0   pitch +6
 *       1        8%       10%
 *       2       15%       20%
 *       4       42%       47%
 *
 * Re-run during T-1.17 on a comparable machine, the same benchmark reported
 * 46%, 85% and **164%** - three to four times higher - while that very machine
 * was at the same moment playing four stems at +6 without a glitch. An offline
 * render is not a reliable proxy for real-time capability: it is throttled in a
 * background tab, it competes with whatever else the machine is doing in that
 * instant, and it runs at page load, which is exactly when the browser is also
 * fetching and decoding four audio files.
 *
 * A benchmark that says "too slow" about a device demonstrably fast enough will
 * quietly take two faders away from people who should have four. So:
 *
 * - **four stems is the default**, and the measurement only overrides it when a
 *   device is not marginal but hopeless (see CLEARLY_INCAPABLE);
 * - the mode is **a control the user can set**, remembered per device, so a
 *   wrong automatic answer is recoverable in one tap rather than being a
 *   permanent mystery.
 *
 * The threshold below is provisional. T-0.2.5, the measurement on real phone
 * hardware, is still blocked on not having a phone, and it is the thing that
 * would let this be calibrated rather than guessed. Until then the automatic
 * path is deliberately timid and the manual path is the one that carries the
 * requirement.
 */

export type StemMode = "four" | "two";

/** Worst case, which is what the decision has to survive: every stem at +6. */
const BENCHMARK_SEMITONES = 6;
const BENCHMARK_SECONDS = 1.0;
const BENCHMARK_RATE = 44100;
const BENCHMARK_STEMS = 4;

/**
 * Best of this many runs. One run catches whatever else the machine was doing
 * in that half-second; the best of three is closer to what the device can do.
 */
const BENCHMARK_RUNS = 3;

/**
 * Above this the device is not marginal, it is hopeless: the worst case would
 * take three times longer than real time, with no interpretation of the
 * measurement under which that plays.
 *
 * Deliberately far above the ~1.0 that a calibrated instrument would use. The
 * cost of being wrong in this direction is a stutter the user can fix in one
 * tap; the cost of being wrong in the other is silently degrading every desktop.
 */
export const CLEARLY_INCAPABLE = 3.0;

export const MODE_STORAGE_KEY = "karuki:stem-mode";

export interface Capability {
  /** Fraction of real time the worst case took. 1.0 means "as long as it lasts". */
  cost: number;
  mode: StemMode;
  /** True when the mode came from the user rather than from the measurement. */
  chosen: boolean;
}

function noiseStem(length: number): { name: string; data: Float32Array[] } {
  // Noise rather than silence: the vocoder's cost depends on the spectrum, and
  // a silent input would flatter the device.
  const channel = new Float32Array(length);
  let seed = 1;
  for (let i = 0; i < length; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    channel[i] = (seed / 0x3fffffff - 1) * 0.25;
  }
  return { name: "benchmark", data: [channel, channel] };
}

async function renderOnce(workletUrl: string, length: number): Promise<number> {
  const offline = new OfflineAudioContext(2, length, BENCHMARK_RATE);
  await offline.audioWorklet.addModule(workletUrl);

  const stems = Array.from({ length: BENCHMARK_STEMS }, () => noiseStem(length));
  const node = new AudioWorkletNode(offline, "pitch-processor", {
    numberOfInputs: 0,
    numberOfOutputs: BENCHMARK_STEMS,
    outputChannelCount: stems.map(() => 2),
    processorOptions: { stems, semitones: BENCHMARK_SEMITONES, tempo: 1, playing: true },
  });
  for (let output = 0; output < BENCHMARK_STEMS; output++) {
    node.connect(offline.destination, output);
  }

  const started = performance.now();
  await offline.startRendering();
  return (performance.now() - started) / 1000 / BENCHMARK_SECONDS;
}

/** The best of a few runs of the worst case. */
export async function measureCost(workletUrl = "/pitch-worklet.js"): Promise<number> {
  const length = Math.floor(BENCHMARK_RATE * BENCHMARK_SECONDS);
  let best = Number.POSITIVE_INFINITY;
  for (let run = 0; run < BENCHMARK_RUNS; run++) {
    best = Math.min(best, await renderOnce(workletUrl, length));
  }
  return best;
}

export function decide(cost: number): StemMode {
  // Four unless the device is hopeless. See the note at the top of this file:
  // the instrument over-reports, so it is only trusted at the extreme.
  return cost >= CLEARLY_INCAPABLE ? "two" : "four";
}

export function readStoredMode(storage?: Storage): StemMode | null {
  try {
    const stored = (storage ?? window.localStorage).getItem(MODE_STORAGE_KEY);
    return stored === "two" || stored === "four" ? stored : null;
  } catch {
    // Private browsing, or storage disabled. Not a reason to fail.
    return null;
  }
}

export function storeMode(mode: StemMode, storage?: Storage): void {
  try {
    (storage ?? window.localStorage).setItem(MODE_STORAGE_KEY, mode);
  } catch {
    // As above. The mode is a convenience, not state we can lose anything over.
  }
}

/**
 * The mode to open with: whatever this device was last set to, or the
 * measurement's opinion if it has never been set.
 *
 * A stored choice is not re-measured. Someone who turned light mode on because
 * their phone stuttered should not have it turned off again by a benchmark that
 * happened to run while the phone was idle.
 */
export async function resolveMode(workletUrl = "/pitch-worklet.js"): Promise<Capability> {
  const stored = readStoredMode();
  if (stored !== null) return { cost: Number.NaN, mode: stored, chosen: true };

  try {
    const cost = await measureCost(workletUrl);
    return { cost, mode: decide(cost), chosen: false };
  } catch {
    // OfflineAudioContext with a worklet is not universal. A device we cannot
    // measure gets the default rather than the pessimistic answer, for the same
    // reason the threshold is high.
    return { cost: Number.NaN, mode: "four", chosen: false };
  }
}

/**
 * Which stems make up the backing channel in two-stem mode.
 *
 * Vocals stay separate and the rest fold together. That is the only split worth
 * having: "remove vocals" is the feature people came for and it survives; what
 * is lost is balancing the drums against the bass, which is not why anyone
 * opened the app.
 */
export const BACKING_PARTS = ["drums", "bass", "other"] as const;
