import { describe, expect, it } from 'vitest';
import { composeDescribedBy } from '@/lib/a11y';

/**
 * `composeDescribedBy` — ядро контракта `aria-describedby` (08-design-system.md «Подсказка под
 * полем формы связывается с контролом», TD-061): описание поля собирается из id подсказки **И**
 * id ошибки (список через пробел, порядок «подсказка, затем ошибка»), а не «или». Нет ни того,
 * ни другого → атрибут НЕ выводится (`undefined`) — висячий IDREF запрещён.
 */
describe('composeDescribedBy (TD-061)', () => {
  it('подсказка + ошибка → «<hintId> <errorId>» именно в этом порядке', () => {
    expect(composeDescribedBy('f-hint', 'f-error')).toBe('f-hint f-error');
  });

  it('только подсказка → «<hintId>»', () => {
    expect(composeDescribedBy('f-hint', false)).toBe('f-hint');
  });

  it('только ошибка → «<errorId>» (подсказки нет — её id в списке не появляется)', () => {
    expect(composeDescribedBy(false, 'f-error')).toBe('f-error');
  });

  it('ни того, ни другого → undefined (атрибут не выводится, висячий IDREF запрещён)', () => {
    expect(composeDescribedBy(false, false)).toBeUndefined();
    expect(composeDescribedBy()).toBeUndefined();
    expect(composeDescribedBy(null, undefined, '')).toBeUndefined();
  });

  it('фильтрует пустые/ложные значения, сохраняя порядок оставшихся id', () => {
    expect(composeDescribedBy(null, 'a', undefined, '', 'b', false)).toBe('a b');
  });
});
