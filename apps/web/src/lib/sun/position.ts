import { getPosition, getTimes } from 'suncalc';
import { compassToInterior, type Mat4 } from '@/lib/geometry/frames';

export interface SunPosition {
  /** Degrees clockwise from true north, [0, 360) (I-G11). */
  azimuth_deg: number;
  /** Apparent elevation above the horizon in degrees (refraction-corrected). */
  elevation_deg: number;
}

/**
 * Realtime sun position on the client. This is the one number the frontend computes (A3).
 * It is held within 0.5 degrees of the server pvlib reference by `position.test.ts`.
 */
export function sunPosition(instant: Date, lat: number, lon: number): SunPosition {
  const p = getPosition(instant, lat, lon);
  return { azimuth_deg: ((p.azimuth % 360) + 360) % 360, elevation_deg: p.altitude };
}

/** Unit vector, in the interior frame, that points from the scene toward the sun. */
export function sunDirectionInterior(instant: Date, lat: number, lon: number, interiorToWorld: Mat4): [number, number, number] {
  const { azimuth_deg, elevation_deg } = sunPosition(instant, lat, lon);
  return compassToInterior(interiorToWorld, azimuth_deg, elevation_deg);
}

export function sunTimes(instant: Date, lat: number, lon: number): { sunrise: Date | null; sunset: Date | null; solarNoon: Date } {
  const t = getTimes(instant, lat, lon);
  return { sunrise: t.sunrise, sunset: t.sunset, solarNoon: t.solarNoon };
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
/** An azimuth (degrees clockwise from north) as one of eight compass points. */
export const compassWord = (az: number) => COMPASS[Math.round(az / 45) % 8]!;
