import { ApiError } from '@/lib/api';

/**
 * Человеческие сообщения об отказе почтовых операций (ADR-053 §2/§2.1/§4).
 * Строки — ПОБУКВЕННО из словаря 08-design-system.md («Локализация UI — русский, словарь строк»).
 *
 * Ключевая норма: там, где агрегатор БЫЛ доступен (422-семейство, `502 mail_send_failed`,
 * `504 mail_timeout`), показывать «Почтовый сервис временно недоступен» / «502 Bad Gateway»
 * ЗАПРЕЩЕНО — это дезинформация (ADR-053 §3). Текст таймаута зависит от ДЕЙСТВИЯ.
 */

/** Действие, на котором произошёл отказ — определяет текст `504 mail_timeout` (ADR-053 §4). */
export type MailAction = 'test' | 'save' | 'reply';

/**
 * Подпись прогресс-состояния долгой проверки соединения (ADR-053 §4, 08-design-system.md).
 * Обещание не короче худшего бюджета ЗАПРОСА (105 с < 120 с = 2 мин).
 */
export const MAIL_CONNECTION_PROGRESS_HINT =
  'Проверяем соединение — это может занять до двух минут…';

/** `502 mail_unavailable` — агрегатор действительно недоступен (без изменений). */
export const MAIL_UNAVAILABLE_MESSAGE = 'Почтовый сервис временно недоступен';

/** `502 mail_send_failed` (reply) — удалённый SMTP не принял письмо; агрегатор работал. */
export const MAIL_SEND_FAILED_MESSAGE =
  'Почтовый сервер не принял письмо. Проверьте настройки SMTP ящика.';

/** `422`-семейство отказов проверки/создания/правки ящика (ADR-053 §2). */
const MAILBOX_CHECK_FAILURE_MESSAGES: Record<string, string> = {
  mail_imap_failed: 'Не удалось подключиться к IMAP. Проверьте хост, порт, SSL и пароль.',
  mail_smtp_failed: 'Не удалось подключиться к SMTP. Проверьте хост, порт, SSL/STARTTLS и пароль.',
  mail_invalid_host: 'Недопустимый адрес сервера: приватные и локальные хосты запрещены.',
};

/** `504 mail_timeout` — тексты по действию (ADR-053 §4). Автоповтора нет (анти-двойная-запись). */
const MAIL_TIMEOUT_MESSAGES: Record<MailAction, string> = {
  test: 'Проверка не завершилась за отведённое время: почтовый сервер не ответил. Проверьте хост и порт.',
  save: 'Операция не завершилась вовремя. Состояние ящика могло измениться — обновите список.',
  reply:
    'Отправка не подтверждена: сервис не ответил вовремя. Письмо могло быть отправлено — проверьте перед повтором.',
};

/**
 * Сообщение для кодов ADR-053 (`504 mail_timeout`, `502 mail_send_failed`, `422 mail_imap_failed`
 * / `mail_smtp_failed` / `mail_invalid_host`). Прочие коды → `null`: вызывающий обрабатывает их
 * своими прежними ветками (409/404/400/422 unprocessable/502 mail_unavailable).
 *
 * Различение — по `error.code` ответа CRM (04-api.md), а не по тексту `message`.
 */
export function mailErrorMessage(err: unknown, action: MailAction): string | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status === 504 && err.code === 'mail_timeout') return MAIL_TIMEOUT_MESSAGES[action];
  if (err.status === 502 && err.code === 'mail_send_failed') return MAIL_SEND_FAILED_MESSAGE;
  if (err.status === 422) {
    const message = MAILBOX_CHECK_FAILURE_MESSAGES[err.code];
    if (message !== undefined) return message;
  }
  return null;
}
