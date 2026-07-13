import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailDetail } from '@/components/MailDetail';
import type { MailMessage } from '@/types/api';

// Изоляция глобального состояния темы: `data-theme` живёт на <html> (общий для всех тестов
// документ jsdom). Тесты тела письма его меняют — сбрасываем ДО и ПОСЛЕ каждого теста, чтобы
// зелёный в одиночку тест не краснел в полном прогоне из-за наследования чужой темы.
beforeEach(() => {
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  delete document.documentElement.dataset.theme;
});

// MailDetail рендерит MailReplyForm → useReplyMail; мокаем как no-op мутацию.
vi.mock('@/features/mail/hooks', () => ({
  useReplyMail: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeMessage(overrides: Partial<MailMessage> = {}): MailMessage {
  return {
    id: 1042,
    subject: 'Отчёт за июнь',
    internal_date: '2026-07-02T09:15:00Z',
    from_addr: 'sender@example.com',
    from_name: 'Иван Петров',
    to_addrs: 'inbox@postapp.store',
    cc_addrs: null,
    mail_account: { id: 3, email: 'inbox@postapp.store', display_name: 'Входящие' },
    body_text: 'Текст письма',
    body_html: null,
    body_present: true,
    body_truncated: false,
    // Персональная непрочитанность (ADR-050 §2.2) — обязательное поле схемы `MailMessage`.
    is_unread: false,
    tags: [{ id: '7a1f0c2e-0000-4000-8000-000000000007', name: 'важное', color: '#EF4444' }],
    ...overrides,
  };
}

// Кнопка «Отметить непрочитанным» в шапке детали (ADR-050 §2.7): рендерится ТОЛЬКО когда
// письмо уже прочитано (`is_unread === false`) И передан обработчик `onMarkUnread`.
//
// ⚠️ Прежнее обоснование опциональности обработчика («супер-админу из `.env` MailPage его не
// передаёт — личного состояния у него нет», ADR-050 §2.5) ОТМЕНЕНО ADR-051 §3: контролы
// прочитанности рендерятся ВСЕМ с `mail:view`, включая супер-админа, и `MailPage` передаёт
// обработчик безусловно. Опциональность пропа остаётся контрактом САМОГО компонента
// (`MailDetail` переиспользуется там, где откат не предусмотрен, — напр. Mini App `/tg/mail`,
// ADR-050 §2.8: индикатор есть, кнопки отката нет).
describe('MailDetail — «Отметить непрочитанным» (ADR-050 §2.7, ADR-051 §3)', () => {
  it('прочитанное письмо + обработчик → кнопка есть, клик отдаёт id письма', async () => {
    const user = userEvent.setup();
    const onMarkUnread = vi.fn();
    render(
      <MailDetail
        message={makeMessage({ is_unread: false })}
        onBack={vi.fn()}
        onMarkUnread={onMarkUnread}
      />,
    );

    await user.click(screen.getByRole('button', { name: /Отметить непрочитанным/ }));

    expect(onMarkUnread).toHaveBeenCalledTimes(1);
    expect(onMarkUnread).toHaveBeenCalledWith(1042);
  });

  it('письмо ещё непрочитано (is_unread=true) → кнопки нет (откатывать нечего)', () => {
    render(
      <MailDetail
        message={makeMessage({ is_unread: true })}
        onBack={vi.fn()}
        onMarkUnread={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('button', { name: /Отметить непрочитанным/ }),
    ).not.toBeInTheDocument();
  });

  // Не «супер-админ» (ADR-051 §3 отменил этот гейт), а контекст без отката — Mini App.
  it('обработчик не передан (напр. Mini App /tg/mail) → кнопки нет', () => {
    render(<MailDetail message={makeMessage({ is_unread: false })} onBack={vi.fn()} />);

    expect(
      screen.queryByRole('button', { name: /Отметить непрочитанным/ }),
    ).not.toBeInTheDocument();
  });

  it('идёт запрос отката (markUnreadPending) → кнопка disabled (нет двойной отправки)', () => {
    render(
      <MailDetail
        message={makeMessage({ is_unread: false })}
        onBack={vi.fn()}
        onMarkUnread={vi.fn()}
        markUnreadPending
      />,
    );

    expect(screen.getByRole('button', { name: /Отметить непрочитанным/ })).toBeDisabled();
  });
});

describe('MailDetail body isolation & notices', () => {
  it('renders body_html only inside a strict sandbox iframe (no scripts/same-origin)', () => {
    const html = '<p>Привет</p><script>window.__pwned = true;</script>';
    render(<MailDetail message={makeMessage({ body_html: html })} onBack={vi.fn()} />);

    const iframe = screen.getByTitle('Тело письма') as HTMLIFrameElement;
    expect(iframe.getAttribute('sandbox')).toBe('');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-scripts');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
    expect(iframe.getAttribute('srcdoc')).toContain('<p>Привет</p>');
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined();
  });

  it('renders body_text in a <pre> and no iframe when body_html is null', () => {
    render(<MailDetail message={makeMessage({ body_html: null })} onBack={vi.fn()} />);

    expect(screen.getByText('Текст письма').tagName).toBe('PRE');
    expect(screen.queryByTitle('Тело письма')).not.toBeInTheDocument();
  });

  // ADR-047 §6: хардкод тёмного фона снят — фон и цвет текста тела письма СЛЕДУЮТ ТЕМЕ CRM.
  // Iframe — собственный документ (CSS-переменные родителя не наследуются), поэтому цвета
  // подставляются литералами, синхронизированными с токенами index.css.
  it('injects the DARK theme colors before the html body in dark theme', () => {
    document.documentElement.dataset.theme = 'dark';
    render(<MailDetail message={makeMessage({ body_html: '<p>Привет</p>' })} onBack={vi.fn()} />);

    const iframe = screen.getByTitle('Тело письма') as HTMLIFrameElement;
    const srcdoc = iframe.getAttribute('srcdoc') ?? '';
    expect(srcdoc).toContain('background:#161A22'); // --surface-2 тёмной
    expect(srcdoc).toContain('color:#E6E9EF'); // --text-primary тёмной
    // Инъекция стиля стоит ПЕРЕД недоверенным телом письма.
    expect(srcdoc.indexOf('#161A22')).toBeLessThan(srcdoc.indexOf('<p>Привет</p>'));
    expect(iframe.className).toContain('bg-surface-2');
  });

  it('injects the LIGHT theme colors before the html body in light theme (no dark hardcode)', () => {
    document.documentElement.dataset.theme = 'light';
    render(<MailDetail message={makeMessage({ body_html: '<p>Привет</p>' })} onBack={vi.fn()} />);

    const iframe = screen.getByTitle('Тело письма') as HTMLIFrameElement;
    const srcdoc = iframe.getAttribute('srcdoc') ?? '';
    expect(srcdoc).toContain('background:#F7F8FA'); // --surface-2 светлой
    expect(srcdoc).toContain('color:#111827'); // --text-primary светлой
    // Прежний безусловный тёмный фон в светлой теме больше НЕ подставляется.
    expect(srcdoc).not.toContain('#161A22');
    expect(srcdoc).not.toContain('#E6E9EF');
    expect(srcdoc.indexOf('#F7F8FA')).toBeLessThan(srcdoc.indexOf('<p>Привет</p>'));
  });

  it('отсутствие data-theme → светлый дефолт (инвариант ADR-046 §4.2), не тёмный', () => {
    delete document.documentElement.dataset.theme;
    render(<MailDetail message={makeMessage({ body_html: '<p>Привет</p>' })} onBack={vi.fn()} />);

    const srcdoc =
      (screen.getByTitle('Тело письма') as HTMLIFrameElement).getAttribute('srcdoc') ?? '';
    expect(srcdoc).toContain('background:#F7F8FA');
    expect(srcdoc).not.toContain('#161A22');
  });

  it('изоляция sandbox не ослабляется сменой темы (sandbox="", no-referrer)', () => {
    document.documentElement.dataset.theme = 'light';
    render(<MailDetail message={makeMessage({ body_html: '<p>Привет</p>' })} onBack={vi.fn()} />);

    const iframe = screen.getByTitle('Тело письма') as HTMLIFrameElement;
    expect(iframe.getAttribute('sandbox')).toBe('');
    expect(iframe.getAttribute('referrerpolicy')).toBe('no-referrer');
  });

  it('renders body_text on the same grey surface (pre bg-surface-2)', () => {
    render(<MailDetail message={makeMessage({ body_html: null })} onBack={vi.fn()} />);

    const pre = screen.getByText('Текст письма');
    expect(pre.tagName).toBe('PRE');
    expect(pre.className).toContain('bg-surface-2');
  });

  it('shows "Получено на: {display_name} <{email}>" fully (break-words, not truncate)', () => {
    render(<MailDetail message={makeMessage()} onBack={vi.fn()} />);

    const receivedLine = screen.getByText('<inbox@postapp.store>').closest('p');
    expect(receivedLine).not.toBeNull();
    expect(receivedLine).toHaveTextContent('Получено на: Входящие <inbox@postapp.store>');
    // Значимый контент не обрезается (CLAUDE.md): перенос, а не усечение.
    expect(receivedLine?.className).toContain('break-words');
    expect(receivedLine?.className).not.toContain('truncate');
  });

  it('renders only the email without empty angle brackets when display_name is empty', () => {
    render(
      <MailDetail
        message={makeMessage({
          mail_account: { id: 3, email: 'inbox@postapp.store', display_name: null },
        })}
        onBack={vi.fn()}
      />,
    );

    const receivedLine = screen.getByText('inbox@postapp.store').closest('p');
    expect(receivedLine).not.toBeNull();
    expect(receivedLine).toHaveTextContent('Получено на: inbox@postapp.store');
    // Без пустых угловых скобок при пустом display_name.
    expect(receivedLine?.textContent).not.toContain('<');
    expect(receivedLine?.textContent).not.toContain('>');
  });

  it('shows a notice when body is truncated', () => {
    render(<MailDetail message={makeMessage({ body_truncated: true })} onBack={vi.fn()} />);

    expect(screen.getByText('Письмо показано не полностью')).toBeInTheDocument();
  });

  it('shows an unavailable notice when body is not present', () => {
    render(
      <MailDetail
        message={makeMessage({ body_present: false, body_html: null })}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText('Тело письма недоступно')).toBeInTheDocument();
  });

  it('falls back to "(без темы)" in the heading when subject is null', () => {
    render(<MailDetail message={makeMessage({ subject: null })} onBack={vi.fn()} />);

    expect(screen.getByRole('heading', { name: '(без темы)' })).toBeInTheDocument();
  });

  it('invokes onBack when the adaptive "Назад" button is clicked', async () => {
    const onBack = vi.fn();
    render(<MailDetail message={makeMessage()} onBack={onBack} />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Назад' }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

// Скрытие полосы прокрутки (08-design-system.md «Скрытие полосы прокрутки», раздел «Где
// применяется» → MAIL — тело письма). jsdom не мерит геометрию полосы — проверяем НАЛИЧИЕ класса
// scrollbar-none и СОХРАНЕНИЕ overflow-класса (прокрутка тела не отменяется). sandbox-iframe тела
// НЕ трогаем — у него собственный документ.
describe('MailDetail scrollbar hiding (scrollbar-none on the body_text <pre>)', () => {
  it('applies scrollbar-none to the body_text <pre> while keeping overflow-auto (scroll preserved)', () => {
    render(<MailDetail message={makeMessage({ body_html: null })} onBack={vi.fn()} />);

    const pre = screen.getByText('Текст письма');
    expect(pre.tagName).toBe('PRE');
    expect(pre.classList.contains('scrollbar-none')).toBe(true);
    // Прокрутка сохранена: <pre> остаётся overflow-auto и НЕ становится overflow-hidden.
    expect(pre.classList.contains('overflow-auto')).toBe(true);
    expect(pre.classList.contains('overflow-hidden')).toBe(false);
  });

  it('does NOT apply scrollbar-none to the sandbox iframe of the html body (own document)', () => {
    render(<MailDetail message={makeMessage({ body_html: '<p>Привет</p>' })} onBack={vi.fn()} />);

    const iframe = screen.getByTitle('Тело письма') as HTMLIFrameElement;
    expect(iframe.classList.contains('scrollbar-none')).toBe(false);
  });
});
