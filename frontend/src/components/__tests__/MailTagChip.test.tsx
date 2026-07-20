import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MailTagChip } from '@/components/MailTagChip';

/**
 * Тег-чип — единый элемент дизайн-системы (08-design-system.md §«Тег-чип», нормативно).
 * Ключевое требование WCAG AA: текст/фон/граница красятся через тема-зависимый
 * `color-mix`, а НЕ сырым `tag.color` (сырой HEX как текст проваливал контраст). Точка-
 * свотч — единственное место сплошного `tag.color`. jsdom темы/`color-mix` не вычисляет,
 * поэтому проверяем присутствие `color-mix` в инлайн-стилях и структуру, не итоговый HEX.
 */
describe('MailTagChip (единый тег-чип, WCAG AA color-mix)', () => {
  function chipOf(name: string): HTMLElement {
    return screen.getByText(name).closest('span[style]') as HTMLElement;
  }

  it('красит текст/фон/границу через color-mix с токенами в channel-формате (ADR-064 §C.2)', () => {
    render(<MailTagChip name="важное" color="#123456" />);
    const chip = chipOf('важное');
    const style = chip.getAttribute('style') ?? '';

    // Все три канала — color-mix с токенами темы, сырой цвет тега только внутри формулы.
    expect(style).toContain('color-mix');

    // ADR-064 §C.2: токен-аргументы обёрнуты в rgb(var(--x)) (голый var(--x) — невалидный
    // цвет, свойство «тихо» отбрасывается). Ассерт на ПОЛНУЮ обёртку, а не на подстроку
    // `var(--text-primary)` (которая ⊂ `rgb(var(--text-primary))` и маскировала бы формат).
    expect(style).toContain('rgb(var(--text-primary))');
    expect(style).toContain('rgb(var(--surface-2))');
    // Голого (необёрнутого) токена в стиле не осталось.
    expect(style).not.toMatch(/[^(]var\(--text-primary\)/);
    expect(style).not.toMatch(/[^(]var\(--surface-2\)/);

    // surface-2 участвует и в заливке, и в границе — обёрнут в обоих (§C.2: surface-2 ×2).
    expect(style.match(/rgb\(var\(--surface-2\)\)/g)).toHaveLength(2);

    // Тег-цвет (${color}) — произвольный цвет тега, НЕ токен ДС: остаётся сырым HEX,
    // rgb()-обёрткой не затронут.
    expect(style).toContain('#123456');
    expect(style).not.toContain('rgb(#123456');
    // Плоский color: НЕ равен сырому tag.color (иначе провал контраста).
    expect(chip.style.color).not.toBe('#123456');
  });

  it('без dot точка-свотч не рендерится (лента и деталь письма)', () => {
    const { container } = render(<MailTagChip name="важное" color="#123456" />);
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull();
  });

  it('dot=true → точка-свотч сплошным tag.color (вкладка «Теги»)', () => {
    const { container } = render(<MailTagChip name="счёт" color="#22C55E" dot />);
    const swatch = container.querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(swatch).not.toBeNull();
    // Свотч — сплошной сырой цвет (jsdom нормализует #22C55E → rgb), НЕ color-mix.
    expect(swatch.style.backgroundColor).toBe('rgb(34, 197, 94)');
    expect(swatch.getAttribute('style') ?? '').not.toContain('color-mix');
  });

  it('рендерит имя тега и проставляет title (усечение длинного имени)', () => {
    render(<MailTagChip name="Очень длинное имя тега" color="#2563eb" />);
    const chip = chipOf('Очень длинное имя тега');
    expect(chip).toHaveAttribute('title', 'Очень длинное имя тега');
  });
});
