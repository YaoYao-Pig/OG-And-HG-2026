type Point = { x: number; y: number };
export type DensityContour = { level: number; segments: Float32Array };

// Equal-weight density of real nodes in layout space, not community boundaries.
// The fixed grid bounds both preprocessing cost and the number of drawn segments.
export function buildDensityContours(nodes: readonly Point[]): DensityContour[] {
  if (nodes.length < 3) return [];
  const size = 144;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x); maxX = Math.max(maxX, node.x);
    minY = Math.min(minY, node.y); maxY = Math.max(maxY, node.y);
  }
  const span = Math.max(maxX - minX, maxY - minY, 0.001) * 1.12;
  const originX = (minX + maxX - span) / 2;
  const originY = (minY + maxY - span) / 2;
  const step = span / (size - 1);
  const field = new Float32Array(size * size);
  for (const node of nodes) {
    const gx = (node.x - originX) / step;
    const gy = (node.y - originY) / step;
    for (let y = Math.max(0, Math.floor(gy) - 4); y <= Math.min(size - 1, Math.ceil(gy) + 4); y++) {
      for (let x = Math.max(0, Math.floor(gx) - 4); x <= Math.min(size - 1, Math.ceil(gx) + 4); x++) {
        field[y * size + x] += Math.exp(-((gx - x) ** 2 + (gy - y) ** 2) / 5);
      }
    }
  }
  const positive = Array.from(field).filter((value) => value > 0.5).sort((a, b) => a - b);
  if (!positive.length) return [];
  return [0.64, 0.84, 0.95].map((quantile, level) => {
    const threshold = positive[Math.floor((positive.length - 1) * quantile)];
    const segments: number[] = [];
    const triangle = (points: [number, number, number][]) => {
      const crossings: number[] = [];
      for (let edge = 0; edge < 3; edge++) {
        const a = points[edge], b = points[(edge + 1) % 3];
        if ((a[2] >= threshold) === (b[2] >= threshold)) continue;
        const t = (threshold - a[2]) / (b[2] - a[2]);
        crossings.push(originX + (a[0] + (b[0] - a[0]) * t) * step,
          originY + (a[1] + (b[1] - a[1]) * t) * step);
      }
      if (crossings.length === 4) segments.push(...crossings);
    };
    for (let y = 0; y < size - 1; y++) {
      for (let x = 0; x < size - 1; x++) {
        const a: [number, number, number] = [x, y, field[y * size + x]];
        const b: [number, number, number] = [x + 1, y, field[y * size + x + 1]];
        const c: [number, number, number] = [x + 1, y + 1, field[(y + 1) * size + x + 1]];
        const d: [number, number, number] = [x, y + 1, field[(y + 1) * size + x]];
        triangle([a, b, c]); triangle([a, c, d]);
      }
    }
    return { level, segments: new Float32Array(segments) };
  });
}
