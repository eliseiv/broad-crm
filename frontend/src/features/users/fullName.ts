import type { UserListItem } from '@/types/api';

/**
 * Отображаемое имя пользователя (08-design-system.md «Страница Пользователи»,
 * ADR-079 §7): `«{last_name} {first_name} {middle_name}»` со схлопыванием пустых
 * частей; все три пусты → фолбэк `username`.
 *
 * Строит его КЛИЕНТ: сервер отдаёт три nullable-колонки и готовой строки не имеет
 * (04-api.md `UserListItem`). Пробельные значения приравниваются к пустым — иначе
 * строка из одних пробелов дала бы «имя», отличное от `username`, и колонка ФИО
 * выглядела бы заполненной.
 */
export function fullName(
  user: Pick<UserListItem, 'last_name' | 'first_name' | 'middle_name' | 'username'>,
): string {
  const parts = [user.last_name, user.first_name, user.middle_name]
    .map((part) => part?.trim() ?? '')
    .filter((part) => part.length > 0);
  return parts.length > 0 ? parts.join(' ') : user.username;
}

/**
 * Строка поиска по пользователю (нормативная область — ФИО + `username` + `telegram`,
 * 08-design-system.md). Регистр снимается `toLocaleLowerCase('ru')` (casefold для
 * кириллицы), поэтому «АННА» находит «Анна».
 */
export function userSearchHaystack(
  user: Pick<UserListItem, 'last_name' | 'first_name' | 'middle_name' | 'username' | 'telegram'>,
): string {
  return [fullName(user), user.username, user.telegram ?? ''].join(' ').toLocaleLowerCase('ru');
}
