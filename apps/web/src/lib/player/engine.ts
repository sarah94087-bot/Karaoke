/**
 * The player engine: four stems, one clock.
 *
 * This wraps the phase 0 worklet (public/pitch-worklet.js) rather than
 * reimplementing it. That engine was measured at zero samples of drift across a
 * whole song, at every pitch and every tempo, and under a cent of pitch error.
 *
 * Chapter 8's first hard requirement is the reason for the shape of this file:
 *
 *   "One central clock of the audio engine is the source of truth for
 *    everything - stems and lyrics alike. No browser timers."
 *
 * So `position` is never computed here. It arrives from the worklet, which
 * derives it from `inputPos`, the single read head every stem is advanced by.
 * There is no setInterval and no requestAnimationFrame counting time in this
 * file, and there must not be one added: a browser timer and an audio clock
 * agree for about a minute, and then the lyrics start sliding.
 *
 * The four gain nodes hang off the worklet's four outputs, so muting a stem
 * does not touch the engine and cannot cost sync (T-0.2.2).
 */

export type StemKind = "vocals" | "drums" | "bass" | "other";

/**
 * What the mixer actually has faders for. In two-stem mode the three
 * non-vocal stems arrive folded into one "backing" channel (T-1.17).
 */
export type Channel = StemKind | "backing";

export interface StemSource {
  kind: StemKind;
  url: string;
}

export interface LoadOptions {
  /**
   * Two-stem mode: vocals stay separate, the rest are summed into one backing
   * channel before they reach the worklet. The vocoder then does half the work,
   * which is the whole point - phase 0 measured four stems at +6 semitones at
   * 47% of a desktop core and two at 20%.
   */
  mode?: "four" | "two";
}

export interface PlayerState {
  /** Seconds, from the audio clock. Never from a timer. */
  position: number;
  duration: number;
  playing: boolean;
  semitones: number;
  tempo: number;
  ready: boolean;
}

/** Phase 0 (T-0.2.2): ~70ms so a mute does not click. */
const FADE_SECONDS = 0.07;

const WORKLET_URL = "/pitch-worklet.js";

export const KEY_RANGE = { min: -6, max: 6 } as const;
export const TEMPO_RANGE = { min: 0.5, max: 1.5 } as const;

interface WorkletStatus {
  type: "status" | "ended";
  posSeconds?: number;
}

export class PlayerEngine {
  private context: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private gains = new Map<Channel, GainNode>();
  private master: GainNode | null = null;
  private volumes = new Map<Channel, number>();
  private loaded: Channel[] = [];

  private state: PlayerState = {
    position: 0,
    duration: 0,
    playing: false,
    semitones: 0,
    tempo: 1,
    ready: false,
  };

  private listeners = new Set<(state: PlayerState) => void>();

  subscribe(listener: (state: PlayerState) => void): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => this.listeners.delete(listener);
  }

  getState(): PlayerState {
    return this.state;
  }

  private emit(patch: Partial<PlayerState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener(this.state);
  }

  /**
   * Fetch and decode every stem, then build the graph.
   *
   * All four are decoded before anything is built. A stem that arrives late
   * would otherwise have to be spliced into a running engine, which is exactly
   * how the sample-lock gets lost.
   */
  async load(stems: StemSource[], options: LoadOptions = {}): Promise<void> {
    this.dispose();

    const context = new AudioContext();
    this.context = context;
    await context.audioWorklet.addModule(WORKLET_URL);

    const decoded = await Promise.all(
      stems.map(async (stem) => {
        const response = await fetch(stem.url);
        if (!response.ok) throw new Error(`could not fetch ${stem.kind}: ${response.status}`);
        const buffer = await context.decodeAudioData(await response.arrayBuffer());
        return { kind: stem.kind, buffer };
      }),
    );

    // The worklet reads Float32Arrays per channel. Mono stems are duplicated so
    // every stem presents the same shape.
    const channels =
      options.mode === "two" ? foldToTwo(decoded) : decoded.map(asChannel);
    const payload = channels.map(({ name, data }) => ({ name, data }));
    this.loaded = channels.map((channel) => channel.name);

    const node = new AudioWorkletNode(context, "pitch-processor", {
      numberOfInputs: 0,
      numberOfOutputs: payload.length,
      outputChannelCount: payload.map(() => 2),
      processorOptions: {
        stems: payload,
        semitones: this.state.semitones,
        tempo: this.state.tempo,
        playing: false,
      },
    });
    this.node = node;

    const master = context.createGain();
    master.connect(context.destination);
    this.master = master;

    channels.forEach(({ name }, index) => {
      const gain = context.createGain();
      gain.gain.value = this.volumes.get(name) ?? 1;
      node.connect(gain, index);
      gain.connect(master);
      this.gains.set(name, gain);
    });

    node.port.onmessage = (event: MessageEvent<WorkletStatus>) => {
      const message = event.data;
      if (message.type === "status" && typeof message.posSeconds === "number") {
        // The clock. Everything that needs to know "where are we" reads this.
        this.emit({ position: message.posSeconds });
      } else if (message.type === "ended") {
        this.emit({ playing: false });
      }
    };

    const duration = decoded[0].buffer.duration;
    this.emit({ ready: true, duration, position: 0, playing: false });
  }

  async play(): Promise<void> {
    if (this.context === null || this.node === null) return;
    // Browsers start a context suspended until a gesture. Without this the
    // graph runs and nothing comes out, which is a confusing way to fail.
    if (this.context.state === "suspended") await this.context.resume();
    this.node.port.postMessage({ type: "play" });
    this.emit({ playing: true });
  }

  pause(): void {
    this.node?.port.postMessage({ type: "pause" });
    this.emit({ playing: false });
  }

  toggle(): Promise<void> | void {
    return this.state.playing ? this.pause() : this.play();
  }

  seek(seconds: number): void {
    const clamped = Math.max(0, Math.min(this.state.duration, seconds));
    this.node?.port.postMessage({ type: "seek", pos: clamped });
    // Reported optimistically so the scrubber does not jump back for the ~116ms
    // until the worklet's next status message. The clock still wins.
    this.emit({ position: clamped });
  }

  setKey(semitones: number): void {
    const clamped = Math.max(KEY_RANGE.min, Math.min(KEY_RANGE.max, Math.round(semitones)));
    this.node?.port.postMessage({ type: "pitch", semitones: clamped });
    this.emit({ semitones: clamped });
  }

  setTempo(ratio: number): void {
    const clamped = Math.max(TEMPO_RANGE.min, Math.min(TEMPO_RANGE.max, ratio));
    this.node?.port.postMessage({ type: "tempo", ratio: clamped });
    this.emit({ tempo: clamped });
  }

  /** Which channels the mixer should show faders for. */
  channels(): Channel[] {
    return [...this.loaded];
  }

  /** Per-channel volume, 0..1. The engine is untouched, so sync cannot be lost. */
  setVolume(kind: Channel, volume: number): void {
    const clamped = Math.max(0, Math.min(1, volume));
    this.volumes.set(kind, clamped);
    const gain = this.gains.get(kind);
    if (gain !== undefined && this.context !== null) {
      // A ramp, not an assignment: a step in gain is an audible click.
      gain.gain.setTargetAtTime(clamped, this.context.currentTime, FADE_SECONDS / 3);
    }
  }

  getVolume(kind: Channel): number {
    return this.volumes.get(kind) ?? 1;
  }

  dispose(): void {
    this.node?.port.postMessage({ type: "pause" });
    this.node?.disconnect();
    this.master?.disconnect();
    for (const gain of this.gains.values()) gain.disconnect();
    this.gains.clear();
    this.loaded = [];
    void this.context?.close();
    this.context = null;
    this.node = null;
    this.master = null;
    this.emit({ ready: false, playing: false, position: 0, duration: 0 });
  }
}


interface DecodedStem {
  kind: StemKind;
  buffer: AudioBuffer;
}

interface WorkletChannel {
  name: Channel;
  data: Float32Array[];
}

function asChannel({ kind, buffer }: DecodedStem): WorkletChannel {
  return {
    name: kind,
    data:
      buffer.numberOfChannels >= 2
        ? [buffer.getChannelData(0), buffer.getChannelData(1)]
        : [buffer.getChannelData(0), buffer.getChannelData(0)],
  };
}

/**
 * Vocals, and everything else summed into one backing channel.
 *
 * Summed here rather than fetched as a pre-mixed file: the four stems are
 * already downloaded and cached, and the cost that matters is the vocoder's,
 * which is per channel. Adding a fifth object to storage would spend chapter
 * 9's budget to save nothing.
 */
function foldToTwo(decoded: DecodedStem[]): WorkletChannel[] {
  const vocals = decoded.find((stem) => stem.kind === "vocals");
  const rest = decoded.filter((stem) => stem.kind !== "vocals");
  if (vocals === undefined || rest.length === 0) return decoded.map(asChannel);

  const length = Math.min(...decoded.map((stem) => stem.buffer.length));
  const backing = [new Float32Array(length), new Float32Array(length)];

  for (const stem of rest) {
    for (let channel = 0; channel < 2; channel++) {
      const source = stem.buffer.getChannelData(
        Math.min(channel, stem.buffer.numberOfChannels - 1),
      );
      const target = backing[channel];
      for (let i = 0; i < length; i++) target[i] += source[i];
    }
  }

  // The three were mixed at unity, so scale back to avoid clipping the sum.
  const scale = 1 / rest.length;
  for (const channel of backing) {
    for (let i = 0; i < length; i++) channel[i] *= scale;
  }

  return [asChannel(vocals), { name: "backing", data: backing }];
}
