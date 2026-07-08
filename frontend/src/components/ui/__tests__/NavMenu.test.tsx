import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { NavMenu } from '@/components/ui/NavMenu';
import type { NavMenuItem } from '@/components/ui/NavMenu';

const ITEMS: NavMenuItem[] = [
  { to: '/servers', label: 'Серверы' },
  { to: '/ai-keys', label: 'ИИ - ключи' },
];

function renderMenu(props?: { active?: boolean; items?: NavMenuItem[] }) {
  return render(
    <MemoryRouter>
      <NavMenu label="Мониторинг" active={props?.active ?? false} items={props?.items ?? ITEMS} />
    </MemoryRouter>,
  );
}

describe('NavMenu (категория-дропдаун, ADR-022, 08-design-system.md «Навигация»)', () => {
  it('рендерит триггер-категорию с подписью', () => {
    renderMenu();
    expect(screen.getByRole('button', { name: /Мониторинг/ })).toBeInTheDocument();
    // Пункты по умолчанию скрыты (панель закрыта).
    expect(screen.queryByRole('menuitem', { name: 'Серверы' })).not.toBeInTheDocument();
  });

  it('без доступных пунктов категория не рендерится (пустой items → null)', () => {
    renderMenu({ items: [] });
    expect(screen.queryByRole('button', { name: /Мониторинг/ })).not.toBeInTheDocument();
  });

  it('клик по триггеру открывает панель с пунктами', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole('button', { name: /Мониторинг/ }));

    expect(await screen.findByRole('menuitem', { name: 'Серверы' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'ИИ - ключи' })).toBeInTheDocument();
  });

  it('клавиатурная доступность: Enter на триггере открывает панель', async () => {
    const user = userEvent.setup();
    renderMenu();

    const trigger = screen.getByRole('button', { name: /Мониторинг/ });
    trigger.focus();
    expect(trigger).toHaveFocus();
    await user.keyboard('{Enter}');

    expect(await screen.findByRole('menuitem', { name: 'Серверы' })).toBeInTheDocument();
  });

  it('Escape закрывает открытую панель', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole('button', { name: /Мониторинг/ }));
    expect(await screen.findByRole('menuitem', { name: 'Серверы' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menuitem', { name: 'Серверы' })).not.toBeInTheDocument();
  });

  it('активная категория подсвечена акцентом, неактивная — вторичным цветом', () => {
    const { rerender } = renderMenu({ active: true });
    expect(screen.getByRole('button', { name: /Мониторинг/ }).className).toContain('text-accent');

    rerender(
      <MemoryRouter>
        <NavMenu label="Мониторинг" active={false} items={ITEMS} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: /Мониторинг/ }).className).toContain(
      'text-text-secondary',
    );
  });
});
