import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MailDetail } from '@/components/MailDetail';
import type { MailMessage } from '@/types/api';

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
    tags: [{ id: 7, name: 'важное', color: '#EF4444' }],
    ...overrides,
  };
}

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

  it('injects the unified grey background (#161A22) before the html body and uses bg-surface-2 on the iframe', () => {
    render(<MailDetail message={makeMessage({ body_html: '<p>Привет</p>' })} onBack={vi.fn()} />);

    const iframe = screen.getByTitle('Тело письма') as HTMLIFrameElement;
    const srcdoc = iframe.getAttribute('srcdoc') ?? '';
    expect(srcdoc).toContain('#161A22');
    // Инъекция серого фона стоит ПЕРЕД телом письма (08-design-system.md «Единый серый фон»).
    expect(srcdoc.indexOf('#161A22')).toBeLessThan(srcdoc.indexOf('<p>Привет</p>'));
    expect(iframe.className).toContain('bg-surface-2');
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
