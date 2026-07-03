import { forwardRef, useId } from 'react';
import type { TextareaHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string | null;
}

/**
 * Многострочное поле ввода (08-design-system.md «Компонент Textarea»). Нативный
 * `<textarea>`, стилизованный Tailwind — БЕЗ новой зависимости. Согласован с `Input`:
 * та же поверхность/граница/focus-ring; вертикальный ресайз разрешён (`resize-y`).
 */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { label, error, className, id, rows = 6, ...props },
  ref,
) {
  const autoId = useId();
  const areaId = id ?? autoId;
  const errorId = `${areaId}-error`;
  const hasError = Boolean(error);

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
        aria-describedby={hasError ? errorId : undefined}
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
      {hasError && (
        <p id={errorId} role="alert" className="text-[12px] text-status-red">
          {error}
        </p>
      )}
    </div>
  );
});
