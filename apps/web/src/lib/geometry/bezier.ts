/**
 * A CSS-style cubic-bezier easing: the curve from (0,0) to (1,1) with control points (x1,y1) and
 * (x2,y2). Returns a function from time 0..1 to progress 0..1.
 */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (t: number) => number {
  const at = (a: number, b: number, s: number) => 3 * a * s * (1 - s) ** 2 + 3 * b * s * s * (1 - s) + s ** 3;
  const slope = (a: number, b: number, s: number) => 3 * a * (1 - s) ** 2 + 6 * (b - a) * s * (1 - s) + 3 * (1 - b) * s * s;
  return (t) => {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    // Solve x(s) = t for the curve parameter s: a few Newton steps, then bisection if the slope is flat.
    let s = t;
    for (let i = 0; i < 6; i++) {
      const dx = at(x1, x2, s) - t;
      const d = slope(x1, x2, s);
      if (Math.abs(dx) < 1e-5) return at(y1, y2, s);
      if (Math.abs(d) < 1e-6) break;
      s -= dx / d;
    }
    let lo = 0;
    let hi = 1;
    s = t;
    for (let i = 0; i < 20; i++) {
      const x = at(x1, x2, s);
      if (Math.abs(x - t) < 1e-5) break;
      if (x < t) lo = s;
      else hi = s;
      s = (lo + hi) / 2;
    }
    return at(y1, y2, s);
  };
}
