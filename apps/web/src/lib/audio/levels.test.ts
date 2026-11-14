import type { NoiseHour } from '@ithaca/schema';
import { describe, expect, it } from 'vitest';
import { AURALIZATION, trafficTexture } from './auralizer';
import { noiseAt, trafficShare } from './levels';

const day: NoiseHour[] = Array.from({ length: 24 }, (_, hour) => ({ hour, laeq_dba: 20 + hour, bands_db: [hour, 2 * hour] }));

describe('noiseAt', () => {
  it('is the hour itself at the middle of the hour', () => {
    expect(noiseAt(day, 8 * 60 + 30)).toEqual({ laeq_dba: 28, bands_db: [8, 16] });
  });

  it('interpolates between hours in dB', () => {
    expect(noiseAt(day, 9 * 60)!.laeq_dba).toBeCloseTo(28.5);
  });

  it('wraps round midnight', () => {
    expect(noiseAt(day, 0)!.laeq_dba).toBeCloseTo((43 + 20) / 2);
  });

  it('falls back to the hour it has when a neighbour is missing', () => {
    expect(noiseAt([day[5]!], 5 * 60 + 50)!.laeq_dba).toBe(25);
    expect(noiseAt([], 300)).toBeNull();
  });
});

describe('trafficShare', () => {
  it('splits energy between roads and the background', () => {
    expect(trafficShare([{ name: 'Road', contribution_dba: 30, kind: 'road' }, { name: 'Background', contribution_dba: 30, kind: 'background' }])).toBeCloseTo(0.5);
  });

  it('reads the background from the name when kind is missing', () => {
    expect(trafficShare([{ name: 'Endicott Ln traffic', contribution_dba: 40 }, { name: 'Background', contribution_dba: 30 }])).toBeCloseTo(10 / 11);
  });
});

describe('trafficTexture', () => {
  it('passes at the peak rate in the loudest hour, and half as often 3 dB down', () => {
    expect(trafficTexture(40, 40, 0.9).ratePerS).toBeCloseTo(AURALIZATION.peakPassesPerSecond);
    expect(trafficTexture(37, 40, 0.9).ratePerS).toBeCloseTo(AURALIZATION.peakPassesPerSecond / 2, 2);
  });

  it('keeps a steady share and a minimum rate', () => {
    expect(trafficTexture(0, 40, 0.9)).toEqual({ share: AURALIZATION.maxPassShare, ratePerS: AURALIZATION.minPassesPerSecond });
    expect(trafficTexture(40, 40, 0).share).toBe(0);
  });
});
