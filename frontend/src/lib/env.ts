function readInt(raw: string | undefined, fallback: number): number {
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/** Конфигурация фронтенда из VITE_* переменных (см. .env.example, 02-tech-stack.md). */
export const env = {
  /**
   * Базовый origin API БЕЗ суффикса '/api' (его добавляет buildUrl).
   * Пусто → same-origin: путь становится '/api/...'.
   * Заданный origin 'https://x' → 'https://x/api/...'. НЕ указывать '/api' здесь.
   */
  apiBaseUrl: (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, ''),
  /** Routine-polling списка серверов GET /api/servers (норматив 15000). */
  pollIntervalMs: readInt(import.meta.env.VITE_POLL_INTERVAL_MS, 15000),
  /** Опрос GET /{id}/status во время провижининга (pending/installing). */
  statusPollIntervalMs: readInt(import.meta.env.VITE_STATUS_POLL_INTERVAL_MS, 2500),
  /** URL Grafana для drill-down; пусто → ссылка скрыта. */
  grafanaUrl: (import.meta.env.VITE_GRAFANA_URL ?? '').replace(/\/$/, ''),
} as const;
