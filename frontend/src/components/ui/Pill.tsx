import type { CSSProperties } from 'react';
import { cn } from '@/lib/cn';

/** Тон пилюли → семантический токен (08-design-system.md «Цветовые пилюли»). */
export type PillTone = 'accent' | 'yellow' | 'neutral' | 'green';

/**
 * Заливка с низкой прозрачностью + акцентный текст по существующим токенам палитры
 * (новые цветовые токены не вводятся, ADR-030). Идиома mail `TagPill`, но заливка —
 * через `color-mix` с CSS-переменными токенов.
 */
const TONE_STYLE: Record<PillTone, CSSProperties> = {
  accent: {
    backgroundColor: 'color-mix(in srgb, rgb(var(--accent)) 16%, transparent)',
    color: 'rgb(var(--accent-hover))',
  },
  yellow: {
    backgroundColor: 'color-mix(in srgb, rgb(var(--status-yellow)) 16%, transparent)',
    color: 'rgb(var(--status-yellow))',
  },
  neutral: {
    backgroundColor: 'rgb(var(--surface-3))',
    color: 'rgb(var(--text-secondary))',
  },
  green: {
    backgroundColor: 'color-mix(in srgb, rgb(var(--status-green)) 16%, transparent)',
    color: 'rgb(var(--status-green))',
  },
};

interface PillProps {
  label: string;
  tone: PillTone;
  /** title-атрибут (полное значение при наведении). */
  title?: string;
  /**
   * Разрешить перенос длинного значения (значимый контент не обрезается — CLAUDE.md).
   * По умолчанию `whitespace-nowrap` (короткие бейджи вроде команды/«Команды нет»).
   */
  wrap?: boolean;
  className?: string;
}

/**
 * Цветная пилюля с заливкой (обобщение mail `TagPill`; `ui/Badge` заливку не даёт).
 * Скругление 8px (rounded-chip), компактные паддинги. Значения не усекаются — длинные
 * при `wrap` переносятся (08-design-system.md «Значимый контент виден полностью»).
 */
export function Pill({ label, tone, title, wrap = false, className }: PillProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-chip px-2 py-0.5 text-[11px] font-medium',
        wrap ? 'whitespace-normal break-words' : 'whitespace-nowrap',
        className,
      )}
      style={TONE_STYLE[tone]}
      title={title}
    >
      {label}
    </span>
  );
}
