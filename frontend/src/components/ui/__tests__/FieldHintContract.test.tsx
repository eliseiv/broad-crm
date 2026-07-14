import type { ReactElement } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Combobox } from '@/components/ui/Combobox';
import { Input } from '@/components/ui/Input';
import { MultiSelect } from '@/components/ui/MultiSelect';
import { Select } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';

/**
 * Контракт `aria-describedby` на ВСЕХ примитивах поля (08-design-system.md §«Подсказка под полем
 * формы связывается с контролом», TD-061):
 *
 *   • подсказка + ошибка → `aria-describedby="<hintId> <errorId>"` — оба id, порядок «подсказка,
 *     затем ошибка»: ошибка НЕ вытесняет подсказку;
 *   • только подсказка → `<hintId>`; только ошибка → `<errorId>`;
 *   • ни того, ни другого → атрибута НЕТ (висячий IDREF запрещён).
 *
 * Каждый id проверяется на РАЗРЕШИМОСТЬ в DOM (`getElementById`) — «атрибут выставлен» без узла
 * с таким id это и есть висячий IDREF, ради запрета которого норма писалась.
 *
 * `ui/Combobox` слота ошибки не имеет (ADR-052) ⇒ его контракт — подсказка/ничего.
 */

const LABEL = 'Поле';
const HINT = 'Подсказка поля';
const ERROR = 'Ошибка поля';

interface HintProps {
  hint?: string;
  error?: string;
}

interface PrimitiveCase {
  /** Имя примитива в отчёте. */
  name: string;
  /** У примитива есть слот ошибки (у Combobox — нет). */
  hasErrorSlot: boolean;
  renderCase: (props: HintProps) => ReactElement;
  /** Контрол, на котором обязан висеть `aria-describedby`. */
  control: () => HTMLElement;
}

const CASES: PrimitiveCase[] = [
  {
    name: 'Input',
    hasErrorSlot: true,
    renderCase: ({ hint, error }) => <Input label={LABEL} hint={hint} error={error} />,
    control: () => screen.getByLabelText(LABEL),
  },
  {
    name: 'Select',
    hasErrorSlot: true,
    renderCase: ({ hint, error }) => (
      <Select label={LABEL} options={[{ value: 'a', label: 'A' }]} hint={hint} error={error} />
    ),
    control: () => screen.getByLabelText(LABEL),
  },
  {
    name: 'Textarea',
    hasErrorSlot: true,
    renderCase: ({ hint, error }) => <Textarea label={LABEL} hint={hint} error={error} />,
    control: () => screen.getByLabelText(LABEL),
  },
  {
    name: 'MultiSelect',
    hasErrorSlot: true,
    renderCase: ({ hint, error }) => (
      <MultiSelect
        label={LABEL}
        value={[]}
        options={[{ value: 'a', label: 'A' }]}
        onChange={vi.fn()}
        hint={hint}
        error={error}
      />
    ),
    // Описание висит на группе чекбоксов (`role="group"`), а не на отдельном чекбоксе.
    control: () => screen.getByRole('group', { name: LABEL }),
  },
  {
    name: 'Combobox',
    hasErrorSlot: false,
    renderCase: ({ hint }) => (
      <Combobox
        label={LABEL}
        options={[{ value: 'a', label: 'A' }]}
        value={null}
        onChange={vi.fn()}
        query=""
        onQueryChange={vi.fn()}
        hint={hint}
      />
    ),
    control: () => screen.getByRole('combobox'),
  },
];

/** Список id из `aria-describedby` (пустой, если атрибута нет). */
function describedIds(el: HTMLElement): string[] {
  const attr = el.getAttribute('aria-describedby');
  return attr === null ? [] : attr.split(' ').filter(Boolean);
}

/** Тексты узлов, на которые ссылается `aria-describedby`. Висячий IDREF → падение теста. */
function describedTexts(el: HTMLElement): string[] {
  return describedIds(el).map((id) => {
    const node = document.getElementById(id);
    expect(node, `висячий IDREF: узла с id="${id}" нет в DOM`).not.toBeNull();
    return node?.textContent ?? '';
  });
}

describe.each(CASES)('$name — контракт aria-describedby (TD-061)', (c) => {
  it('только подсказка → один id подсказки, он разрешается в DOM', () => {
    render(c.renderCase({ hint: HINT }));

    const el = c.control();
    const hintNode = screen.getByText(HINT);

    expect(el).toHaveAttribute('aria-describedby', hintNode.id);
    expect(describedTexts(el)).toEqual([HINT]);
    expect(el).toHaveAccessibleDescription(HINT);
  });

  it('ни подсказки, ни ошибки → атрибута нет (висячий IDREF запрещён)', () => {
    render(c.renderCase({}));

    const el = c.control();
    expect(el).not.toHaveAttribute('aria-describedby');
    expect(el).toHaveAccessibleDescription('');
  });
});

// Кейсы с ошибкой — только для примитивов со слотом ошибки (`ui/Combobox` его не имеет, ADR-052).
describe.each(CASES.filter((c) => c.hasErrorSlot))(
  '$name — композиция подсказки и ошибки (TD-061)',
  (c) => {
    it('подсказка + ошибка → оба id, порядок «подсказка, затем ошибка»', () => {
      render(c.renderCase({ hint: HINT, error: ERROR }));

      const el = c.control();
      const hintNode = screen.getByText(HINT);
      const errorNode = screen.getByText(ERROR);

      expect(el).toHaveAttribute('aria-describedby', `${hintNode.id} ${errorNode.id}`);
      expect(describedTexts(el)).toEqual([HINT, ERROR]);
      // Ошибка НЕ вытесняет подсказку: скринридер читает обе.
      expect(el).toHaveAccessibleDescription(`${HINT} ${ERROR}`);
      expect(hintNode).toBeVisible();
    });

    it('только ошибка → один id ошибки (подсказки в списке нет)', () => {
      render(c.renderCase({ error: ERROR }));

      const el = c.control();
      const errorNode = screen.getByText(ERROR);

      expect(el).toHaveAttribute('aria-describedby', errorNode.id);
      expect(describedTexts(el)).toEqual([ERROR]);
      expect(el).toHaveAccessibleDescription(ERROR);
    });
  },
);

/**
 * ARIA-контракт `ui/Combobox` (ADR-052) не сломан подсказкой: `aria-controls`/
 * `aria-activedescendant` по-прежнему выводятся ТОЛЬКО когда `<ul role="listbox">` реально
 * отрисован, а `aria-describedby` живёт независимо от них.
 */
describe('Combobox — подсказка не ломает ARIA-контракт ADR-052', () => {
  it('панель закрыта: описание есть, aria-controls/activedescendant отсутствуют', () => {
    render(
      <Combobox
        label={LABEL}
        options={[{ value: 'a', label: 'A' }]}
        value={null}
        onChange={vi.fn()}
        query=""
        onQueryChange={vi.fn()}
        hint={HINT}
      />,
    );

    const input = screen.getByRole('combobox');
    expect(input).toHaveAccessibleDescription(HINT);
    expect(input).not.toHaveAttribute('aria-controls');
    expect(input).not.toHaveAttribute('aria-activedescendant');
    expect(input).toHaveAttribute('aria-expanded', 'false');
  });

  it('пустой источник опций: панель без listbox ⇒ aria-controls не выводится, описание цело', async () => {
    const user = userEvent.setup();
    render(
      <Combobox
        label={LABEL}
        options={[]}
        value={null}
        onChange={vi.fn()}
        query=""
        onQueryChange={vi.fn()}
        hint={HINT}
        noOptionsMessage="Нет вариантов"
      />,
    );

    const input = screen.getByRole('combobox');
    await user.click(input);

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute('aria-controls');
    expect(input).not.toHaveAttribute('aria-activedescendant');
    expect(input).toHaveAccessibleDescription(HINT);
  });

  it('панель открыта со списком: aria-controls указывает на существующий listbox', async () => {
    const user = userEvent.setup();
    render(
      <Combobox
        label={LABEL}
        options={[
          { value: 'a', label: 'A' },
          { value: 'b', label: 'B' },
        ]}
        value={null}
        onChange={vi.fn()}
        query=""
        onQueryChange={vi.fn()}
        hint={HINT}
      />,
    );

    const input = screen.getByRole('combobox');
    await user.click(input);

    const listbox = screen.getByRole('listbox');
    expect(input).toHaveAttribute('aria-controls', listbox.id);
    expect(document.getElementById(listbox.id)).not.toBeNull();
    expect(input).toHaveAccessibleDescription(HINT);
  });
});
