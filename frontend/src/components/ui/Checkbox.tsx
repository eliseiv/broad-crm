import { forwardRef, useId } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/cn';

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** Видимая подпись справа от бокса. Если отсутствует — задайте `aria-label`. */
  label?: ReactNode;
}

/**
 * Нативный стилизованный `<input type="checkbox">` (08-design-system.md «Компонент
 * Checkbox», 02-tech-stack.md) — без новой зависимости (по образцу нативного Select).
 * Тёмные токены: невыбранный — рамка/фон surface-2; выбранный — accent + контрастная
 * галочка; видимый focus-ring accent 2px; disabled — приглушённый. Используется в
 * матрице прав роли и как тумблер статуса «Активен».
 */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, className, id, disabled, ...props },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;

  return (
    <label
      htmlFor={inputId}
      className={cn(
        'inline-flex select-none items-center gap-2',
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
      )}
    >
      <span className="relative inline-flex h-[18px] w-[18px] shrink-0">
        <input
          ref={ref}
          id={inputId}
          type="checkbox"
          disabled={disabled}
          className={cn(
            'peer h-[18px] w-[18px] cursor-[inherit] appearance-none rounded-[5px] border bg-surface-2 transition-colors duration-150',
            'border-border-strong',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-0',
            'checked:border-accent checked:bg-accent',
            'disabled:cursor-not-allowed',
            className,
          )}
          {...props}
        />
        <Check
          className="pointer-events-none absolute inset-0 m-auto h-3 w-3 text-white opacity-0 peer-checked:opacity-100"
          aria-hidden="true"
          strokeWidth={3}
        />
      </span>
      {label != null && <span className="text-sm text-text-primary">{label}</span>}
    </label>
  );
});
