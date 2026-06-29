/** Форматирует uptime в секундах → «Nд Nч Nм» (08-design-system.md, рус. сокращения). */
export function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—';
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}д`);
  if (hours > 0 || days > 0) parts.push(`${hours}ч`);
  parts.push(`${minutes}м`);
  return parts.join(' ');
}

/**
 * Относительное время от ISO-метки (08-design-system.md):
 * <60с → «только что», 1–59 мин → «N мин назад», 1–23 ч → «N ч назад», ≥1 дн → «N дн назад».
 */
export function formatRelativeTime(
  iso: string | null | undefined,
  now: number = Date.now(),
): string {
  if (!iso) return '—';
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '—';
  const diffSec = Math.max(0, Math.round((now - ts) / 1000));
  if (diffSec < 60) return 'только что';
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin} мин назад`;
  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 24) return `${diffHour} ч назад`;
  const diffDay = Math.round(diffHour / 24);
  return `${diffDay} дн назад`;
}

/** Аккуратное число: целые без дробной части, иначе один знак. */
export function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** Русская форма мн.ч. для «ядро» по числу (08-design-system.md). */
export function pluralizeCores(n: number): string {
  const abs = Math.abs(Math.trunc(n));
  const mod10 = abs % 10;
  const mod100 = abs % 100;
  if (mod10 === 1 && mod100 !== 11) return 'ядро';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'ядра';
  return 'ядер';
}

/** «N ядер» с правильной формой (1 ядро / 2–4 ядра / 5+ ядер). */
export function formatCores(total: number): string {
  return `${total} ${pluralizeCores(total)}`;
}
