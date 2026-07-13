import type { MailMailbox } from '@/types/api';

/**
 * ЕДИНЫЙ источник истины правила поиска по почтам (08-design-system.md «Единое правило поиска
 * по почтам», ADR-052 §3.3). Импортируется ВСЕМИ тремя местами: выпадающий список вкладки
 * «Сообщения», выпадающий список вкладки «Почты», таблица ящиков вкладки «Почты».
 * Дублировать предикат в другом файле ЗАПРЕЩЕНО (прецедент — раздвоенный билдер `srcDoc`,
 * ADR-047 §6).
 *
 * Поля — РОВНО ТРИ: `number` («Номер»), `app_name` («Приложение»), `email` (адрес почты).
 * `display_name` НЕ входит (производная склейка number+app_name — ADR-047 §3.3).
 */
export function mailboxSearchKeywords(mb: MailMailbox): string[] {
  return [mb.number ?? '', mb.app_name ?? '', mb.email];
}

/**
 * Предикат таблицы: подстрока, регистронезависимо, по `trim()`-нутому запросу.
 * Пустой запрос → фильтр не применяется (совпадает всё).
 *
 * Осознанное ограничение (не дефект): поля матчатся ПО ОТДЕЛЬНОСТИ ⇒ запрос, пересекающий
 * границу полей («12345 What» при number="12345", app_name="WhatsApp"), совпадения не даёт.
 */
export function matchesMailboxQuery(mb: MailMailbox, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (q === '') return true;
  return mailboxSearchKeywords(mb).some((k) => k.toLowerCase().includes(q));
}
