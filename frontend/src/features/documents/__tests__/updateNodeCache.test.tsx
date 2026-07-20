import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { documentNodeKey, useUpdateNode } from '@/features/documents/hooks';
import type { DocumentNode } from '@/types/api';

// Мокаем только внешнюю границу — HTTP-слой api. Хук (собственный код) не мокается.
const api = vi.hoisted(() => ({
  updateNode: vi.fn(),
  // Прочие функции api импортируются модулем hooks.ts на верхнем уровне — обязаны существовать.
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

/**
 * Ответ мутирующего PATCH — по контракту (ADR-063 §A, 04-api.md): `content_md: null` ВСЕГДА
 * (единый `_serialize(include_content=False)`), новая `content_version`. Именно этот `null`
 * не должен попасть в кэш узла.
 */
function patchResponse(over: Partial<DocumentNode> = {}): DocumentNode {
  return makeNode({ content_md: null, content_version: 2, ...over });
}

describe('useUpdateNode — контракт слияния кэша узла (ADR-063 §A)', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
  });

  function wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  function nodeCache(id: string): DocumentNode | undefined {
    return queryClient.getQueryData<DocumentNode>([...documentNodeKey, id]);
  }

  it('сохранение контента: content_md берётся из тела запроса, а не из null-ответа PATCH', async () => {
    // Прежний кэш узла существует (документ был открыт → GET заполнил кэш).
    queryClient.setQueryData([...documentNodeKey, 'doc-1'], makeNode({ content_md: 'старый' }));
    api.updateNode.mockResolvedValue(patchResponse({ content_version: 7 }));

    const { result } = renderHook(() => useUpdateNode(), { wrapper });
    await act(async () => {
      result.current.mutate({ id: 'doc-1', payload: { content_md: 'новый текст' } });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const cached = nodeCache('doc-1');
    // Ключевой регресс: НЕ null (из ответа), а отправленное клиентом значение.
    expect(cached?.content_md).toBe('новый текст');
    // Остальные поля — из ответа сервера.
    expect(cached?.content_version).toBe(7);
  });

  it('пустая строка — валидный контент нового документа и НЕ теряется (строгая проверка !== undefined)', async () => {
    queryClient.setQueryData([...documentNodeKey, 'doc-1'], makeNode({ content_md: 'было' }));
    api.updateNode.mockResolvedValue(patchResponse());

    const { result } = renderHook(() => useUpdateNode(), { wrapper });
    await act(async () => {
      result.current.mutate({ id: 'doc-1', payload: { content_md: '' } });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // '' — валидное значение: должно попасть в кэш, а не подмениться прежним 'было' и не стать null.
    expect(nodeCache('doc-1')?.content_md).toBe('');
  });

  it('переименование (PATCH без content_md): content_md прежнего кэша не обнуляется', async () => {
    queryClient.setQueryData(
      [...documentNodeKey, 'doc-1'],
      makeNode({ content_md: 'тело документа' }),
    );
    api.updateNode.mockResolvedValue(patchResponse({ name: 'Новое имя', content_version: 3 }));

    const { result } = renderHook(() => useUpdateNode(), { wrapper });
    await act(async () => {
      result.current.mutate({ id: 'doc-1', payload: { name: 'Новое имя' } });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const cached = nodeCache('doc-1');
    // content_md не тронут (взят из прежнего кэша), имя/версия — из ответа.
    expect(cached?.content_md).toBe('тело документа');
    expect(cached?.name).toBe('Новое имя');
    expect(cached?.content_version).toBe(3);
  });

  it('нет прежнего кэша (undefined) → частичная запись не сеется', async () => {
    // Кэш узла пуст (документ не открывали). setQueryData НЕ вызывался.
    api.updateNode.mockResolvedValue(patchResponse({ id: 'doc-1' }));

    const { result } = renderHook(() => useUpdateNode(), { wrapper });
    await act(async () => {
      result.current.mutate({ id: 'doc-1', payload: { name: 'Имя' } });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // Никакой частичной записи: следующий GET заполнит кэш целиком.
    expect(nodeCache('doc-1')).toBeUndefined();
  });
});
