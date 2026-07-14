import { describe, expect, it } from 'vitest';
import { ApiError } from '@/lib/api';
import {
  MAIL_CONNECTION_PROGRESS_HINT,
  MAIL_SEND_FAILED_MESSAGE,
  MAIL_UNAVAILABLE_MESSAGE,
  mailErrorMessage,
} from '@/features/mail/errorMessages';

/**
 * Словарь сообщений об отказе почтовых операций (ADR-053 §2/§2.1/§4; строки — побуквенно
 * из 08-design-system.md). Различение — по `error.code`, а НЕ по статусу и не по тексту
 * `message`: у `502` есть коды `mail_unavailable` и `mail_send_failed` с ПРОТИВОПОЛОЖНЫМ
 * смыслом (агрегатор упал vs агрегатор работал, но SMTP отклонил письмо).
 */
describe('mailErrorMessage (ADR-053 §2/§4)', () => {
  it.each([
    ['mail_imap_failed', 'Не удалось подключиться к IMAP. Проверьте хост, порт, SSL и пароль.'],
    [
      'mail_smtp_failed',
      'Не удалось подключиться к SMTP. Проверьте хост, порт, SSL/STARTTLS и пароль.',
    ],
    ['mail_invalid_host', 'Недопустимый адрес сервера: приватные и локальные хосты запрещены.'],
  ])('422 %s → истинная причина отказа проверки ящика', (code, expected) => {
    expect(mailErrorMessage(new ApiError(422, code, 'ignored'), 'test')).toBe(expected);
    // Тот же код на создании/правке даёт то же сообщение (create/patch — тоже mail-server-пути).
    expect(mailErrorMessage(new ApiError(422, code, 'ignored'), 'save')).toBe(expected);
  });

  it('422 с нераспознанным кодом → null (вызывающий обрабатывает прежней веткой)', () => {
    expect(mailErrorMessage(new ApiError(422, 'unprocessable', 'x'), 'test')).toBeNull();
  });

  it('504 mail_timeout → текст ЗАВИСИТ ОТ ДЕЙСТВИЯ (§4)', () => {
    const err = new ApiError(504, 'mail_timeout', 'ignored');

    expect(mailErrorMessage(err, 'test')).toBe(
      'Проверка не завершилась за отведённое время: почтовый сервер не ответил. Проверьте хост и порт.',
    );
    expect(mailErrorMessage(err, 'save')).toBe(
      'Операция не завершилась вовремя. Состояние ящика могло измениться — обновите список.',
    );
    expect(mailErrorMessage(err, 'reply')).toBe(
      'Отправка не подтверждена: сервис не ответил вовремя. Письмо могло быть отправлено — проверьте перед повтором.',
    );
  });

  it('504 mail_timeout ≠ «сервис недоступен» (§3)', () => {
    expect(mailErrorMessage(new ApiError(504, 'mail_timeout', 'x'), 'test')).not.toBe(
      MAIL_UNAVAILABLE_MESSAGE,
    );
  });

  it('502 mail_send_failed (reply) → SMTP не принял письмо, агрегатор РАБОТАЛ', () => {
    expect(mailErrorMessage(new ApiError(502, 'mail_send_failed', 'x'), 'reply')).toBe(
      MAIL_SEND_FAILED_MESSAGE,
    );
  });

  it('502 mail_unavailable → null: «сервис недоступен» остаётся прежней веткой вызывающего', () => {
    expect(mailErrorMessage(new ApiError(502, 'mail_unavailable', 'x'), 'save')).toBeNull();
  });

  it('различение по code, а не по статусу: 502 с чужим кодом не выдаётся за send_failed', () => {
    expect(mailErrorMessage(new ApiError(502, 'some_future_code', 'x'), 'reply')).toBeNull();
    // И наоборот: код mail_timeout вне своего статуса не подхватывается.
    expect(mailErrorMessage(new ApiError(500, 'mail_timeout', 'x'), 'test')).toBeNull();
  });

  it('не-ApiError (сеть/abort) → null', () => {
    expect(mailErrorMessage(new Error('network down'), 'test')).toBeNull();
    expect(mailErrorMessage(new DOMException('aborted', 'AbortError'), 'test')).toBeNull();
  });

  it('подпись прогресса обещает не меньше худшего бюджета ЗАПРОСА (105 с < 2 мин, §4)', () => {
    expect(MAIL_CONNECTION_PROGRESS_HINT).toBe(
      'Проверяем соединение — это может занять до двух минут…',
    );
  });
});
