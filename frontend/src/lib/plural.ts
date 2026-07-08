/**
 * Русские формы множественного числа по правилу окончаний (то же правило, что
 * для «ядро/ядра/ядер», 08-design-system.md «Единицы измерения»).
 *  - оканчивается на 1 (кроме 11) → форма `one`;
 *  - на 2–4 (кроме 12–14) → форма `few`;
 *  - на 0, 5–9, 11–14 → форма `many`.
 */
export function pluralRu(n: number, forms: { one: string; few: string; many: string }): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return forms.one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return forms.few;
  return forms.many;
}

/** «N пользователь / пользователя / пользователей» (число носителей роли). */
export function usersPlural(n: number): string {
  return `${n} ${pluralRu(n, { one: 'пользователь', few: 'пользователя', many: 'пользователей' })}`;
}

/** «N участник / участника / участников» (число участников команды). */
export function membersPlural(n: number): string {
  return `${n} ${pluralRu(n, { one: 'участник', few: 'участника', many: 'участников' })}`;
}
