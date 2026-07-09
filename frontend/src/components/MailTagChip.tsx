import type { CSSProperties } from 'react';
import { cn } from '@/lib/cn';

interface MailTagChipProps {
  /** Имя тега — текст чипа. */
  name: string;
  /** HEX из палитры тегов (08-design-system.md). */
  color: string;
  /** Точка-свотч слева (сплошной `color`) — вкладка «Теги». В ленте/детали не нужна. */
  dot?: boolean;
  /** Разрешить перенос длинного имени (деталь/вкладка «Теги»); false → `truncate`+title (компактный список). */
  wrap?: boolean;
  className?: string;
}

/**
 * Единый «тег-чип» дизайн-системы (08-design-system.md §«Тег-чип», нормативно) — во всех
 * местах CRM (лента писем, деталь письма, вкладка «Теги») тег рендерится одинаково по
 * `tag.color`. Это отдельный элемент, НЕ `ui/Pill` (принимает только tone-токены) и НЕ
 * `ui/Badge` (без заливки).
 *
 * Цвет/контраст — тема-зависимый `color-mix` (по расчёту WCAG в docs: worst-case 5.07,
 * все 16 комбинаций ≥ AA 4.5): текст = 50% tag + `--text-primary`, заливка = 16% tag +
 * `--surface-2`, граница = 40% tag + `--surface-2`. Сырой `tag.color` как текст запрещён
 * (ломает контраст тёмных цветов на тёмной теме и янтарного на светлой). Точка-свотч —
 * единственное место сплошного `tag.color` (индикатор хью, не текст).
 */
export function MailTagChip({
  name,
  color,
  dot = false,
  wrap = false,
  className,
}: MailTagChipProps) {
  const style: CSSProperties = {
    color: `color-mix(in srgb, ${color} 50%, var(--text-primary))`,
    backgroundColor: `color-mix(in srgb, ${color} 16%, var(--surface-2))`,
    borderColor: `color-mix(in srgb, ${color} 40%, var(--surface-2))`,
  };
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium',
        wrap ? 'max-w-full whitespace-normal break-words' : 'max-w-full truncate',
        className,
      )}
      style={style}
      title={name}
    >
      {dot && (
        <span
          className="h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: color }}
          aria-hidden="true"
        />
      )}
      <span className={wrap ? 'break-words' : 'truncate'}>{name}</span>
    </span>
  );
}
