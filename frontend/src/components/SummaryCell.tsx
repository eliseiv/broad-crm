import { cn } from '@/lib/cn';

/**
 * Ячейка сводной плашки над таблицей (подпись + крупное значение). Примитив
 * СУЩЕСТВУЮЩИЙ — он жил локально в `pages/BackendUsersPage.tsx` и вынесен сюда
 * без изменения разметки, когда второй потребитель появился на `/users`
 * (ADR-079 §10, ADR-080 §6). Новый компонент дизайн-системы этим НЕ вводится:
 * копия того же JSX в двух страницах разошлась бы при первой же правке.
 *
 * Обёртка — grid `gap-px` на фоне `bg-border-subtle` (разделители — просветы фона),
 * см. потребителей.
 */
export function SummaryCell({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={cn('bg-surface-1 px-5 py-4', className)}>
      <p className="text-[12px] text-text-tertiary">{label}</p>
      <p className="mt-1 text-xl font-bold text-text-primary">{value}</p>
    </div>
  );
}
