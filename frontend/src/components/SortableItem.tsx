import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { cn } from '@/lib/cn';

interface SortableItemProps {
  id: string;
  children: ReactNode;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mq.matches);
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  return reduced;
}

/**
 * Обёртка sortable-карточки (@dnd-kit). Вся карточка — область хвата: listeners
 * навешаны на корневой div. Короткий клик (<200 мс) не стартует drag (PointerSensor
 * с activationConstraint) и проходит в onClick карточки → edit. Зажатие + движение →
 * drag. Кнопка «Удалить» гасит pointer/click (stopPropagation) внутри карточки.
 * 08-design-system.md «Перестановка карточек».
 */
export function SortableItem({ id, children }: SortableItemProps) {
  const reducedMotion = usePrefersReducedMotion();
  const { listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition: reducedMotion ? undefined : transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      // Только listeners (pointer). attributes (role=button/tabIndex) не навешиваем:
      // клавиатурный DnD — вне scope Этапа 1 (TD-022); a11y-интеракции (edit/удаление)
      // обеспечивает сама карточка.
      {...listeners}
      className={cn(
        // Обёртка занимает ячейку грида полностью (как раньше ServerCard напрямую):
        // h-full — равная высота карточек в ряду; w-full/min-w-0 — корректный расчёт
        // min-content внутренней сетки метрик (grid-cols-3) при minmax(0,1fr) колонках.
        'h-full w-full min-w-0',
        // touch-action не блокируем: delay-активация (200 мс) сама разводит
        // тап/скролл и drag, а touch-none ломал бы прокрутку списка на тач-устройствах.
        isDragging &&
          'relative z-20 opacity-70 shadow-[0_12px_32px_rgba(0,0,0,0.55)] [&>*]:pointer-events-none',
      )}
    >
      {children}
    </div>
  );
}
