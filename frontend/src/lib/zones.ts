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

/**
 * Градиенты дуги по зоне (08-design-system.md). Тема-зависимы (ADR-033) —
 * ссылаются на CSS-переменные, переключаемые вместе с data-theme (index.css).
 */
export const ZONE_GRADIENT: Record<Zone, { from: string; to: string }> = {
  green: { from: 'var(--gauge-green-from)', to: 'var(--gauge-green-to)' },
  yellow: { from: 'var(--gauge-yellow-from)', to: 'var(--gauge-yellow-to)' },
  red: { from: 'var(--gauge-red-from)', to: 'var(--gauge-red-to)' },
};

/**
 * Сплошной цвет зоны (свечение gauge). Тема-зависим — статусные переменные
 * (index.css) переключаются с data-theme.
 */
export const ZONE_COLOR: Record<Zone, string> = {
  green: 'var(--status-green)',
  yellow: 'var(--status-yellow)',
  red: 'var(--status-red)',
};
