import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PropsWithChildren } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentsPage } from '@/pages/DocumentsPage';
import { loginAs, logout } from '@/test/authTestUtils';
import type { DocumentNode } from '@/types/api';

beforeAll(() => {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  proto.hasPointerCapture ??= () => false;
  proto.setPointerCapture ??= () => {};
  proto.releasePointerCapture ??= () => {};
  proto.scrollIntoView ??= () => {};
});

const DOC: DocumentNode = {
  id: 'n1',
  node_type: 'document',
  parent_id: null,
  name: 'Регламент',
  content_md: '',
  owner_id: 'o',
  visibility_mode: 'inherit',
  content_version: 1,
  position: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

vi.mock('@/features/documents/hooks', () => ({
  documentNodeKey: ['documents', 'node'],
  useDocumentTree: () => ({
    data: [DOC],
    isLoading: false,
    error: null,
    dataUpdatedAt: 1,
    refetch: vi.fn(),
  }),
  useDocumentNode: () => ({
    data: DOC,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateDocument: () => ({ mutate: vi.fn(), isPending: false }),
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

function wrapper({ children }: PropsWithChildren) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

async function openKebab() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: /Действия: Регламент/ }));
  return user;
}

describe('DocumentsPage kebab — «Сменить видимость» (ADR-078)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => logout());

  it('share ∈ permissions → пункт есть (даже без isAdminLevel)', async () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      isAdminLevel: false,
      permissions: { documents: ['view', 'edit', 'share'] },
    });
    render(<DocumentsPage />, { wrapper });
    await openKebab();
    expect(screen.getByRole('menuitem', { name: 'Сменить видимость' })).toBeInTheDocument();
  });

  it('роль «Админ»: isAdminLevel=true при permissions без share → пункт есть (фолбэк)', async () => {
    loginAs({
      isSuperadmin: false,
      role: 'Админ',
      isAdminLevel: true,
      permissions: { documents: ['view', 'edit'] },
    });
    render(<DocumentsPage />, { wrapper });
    await openKebab();
    expect(screen.getByRole('menuitem', { name: 'Сменить видимость' })).toBeInTheDocument();
  });

  it('роль «Админ»: share в permissions + isAdminLevel → Users-баг закрыт (оба гейта)', async () => {
    loginAs({
      isSuperadmin: false,
      role: 'Админ',
      isAdminLevel: true,
      permissions: { documents: ['view', 'create', 'edit', 'delete', 'share'] },
    });
    render(<DocumentsPage />, { wrapper });
    await openKebab();
    expect(screen.getByRole('menuitem', { name: 'Сменить видимость' })).toBeInTheDocument();
  });

  it('нет share и isAdminLevel=false → пункта нет', async () => {
    loginAs({
      isSuperadmin: false,
      role: 'Админ',
      isAdminLevel: false,
      permissions: { documents: ['view', 'edit'] },
    });
    render(<DocumentsPage />, { wrapper });
    await openKebab();
    expect(screen.queryByRole('menuitem', { name: 'Сменить видимость' })).not.toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Переименовать' })).toBeInTheDocument();
  });
});
