import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  ACTION_LABEL,
  PAGE_LABEL,
  actionLabel,
  catalogActionColumns,
  pageLabel,
} from '@/features/users/labels';
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

  it('PAGE_LABEL.broadcast === "Рассылка"; extra-действия имеют подписи (TD-068/TD-071)', () => {
    expect(PAGE_LABEL.broadcast).toBe('Рассылка');
    expect(actionLabel('share')).toBe('Видимость');
    expect(actionLabel('send')).toBe('Отправка');
    expect(actionLabel('sync')).toBe('Синк');
    expect(actionLabel('tags')).toBe('Теги');
    expect(actionLabel('transfer')).toBe('Перенос');
  });

  it('каждый ключ нормативного CATALOG имеет PAGE_LABEL; extra-действия — ACTION_LABEL', () => {
    const catalogPages = [
      'dashboard',
      'servers',
      'ai-keys',
      'proxies',
      'backends',
      'backend-users',
      'backend-economics',
      'mail',
      'sms',
      'roles',
      'teams',
      'documents',
      'broadcast',
    ];
    for (const page of catalogPages) {
      expect(PAGE_LABEL[page], `нет PAGE_LABEL для ${page}`).toBeTruthy();
      expect(PAGE_LABEL[page]).not.toBe(page);
    }
    for (const action of ['share', 'send', 'sync', 'tags', 'transfer']) {
      expect(ACTION_LABEL[action]).toBeTruthy();
    }
  });

  it('матрица из каталога показывает столбцы share/send/sync/tags/transfer', () => {
    const catalog: PermissionCatalogPage[] = [
      { page: 'documents', actions: ['view', 'create', 'edit', 'delete', 'share'] },
      { page: 'broadcast', actions: ['view', 'send'] },
      { page: 'mail', actions: ['view', 'create', 'edit', 'delete', 'sync', 'tags'] },
      { page: 'sms', actions: ['view', 'edit', 'transfer', 'sync', 'delete'] },
    ];
    expect(catalogActionColumns(catalog)).toEqual([
      'view',
      'create',
      'edit',
      'delete',
      'share',
      'send',
      'sync',
      'tags',
      'transfer',
    ]);
    render(<RoleEditorModal open onOpenChange={vi.fn()} catalog={catalog} mode="add" />);
    expect(screen.getByText('Видимость')).toBeInTheDocument();
    expect(screen.getByText('Отправка')).toBeInTheDocument();
    expect(screen.getByText('Синк')).toBeInTheDocument();
    expect(screen.getByText('Теги')).toBeInTheDocument();
    expect(screen.getByText('Перенос')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Документы — Видимость' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Рассылка — Отправка' })).toBeInTheDocument();
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
