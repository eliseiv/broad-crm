import { forwardRef, useId } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string | null;
  mono?: boolean;
  /** Иконка/кнопка справа внутри поля (например, toggle пароля). */
  trailing?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, mono, trailing, className, id, ...props },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const errorId = `${inputId}-error`;
  const hasError = Boolean(error);

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
          aria-describedby={hasError ? errorId : undefined}
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
      {hasError && (
        <p id={errorId} role="alert" className="text-[12px] text-status-red">
          {error}
        </p>
      )}
    </div>
  );
});
