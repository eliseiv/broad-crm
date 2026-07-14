import { forwardRef, useId } from 'react';
import type { ReactNode, TextareaHTMLAttributes } from 'react';
import { FieldHint } from '@/components/ui/FieldHint';
import { composeDescribedBy } from '@/lib/a11y';
import { cn } from '@/lib/cn';

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string | null;
  /**
   * Подсказка (help-text) под полем. Рендерится примитивом и связывается с контролом через
   * `aria-describedby` (08-design-system.md, TD-061) — соседним `<p>` не рендерить.
   */
  hint?: ReactNode;
}

/**
 * Многострочное поле ввода (08-design-system.md «Компонент Textarea»). Нативный
 * `<textarea>`, стилизованный Tailwind — БЕЗ новой зависимости. Согласован с `Input`:
 * та же поверхность/граница/focus-ring; вертикальный ресайз разрешён (`resize-y`).
 */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, error, hint, className, id, rows = 6, ...props },
  ref,
) {
  const autoId = useId();
  const areaId = id ?? autoId;
  const errorId = `${areaId}-error`;
  const hintId = `${areaId}-hint`;
  const hasError = Boolean(error);
  const hasHint = Boolean(hint);

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={areaId} className="text-[13px] font-medium text-text-secondary">
          {label}
        </label>
      )}
      <textarea
        ref={ref}
        id={areaId}
        rows={rows}
        aria-invalid={hasError}
        aria-describedby={composeDescribedBy(hasHint && hintId, hasError && errorId)}
        className={cn(
          'w-full resize-y rounded-[10px] border bg-surface-2 px-3 py-2 text-sm text-text-primary',
          'placeholder:text-text-tertiary transition-colors duration-150',
          'focus:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40',
          'disabled:cursor-not-allowed disabled:opacity-60',
          hasError ? 'border-status-red focus-visible:ring-status-red/40' : 'border-border-strong',
          className,
        )}
        {...props}
      />
      {hasHint && <FieldHint id={hintId}>{hint}</FieldHint>}
      {hasError && (
        <p id={errorId} role="alert" className="text-[12px] text-status-red">
          {error}
        </p>
      )}
    </div>
  );
});
