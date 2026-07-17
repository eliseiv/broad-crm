import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import { Markdown } from 'tiptap-markdown';
import type { PropsWithChildren } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DocumentEditor } from '@/components/DocumentEditor';
import type { DocumentNode } from '@/types/api';

// Конфиг extension'ов — ТОЧНО как в DocumentEditor (ADR-062 §2): StarterKit + Link + Markdown.
function makeHeadlessEditor(markdown: string): Editor {
  return new Editor({
    extensions: [
      StarterKit,
      Link.configure({ openOnClick: false, autolink: true, linkOnPaste: true }),
      Markdown.configure({ html: false, transformPastedText: true, transformCopiedText: true }),
    ],
    content: markdown,
  });
}

function roundTrip(markdown: string): string {
  const editor = makeHeadlessEditor(markdown);
  const out = editor.storage.markdown.getMarkdown() as string;
  editor.destroy();
  return out;
}

describe('DocumentEditor markdown round-trip (ADR-062: URL не теряется)', () => {
  it('сохраняет заголовки/жирный/курсив/зачёркнутый/код/списки/цитату при round-trip', () => {
    const md = [
      '# Заголовок 1',
      '',
      '**жирный** и *курсив* и ~~зачёркнутый~~ и `моно`',
      '',
      '- пункт A',
      '- пункт B',
      '',
      '1. один',
      '2. два',
      '',
      '> цитата',
    ].join('\n');
    const out = roundTrip(md);
    expect(out).toContain('# Заголовок 1');
    expect(out).toContain('**жирный**');
    expect(out).toContain('*курсив*');
    expect(out).toContain('~~зачёркнутый~~');
    expect(out).toContain('`моно`');
    expect(out).toContain('- пункт A');
    expect(out).toContain('1. один');
    expect(out).toContain('> цитата');
  });

  it('НЕ теряет URL ссылки [text](url), включая query и anchor', () => {
    const url = 'https://example.com/docs/path?q=1&x=2#section-anchor';
    const out = roundTrip(`Смотри [руководство](${url}) здесь.`);
    expect(out).toContain(url);
    expect(out).toContain('[руководство]');
  });

  it('сохраняет несколько ссылок с разными query-параметрами', () => {
    const md = '[a](https://a.test/?p=1) и [b](https://b.test/x#top)';
    const out = roundTrip(md);
    expect(out).toContain('https://a.test/?p=1');
    expect(out).toContain('https://b.test/x#top');
  });
});

// --- Рендер компонента: тулбар/ссылка/RBAC ---------------------------------

const DOC: DocumentNode = {
  id: 'd1',
  node_type: 'document',
  parent_id: null,
  name: 'Регламент',
  content_md: 'Текст документа',
  owner_id: 'o',
  visibility_mode: 'inherit',
  content_version: 3,
  position: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function wrapper({ children }: PropsWithChildren) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe('DocumentEditor компонент (тулбар «ссылка», RBAC-режим)', () => {
  afterEach(() => vi.restoreAllMocks());

  it('canEdit: тулбар с кнопкой «Ссылка» (aria-pressed=false), есть «Сохранить»', async () => {
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    const linkBtn = await screen.findByRole('button', { name: 'Ссылка' });
    expect(linkBtn).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: /Сохранить/ })).toBeInTheDocument();
    // Тулбар форматирования присутствует.
    expect(screen.getByRole('toolbar', { name: 'Форматирование' })).toBeInTheDocument();
  });

  it('клик по «Ссылка» вызывает prompt для ввода URL', async () => {
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('https://x.test');
    const user = (await import('@testing-library/user-event')).default.setup();
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    const linkBtn = await screen.findByRole('button', { name: 'Ссылка' });
    await user.click(linkBtn);
    expect(promptSpy).toHaveBeenCalledTimes(1);
  });

  it('read-only (canEdit=false): тулбара и «Сохранить» нет, показан режим просмотра', async () => {
    render(<DocumentEditor node={DOC} canEdit={false} />, { wrapper });
    await waitFor(() => expect(screen.getByText(/Режим просмотра/)).toBeInTheDocument());
    expect(screen.queryByRole('toolbar', { name: 'Форматирование' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Сохранить/ })).not.toBeInTheDocument();
  });
});
