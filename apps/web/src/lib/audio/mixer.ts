import { OCTAVE_BANDS_HZ } from '@ithaca/schema';
import { AURALIZATION, auralizationParams, pinkNoise } from './auralizer';
import type { SoundLevel } from './soundscape';

/**
 * The outside, heard on the big screen: one voice per chosen sound, each as loud as the
 * soundscape says for the hour. Everything is made here from noise and oscillators, no recordings:
 *   - highway: a steady roar shaped by its octave bands, with a slow swell;
 *   - streets: a shaped hum, with cars that swell and pan past more often as the level rises;
 *   - birds:   chirps, trills and whistles, more of them as the level rises (the dawn chorus);
 *   - cicadas: a buzzing band near 5 kHz; crickets: chirps of three pulses near 4.5 kHz;
 *   - frogs:   a chorus of pulsed calls near 1 to 2 kHz;
 *   - dogs:    bouts of barks, now and then, more of them when the neighbourhood is up.
 * Loudness follows the calibration: `fullScaleDba` plays at full scale.
 */
export interface Mix {
  highway: SoundLevel | null;
  streets: SoundLevel | null;
  birds: SoundLevel | null;
  cicadas: SoundLevel | null;
  crickets: SoundLevel | null;
  frogs: SoundLevel | null;
  dogs: SoundLevel | null;
  /**
   * Outside levels, dBA, of the sounds made of separate events. They set how many birds call,
   * cars pass and dogs bark; walls make each one quieter, not rarer.
   */
  outdoor: { birds?: number; streets?: number; dogs?: number };
}

export const SILENT_MIX: Mix = { highway: null, streets: null, birds: null, cicadas: null, crickets: null, frogs: null, dogs: null, outdoor: {} };

/** The level heard inside: each band less what the walls and windows stop (`attenuationDb`, per band). */
export function indoors(level: SoundLevel | null, attenuationDb: number[] | null): SoundLevel | null {
  if (!level || !attenuationDb) return level;
  const a = AURALIZATION.aWeightDb;
  const bands = level.bands_db.map((b, i) => Math.max(0, b - (attenuationDb[i] ?? 0)));
  const weighted = (bs: number[]) => bs.reduce((s, b, i) => s + (b > 0 ? 10 ** ((b + a[i]!) / 10) : 0), 0);
  const before = weighted(level.bands_db);
  const after = weighted(bands);
  const drop = before > 0 && after > 0 ? 10 * Math.log10(after / before) : -60;
  return { laeq_dba: Math.max(0, level.laeq_dba + drop), bands_db: bands };
}

/** Linear gain for a level: 1 at `fullScaleDba`. */
const gainFor = (laeq: number, fullScaleDba: number) => (laeq > 0 ? Math.min(1, 10 ** ((laeq - fullScaleDba) / 20)) : 0);
/** RMS of the noise buffers; a voice at gain 1 plays this loud. */
const UNIT_RMS = 0.25;
const RAMP = AURALIZATION.rampSeconds / 3;

/** Deterministic noise in [-1, 1]. */
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

function normalize(x: Float32Array): Float32Array {
  let sum = 0;
  for (const v of x) sum += v * v;
  const k = UNIT_RMS / Math.sqrt(sum / x.length || 1);
  for (let i = 0; i < x.length; i++) x[i]! *= k;
  return x;
}

/** Cicadas: noise through a resonance near 5 kHz, pulsing at ~110 Hz, with slow surges. */
function cicadaBuffer(rate: number, seconds: number, seed: number): Float32Array {
  const n = Math.floor(rate * seconds);
  const out = new Float32Array(n);
  const white = seeded(seed);
  const w0 = (2 * Math.PI * 4900) / rate;
  const alpha = Math.sin(w0) / (2 * 3);
  const [b0, b2, a0, a1, a2] = [alpha, -alpha, 1 + alpha, -2 * Math.cos(w0), 1 - alpha];
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < n; i++) {
    const x = white();
    const y = (b0 * x + b2 * x2 - a1 * y1 - a2 * y2) / a0;
    x2 = x1; x1 = x; y2 = y1; y1 = y;
    const t = i / rate;
    // The loop is a whole number of surges and pulses long, so it repeats without a seam.
    const pulse = 0.5 + 0.5 * Math.sin(2 * Math.PI * 110 * t);
    const surge = 0.7 + 0.3 * Math.sin((2 * Math.PI * t) / seconds);
    out[i] = y * pulse * surge;
  }
  return normalize(out);
}

/** Crickets: a few insects, each a chirp of three short pulses near 4.5 kHz, at its own pace. */
function cricketBuffer(rate: number, seconds: number, seed: number): Float32Array {
  const n = Math.floor(rate * seconds);
  const out = new Float32Array(n);
  const rnd = seeded(seed);
  for (let c = 0; c < 4; c++) {
    const freq = 4300 + 500 * (rnd() * 0.5 + 0.5);
    const period = seconds / Math.round(3 + 3 * (rnd() * 0.5 + 0.5)); // whole chirps per loop
    const offset = (rnd() * 0.5 + 0.5) * period;
    const amp = 0.6 + 0.4 * (rnd() * 0.5 + 0.5);
    for (let i = 0; i < n; i++) {
      const t = (i / rate + offset) % period;
      const p = Math.floor(t / 0.032);
      const within = t - p * 0.032;
      if (p > 2 || within > 0.018) continue;
      const env = Math.sin((Math.PI * within) / 0.018);
      out[i]! += amp * env * Math.sin((2 * Math.PI * freq * i) / rate);
    }
  }
  return normalize(out);
}

/** Frogs: a chorus, each frog a call of quick pulses on its own pitch, repeated at its own pace. */
function frogBuffer(rate: number, seconds: number, seed: number): Float32Array {
  const n = Math.floor(rate * seconds);
  const out = new Float32Array(n);
  const rnd = seeded(seed);
  const u = () => rnd() * 0.5 + 0.5;
  for (let f = 0; f < 6; f++) {
    const freq = 900 + 1100 * u();
    const pulseHz = 18 + 30 * u();
    const callS = 0.25 + 0.45 * u();
    const period = seconds / Math.round(2 + 4 * u()); // whole calls per loop
    const offset = u() * period;
    const amp = 0.4 + 0.6 * u();
    for (let i = 0; i < n; i++) {
      const t = (i / rate + offset) % period;
      if (t > callS) continue;
      const env = Math.sin((Math.PI * t) / callS);
      const pulse = Math.max(0, Math.sin(2 * Math.PI * pulseHz * t)) ** 2;
      out[i]! += amp * env * pulse * Math.sin((2 * Math.PI * freq * i) / rate);
    }
  }
  return normalize(out);
}

/** A looped noise voice through eight octave band filters, so its spectrum is the dataset's. */
class BandVoice {
  readonly input: GainNode;
  readonly output: GainNode;
  private bands: GainNode[];
  private source: AudioBufferSourceNode;

  constructor(private ctx: AudioContext, buffer: AudioBuffer, bedGain = 1) {
    this.input = ctx.createGain();
    this.output = ctx.createGain();
    this.output.gain.value = 0;
    const bed = ctx.createGain();
    bed.gain.value = bedGain;
    this.source = ctx.createBufferSource();
    this.source.buffer = buffer;
    this.source.loop = true;
    this.source.connect(bed).connect(this.input);
    const sum = ctx.createGain();
    this.bands = OCTAVE_BANDS_HZ.map((hz) => {
      const filter = ctx.createBiquadFilter();
      filter.type = 'bandpass';
      filter.frequency.value = hz;
      filter.Q.value = AURALIZATION.bandQ;
      const g = ctx.createGain();
      g.gain.value = 0;
      this.input.connect(filter).connect(g).connect(sum);
      return g;
    });
    sum.connect(this.output);
    this.source.start(0, Math.random() * buffer.duration);
  }

  set(level: SoundLevel | null, fullScaleDba: number): void {
    const at = this.ctx.currentTime;
    if (!level || level.laeq_dba <= 0) {
      this.output.gain.setTargetAtTime(0, at, RAMP);
      return;
    }
    const p = auralizationParams(level, 0.1, fullScaleDba);
    this.output.gain.setTargetAtTime(p.gain, at, RAMP);
    this.bands.forEach((g, i) => g.gain.setTargetAtTime(p.bandGains[i] ?? 0, at, RAMP));
  }
}

/** A steady looped buffer at a level. */
class LoopVoice {
  readonly output: GainNode;
  constructor(private ctx: AudioContext, buffer: AudioBuffer) {
    this.output = ctx.createGain();
    this.output.gain.value = 0;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    source.connect(this.output);
    source.start(0, Math.random() * buffer.duration);
  }
  set(level: SoundLevel | null, fullScaleDba: number): void {
    this.output.gain.setTargetAtTime(level ? gainFor(level.laeq_dba, fullScaleDba) : 0, this.ctx.currentTime, RAMP);
  }
}

/** Share of street energy carried by separate passing cars; the hum carries the rest. */
const PASS_SHARE = 0.5;
const PASS_SIGMA_S = 1.2;
const BIRD_TICK_MS = 150;
/** Mean length of one bird call, s, and the share of it that is sound. */
const CALL_S = 0.5;
const CALL_FILL = 0.5;

export class SoundscapeMixer {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private highway: BandVoice | null = null;
  private streets: BandVoice | null = null;
  private passBuffer: AudioBuffer | null = null;
  private birdsBus: GainNode | null = null;
  private cicadas: LoopVoice | null = null;
  private crickets: LoopVoice | null = null;
  private frogs: LoopVoice | null = null;
  private dogsBus: GainNode | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private mix: Mix = SILENT_MIX;
  private fullScale: number = AURALIZATION.defaultFullScaleDba;
  private on = false;

  async start(): Promise<void> {
    if (this.on) return;
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx ??= new Ctor();
    const ctx = this.ctx;
    if (ctx.state === 'suspended') await ctx.resume();
    if (!this.master) this.build(ctx);
    this.master!.gain.setTargetAtTime(1, ctx.currentTime, 0.05);
    this.on = true;
    this.timer = setInterval(() => this.tick(), BIRD_TICK_MS);
    this.update(this.mix, this.fullScale);
  }

  private build(ctx: AudioContext): void {
    const rate = ctx.sampleRate;
    const stereo = (make: (seed: number) => Float32Array, seeds: [number, number]) => {
      const a = make(seeds[0]);
      const buffer = ctx.createBuffer(2, a.length, rate);
      buffer.copyToChannel(a as Float32Array<ArrayBuffer>, 0);
      buffer.copyToChannel(make(seeds[1]) as Float32Array<ArrayBuffer>, 1);
      return buffer;
    };
    const pink = (seconds: number) => (seed: number) => pinkNoise(seconds * rate, seed);

    this.master = ctx.createGain();
    this.master.gain.value = 0;
    const limiter = ctx.createDynamicsCompressor();
    limiter.threshold.value = -6;
    limiter.ratio.value = 12;
    this.master.connect(limiter).connect(ctx.destination);

    this.highway = new BandVoice(ctx, stereo(pink(7), [11, 12]));
    // The highway breathes slowly as the traffic bunches up and thins out.
    const swell = ctx.createGain();
    const lfo = ctx.createOscillator();
    const depth = ctx.createGain();
    lfo.frequency.value = 0.07;
    depth.gain.value = 0.18;
    lfo.connect(depth).connect(swell.gain);
    lfo.start();
    this.highway.output.connect(swell).connect(this.master);

    this.streets = new BandVoice(ctx, stereo(pink(6), [21, 22]), Math.sqrt(1 - PASS_SHARE));
    this.streets.output.connect(this.master);
    this.passBuffer = ctx.createBuffer(1, 10 * rate, rate);
    this.passBuffer.copyToChannel(pinkNoise(this.passBuffer.length, 23) as Float32Array<ArrayBuffer>, 0);

    this.birdsBus = ctx.createGain();
    this.birdsBus.connect(this.master);

    this.cicadas = new LoopVoice(ctx, stereo((s) => cicadaBuffer(rate, 8, s), [31, 32]));
    this.cicadas.output.connect(this.master);
    this.crickets = new LoopVoice(ctx, stereo((s) => cricketBuffer(rate, 6, s), [41, 42]));
    this.crickets.output.connect(this.master);
    this.frogs = new LoopVoice(ctx, stereo((s) => frogBuffer(rate, 9, s), [51, 52]));
    this.frogs.output.connect(this.master);

    this.dogsBus = ctx.createGain();
    this.dogsBus.connect(this.master);
  }

  /** Set what plays and how loud. Safe before start(). */
  update(mix: Mix, fullScaleDba: number): void {
    this.mix = mix;
    this.fullScale = fullScaleDba;
    if (!this.on) return;
    this.highway?.set(mix.highway, fullScaleDba);
    this.streets?.set(mix.streets, fullScaleDba);
    this.cicadas?.set(mix.cicadas, fullScaleDba);
    this.crickets?.set(mix.crickets, fullScaleDba);
    this.frogs?.set(mix.frogs, fullScaleDba);
  }

  /** Cars per second on the streets: more as the outside level rises (twice the energy, twice the cars). */
  private passRate(): number {
    const l = this.mix.streets ? (this.mix.outdoor.streets ?? this.mix.streets.laeq_dba) : 0;
    return l > 0 ? Math.min(0.4, Math.max(1 / 60, 0.2 * 10 ** ((l - 50) / 10))) : 0;
  }

  /** Bird calls per second: a busy dawn chorus near 48 dBA outside, a call a minute near 25. */
  private callRate(): number {
    const l = this.mix.birds ? (this.mix.outdoor.birds ?? this.mix.birds.laeq_dba) : 0;
    return l > 15 ? Math.min(3, 0.15 * 10 ** ((l - 35) / 10)) : 0;
  }

  /** Bouts of barking per second: one every 20 s or so when the dogs are up (about 29 dBA outside), rare at night. */
  private barkRate(): number {
    const l = this.mix.dogs ? (this.mix.outdoor.dogs ?? this.mix.dogs.laeq_dba) : 0;
    return l > 10 ? Math.min(0.2, 0.02 * 10 ** ((l - 25) / 10)) : 0;
  }

  private tick(): void {
    const dt = BIRD_TICK_MS / 1000;
    const calls = this.callRate();
    if (calls > 0 && Math.random() < 1 - Math.exp(-calls * dt)) this.bird(calls);
    const cars = this.passRate();
    if (cars > 0 && Math.random() < 1 - Math.exp(-cars * dt)) this.car(cars);
    const barks = this.barkRate();
    if (barks > 0 && Math.random() < 1 - Math.exp(-barks * dt)) this.bark(barks);
  }

  /** One bout of barking: one to four barks from one dog, somewhere in the stereo field. */
  private bark(rate: number): void {
    const ctx = this.ctx;
    if (!ctx || !this.dogsBus || !this.mix.dogs) return;
    const n = 1 + Math.floor(Math.random() * 4);
    const len = 0.16;
    const gap = 0.25 + Math.random() * 0.2;
    // Rare bouts are louder each, so the hour's average energy is the dataset's level.
    const duty = Math.max(0.005, Math.min(1, rate * n * len));
    const peak = Math.min(0.9, (UNIT_RMS * Math.SQRT2 * gainFor(this.mix.dogs.laeq_dba, this.fullScale)) / Math.sqrt(duty));
    const pan = ctx.createStereoPanner();
    pan.pan.value = (Math.random() * 2 - 1) * 0.8;
    // A bark is a short buzzy tone whose pitch drops, through the resonance of a dog's mouth.
    const mouth = ctx.createBiquadFilter();
    mouth.type = 'bandpass';
    mouth.frequency.value = 700 + Math.random() * 500;
    mouth.Q.value = 1.4;
    mouth.connect(pan).connect(this.dogsBus);
    const pitch = 280 + Math.random() * 260;
    let t = ctx.currentTime + 0.05;
    for (let i = 0; i < n; i++) {
      const osc = ctx.createOscillator();
      osc.type = 'sawtooth';
      const env = ctx.createGain();
      osc.frequency.setValueAtTime(pitch * 1.25, t);
      osc.frequency.exponentialRampToValueAtTime(pitch * 0.8, t + len);
      env.gain.setValueAtTime(0, t);
      env.gain.linearRampToValueAtTime(peak, t + 0.012);
      env.gain.setTargetAtTime(0, t + 0.04, len / 4);
      osc.connect(env).connect(mouth);
      osc.onended = () => [osc, env].forEach((x) => x.disconnect());
      osc.start(t);
      osc.stop(t + len + 0.1);
      t += len + gap;
    }
    setTimeout(() => [mouth, pan].forEach((x) => x.disconnect()), (t - ctx.currentTime + 0.5) * 1000);
  }

  /** One car: a Gaussian swell of the streets' shaped noise that pans across. */
  private car(rate: number): void {
    const ctx = this.ctx;
    if (!ctx || !this.streets || !this.passBuffer) return;
    const duration = 6 * PASS_SIGMA_S;
    const energy = 0.5 + Math.random();
    const peak = Math.SQRT2 * Math.sqrt((PASS_SHARE * energy) / (rate * PASS_SIGMA_S * Math.sqrt(Math.PI)));
    const curve = new Float32Array(64);
    for (let i = 0; i < curve.length; i++) {
      const t = (i / (curve.length - 1) - 0.5) * duration;
      curve[i] = peak * Math.exp(-(t * t) / (2 * PASS_SIGMA_S * PASS_SIGMA_S));
    }
    const at = ctx.currentTime + 0.05;
    const voice = ctx.createBufferSource();
    voice.buffer = this.passBuffer;
    const env = ctx.createGain();
    env.gain.value = 0;
    env.gain.setValueCurveAtTime(curve, at, duration);
    const pan = ctx.createStereoPanner();
    const side = Math.random() < 0.5 ? -1 : 1;
    pan.pan.setValueAtTime(-0.7 * side, at);
    pan.pan.linearRampToValueAtTime(0.7 * side, at + duration);
    voice.connect(env).connect(pan).connect(this.streets.input);
    voice.onended = () => [voice, env, pan].forEach((n) => n.disconnect());
    voice.start(at, Math.random() * (this.passBuffer.duration - duration));
    voice.stop(at + duration + 0.05);
  }

  /** One bird call: a few notes from one of three kinds of song, somewhere in the stereo field. */
  private bird(rate: number): void {
    const ctx = this.ctx;
    if (!ctx || !this.birdsBus || !this.mix.birds) return;
    // Sparse calls are louder each, so the hour's average energy is the dataset's level.
    const duty = Math.max(0.02, Math.min(1, rate * CALL_S * CALL_FILL));
    const peak = Math.min(0.9, (UNIT_RMS * Math.SQRT2 * gainFor(this.mix.birds.laeq_dba, this.fullScale)) / Math.sqrt(duty));
    const pan = ctx.createStereoPanner();
    pan.pan.value = (Math.random() * 2 - 1) * 0.8;
    pan.connect(this.birdsBus);
    const base = 2200 + Math.random() * 2600;
    const kind = Math.random();
    const notes: { f0: number; f1: number; len: number; gap: number }[] = [];
    if (kind < 0.45) {
      // a chirp: two to five short sweeps
      const n = 2 + Math.floor(Math.random() * 4);
      const sweep = (Math.random() < 0.5 ? -1 : 1) * (500 + Math.random() * 1200);
      for (let i = 0; i < n; i++) notes.push({ f0: base, f1: base + sweep, len: 0.06 + Math.random() * 0.06, gap: 0.04 + Math.random() * 0.05 });
    } else if (kind < 0.75) {
      // a trill: many quick notes
      const n = 8 + Math.floor(Math.random() * 8);
      for (let i = 0; i < n; i++) notes.push({ f0: base + 300, f1: base - 300, len: 0.025, gap: 0.02 });
    } else {
      // a whistle: one or two long slides
      const n = 1 + Math.floor(Math.random() * 2);
      for (let i = 0; i < n; i++) notes.push({ f0: base * 0.7, f1: base * (0.9 + Math.random() * 0.3), len: 0.25 + Math.random() * 0.15, gap: 0.12 });
    }
    let t = ctx.currentTime + 0.05;
    for (const note of notes) {
      const osc = ctx.createOscillator();
      const env = ctx.createGain();
      osc.frequency.setValueAtTime(note.f0, t);
      osc.frequency.exponentialRampToValueAtTime(note.f1, t + note.len);
      env.gain.setValueAtTime(0, t);
      env.gain.linearRampToValueAtTime(peak, t + Math.min(0.01, note.len / 3));
      env.gain.setTargetAtTime(0, t + note.len * 0.6, note.len / 6);
      osc.connect(env).connect(pan);
      osc.onended = () => [osc, env].forEach((n) => n.disconnect());
      osc.start(t);
      osc.stop(t + note.len + 0.05);
      t += note.len + note.gap;
    }
    setTimeout(() => pan.disconnect(), (t - ctx.currentTime + 0.5) * 1000);
  }

  stop(): void {
    if (!this.on || !this.ctx || !this.master) return;
    this.on = false;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.master.gain.setTargetAtTime(0, this.ctx.currentTime, 0.03);
    const ctx = this.ctx;
    setTimeout(() => {
      if (!this.on && ctx.state === 'running') void ctx.suspend();
    }, 250);
  }

  dispose(): void {
    this.stop();
    void this.ctx?.close();
    this.ctx = null;
    this.master = null;
  }
}
