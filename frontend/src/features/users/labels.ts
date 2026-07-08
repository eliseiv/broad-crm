/**
 * Локализованные подписи страниц/действий матрицы прав (08-design-system.md
 * «Локализация страницы Пользователи»). Сервер отдаёт технические ключи
 * `page`/`action`; локализация — на стороне frontend.
 */

/** Порядок столбцов действий в матрице прав. */
export const ACTION_ORDER = ['view', 'create', 'edit', 'delete'] as const;

/** Подписи действий (столбцы матрицы). */
export const ACTION_LABEL: Record<string, string> = {
  view: 'Просмотр',
  create: 'Создание',
  edit: 'Изменение',
  delete: 'Удаление',
};

/** Подписи страниц каталога (строки матрицы). */
export const PAGE_LABEL: Record<string, string> = {
  dashboard: 'Дашборд',
  servers: 'Серверы',
  'ai-keys': 'ИИ - ключи',
  proxies: 'Прокси',
  backends: 'Бэки',
  mail: 'Почты',
  roles: 'Роли',
  teams: 'Команды',
};

/** Локализованное имя страницы (фолбэк — технический ключ). */
export function pageLabel(page: string): string {
  return PAGE_LABEL[page] ?? page;
}
