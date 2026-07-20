import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PropsWithChildren } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentEditor } from '@/components/DocumentEditor';
import type { DocumentNode } from '@/types/api';

// Мокаем только HTTP-границу (api). Компонент и хук useUpdateNode — реальные (свой код не мокается).
const api = vi.hoisted(() => ({
  updateNode: vi.fn(),
  copyNode: vi.fn(),
  createDocument: vi.fn(),
  createFolder: vi.fn(),
  deleteNode: vi.fn(),
  getNode: vi.fn(),
  getNodeVisibility: vi.fn(),
  getTree: vi.fn(),
  listRoleRefs: vi.fn(),
  reorderNodes: vi.fn(),
  setVisibility: vi.fn(),
  uploadMd: vi.fn(),
}));

vi.mock('@/features/documents/api', () => api);

function makeNode(over: Partial<DocumentNode> = {}): DocumentNode {
  return {
    id: 'doc-1',
    node_type: 'document',
    parent_id: null,
    name: 'Регламент',
    content_md: 'исходный текст',
    owner_id: 'owner-1',
    visibility_mode: 'inherit',
    content_version: 1,
    position: 0,
    created_at: '2026-07-20T00:00:00Z',
    updated_at: '2026-07-20T00:00:00Z',
    ...over,
  };
}

/** Ответ мутирующего PATCH: content_md ВСЕГДА null, новая content_version (ADR-063 §A). */
function patchResponse(over: Partial<DocumentNode> = {}): DocumentNode {
  return makeNode({ content_md: null, content_version: 2, ...over });
}

function wrapper({ children }: PropsWithChildren) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function proseText(container: HTMLElement): string {
  return container.querySelector('.doc-prose')?.textContent ?? '';
}

function saveButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: /Сохранить/ }) as HTMLButtonElement;
}

describe('DocumentEditor — ресинк версии и жизненный цикл (ADR-063 §B)', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('§B.2 ВНЕШНЯЯ смена content_version (409-refetch/чужая правка) → контент ресинкается', async () => {
    const node = makeNode({ content_version: 3, content_md: 'старое содержимое' });
    const { container, rerender } = render(<DocumentEditor node={node} canEdit={true} />, {
      wrapper,
    });
    await screen.findByRole('button', { name: 'Ссылка' });
    expect(proseText(container)).toContain('старое содержимое');

    // Приходит узел с ДРУГОЙ версией (внешнее изменение) — тот же id, компонент НЕ ремоунтится.
    const external = makeNode({ content_version: 5, content_md: 'изменено извне' });
    rerender(<DocumentEditor node={external} canEdit={true} />);

    await waitFor(() => expect(proseText(container)).toContain('изменено извне'));
    expect(proseText(container)).not.toContain('старое содержимое');
    // Ресинк сбрасывает флаг несохранённых изменений (setContent emitUpdate=false).
    await waitFor(() => expect(saveButton()).toBeDisabled());
  });

  it('§B.2 та же content_version, но иной content_md → ресинк НЕ срабатывает (гейт по версии, не по контенту)', async () => {
    const node = makeNode({ content_version: 4, content_md: 'оригинал' });
    const { container, rerender } = render(<DocumentEditor node={node} canEdit={true} />, {
      wrapper,
    });
    await screen.findByRole('button', { name: 'Ссылка' });
    expect(proseText(container)).toContain('оригинал');

    // Версия НЕ изменилась → контент не должен подмениться, даже если content_md в пропсе иной.
    rerender(
      <DocumentEditor
        node={makeNode({ content_version: 4, content_md: 'ДРУГОЕ' })}
        canEdit={true}
      />,
    );

    // Немного ждём на случай асинхронного эффекта — контент обязан остаться прежним.
    await new Promise((r) => setTimeout(r, 30));
    expect(proseText(container)).toContain('оригинал');
    expect(proseText(container)).not.toContain('ДРУГОЕ');
  });

  it('§B.3 СОБСТВЕННОЕ сохранение → ресинк НЕ срабатывает (курсор/контент сохраняются; savingRef удалён)', async () => {
    const user = userEvent.setup();
    api.updateNode.mockResolvedValue(patchResponse({ content_version: 9 }));

    const node = makeNode({ content_version: 1, content_md: 'мой текст' });
    const { container, rerender } = render(<DocumentEditor node={node} canEdit={true} />, {
      wrapper,
    });
    await screen.findByRole('button', { name: 'Ссылка' });

    // Сохранение (dirty при монтировании true — редактор эмитит update на парсинге markdown).
    await user.click(saveButton());
    // Дожидаемся onSuccess: base → 9, dirty сброшен (контент не менялся во время полёта).
    await waitFor(() => expect(saveButton()).toBeDisabled());
    expect(api.updateNode).toHaveBeenCalledWith(
      'doc-1',
      expect.objectContaining({ content_md: 'мой текст', expected_version: 1 }),
    );

    // Родитель отдаёт узел с новой (уже применённой) версией 9. §B.3: base уже равен 9 в колбэке
    // мутации ⇒ расхождения нет ⇒ ресинк НЕ срабатывает и НЕ затирает то, что в редакторе.
    // Даже если content_md в пропсе разошёлся (напр. отдали устаревшее серверное тело) — версия
    // совпала, значит замены быть не должно.
    rerender(
      <DocumentEditor
        node={makeNode({ content_version: 9, content_md: 'СЕРВЕРНОЕ УСТАРЕВШЕЕ' })}
        canEdit={true}
      />,
    );

    await new Promise((r) => setTimeout(r, 30));
    expect(proseText(container)).toContain('мой текст');
    expect(proseText(container)).not.toContain('СЕРВЕРНОЕ УСТАРЕВШЕЕ');
  });
});

describe('DocumentEditor — потеря правок во время полёта PATCH (ADR-063, сценарий 6)', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('чистое сохранение (без правок в полёте) → dirty сбрасывается, кнопка гаснет', async () => {
    const user = userEvent.setup();
    api.updateNode.mockResolvedValue(patchResponse({ content_version: 2 }));

    render(<DocumentEditor node={makeNode({ content_md: 'текст' })} canEdit={true} />, { wrapper });
    await screen.findByRole('button', { name: 'Ссылка' });

    await user.click(saveButton());
    // Контент не менялся во время полёта → current === markdown → dirty=false → кнопка disabled.
    await waitFor(() => expect(saveButton()).toBeDisabled());
  });

  it('правки, набранные ВО ВРЕМЯ полёта PATCH, сохраняют dirty (кнопка остаётся активной)', async () => {
    const user = userEvent.setup();
    // Управляемый (deferred) ответ: держим PATCH «в полёте», пока не наберём правки.
    let resolvePatch: (v: DocumentNode) => void = () => {};
    api.updateNode.mockReturnValue(
      new Promise<DocumentNode>((resolve) => {
        resolvePatch = resolve;
      }),
    );

    const { container } = render(
      <DocumentEditor node={makeNode({ content_md: 'база' })} canEdit={true} />,
      { wrapper },
    );
    await screen.findByRole('button', { name: 'Ссылка' });

    // Стартуем сохранение — захватывается markdown='база', PATCH уходит в полёт (pending).
    await user.click(saveButton());
    await waitFor(() => expect(api.updateNode).toHaveBeenCalledTimes(1));

    // Пока PATCH в полёте — печатаем: контент редактора расходится с отправленным markdown.
    const prose = container.querySelector('.doc-prose') as HTMLElement;
    await user.click(prose);
    await user.keyboard('НОВОЕ');
    expect(proseText(container)).toContain('НОВОЕ');

    // PATCH завершается: current ('НОВОЕ…') !== markdown ('база') ⇒ dirty НЕ сбрасывается.
    resolvePatch(patchResponse({ content_version: 2 }));

    await waitFor(() => expect(saveButton()).toBeEnabled());
    // Набранные в полёте правки на экране целы (молчаливой потери нет).
    expect(proseText(container)).toContain('НОВОЕ');
  });
});
