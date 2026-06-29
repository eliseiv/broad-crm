export type Zone = 'green' | 'yellow' | 'red';

/**
 * Единый источник порогов зон (совпадает с backend — 04-api.md «Пороги зон»).
 * Граничные значения: 80 → yellow, 90 → yellow, 90.01 → red.
 */
export const ZONE_THRESHOLDS = { yellow: 80, red: 90 } as const;

export function usageToZone(p: number): Zone {
  if (p > ZONE_THRESHOLDS.red) return 'red'; // > 90
  if (p >= ZONE_THRESHOLDS.yellow) return 'yellow'; // 80..90
  return 'green'; // < 80
}

/** Градиенты дуги по зоне (08-design-system.md). */
export const ZONE_GRADIENT: Record<Zone, { from: string; to: string }> = {
  green: { from: '#16A34A', to: '#4ADE80' },
  yellow: { from: '#CA8A04', to: '#FACC15' },
  red: { from: '#DC2626', to: '#F87171' },
};

/** Сплошной цвет зоны (для свечения, точек статуса). */
export const ZONE_COLOR: Record<Zone, string> = {
  green: '#22C55E',
  yellow: '#EAB308',
  red: '#EF4444',
};
