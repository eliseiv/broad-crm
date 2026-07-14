import { forwardRef, useId } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { FieldHint } from '@/components/ui/FieldHint';
import { composeDescribedBy } from '@/lib/a11y';
import { cn } from '@/lib/cn';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string | null;
  mono?: boolean;
  /** Иконка/кнопка справа внутри поля (например, toggle пароля). */
  trailing?: ReactNode;
  /**
   * Подсказка (help-text) под полем. Рендерится примитивом и связывается с контролом через
   * `aria-describedby` (08-design-system.md, TD-061) — соседним `<p>` не рендерить.
   */
  hint?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, mono, trailing, hint, className, id, ...props },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const errorId = `${inputId}-error`;
  const hintId = `${inputId}-hint`;
  const hasError = Boolean(error);
  const hasHint = Boolean(hint);

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={inputId} className="text-[13px] font-medium text-text-secondary">
          {label}
        </label>
      )}
      <div className="relative">
        <input
          ref={ref}
          id={inputId}
          aria-invalid={hasError}
          aria-describedby={composeDescribedBy(hasHint && hintId, hasError && errorId)}
          className={cn(
            'h-10 w-full rounded-[10px] border bg-surface-2 px-3 text-sm text-text-primary',
            'placeholder:text-text-tertiary transition-colors duration-150',
            'focus:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40',
            'disabled:cursor-not-allowed disabled:opacity-60',
            mono && 'font-mono tracking-tight',
            trailing && 'pr-10',
            hasError
              ? 'border-status-red focus-visible:ring-status-red/40'
              : 'border-border-strong',
            className,
          )}
          {...props}
        />
        {trailing && <div className="absolute inset-y-0 right-2 flex items-center">{trailing}</div>}
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
