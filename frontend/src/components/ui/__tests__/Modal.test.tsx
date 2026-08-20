import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Modal } from '@/components/ui/Modal';

/**
 * Регресс-гейт раскладки модалки.
 *
 * Прод-баг: высокая форма («Новый сотрудник» — ФИО, Телеграм, пароль, роли, команды,
 * блоки каналов) уходила за верхний и нижний край экрана. Контент центрирован
 * (`top-1/2 -translate-y-1/2`) и не имел ни предела высоты, ни прокрутки, поэтому
 * крайние поля становились физически недостижимы — прокручивать было нечего.
 *
 * jsdom не считает раскладку, поэтому проверяются именно те классы, которые её
 * задают: предел по вьюпорту + колоночный flex + прокручиваемое тело. Тест сторожит
 * СВОЙСТВО (тело скроллится, шапка/футер закреплены), а не конкретные пиксели.
 */
describe('Modal (раскладка по высоте)', () => {
  function renderModal() {
    return render(
      <Modal open onOpenChange={() => {}} title="Новый сотрудник" footer={<button>ОК</button>}>
        <p>Поле формы</p>
      </Modal>,
    );
  }

  it('ограничивает высоту вьюпортом и раскладывается колонкой', () => {
    renderModal();

    const content = screen.getByRole('dialog');
    expect(content.className).toContain('max-h-[calc(100dvh-2rem)]');
    expect(content.className).toContain('flex');
    expect(content.className).toContain('flex-col');
  });

  it('тело прокручивается и умеет сжиматься (min-h-0), шапка и футер закреплены', () => {
    renderModal();

    const body = screen.getByText('Поле формы').parentElement;
    expect(body).not.toBeNull();
    expect(body!.className).toContain('overflow-y-auto');
    // Без min-h-0 flex-элемент не сжимается ниже content-size и полосы прокрутки
    // не появляется — именно это делало поля недостижимыми.
    expect(body!.className).toContain('min-h-0');
    expect(body!.className).toContain('flex-1');

    const footer = screen.getByRole('button', { name: 'ОК' }).parentElement;
    expect(footer!.className).toContain('shrink-0');
  });
});
