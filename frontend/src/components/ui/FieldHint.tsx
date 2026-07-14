import type { ReactNode } from 'react';

interface FieldHintProps {
  /** id, на который ссылается `aria-describedby` контрола (обязателен — иначе связи нет). */
  id: string;
  children: ReactNode;
}

/**
 * Подсказка (help-text) под полем формы — единый примитив ДС (08-design-system.md «Подсказка под
 * полем формы связывается с контролом», TD-061). Рендерится ВНУТРИ примитива поля (`ui/Input`,
 * `ui/Select`, `ui/Textarea`, `ui/MultiSelect`, `ui/Combobox`) и связывается с контролом через
 * `aria-describedby` — соседним `<p>` у потребителя подсказку рендерить НЕЛЬЗЯ (скринридер её
 * не озвучит).
 */
export function FieldHint({ id, children }: FieldHintProps) {
  return (
    <p id={id} className="text-[12px] leading-relaxed text-text-secondary">
      {children}
    </p>
  );
}
