/**
 * Локализованные подписи страниц/действий матрицы прав (08-design-system.md §Страница «Роли»).
 * Сервер (`backend/app/domain/permissions.py::CATALOG`) отдаёт только технические ключи
 * `page`/`action` и человекочитаемых имён не содержит — локализация живёт на frontend.
 *
 * Инвариант (ADR-063 §D): каждый ключ серверного каталога обязан иметь русскую подпись здесь.
 * Фолбэк «показать сырой ключ» — не допустимое конечное состояние UI: добавление страницы или
 * действия в серверный каталог обязано в том же изменении сопровождаться подписью в этом словаре.
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
  'backend-users': 'Пользователи бэков',
  mail: 'Почты',
  sms: 'СМС',
  roles: 'Роли',
  teams: 'Команды',
  documents: 'Документы',
};

/** Локализованное имя страницы (фолбэк — технический ключ). */
export function pageLabel(page: string): string {
  return PAGE_LABEL[page] ?? page;
}
