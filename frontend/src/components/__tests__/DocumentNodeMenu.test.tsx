import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { DocumentNodeMenu } from '@/components/DocumentNodeRow';
import type { DocumentNode } from '@/types/api';

// Radix DropdownMenu использует Pointer Capture API, которого нет в jsdom.
beforeAll(() => {
  const proto = Element.prototype as unknown as Record<string, unknown>;
  proto.hasPointerCapture ??= () => false;
  proto.setPointerCapture ??= () => {};
  proto.releasePointerCapture ??= () => {};
  proto.scrollIntoView ??= () => {};
});

const NODE: DocumentNode = {
  id: 'n1',
  node_type: 'document',
  parent_id: null,
  name: 'Регламент',
  content_md: null,
  owner_id: 'o',
  visibility_mode: 'inherit',
  content_version: 1,
  position: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function renderMenu(perms: {
  canEdit?: boolean;
  canCreate?: boolean;
  canShare?: boolean;
  canDelete?: boolean;
}) {
  const handlers = {
    onRename: vi.fn(),
    onCopy: vi.fn(),
    onVisibility: vi.fn(),
    onDelete: vi.fn(),
  };
  render(
    <DocumentNodeMenu
      node={NODE}
      canEdit={perms.canEdit ?? false}
      canCreate={perms.canCreate ?? false}
      canShare={perms.canShare ?? false}
      canDelete={perms.canDelete ?? false}
      {...handlers}
    />,
  );
  return handlers;
}

describe('DocumentNodeMenu (kebab, RBAC-гейтинг пунктов, ADR-061)', () => {
  it('со всеми правами показывает 4 пункта', async () => {
    const user = userEvent.setup();
    renderMenu({ canEdit: true, canCreate: true, canShare: true, canDelete: true });
    await user.click(screen.getByRole('button', { name: /Действия: Регламент/ }));
    expect(screen.getByRole('menuitem', { name: 'Переименовать' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Создать копию' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Сменить видимость' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Удалить' })).toBeInTheDocument();
  });

  it('без права share пункт «Сменить видимость» отсутствует', async () => {
    const user = userEvent.setup();
    renderMenu({ canEdit: true, canCreate: true, canShare: false, canDelete: true });
    await user.click(screen.getByRole('button', { name: /Действия/ }));
    expect(screen.queryByRole('menuitem', { name: 'Сменить видимость' })).not.toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Переименовать' })).toBeInTheDocument();
  });

  it('без единого права меню не рендерится (нет триггера)', () => {
    renderMenu({});
    expect(screen.queryByRole('button', { name: /Действия/ })).not.toBeInTheDocument();
  });

  it('выбор пункта «Удалить» вызывает onDelete с узлом', async () => {
    const user = userEvent.setup();
    const handlers = renderMenu({ canDelete: true });
    await user.click(screen.getByRole('button', { name: /Действия/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Удалить' }));
    expect(handlers.onDelete).toHaveBeenCalledWith(NODE);
  });
});
