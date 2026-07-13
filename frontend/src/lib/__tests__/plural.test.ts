import { describe, expect, it } from 'vitest';
import { mailsPlural, membersPlural, numbersPlural, pluralRu, usersPlural } from '@/lib/plural';

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

describe('numbersPlural (число SMS-номеров команды)', () => {
  it.each([
    [1, '1 номер'],
    [2, '2 номера'],
    [4, '4 номера'],
    [5, '5 номеров'],
    [0, '0 номеров'],
    [11, '11 номеров'],
    [22, '22 номера'],
  ])('%i → «%s»', (n, expected) => {
    expect(numbersPlural(n)).toBe(expected);
  });
});

// Формы «почта / почты / почт» — нормативный словарь 08-design-system.md
// («Счётчик почт (mailbox_count) — чип на карточке», ADR-048 §1). `0` допустим.
describe('mailsPlural (число почтовых ящиков команды, mailbox_count)', () => {
  it.each([
    [0, '0 почт'],
    [1, '1 почта'],
    [2, '2 почты'],
    [3, '3 почты'],
    [4, '4 почты'],
    [5, '5 почт'],
    [11, '11 почт'],
    [21, '21 почта'],
    [22, '22 почты'],
  ])('%i → «%s»', (n, expected) => {
    expect(mailsPlural(n)).toBe(expected);
  });
});
