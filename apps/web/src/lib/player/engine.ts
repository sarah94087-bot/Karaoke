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
 * `positionNow()` (T-2.6) is the one thing that looks like an exception and is
 * not. The worklet reports every 40 render quanta, about 116ms, which is longer
 * than the whole 100ms budget chapter 8 gives the lyrics. So between reports
 * the position is carried forward using `AudioContext.currentTime` - which is
 * the same audio clock the worklet is counting on, read from the other end.
 * Nothing here measures time with a browser timer.
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
  /**
   * The song ran out, as opposed to somebody having pressed pause. T-5.1 needs
   * the difference: reaching the end is what moves an evening on to the next
   * song in the queue, and a pause is a person deciding to stop.
   */
  ended: boolean;
}

/** Phase 0 (T-0.2.2): ~70ms so a mute does not click. */
const FADE_SECONDS = 0.07;

const WORKLET_URL = "/pitch-worklet.js";

/**
 * The position between two worklet reports.
 *
 * Exported and pure so the arithmetic can be tested without Web Audio: this is
 * what the lyrics are timed by, and the acceptance criterion for T-2.6 is a
 * number of milliseconds. `elapsed` is time from the **audio** clock, and the
 * read head advances at the playback rate - which is what tempo means here.
 */
export function extrapolate(
  reported: number,
  elapsed: number,
  tempo: number,
  duration: number,
): number {
  return Math.max(0, Math.min(duration, reported + elapsed * tempo));
}

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
    ended: false,
  };

  private listeners = new Set<(state: PlayerState) => void>();

  /**
   * The last thing the worklet said, and the audio-clock time it said it at.
   * Together they are what lets `positionNow()` fill in the gap between
   * reports without inventing a clock of its own.
   */
  private lastReport: { position: number; at: number } | null = null;

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
        this.lastReport = { position: message.posSeconds, at: context.currentTime };
        this.emit({ position: message.posSeconds });
      } else if (message.type === "ended") {
        this.emit({ playing: false, ended: true });
      }
    };

    const duration = decoded[0].buffer.duration;
    this.emit({ ready: true, duration, position: 0, playing: false, ended: false });
  }

  /**
   * Where the song is *right now*, not where it was at the last report.
   *
   * The worklet speaks every ~116ms; a highlight that only moved when it spoke
   * would be up to 116ms late on its own, before any error in the lyrics
   * themselves. So the reported position is carried forward by the audio
   * clock's own elapsed time, scaled by tempo - the read head advances at the
   * playback rate, which is exactly what tempo means here.
   */
  positionNow(): number {
    const report = this.lastReport;
    if (this.context === null || report === null || !this.state.playing) {
      return this.state.position;
    }
    return extrapolate(
      report.position,
      this.context.currentTime - report.at,
      this.state.tempo,
      this.state.duration,
    );
  }

  async play(): Promise<void> {
    if (this.context === null || this.node === null) return;
    // Browsers start a context suspended until a gesture. Without this the
    // graph runs and nothing comes out, which is a confusing way to fail.
    if (this.context.state === "suspended") await this.context.resume();
    this.node.port.postMessage({ type: "play" });
    this.emit({ playing: true, ended: false });
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
    // The estimate has to move with the seek, or the lyrics keep running from
    // where the song used to be until the worklet's next report.
    this.lastReport =
      this.context === null ? null : { position: clamped, at: this.context.currentTime };
    // Reported optimistically so the scrubber does not jump back for the ~116ms
    // until the worklet's next status message. The clock still wins.
    // Seeking also clears the end: someone who has gone back into a song that
    // finished is singing it again, not still standing at the end of it.
    this.emit({ position: clamped, ended: false });
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
    this.lastReport = null;
    this.emit({ ready: false, playing: false, position: 0, duration: 0, ended: false });
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
