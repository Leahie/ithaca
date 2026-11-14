import { OCTAVE_BANDS_HZ, type NoiseHour } from '@ithaca/schema';

/**
 * Auralization of a NoiseReport (A4).
 *
 * What you hear is set by the report and nothing else:
 *   - loudness  <- laeq_dba        (master gain)
 *   - spectrum  <- bands_db        (one band-pass filter and gain per octave band)
 *   - reverb    <- rt60_s          (synthetic impulse response)
 *
 * The source is steady pink noise, which carries equal power in every octave band, so the
 * band gains alone shape the spectrum. The only free values are listed in AURALIZATION and
 * none of them changes the relation between two reports: they set the playback reference.
 */
export const AURALIZATION = {
  /** A-weighting per octave band, dB (IEC 61672), bands as in I-G14. */
  aWeightDb: [-26.2, -16.1, -8.6, -3.2, 0, 1.2, 1.0, -1.1],
  /** Band-pass Q for one-octave bands. */
  bandQ: Math.SQRT2,
  /** Reverberant-to-direct energy ratio is rt60_s / this. A longer RT60 sounds more reverberant. */
  reverbRefSeconds: 0.5,
  /** Parameter changes glide over this time, so toggles do not click. */
  rampSeconds: 0.12,
  /** Default calibration: the level, in dBA, that plays at full scale. */
  defaultFullScaleDba: 65,
} as const;

export interface AuralizationParams {
  /** Linear master gain, 0..1. */
  gain: number;
  /** Linear gain per octave band; A-weighted power of the set is 1. */
  bandGains: number[];
  rt60_s: number;
}

/** Pure mapping from one report hour to audio parameters. */
export function auralizationParams(hour: Pick<NoiseHour, 'laeq_dba' | 'bands_db'>, rt60_s: number, fullScaleDba: number = AURALIZATION.defaultFullScaleDba): AuralizationParams {
  const bands = OCTAVE_BANDS_HZ.map((_, i) => hour.bands_db[i] ?? -Infinity);
  const peak = Math.max(...bands);
  const shape = bands.map((b) => (Number.isFinite(b) ? 10 ** ((b - peak) / 20) : 0));
  // Normalize so the A-weighted power of the shaped noise is 1: loudness then follows laeq_dba alone.
  const weighted = Math.sqrt(shape.reduce((s, g, i) => s + g * g * 10 ** (AURALIZATION.aWeightDb[i]! / 10), 0)) || 1;
  return {
    gain: Math.min(1, 10 ** ((hour.laeq_dba - fullScaleDba) / 20)),
    bandGains: shape.map((g) => g / weighted),
    rt60_s: Math.max(0.05, rt60_s),
  };
}

/** Deterministic white noise in [-1, 1] (mulberry32). */
function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return (((t ^ (t >>> 14)) >>> 0) / 4294967296) * 2 - 1;
  };
}

/** Pink noise (Paul Kellet's filter), normalized to a 0.25 RMS so the band sum has headroom. */
export function pinkNoise(length: number, seed = 1): Float32Array {
  const white = seeded(seed);
  const out = new Float32Array(length);
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  let sum = 0;
  for (let i = 0; i < length; i++) {
    const w = white();
    b0 = 0.99886 * b0 + w * 0.0555179;
    b1 = 0.99332 * b1 + w * 0.0750759;
    b2 = 0.969 * b2 + w * 0.153852;
    b3 = 0.8665 * b3 + w * 0.3104856;
    b4 = 0.55 * b4 + w * 0.5329522;
    b5 = -0.7616 * b5 - w * 0.016898;
    const v = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362;
    b6 = w * 0.115926;
    out[i] = v;
    sum += v * v;
  }
  const scale = 0.25 / Math.sqrt(sum / length || 1);
  for (let i = 0; i < length; i++) out[i]! *= scale;
  return out;
}

/**
 * Impulse response for a room with the given RT60: a direct impulse plus a noise tail that
 * decays 60 dB in rt60 seconds. Total energy is 1, so the reverb does not change the level.
 */
export function impulseResponse(rt60_s: number, sampleRate: number, seed = 7): Float32Array {
  const length = Math.max(64, Math.ceil(Math.min(6, rt60_s * 1.3) * sampleRate));
  const noise = seeded(seed);
  const ir = new Float32Array(length);
  let tail = 0;
  for (let i = 1; i < length; i++) {
    const v = noise() * 10 ** ((-3 * (i / sampleRate)) / rt60_s);
    ir[i] = v;
    tail += v * v;
  }
  const ratio = rt60_s / AURALIZATION.reverbRefSeconds; // reverberant / direct energy
  const direct = Math.sqrt(1 / (1 + ratio));
  const tailScale = tail > 0 ? Math.sqrt(ratio / (1 + ratio) / tail) : 0;
  ir[0] = direct;
  for (let i = 1; i < length; i++) ir[i]! *= tailScale;
  return ir;
}

/** The Web Audio graph: pink noise -> 8 band filters -> convolver -> master gain. */
export class Auralizer {
  private ctx: AudioContext | null = null;
  private source: AudioBufferSourceNode | null = null;
  private bandGains: GainNode[] = [];
  private convolver: ConvolverNode | null = null;
  private master: GainNode | null = null;
  private rt60 = 0;
  private params: AuralizationParams | null = null;

  get playing(): boolean {
    return !!this.source;
  }

  async start(): Promise<void> {
    if (this.source) return;
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx ??= new Ctor();
    const ctx = this.ctx;
    if (ctx.state === 'suspended') await ctx.resume();

    const seconds = 6;
    const buffer = ctx.createBuffer(2, seconds * ctx.sampleRate, ctx.sampleRate);
    buffer.copyToChannel(pinkNoise(buffer.length, 1) as Float32Array<ArrayBuffer>, 0);
    buffer.copyToChannel(pinkNoise(buffer.length, 2) as Float32Array<ArrayBuffer>, 1);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;

    this.convolver = ctx.createConvolver();
    this.convolver.normalize = false;
    this.master = ctx.createGain();
    this.master.gain.value = 0;
    this.bandGains = OCTAVE_BANDS_HZ.map((hz) => {
      const filter = ctx.createBiquadFilter();
      filter.type = 'bandpass';
      filter.frequency.value = hz;
      filter.Q.value = AURALIZATION.bandQ;
      const gain = ctx.createGain();
      gain.gain.value = 0;
      source.connect(filter).connect(gain).connect(this.convolver!);
      return gain;
    });
    this.convolver.connect(this.master).connect(ctx.destination);
    this.rt60 = 0;
    source.start();
    this.source = source;
    if (this.params) this.update(this.params);
  }

  /** Apply report-derived parameters. Safe to call before start(). */
  update(params: AuralizationParams): void {
    this.params = params;
    const ctx = this.ctx;
    if (!ctx || !this.source || !this.master || !this.convolver) return;
    const at = ctx.currentTime;
    const tc = AURALIZATION.rampSeconds / 3;
    this.master.gain.setTargetAtTime(params.gain, at, tc);
    this.bandGains.forEach((g, i) => g.gain.setTargetAtTime(params.bandGains[i] ?? 0, at, tc));
    if (Math.abs(params.rt60_s - this.rt60) > 0.01) {
      this.rt60 = params.rt60_s;
      const ir = impulseResponse(params.rt60_s, ctx.sampleRate);
      const buffer = ctx.createBuffer(2, ir.length, ctx.sampleRate);
      buffer.copyToChannel(ir as Float32Array<ArrayBuffer>, 0);
      buffer.copyToChannel(impulseResponse(params.rt60_s, ctx.sampleRate, 11) as Float32Array<ArrayBuffer>, 1);
      this.convolver.buffer = buffer;
    }
  }

  stop(): void {
    const ctx = this.ctx;
    if (!ctx || !this.source || !this.master) return;
    const source = this.source;
    this.source = null;
    this.master.gain.setTargetAtTime(0, ctx.currentTime, 0.03);
    setTimeout(() => {
      try {
        source.stop();
        source.disconnect();
      } catch {
        /* already stopped */
      }
    }, 200);
  }

  dispose(): void {
    this.stop();
    void this.ctx?.close();
    this.ctx = null;
  }
}
