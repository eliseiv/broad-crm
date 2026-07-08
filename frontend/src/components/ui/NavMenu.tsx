import { useState } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { ChevronDown } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { cn } from '@/lib/cn';

export interface NavMenuItem {
  to: string;
  label: string;
}

interface NavMenuProps {
  /** Подпись категории-триггера (напр. «Мониторинг»). */
  label: string;
  /** Категория активна (содержит текущий маршрут) — акцентная подсветка триггера. */
  active: boolean;
  /** Пункты категории (уже отфильтрованы по правам вызывающим). */
  items: NavMenuItem[];
}

/**
 * Категория-дропдаун верхней навигации (08-design-system.md «Навигация
 * (категории-дропдауны, AppLayout)», ADR-022). Триггер (подпись + chevron-down)
 * + панель с пунктами-NavLink. Клавиатурная доступность (открытие/закрытие,
 * стрелки, Esc, возврат фокуса) — от `@radix-ui/react-dropdown-menu`
 * (консистентно с radix Dialog). Граница безопасности — серверный 403; категории/
 * пункты — только UX. Категория без доступных пунктов не рендерится вызывающим.
 */
export function NavMenu({ label, active, items }: NavMenuProps) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger
        className={cn(
          'flex items-center gap-1 rounded-md px-3 py-2 text-[14px] font-medium transition-colors',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
          active || open ? 'text-accent' : 'text-text-secondary hover:text-text-primary',
        )}
      >
        {label}
        <ChevronDown
          className={cn('h-4 w-4 transition-transform', open && 'rotate-180')}
          aria-hidden="true"
        />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={8}
          className={cn(
            // Вертикальный стек пунктов (по одному в строку) + «квадратная» панель 6px
            // (ADR-023, 08-design-system.md «Навигация»).
            'z-50 flex min-w-[180px] flex-col rounded-nav border border-border-strong bg-surface-1 p-1.5 shadow-card',
            // Анимация появления для ЗАЯКОРЕННОЙ панели: только opacity + лёгкий
            // translateY (keyframe `fade-in`), БЕЗ translate(-50%,-50%) — та центрирующая
            // анимация (`content-in`) относится к модалке и вызывала «прыжок» дропдауна.
            'data-[state=open]:animate-fade-in focus:outline-none',
          )}
        >
          {items.map((item) => (
            <DropdownMenu.Item key={item.to} asChild>
              <NavLink
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'flex w-full cursor-pointer select-none items-center rounded-md px-3 py-2 text-[14px] font-medium outline-none transition-colors',
                    'data-[highlighted]:bg-surface-3 data-[highlighted]:text-text-primary',
                    isActive ? 'text-accent' : 'text-text-secondary',
                  )
                }
              >
                {item.label}
              </NavLink>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
