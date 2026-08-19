/*
 * pitch-worklet.js — phase vocoder pitch shifter for N stems driven by ONE clock.
 *
 * Taken from research/prototype/pitch-worklet.js unchanged (T-1.12 integrates the
 * phase 0 engine, it does not rewrite it). Phase 0 measured this at 0 samples of
 * drift across a whole song at every pitch and every tempo, and under a cent of
 * pitch error; changing it would mean re-earning those numbers.
 *
 * It lives in public/ because addModule() needs a URL, and it must stay a plain
 * .js file: AudioWorkletGlobalScope has no module bundler and no imports.
 *
 * Why one processor for all stems: T-0.2.1 proved the stems stay sample-locked when
 * they share a single AudioContext clock. Running an independent shifter per stem would
 * give each its own buffering and throw that away. Here every stem is advanced by the
 * same input hop in the same frame, so lock is structural, not a coincidence.
 *
 * Pitch and tempo are the same machine with two knobs.
 *   p = 2^(semitones/12)     pitch ratio
 *   T = tempo ratio          (0.5 = half speed, 1.5 = one-and-a-half)
 *
 * Stretch the signal by S, then read it back at rate R:
 *   final pitch    = R          -> R = p
 *   final duration = S / R      -> S = p / T
 *   analysis hop   Ha = Hs / S  =  Hs * T / p      (varies)
 *   synthesis hop  Hs = N/4                        (fixed)
 *
 * Because Hs is fixed, the overlap-add normalisation (1.5 for Hann at 75% overlap)
 * stays correct for every combination of pitch and tempo.
 * Note that frames-per-second-of-output depends on p only, not on T: tempo is free,
 * pitch is what costs CPU.
 *
 * Phase locking follows Laroche & Dolson: bins are grouped around spectral peaks and
 * their phases are kept rigid relative to the peak. Without it a plain vocoder smears
 * transients into the "phasey" sound that would fail this task's listening test.
 */

const N = 2048;           // FFT size
const HS = N / 4;         // fixed synthesis hop (75% overlap)
const RING = 1 << 14;     // stretched-output ring buffer per channel
const TWO_PI = Math.PI * 2;

// ---------- FFT: iterative radix-2, precomputed tables ----------
const LOG_N = Math.log2(N) | 0;
const REV = new Uint16Array(N);
for (let i = 0; i < N; i++) {
  let r = 0;
  for (let b = 0; b < LOG_N; b++) if (i & (1 << b)) r |= 1 << (LOG_N - 1 - b);
  REV[i] = r;
}
const COS = new Float32Array(N / 2), SIN = new Float32Array(N / 2);
for (let i = 0; i < N / 2; i++) {
  COS[i] = Math.cos(-TWO_PI * i / N);
  SIN[i] = Math.sin(-TWO_PI * i / N);
}

function fft(re, im, inverse) {
  for (let i = 0; i < N; i++) {
    const j = REV[i];
    if (j > i) {
      let t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }
  for (let len = 2; len <= N; len <<= 1) {
    const half = len >> 1, step = N / len;
    for (let i = 0; i < N; i += len) {
      for (let k = 0, idx = 0; k < half; k++, idx += step) {
        const c = COS[idx], s = inverse ? -SIN[idx] : SIN[idx];
        const a = i + k, b = a + half;
        const tr = re[b] * c - im[b] * s;
        const ti = re[b] * s + im[b] * c;
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr;        im[a] += ti;
      }
    }
  }
  if (inverse) {
    for (let i = 0; i < N; i++) { re[i] /= N; im[i] /= N; }
  }
}

// Hann window; with 75% overlap and analysis+synthesis windowing the OLA gain is 1.5
const WIN = new Float32Array(N);
for (let i = 0; i < N; i++) WIN[i] = 0.5 * (1 - Math.cos(TWO_PI * i / N));
const OLA_GAIN = 1.5;

function princarg(x) {
  return x - TWO_PI * Math.round(x / TWO_PI);
}

// ---------- per-channel vocoder state ----------
class ChannelState {
  constructor() {
    this.prevPhase = new Float32Array(N / 2 + 1);
    this.sumPhase  = new Float32Array(N / 2 + 1);
    this.ring      = new Float32Array(RING);
  }
  reset() {
    this.prevPhase.fill(0);
    this.sumPhase.fill(0);
    this.ring.fill(0);
  }
}

class PitchProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions;
    this.stems = o.stems;                 // [{name, data:[Float32Array L, Float32Array R]}]
    this.nStems = this.stems.length;
    this.length = this.stems[0].data[0].length;

    this.states = this.stems.map(s => s.data.map(() => new ChannelState()));

    // Initial state must come through processorOptions, not a port message: in an
    // OfflineAudioContext rendering starts before any posted message is delivered.
    const st0 = o.semitones || 0;
    this.alpha = Math.pow(2, st0 / 12);        // read rate == pitch ratio
    this.pendingAlpha = this.alpha;
    this.tempo = o.tempo || 1;
    this.pendingTempo = this.tempo;
    this.playing = !!o.playing;

    this.inputPos = 0;       // float, in source samples — SHARED by every stem
    this.writePos = 0;       // ring write head (start of newest frame)
    this.readPos  = 0;       // float ring read head
    this.valid    = 0;       // ring samples finalised so far

    // scratch
    this.re = new Float32Array(N);
    this.im = new Float32Array(N);
    this.mag = new Float32Array(N / 2 + 1);
    this.freq = new Float32Array(N / 2 + 1);
    this.isPeak = new Uint8Array(N / 2 + 1);

    this.frameCount = 0;
    this.cpuAccum = 0;
    this.quantaAccum = 0;
    this.reportCounter = 0;

    this.port.onmessage = e => this.onMessage(e.data);
  }

  onMessage(m) {
    switch (m.type) {
      case 'play':  this.playing = true;  break;
      case 'pause': this.playing = false; break;
      case 'pitch': this.pendingAlpha = Math.pow(2, m.semitones / 12); break;
      case 'tempo': this.pendingTempo = Math.max(0.25, Math.min(2, m.ratio)); break;
      case 'seek':  this.seek(m.pos); break;
    }
  }

  seek(posSeconds) {
    this.inputPos = Math.max(0, Math.min(this.length - 1, posSeconds * sampleRate));
    this.writePos = 0;
    this.readPos = 0;
    this.valid = 0;
    for (const st of this.states) for (const c of st) c.reset();
  }

  /* One analysis/synthesis frame across EVERY channel of EVERY stem.
     All of them consume the same integer hop, so they cannot drift apart. */
  processFrame() {
    const Ha = HS * this.tempo / this.alpha;

    const start = Math.floor(this.inputPos);
    const nextPos = this.inputPos + Ha;
    const hop = Math.floor(nextPos) - start;      // exact integer hop actually used

    // zero the region of the ring this frame is the first to touch
    const virginStart = this.writePos + N - HS;
    for (let s = 0; s < this.nStems; s++) {
      for (let c = 0; c < this.states[s].length; c++) {
        const ring = this.states[s][c].ring;
        for (let i = 0; i < HS; i++) ring[(virginStart + i) & (RING - 1)] = 0;
      }
    }

    for (let s = 0; s < this.nStems; s++) {
      const chans = this.stems[s].data;
      for (let c = 0; c < chans.length; c++) {
        this.processChannel(chans[c], this.states[s][c], start, hop);
      }
    }

    this.inputPos = nextPos;
    this.writePos = (this.writePos + HS) & (RING - 1);
    this.valid += HS;
    this.frameCount++;
  }

  processChannel(src, state, start, hop) {
    const { re, im, mag, freq, isPeak } = this;
    const prevPhase = state.prevPhase, sumPhase = state.sumPhase, ring = state.ring;
    const len = this.length;

    // --- analysis ---
    for (let i = 0; i < N; i++) {
      const idx = start + i;
      re[i] = (idx < len ? src[idx] : 0) * WIN[i];
      im[i] = 0;
    }
    fft(re, im, false);

    const expectedBase = TWO_PI * hop / N;
    const nb = N / 2;
    for (let k = 0; k <= nb; k++) {
      const r = re[k], ii = im[k];
      mag[k] = Math.sqrt(r * r + ii * ii);
      const phase = Math.atan2(ii, r);
      const dev = princarg(phase - prevPhase[k] - expectedBase * k);
      prevPhase[k] = phase;
      // true bin frequency in rad/sample, then advance by the synthesis hop
      freq[k] = (expectedBase * k + dev) / hop;
    }

    // --- peak picking (magnitude local max over +-2 bins) ---
    for (let k = 0; k <= nb; k++) {
      const m = mag[k];
      isPeak[k] = (m > 1e-9 &&
        m > (mag[k - 1] || 0) && m > (mag[k - 2] || 0) &&
        m > (mag[k + 1] || 0) && m > (mag[k + 2] || 0)) ? 1 : 0;
    }

    // --- synthesis phase, locked to the nearest peak ---
    let peak = -1;
    for (let k = 0; k <= nb; k++) if (isPeak[k]) { peak = k; break; }
    if (peak < 0) {
      for (let k = 0; k <= nb; k++) sumPhase[k] += HS * freq[k];
    } else {
      let cur = peak, next = -1;
      for (let k = peak + 1; k <= nb; k++) if (isPeak[k]) { next = k; break; }
      sumPhase[cur] += HS * freq[cur];
      for (let k = 0; k <= nb; k++) {
        if (next >= 0 && k > ((cur + next) >> 1)) {    // moved into the next peak's region
          cur = next;
          next = -1;
          for (let j = cur + 1; j <= nb; j++) if (isPeak[j]) { next = j; break; }
          sumPhase[cur] += HS * freq[cur];
        }
        if (k !== cur) {
          // rigid phase relative to the region's peak — this is what kills phasiness
          sumPhase[k] = sumPhase[cur] + (prevPhase[k] - prevPhase[cur]);
        }
      }
    }

    // --- inverse ---
    for (let k = 0; k <= nb; k++) {
      const m = mag[k], p = sumPhase[k];
      re[k] = m * Math.cos(p);
      im[k] = m * Math.sin(p);
      if (k > 0 && k < nb) { re[N - k] = re[k]; im[N - k] = -im[k]; }
    }
    im[0] = 0; im[nb] = 0;          // DC and Nyquist must stay real
    fft(re, im, true);

    // --- overlap-add into the ring ---
    const w = this.writePos;
    for (let i = 0; i < N; i++) {
      ring[(w + i) & (RING - 1)] += re[i] * WIN[i] / OLA_GAIN;
    }
  }

  process(inputs, outputs) {
    // NOTE: DSP cost is deliberately not measured here. `currentTime` does not advance
    // inside a render call and `performance` is not exposed in AudioWorkletGlobalScope,
    // so any in-worklet figure would read as a flat 0%. Cost is measured instead by
    // timing an OfflineAudioContext render on the main thread (see benchmark in pitch.html).
    const quantum = outputs[0][0].length;

    if (!this.playing) {
      for (const out of outputs) for (const ch of out) ch.fill(0);
      return true;
    }

    // pitch/tempo changes take effect on a frame boundary so the ring stays consistent
    if (this.pendingAlpha !== this.alpha) this.alpha = this.pendingAlpha;
    if (this.pendingTempo !== this.tempo) this.tempo = this.pendingTempo;
    const alpha = this.alpha;

    // make sure enough stretched samples exist for this quantum
    const needed = quantum * alpha + 4;
    let guard = 0;
    while (this.valid - this.readPos < needed && guard++ < 64) {
      if (this.inputPos >= this.length) break;
      this.processFrame();
    }

    if (this.inputPos >= this.length && this.valid - this.readPos < needed) {
      for (const out of outputs) for (const ch of out) ch.fill(0);
      this.playing = false;
      this.port.postMessage({ type: 'ended' });
      return true;
    }

    // resample the stretched signal by alpha -> original duration, shifted pitch
    for (let s = 0; s < this.nStems; s++) {
      const out = outputs[s];
      let rp = this.readPos;
      for (let ch = 0; ch < out.length; ch++) {
        const ring = this.states[s][Math.min(ch, this.states[s].length - 1)].ring;
        rp = this.readPos;
        for (let i = 0; i < quantum; i++) {
          const i0 = Math.floor(rp), frac = rp - i0;
          const a = ring[i0 & (RING - 1)], b = ring[(i0 + 1) & (RING - 1)];
          out[ch][i] = a + (b - a) * frac;
          rp += alpha;
        }
      }
      if (s === this.nStems - 1) this.readPos = rp;
    }

    if (++this.reportCounter >= 40) {
      this.port.postMessage({
        type: 'status',
        posSeconds: this.inputPos / sampleRate,
        frames: this.frameCount
      });
      this.reportCounter = 0;
    }
    return true;
  }
}

registerProcessor('pitch-processor', PitchProcessor);
