import { forwardRef, useId } from 'react';
import type { ReactNode, SelectHTMLAttributes } from 'react';
import { ChevronDown } from 'lucide-react';
import { FieldHint } from '@/components/ui/FieldHint';
import { composeDescribedBy } from '@/lib/a11y';
import { cn } from '@/lib/cn';

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'> {
  label?: string;
  error?: string | null;
  options: SelectOption[];
  /**
   * Подсказка (help-text) под полем. Рендерится примитивом и связывается с контролом через
   * `aria-describedby` (08-design-system.md, TD-061) — соседним `<p>` не рендерить.
   */
  hint?: ReactNode;
}

/**
 * Нативный <select>, стилизованный Tailwind (08-design-system.md «Компонент Select»,
 * 02-tech-stack.md). Без новой зависимости — доступность даёт нативный контрол.
 * Согласован по высоте/фокусу/палитре с Input.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, error, options, hint, className, id, ...props },
  ref,
) {
  const autoId = useId();
  const selectId = id ?? autoId;
  const errorId = `${selectId}-error`;
  const hintId = `${selectId}-hint`;
  const hasError = Boolean(error);
  const hasHint = Boolean(hint);

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={selectId} className="text-[13px] font-medium text-text-secondary">
          {label}
        </label>
      )}
      <div className="relative">
        <select
          ref={ref}
          id={selectId}
          aria-invalid={hasError}
          aria-describedby={composeDescribedBy(hasHint && hintId, hasError && errorId)}
          className={cn(
            'h-10 w-full appearance-none rounded-[10px] border bg-surface-2 pl-3 pr-9 text-sm text-text-primary',
            'transition-colors duration-150',
            'focus:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40',
            'disabled:cursor-not-allowed disabled:opacity-60',
            hasError
              ? 'border-status-red focus-visible:ring-status-red/40'
              : 'border-border-strong',
            className,
          )}
          {...props}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value} className="bg-surface-2 text-text-primary">
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown
          className="pointer-events-none absolute inset-y-0 right-3 my-auto h-4 w-4 text-text-tertiary"
          aria-hidden="true"
        />
      </div>
      {hasHint && <FieldHint id={hintId}>{hint}</FieldHint>}
      {hasError && (
        <p id={errorId} role="alert" className="text-[12px] text-status-red">
          {error}
        </p>
      )}
    </div>
  );
});
