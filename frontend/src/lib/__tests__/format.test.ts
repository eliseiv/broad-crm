import { describe, expect, it } from 'vitest';
import {
  formatCores,
  formatNumber,
  formatRelativeTime,
  formatUptime,
  pluralizeCores,
} from '@/lib/format';

describe('format helpers', () => {
  it('formats uptime as days hours minutes', () => {
    expect(formatUptime(1323120)).toBe('15д 7ч 32м');
    expect(formatUptime(59)).toBe('0м');
    expect(formatUptime(null)).toBe('—');
    expect(formatUptime(-1)).toBe('—');
  });

  it('formats relative timestamps', () => {
    const now = Date.parse('2026-06-28T12:00:00.000Z');

    expect(formatRelativeTime('2026-06-28T11:59:56.000Z', now)).toBe('только что');
    expect(formatRelativeTime('2026-06-28T11:59:30.000Z', now)).toBe('только что');
    expect(formatRelativeTime('2026-06-28T11:30:00.000Z', now)).toBe('30 мин назад');
    expect(formatRelativeTime('2026-06-28T09:00:00.000Z', now)).toBe('3 ч назад');
    expect(formatRelativeTime('2026-06-25T12:00:00.000Z', now)).toBe('3 дн назад');
    expect(formatRelativeTime(null, now)).toBe('—');
  });

  it('formats numbers with at most one fraction digit', () => {
    expect(formatNumber(8)).toBe('8');
    expect(formatNumber(2.6)).toBe('2.6');
  });

  it('pluralizes and formats CPU cores in Russian', () => {
    expect(pluralizeCores(1)).toBe('ядро');
    expect(pluralizeCores(2)).toBe('ядра');
    expect(pluralizeCores(5)).toBe('ядер');
    expect(pluralizeCores(8)).toBe('ядер');
    expect(pluralizeCores(11)).toBe('ядер');
    expect(pluralizeCores(22)).toBe('ядра');
    expect(formatCores(1)).toBe('1 ядро');
    expect(formatCores(2)).toBe('2 ядра');
    expect(formatCores(5)).toBe('5 ядер');
    expect(formatCores(8)).toBe('8 ядер');
    expect(formatCores(11)).toBe('11 ядер');
    expect(formatCores(22)).toBe('22 ядра');
  });
});
