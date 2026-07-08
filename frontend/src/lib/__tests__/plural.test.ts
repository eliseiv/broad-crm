import { describe, expect, it } from 'vitest';
import { membersPlural, pluralRu, usersPlural } from '@/lib/plural';

const FORMS = { one: 'ядро', few: 'ядра', many: 'ядер' };

describe('pluralRu (русские формы мн.ч. по правилу окончаний, 08-design-system.md)', () => {
  // Примеры из словаря «ядро/ядра/ядер».
  it.each([
    [1, 'ядро'],
    [2, 'ядра'],
    [4, 'ядра'],
    [5, 'ядер'],
    [8, 'ядер'],
    [11, 'ядер'],
    [12, 'ядер'],
    [14, 'ядер'],
    [21, 'ядро'],
    [22, 'ядра'],
    [101, 'ядро'],
    [111, 'ядер'],
  ])('%i → «%s»', (n, expected) => {
    expect(pluralRu(n, FORMS)).toBe(expected);
  });

  // 0 и «многие» — форма many.
  it('0 → форма many', () => {
    expect(pluralRu(0, FORMS)).toBe('ядер');
  });
});

describe('usersPlural (число носителей роли)', () => {
  it.each([
    [1, '1 пользователь'],
    [2, '2 пользователя'],
    [3, '3 пользователя'],
    [5, '5 пользователей'],
    [0, '0 пользователей'],
    [11, '11 пользователей'],
    [21, '21 пользователь'],
  ])('%i → «%s»', (n, expected) => {
    expect(usersPlural(n)).toBe(expected);
  });
});

describe('membersPlural (число участников команды)', () => {
  it.each([
    [1, '1 участник'],
    [2, '2 участника'],
    [4, '4 участника'],
    [5, '5 участников'],
    [0, '0 участников'],
    [11, '11 участников'],
    [22, '22 участника'],
  ])('%i → «%s»', (n, expected) => {
    expect(membersPlural(n)).toBe(expected);
  });
});
