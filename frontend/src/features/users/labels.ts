/**
 * Локализованные подписи страниц/действий матрицы прав (08-design-system.md §Страница «Роли»).
 * Сервер (`backend/app/domain/permissions.py::CATALOG`) отдаёт только технические ключи
 * `page`/`action` и человекочитаемых имён не содержит — локализация живёт на frontend.
 *
 * Инвариант (ADR-063 §D): каждый ключ серверного каталога обязан иметь русскую подпись здесь.
 * Фолбэк «показать сырой ключ» — не допустимое конечное состояние UI: добавление страницы или
 * действия в серверный каталог обязано в том же изменении сопровождаться подписью в этом словаре.
 */

/** Предпочтительный порядок столбцов (сортировка объединения действий каталога). */
export const ACTION_ORDER = [
  'view',
  'create',
  'edit',
  'delete',
  'share',
  'send',
  'sync',
  'tags',
  'transfer',
  'assign_any',
] as const;

/** Подписи действий (столбцы матрицы). */
export const ACTION_LABEL: Record<string, string> = {
  view: 'Просмотр',
  create: 'Создание',
  edit: 'Изменение',
  delete: 'Удаление',
  share: 'Видимость',
  send: 'Отправка',
  sync: 'Синк',
  tags: 'Теги',
  transfer: 'Перенос',
  assign_any: 'Назначение любых ролей',
};

/** Подписи страниц каталога (строки матрицы). */
export const PAGE_LABEL: Record<string, string> = {
  dashboard: 'Дашборд',
  servers: 'Серверы',
  'ai-keys': 'ИИ - ключи',
  proxies: 'Прокси',
  backends: 'Бэки',
  'backend-users': 'Пользователи бэков',
  'backend-economics': 'Продукты и тарифы',
  mail: 'Почты',
  sms: 'СМС',
  roles: 'Роли',
  teams: 'Команды',
  documents: 'Документы',
  broadcast: 'Рассылка',
  users: 'Пользователи',
};

const ACTION_RANK = new Map<string, number>(ACTION_ORDER.map((action, index) => [action, index]));

/** Локализованное имя страницы (фолбэк — технический ключ, только авария). */
export function pageLabel(page: string): string {
  return PAGE_LABEL[page] ?? page;
}

/** Локализованное имя действия (фолбэк — технический ключ, только авария). */
export function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

/**
 * Столбцы матрицы = объединение действий серверного каталога (ADR-076, закрытие TD-068).
 * Порядок — ACTION_ORDER, неизвестные ключи — в конце.
 */
export function catalogActionColumns(catalog: { actions: string[] }[]): string[] {
  const seen = new Set<string>();
  for (const { actions } of catalog) {
    for (const action of actions) seen.add(action);
  }
  return [...seen].sort((a, b) => {
    const rankA = ACTION_RANK.get(a) ?? ACTION_ORDER.length;
    const rankB = ACTION_RANK.get(b) ?? ACTION_ORDER.length;
    if (rankA !== rankB) return rankA - rankB;
    return a.localeCompare(b);
  });
}
