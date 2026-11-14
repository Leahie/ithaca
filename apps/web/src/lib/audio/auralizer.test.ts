import { describe, expect, it } from 'vitest';
import { AURALIZATION, auralizationParams, impulseResponse, pinkNoise } from './auralizer';

const hour = (laeq_dba: number, bands_db: number[]) => ({ laeq_dba, bands_db });
const FLAT = [40, 40, 40, 40, 40, 40, 40, 40];
const RUMBLE = [48, 42, 36, 29, 26, 23, 20, 16];

describe('auralization parameters come from the report only (A4)', () => {
  it('gain follows laeq_dba: +6 dB doubles the amplitude', () => {
    const a = auralizationParams(hour(30, RUMBLE), 0.5, 65);
    const b = auralizationParams(hour(36, RUMBLE), 0.5, 65);
    expect(b.gain / a.gain).toBeCloseTo(10 ** (6 / 20), 6);
    expect(b.bandGains).toEqual(a.bandGains); // the spectrum is untouched by the level
  });

  it('the calibration level plays at full scale and never clips above it', () => {
    expect(auralizationParams(hour(65, FLAT), 0.5, 65).gain).toBeCloseTo(1);
    expect(auralizationParams(hour(80, FLAT), 0.5, 65).gain).toBe(1);
  });

  it('the filter follows bands_db: band ratios equal the dB differences', () => {
    const p = auralizationParams(hour(30, RUMBLE), 0.5);
    for (let i = 1; i < RUMBLE.length; i++) {
      expect(20 * Math.log10(p.bandGains[i]! / p.bandGains[0]!)).toBeCloseTo(RUMBLE[i]! - RUMBLE[0]!, 6);
    }
  });

  it('band gains are normalized to unit A-weighted power, so only laeq sets loudness', () => {
    for (const bands of [FLAT, RUMBLE]) {
      const p = auralizationParams(hour(30, bands), 0.5);
      const power = p.bandGains.reduce((s, g, i) => s + g * g * 10 ** (AURALIZATION.aWeightDb[i]! / 10), 0);
      expect(power).toBeCloseTo(1, 9);
    }
  });

  it('a uniform shift of every band changes nothing but the level', () => {
    const a = auralizationParams(hour(30, RUMBLE), 0.5);
    const b = auralizationParams(hour(30, RUMBLE.map((v) => v + 7)), 0.5);
    b.bandGains.forEach((g, i) => expect(g).toBeCloseTo(a.bandGains[i]!, 9));
  });
});

describe('impulse response from rt60_s', () => {
  const rate = 8000;
  const energy = (x: Float32Array, from = 0, to = x.length) => {
    let s = 0;
    for (let i = from; i < to; i++) s += x[i]! * x[i]!;
    return s;
  };

  it('has unit energy, so reverb does not change the level', () => {
    for (const rt of [0.3, 0.8, 1.6]) expect(energy(impulseResponse(rt, rate))).toBeCloseTo(1, 5);
  });

  it('decays 60 dB in rt60 seconds', () => {
    const rt = 1.0;
    const ir = impulseResponse(rt, rate);
    const window = Math.round(0.05 * rate);
    const early = energy(ir, Math.round(0.1 * rate), Math.round(0.1 * rate) + window);
    const late = energy(ir, Math.round(0.6 * rate), Math.round(0.6 * rate) + window);
    // 0.5 s apart at 60 dB/s is 30 dB.
    expect(10 * Math.log10(early / late)).toBeGreaterThan(27);
    expect(10 * Math.log10(early / late)).toBeLessThan(33);
  });

  it('a longer RT60 puts more of the energy in the tail', () => {
    const direct = (rt: number) => impulseResponse(rt, rate)[0]! ** 2;
    expect(direct(0.3)).toBeGreaterThan(direct(1.2));
    expect(direct(0.5)).toBeCloseTo(0.5, 6); // equal direct and reverberant energy at the reference
  });
});

describe('pink noise source', () => {
  it('is deterministic and has about equal power per octave', () => {
    const a = pinkNoise(1 << 15, 1);
    expect(Array.from(a.slice(0, 8))).toEqual(Array.from(pinkNoise(1 << 15, 1).slice(0, 8)));
    // Power in two neighbouring octaves, by differencing block averages (a crude filter bank).
    const blockPower = (block: number) => {
      let s = 0;
      let n = 0;
      for (let i = 0; i + 2 * block <= a.length; i += 2 * block) {
        let lo = 0;
        let hi = 0;
        for (let k = 0; k < block; k++) {
          lo += a[i + k]!;
          hi += a[i + block + k]!;
        }
        s += ((lo - hi) / (2 * block)) ** 2;
        n++;
      }
      return s / n;
    };
    const ratioDb = 10 * Math.log10(blockPower(8) / blockPower(16));
    expect(Math.abs(ratioDb)).toBeLessThan(3); // white noise would give about +3 dB
  });
});
