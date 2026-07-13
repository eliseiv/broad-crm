import { describe, expect, it } from 'vitest';
import { mailboxSearchKeywords, matchesMailboxQuery } from '@/features/mail/mailboxSearch';
import type { MailMailbox } from '@/types/api';

/**
 * ЕДИНЫЙ предикат поиска по почтам (ADR-052 §3.3, 08-design-system.md «Единое правило поиска
 * по почтам»). Его импортируют ВСЕ ТРИ места: выпадающий список «Сообщений», выпадающий список
 * «Почт», таблица ящиков «Почт» — поэтому норма проверяется здесь один раз и предметно.
 *
 * Поля — РОВНО ТРИ: `number`, `app_name`, `email`. Подстрока, регистронезависимо, по
 * `trim()`-нутому запросу. `display_name` НЕ участвует (производная склейка number+app_name,
 * ADR-047 §3.3) — это явная норма, и на неё есть отдельный кейс.
 */

function mailbox(over: Partial<MailMailbox> = {}): MailMailbox {
  return {
    id: 1,
    email: 'alpha@postapp.store',
    number: '5108',
    app_name: 'Klyro Forge',
    display_name: '5108 Klyro Forge',
    team_id: null,
    is_active: true,
    last_synced_at: null,
    last_sync_error: null,
    consecutive_failures: 0,
    ...over,
  };
}

describe('mailboxSearchKeywords (ADR-052 §3.3)', () => {
  it('возвращает РОВНО три ключа в порядке number → app_name → email', () => {
    expect(mailboxSearchKeywords(mailbox())).toEqual([
      '5108',
      'Klyro Forge',
      'alpha@postapp.store',
    ]);
  });

  it('пустые `number`/`app_name` дают пустые строки (ключей всё равно три)', () => {
    const keys = mailboxSearchKeywords(mailbox({ number: null, app_name: null }));

    expect(keys).toEqual(['', '', 'alpha@postapp.store']);
    expect(keys).toHaveLength(3);
  });

  it('`display_name` в ключи НЕ входит (норма ADR-052 §3.3 / ADR-050 §1.1)', () => {
    const keys = mailboxSearchKeywords(mailbox({ display_name: 'Секретное имя' }));

    expect(keys).not.toContain('Секретное имя');
  });
});

describe('matchesMailboxQuery (ADR-052 §3.3)', () => {
  it('совпадает по номеру — подстрока, регистронезависимо', () => {
    expect(matchesMailboxQuery(mailbox(), '510')).toBe(true);
  });

  it('совпадает по приложению — регистронезависимо', () => {
    expect(matchesMailboxQuery(mailbox(), 'KLYRO')).toBe(true);
    expect(matchesMailboxQuery(mailbox(), 'forge')).toBe(true);
  });

  it('совпадает по адресу почты', () => {
    expect(matchesMailboxQuery(mailbox(), 'postapp.store')).toBe(true);
  });

  it('НЕ совпадает по `display_name` (искать по производной склейке запрещено)', () => {
    // Запрос совпал бы с `display_name = "Личная почта"`, но не совпадает ни с одним из ТРЁХ полей.
    const mb = mailbox({ number: null, app_name: null, display_name: 'Личная почта' });

    expect(matchesMailboxQuery(mb, 'Личная')).toBe(false);
  });

  it('пустой запрос и пробелы фильтр НЕ применяют (совпадает всё)', () => {
    expect(matchesMailboxQuery(mailbox(), '')).toBe(true);
    expect(matchesMailboxQuery(mailbox(), '   ')).toBe(true);
  });

  it('запрос `trim()`-ается перед сравнением', () => {
    expect(matchesMailboxQuery(mailbox(), '  nova  ')).toBe(false);
    expect(matchesMailboxQuery(mailbox({ app_name: 'Nova Ledger' }), '  nova  ')).toBe(true);
  });

  it('несовпадающий запрос → false', () => {
    expect(matchesMailboxQuery(mailbox(), 'zzz-nomatch')).toBe(false);
  });

  it('осознанное ограничение (не дефект): запрос через границу полей совпадения не даёт', () => {
    // «5108 Klyro» визуально слитно читается в лейбле, но поля матчатся ПО ОТДЕЛЬНОСТИ.
    expect(matchesMailboxQuery(mailbox(), '5108 Klyro')).toBe(false);
  });
});
