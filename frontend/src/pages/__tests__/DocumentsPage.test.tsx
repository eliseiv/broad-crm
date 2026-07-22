import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentsPage } from '@/pages/DocumentsPage';
import type { DocumentNode } from '@/types/api';

// --- Управляемое состояние моков (дерево/узел/созданный узел) ---------------
const state = vi.hoisted(() => ({
  tree: [] as DocumentNode[],
  treeUpdatedAt: 0,
  nodeData: undefined as DocumentNode | undefined,
  createdNode: undefined as DocumentNode | undefined,
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => true,
  useCan: () => true,
}));

// Мокаем слой хуков документов (границы страницы). Логика самой страницы (fallback justCreated,
// expandFrom, selectedNode) — реальная и является предметом проверки.
vi.mock('@/features/documents/hooks', () => ({
  documentNodeKey: ['documents', 'node'],
  useDocumentTree: () => ({
    data: state.tree,
    isLoading: false,
    error: null,
    dataUpdatedAt: state.treeUpdatedAt,
    refetch: vi.fn(),
  }),
  useDocumentNode: (_id: string | null, enabled: boolean) => ({
    data: enabled ? state.nodeData : undefined,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateDocument: () => ({
    mutate: (_payload: unknown, opts?: { onSuccess?: (n: DocumentNode) => void }) =>
      opts?.onSuccess?.(state.createdNode as DocumentNode),
    isPending: false,
  }),
  useCreateFolder: () => ({ mutate: vi.fn(), isPending: false }),
  useUploadMd: () => ({ mutate: vi.fn(), isPending: false }),
  useCopyNode: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteNode: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateNode: () => ({ mutate: vi.fn(), isPending: false }),
  useUploadAttachment: () => ({ mutate: vi.fn(), isPending: false }),
  useSetVisibility: () => ({ mutate: vi.fn(), isPending: false }),
  useRoleRefs: () => ({ data: [], isLoading: false, error: null }),
  useNodeVisibility: () => ({ data: undefined, isLoading: false, error: null }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function folder(id: string, name: string): DocumentNode {
  return {
    id,
    node_type: 'folder',
    parent_id: null,
    name,
    content_md: null,
    owner_id: 'o',
    visibility_mode: 'inherit',
    content_version: 0,
    position: 0,
    created_at: '2026-07-20T00:00:00Z',
    updated_at: '2026-07-20T00:00:00Z',
  };
}

function doc(id: string, name: string, parentId: string | null): DocumentNode {
  return {
    id,
    node_type: 'document',
    parent_id: parentId,
    name,
    content_md: '',
    owner_id: 'o',
    visibility_mode: 'inherit',
    content_version: 1,
    position: 0,
    created_at: '2026-07-20T00:00:00Z',
    updated_at: '2026-07-20T00:00:00Z',
  };
}

function wrapper({ children }: PropsWithChildren) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const FOLDER = folder('f1', 'Папка F');
const CREATED = doc('x1', 'Инструкция', 'f1');

/** Открывает модалку «Новый документ», вводит имя, подтверждает — триггерит openCreated(CREATED). */
async function createDocumentViaModal(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Новый документ' }));
  await user.type(await screen.findByLabelText('Название'), 'Инструкция');
  await user.click(screen.getByRole('button', { name: 'Создать' }));
}

describe('DocumentsPage — немедленный показ созданного узла (ADR-063, сценарий 5)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.tree = [FOLDER]; // папка-предок свёрнута; X ещё не в дереве (рефетч в полёте)
    state.treeUpdatedAt = 1000;
    state.nodeData = CREATED;
    state.createdNode = CREATED;
  });

  it('созданный документ сразу открыт в правой панели (без мигания заглушкой)', async () => {
    const user = userEvent.setup();
    render(<DocumentsPage />, { wrapper });

    // До создания — заглушка выбора.
    expect(screen.getByText('Выберите документ или папку')).toBeInTheDocument();

    await createDocumentViaModal(user);

    // Правая панель — редактор созданного документа (кнопка «Сохранить»), не заглушка.
    expect(await screen.findByRole('button', { name: /Сохранить/ })).toBeInTheDocument();
    expect(screen.queryByText('Выберите документ или папку')).not.toBeInTheDocument();
  });

  it('после рефетча с узлом: документ виден в дереве, предок раскрыт, редактор остаётся', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<DocumentsPage />, { wrapper });
    await createDocumentViaModal(user);
    await screen.findByRole('button', { name: /Сохранить/ });

    // Рефетч дерева принёс X (новый снимок).
    state.tree = [FOLDER, CREATED];
    state.treeUpdatedAt = 2000;
    rerender(<DocumentsPage />);

    const tree = screen.getByRole('tree', { name: 'Дерево документов' });
    // X виден в дереве — значит предок раскрыт (expandFrom по parent_id).
    await waitFor(() => expect(within(tree).getByText('Инструкция')).toBeInTheDocument());
    const folderRow = Array.from(tree.querySelectorAll('[role="treeitem"]')).find((el) =>
      el.textContent?.includes('Папка F'),
    );
    expect(folderRow?.getAttribute('aria-expanded')).toBe('true');
    // Редактор всё ещё открыт (теперь узел резолвится из дерева).
    expect(screen.getByRole('button', { name: /Сохранить/ })).toBeInTheDocument();
  });

  it('регресс iteration-1: рефетч без узла (каскадное удаление предка) → документ НЕ остаётся открытым', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<DocumentsPage />, { wrapper });
    await createDocumentViaModal(user);
    await screen.findByRole('button', { name: /Сохранить/ });

    // Рефетч: предок F удалён каскадом ⇒ X отсутствует в новом снимке. dataUpdatedAt сменился.
    state.tree = [];
    state.treeUpdatedAt = 3000;
    rerender(<DocumentsPage />);

    // Фолбэк самоочищается (не залипает на удалённом узле) → заглушка, редактора нет.
    await waitFor(() =>
      expect(screen.getByText('Выберите документ или папку')).toBeInTheDocument(),
    );
    expect(screen.queryByRole('button', { name: /Сохранить/ })).not.toBeInTheDocument();
  });
});
