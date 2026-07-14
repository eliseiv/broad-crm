import { useId } from 'react';
import type { ReactNode } from 'react';
import { Checkbox } from '@/components/ui/Checkbox';
import { FieldHint } from '@/components/ui/FieldHint';
import { composeDescribedBy } from '@/lib/a11y';
import { cn } from '@/lib/cn';

export interface MultiSelectOption {
  value: string;
  label: string;
}

interface MultiSelectProps {
  label?: string;
  /** Выбранные значения. */
  value: string[];
  options: MultiSelectOption[];
  onChange: (next: string[]) => void;
  /**
   * Значения, зафиксированные как выбранные (нельзя снять) — например, лидер
   * в форме команды всегда является участником (08-design-system.md).
   */
  lockedValues?: string[];
  error?: string | null;
  /** Подсказка при пустом списке опций (текст ВНУТРИ списка, не help-text). */
  emptyHint?: string;
  /**
   * Подсказка (help-text) под полем. Рендерится примитивом и связывается с группой через
   * `aria-describedby` (08-design-system.md, TD-061) — соседним `<p>` не рендерить.
   */
  hint?: ReactNode;
  disabled?: boolean;
}

/**
 * Мультивыбор из списка сущностей на базе нативного `Checkbox` (08-design-system.md
 * «Компонент мультивыбор (MultiSelect)») — без новой зависимости. Применяется для
 * поля «Команды» (форма пользователя) и «Участники» (форма команды). Пустой выбор
 * допустим; `lockedValues` (лидер) — всегда отмечены и недоступны для снятия.
 * Доступность — нативные чекбоксы (клавиатура/скринридер); видимый focus-ring.
 */
export function MultiSelect({
  label,
  value,
  options,
  onChange,
  lockedValues = [],
  error,
  emptyHint = 'Нет доступных вариантов',
  hint,
  disabled = false,
}: MultiSelectProps) {
  const autoId = useId();
  const errorId = `${autoId}-error`;
  const hintId = `${autoId}-hint`;
  const hasError = Boolean(error);
  const hasHint = Boolean(hint);
  const locked = new Set(lockedValues);
  const selected = new Set(value);

  const toggle = (val: string, checked: boolean) => {
    if (locked.has(val)) return; // зафиксированные не меняются
    const next = new Set(value);
    if (checked) next.add(val);
    else next.delete(val);
    onChange(Array.from(next));
  };

  return (
    <div className="flex flex-col gap-1.5">
      {label && <span className="text-[13px] font-medium text-text-secondary">{label}</span>}
      <div
        role="group"
        aria-label={label}
        aria-invalid={hasError}
        aria-describedby={composeDescribedBy(hasHint && hintId, hasError && errorId)}
        className={cn(
          'scrollbar-none flex max-h-44 flex-col gap-2 overflow-y-auto rounded-[10px] border bg-surface-2 p-3',
          hasError ? 'border-status-red' : 'border-border-strong',
          disabled && 'cursor-not-allowed opacity-60',
        )}
      >
        {options.length === 0 ? (
          <p className="text-[13px] text-text-tertiary">{emptyHint}</p>
        ) : (
          options.map((opt) => {
            const isLocked = locked.has(opt.value);
            return (
              <Checkbox
                key={opt.value}
                label={opt.label}
                checked={selected.has(opt.value) || isLocked}
                disabled={disabled || isLocked}
                onChange={(e) => toggle(opt.value, e.target.checked)}
              />
            );
          })
        )}
      </div>
      {hasHint && <FieldHint id={hintId}>{hint}</FieldHint>}
      {hasError && (
        <p id={errorId} role="alert" className="text-[12px] text-status-red">
          {error}
        </p>
      )}
    </div>
  );
}
