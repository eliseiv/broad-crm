import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PAGE_LABEL, pageLabel } from '@/features/users/labels';
import { RoleEditorModal } from '@/components/RoleEditorModal';
import type { PermissionCatalogPage } from '@/types/api';

vi.mock('@/features/users/hooks', () => ({
  useCreateRole: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateRole: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteRole: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe('Локализация каталога прав — раздел «Документы» (ADR-063 §D)', () => {
  it('PAGE_LABEL.documents === "Документы"', () => {
    expect(PAGE_LABEL.documents).toBe('Документы');
  });

  it('pageLabel("documents") возвращает русскую подпись, а не сырой ключ', () => {
    expect(pageLabel('documents')).toBe('Документы');
    expect(pageLabel('documents')).not.toBe('documents');
  });

  it('pageLabel неизвестного ключа деградирует в сам ключ (фолбэк сохранён для прочих)', () => {
    expect(pageLabel('__unknown__')).toBe('__unknown__');
  });

  it('матрица прав /roles рендерит "Документы", а не сырой ключ "documents"', () => {
    const catalog: PermissionCatalogPage[] = [
      { page: 'documents', actions: ['view', 'create', 'edit', 'delete'] },
    ];
    render(<RoleEditorModal open onOpenChange={vi.fn()} catalog={catalog} mode="add" />);

    // Строка матрицы подписана «Документы».
    expect(screen.getByText('Документы')).toBeInTheDocument();
    // Чекбоксы действий именуются локализованно; сырого ключа "documents" в подписях нет.
    expect(screen.getByRole('checkbox', { name: 'Документы — Просмотр' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Документы — Удаление' })).toBeInTheDocument();
    expect(screen.queryByText('documents')).not.toBeInTheDocument();
  });
});
