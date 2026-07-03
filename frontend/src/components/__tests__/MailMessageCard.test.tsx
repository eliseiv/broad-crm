import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { describe, expect, it } from 'vitest';
import { MailMessageCard } from '@/components/MailMessageCard';
import type { MailMessage } from '@/types/api';

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

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

async function expand(): Promise<void> {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { expanded: false }));
}

describe('MailMessageCard body isolation', () => {
  it('renders body_html only inside a strict sandbox iframe (no scripts/same-origin)', async () => {
    const html = '<p>Привет</p><script>window.__pwned = true;</script>';
    render(<MailMessageCard message={makeMessage({ body_html: html })} />, { wrapper });
    await expand();

    const iframe = screen.getByTitle('Тело письма') as HTMLIFrameElement;
    // sandbox присутствует и ПУСТОЙ: без allow-scripts / allow-same-origin.
    expect(iframe.getAttribute('sandbox')).toBe('');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-scripts');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
    // HTML отдаётся через srcDoc (не подгружается по URL).
    expect(iframe.getAttribute('srcdoc')).toContain('<p>Привет</p>');
    // Скрипт письма не мог исполниться (нет allow-scripts) — переменная не появилась.
    expect((window as unknown as Record<string, unknown>).__pwned).toBeUndefined();
  });

  it('renders body_text in a <pre> and no iframe when body_html is null', async () => {
    render(<MailMessageCard message={makeMessage({ body_html: null })} />, { wrapper });
    await expand();

    expect(screen.getByText('Текст письма').tagName).toBe('PRE');
    expect(screen.queryByTitle('Тело письма')).not.toBeInTheDocument();
  });

  it('shows a notice when body is truncated', async () => {
    render(<MailMessageCard message={makeMessage({ body_truncated: true })} />, { wrapper });
    await expand();

    expect(screen.getByText('Письмо показано не полностью')).toBeInTheDocument();
  });

  it('shows unavailable notice when body is not present', async () => {
    render(
      <MailMessageCard message={makeMessage({ body_present: false, body_html: null })} />,
      { wrapper },
    );
    await expand();

    expect(screen.getByText('Тело письма недоступно')).toBeInTheDocument();
  });

  it('falls back to "(без темы)" when subject is null', () => {
    render(<MailMessageCard message={makeMessage({ subject: null })} />, { wrapper });
    expect(screen.getByText('(без темы)')).toBeInTheDocument();
  });
});
