import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SecretRevealField } from '@/components/DetailFields';

// toast (sonner) — spy на ветку ошибки reveal.
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('sonner', () => ({ toast }));

const MASK = '••••••••';

describe('SecretRevealField (reveal по требованию, ADR-035)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('по умолчанию скрыт: показывает маску, есть кнопка-глаз «Показать», значение не запрошено', () => {
    const reveal = vi.fn().mockResolvedValue({ value: 'p@ss-secret' });
    render(
      <SecretRevealField
        label="Пароль"
        reveal={reveal}
        showAria="Показать пароль"
        hideAria="Скрыть пароль"
        canReveal
      />,
    );

    expect(screen.getByText(MASK)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Показать пароль' })).toBeInTheDocument();
    // Значение не показано и reveal ещё не вызывался (on-demand).
    expect(screen.queryByText('p@ss-secret')).not.toBeInTheDocument();
    expect(reveal).not.toHaveBeenCalled();
  });

  it('клик по «глазу» вызывает reveal-функцию и показывает значение', async () => {
    const user = userEvent.setup();
    const reveal = vi.fn().mockResolvedValue({ value: 'p@ss-secret' });
    render(
      <SecretRevealField
        label="Пароль"
        reveal={reveal}
        showAria="Показать пароль"
        hideAria="Скрыть пароль"
        canReveal
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Показать пароль' }));

    expect(reveal).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('p@ss-secret')).toBeInTheDocument();
    // Маска больше не отображается, глаз теперь «Скрыть».
    expect(screen.queryByText(MASK)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Скрыть пароль' })).toBeInTheDocument();
  });

  it('повторный клик скрывает значение обратно в маску (plaintext не держится)', async () => {
    const user = userEvent.setup();
    const reveal = vi.fn().mockResolvedValue({ value: 'p@ss-secret' });
    render(
      <SecretRevealField
        label="Пароль"
        reveal={reveal}
        showAria="Показать пароль"
        hideAria="Скрыть пароль"
        canReveal
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Показать пароль' }));
    await screen.findByText('p@ss-secret');

    await user.click(screen.getByRole('button', { name: 'Скрыть пароль' }));

    expect(screen.queryByText('p@ss-secret')).not.toBeInTheDocument();
    expect(screen.getByText(MASK)).toBeInTheDocument();
    // Скрытие не делает нового запроса (значение просто стирается из локального стейта).
    expect(reveal).toHaveBeenCalledTimes(1);
  });

  it('ошибка reveal (reject) → toast «Не удалось показать», значение не показано, маска остаётся', async () => {
    const user = userEvent.setup();
    const reveal = vi.fn().mockRejectedValue(new Error('boom'));
    render(
      <SecretRevealField
        label="Пароль"
        reveal={reveal}
        showAria="Показать пароль"
        hideAria="Скрыть пароль"
        canReveal
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Показать пароль' }));

    expect(reveal).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith('Не удалось показать');
    // Значение не раскрыто, маска на месте, глаз снова «Показать».
    expect(screen.getByText(MASK)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Показать пароль' })).toBeInTheDocument();
  });

  it('canReveal=false → статичная маска БЕЗ кнопки-глаза (reveal недоступен)', () => {
    const reveal = vi.fn();
    render(
      <SecretRevealField
        label="Пароль"
        reveal={reveal}
        showAria="Показать пароль"
        hideAria="Скрыть пароль"
        canReveal={false}
      />,
    );

    expect(screen.getByText(MASK)).toBeInTheDocument();
    // Без права reveal — ни «Показать», ни «Скрыть», ни «Скопировать».
    expect(screen.queryByRole('button', { name: 'Показать пароль' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Скрыть пароль' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Скопировать' })).not.toBeInTheDocument();
    expect(reveal).not.toHaveBeenCalled();
  });

  it('maskDisplay задаёт кастомную маску (напр. key_masked ИИ-ключа)', () => {
    const reveal = vi.fn().mockResolvedValue({ value: 'sk-proj-FULL' });
    render(
      <SecretRevealField
        label="Ключ"
        reveal={reveal}
        showAria="Показать ключ"
        hideAria="Скрыть ключ"
        canReveal
        maskDisplay="sk-p…bA3T"
      />,
    );

    expect(screen.getByText('sk-p…bA3T')).toBeInTheDocument();
    expect(screen.queryByText(MASK)).not.toBeInTheDocument();
  });
});
