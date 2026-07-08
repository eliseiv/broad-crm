import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MultiSelect } from '@/components/ui/MultiSelect';
import type { MultiSelectOption } from '@/components/ui/MultiSelect';

const OPTIONS: MultiSelectOption[] = [
  { value: 'u1', label: 'Никита' },
  { value: 'u2', label: 'Мария' },
  { value: 'u3', label: 'Иван' },
];

describe('MultiSelect (08-design-system.md «Компонент мультивыбор»)', () => {
  it('рендерит опции как чекбоксы', () => {
    render(<MultiSelect label="Участники" value={[]} options={OPTIONS} onChange={vi.fn()} />);
    expect(screen.getByRole('checkbox', { name: 'Никита' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Мария' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Иван' })).toBeInTheDocument();
  });

  it('отмечает выбранные значения', () => {
    render(<MultiSelect value={['u2']} options={OPTIONS} onChange={vi.fn()} />);
    expect(screen.getByRole('checkbox', { name: 'Мария' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Никита' })).not.toBeChecked();
  });

  it('выбор добавляет значение через onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MultiSelect value={[]} options={OPTIONS} onChange={onChange} />);

    await user.click(screen.getByRole('checkbox', { name: 'Мария' }));

    expect(onChange).toHaveBeenCalledWith(['u2']);
  });

  it('снятие удаляет значение через onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MultiSelect value={['u1', 'u2']} options={OPTIONS} onChange={onChange} />);

    await user.click(screen.getByRole('checkbox', { name: 'Никита' }));

    expect(onChange).toHaveBeenCalledWith(['u2']);
  });

  it('locked-значение (лидер) всегда отмечено и недоступно для снятия', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<MultiSelect value={[]} options={OPTIONS} onChange={onChange} lockedValues={['u1']} />);

    const leader = screen.getByRole('checkbox', { name: 'Никита' });
    expect(leader).toBeChecked();
    expect(leader).toBeDisabled();

    // Клик по зафиксированному чекбоксу не приводит к onChange (снять нельзя).
    await user.click(leader);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('пустой список опций показывает подсказку', () => {
    render(<MultiSelect value={[]} options={[]} onChange={vi.fn()} emptyHint="Пока нет команд" />);
    expect(screen.getByText('Пока нет команд')).toBeInTheDocument();
  });

  it('показывает сообщение об ошибке', () => {
    render(
      <MultiSelect value={[]} options={OPTIONS} onChange={vi.fn()} error="Проверьте участников" />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Проверьте участников');
  });
});
