import * as Menu from '@radix-ui/react-dropdown-menu';
import { MoreVertical } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';

/** Пункт kebab-меню (08-design-system.md «Компонент kebab-меню (DropdownMenu)»). */
export interface DropdownMenuItem {
  label: string;
  /** Иконка `lucide-react` (компонент), рендерится слева от подписи. */
  icon?: LucideIcon;
  onSelect: () => void;
  /** `danger` — деструктивный пункт («Удалить»), красный (--status-red). */
  tone?: 'default' | 'danger';
  disabled?: boolean;
}

interface DropdownMenuProps {
  items: DropdownMenuItem[];
  /** Обязательный aria-label триггера (у иконки-кнопки нет видимого текста). */
  triggerAriaLabel: string;
  /** Доп. классы триггера (напр. управление видимостью на hover строки). */
  triggerClassName?: string;
}

/**
 * Меню на 3 точки — обёртка над `@radix-ui/react-dropdown-menu` (уже в package.json,
 * НЕ новая зависимость; 08-design-system.md, ADR-061). Триггер — иконка-кнопка
 * (`more-vertical`); поверхность `--surface-1`, граница `--border-subtle`, тень card;
 * danger-пункт — `--status-red`. Esc/клик-вне/focus-trap/возврат фокуса — из Radix.
 * Пустой список пунктов (все скрыты гейтами) → меню не рендерится.
 */
export function DropdownMenu({ items, triggerAriaLabel, triggerClassName }: DropdownMenuProps) {
  if (items.length === 0) return null;
  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <button
          type="button"
          aria-label={triggerAriaLabel}
          onClick={(e) => e.stopPropagation()}
          className={cn(
            'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-text-tertiary transition-colors',
            'hover:bg-surface-3 hover:text-text-primary',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            'data-[state=open]:bg-surface-3 data-[state=open]:text-text-primary',
            triggerClassName,
          )}
        >
          <MoreVertical className="h-4 w-4" aria-hidden="true" />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="end"
          sideOffset={4}
          onClick={(e) => e.stopPropagation()}
          className={cn(
            'z-50 min-w-[11rem] rounded-[10px] border border-border-subtle bg-surface-1 p-1 shadow-card',
            'data-[state=open]:animate-content-in focus:outline-none',
          )}
        >
          {items.map((item, index) => {
            const Icon = item.icon;
            return (
              <Menu.Item
                key={`${item.label}-${index}`}
                disabled={item.disabled}
                onSelect={() => item.onSelect()}
                className={cn(
                  'flex cursor-pointer select-none items-center gap-2 rounded-md px-2.5 py-2 text-[13px] outline-none',
                  'data-[highlighted]:bg-surface-2',
                  'data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50',
                  item.tone === 'danger'
                    ? 'text-status-red data-[highlighted]:bg-status-red/10'
                    : 'text-text-primary',
                )}
              >
                {Icon && <Icon className="h-4 w-4" aria-hidden={true} />}
                {item.label}
              </Menu.Item>
            );
          })}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
