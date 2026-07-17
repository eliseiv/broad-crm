import { render, screen, fireEvent, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TreeView } from '@/components/ui/TreeView';
import { buildTree, flattenVisible } from '@/features/documents/tree';
import type { DocumentNode } from '@/types/api';

function node(partial: Partial<DocumentNode> & { id: string }): DocumentNode {
  return {
    node_type: 'folder',
    parent_id: null,
    name: partial.id,
    content_md: null,
    owner_id: 'owner',
    visibility_mode: 'inherit',
    content_version: 1,
    position: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...partial,
  };
}

const flat: DocumentNode[] = [
  node({ id: 'F1', name: 'Гайды', node_type: 'folder' }),
  node({ id: 'D1', name: 'Онбординг', node_type: 'document', parent_id: 'F1' }),
  node({ id: 'F2', name: 'Пусто', node_type: 'folder' }),
];

function renderTree(
  expanded: Set<string>,
  overrides: Partial<Parameters<typeof TreeView>[0]> = {},
) {
  const rows = flattenVisible(buildTree(flat), expanded);
  const onSelect = vi.fn();
  const onToggleExpand = vi.fn();
  render(
    <TreeView
      rows={rows}
      selectedId={null}
      onSelect={onSelect}
      onToggleExpand={onToggleExpand}
      ariaLabel="Дерево документов"
      {...overrides}
    />,
  );
  return { onSelect, onToggleExpand };
}

describe('TreeView (ARIA-tree + клавиатура, ADR-061/08-design-system)', () => {
  it('рендерит валидный ARIA-tree: role tree/treeitem, aria-level, aria-expanded', () => {
    renderTree(new Set());
    const tree = screen.getByRole('tree', { name: 'Дерево документов' });
    expect(tree).toBeInTheDocument();
    const items = within(tree).getAllByRole('treeitem');
    expect(items).toHaveLength(2); // F1, F2 (D1 скрыт — F1 свёрнут)
    // Папка с детьми несёт aria-expanded; уровень корня = 1.
    const f1 = screen.getByRole('treeitem', { name: /Гайды/ });
    expect(f1).toHaveAttribute('aria-level', '1');
    expect(f1).toHaveAttribute('aria-expanded', 'false');
    // Пустая папка НЕ несёт aria-expanded (нет детей).
    const f2 = screen.getByRole('treeitem', { name: /Пусто/ });
    expect(f2).not.toHaveAttribute('aria-expanded');
  });

  it('раскрытая папка показывает ребёнка с aria-level=2', () => {
    renderTree(new Set(['F1']));
    const child = screen.getByRole('treeitem', { name: /Онбординг/ });
    expect(child).toHaveAttribute('aria-level', '2');
    expect(screen.getByRole('treeitem', { name: /Гайды/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('ArrowDown/ArrowUp перемещают фокус между строками', () => {
    renderTree(new Set());
    const items = screen.getAllByRole('treeitem');
    items[0].focus();
    fireEvent.keyDown(items[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: 'ArrowUp' });
    expect(document.activeElement).toBe(items[0]);
  });

  it('Home/End прыгают на края', () => {
    renderTree(new Set(['F1']));
    const items = screen.getAllByRole('treeitem'); // F1, D1, F2
    items[1].focus();
    fireEvent.keyDown(items[1], { key: 'End' });
    expect(document.activeElement).toBe(items[items.length - 1]);
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(items[0]);
  });

  it('Enter выбирает узел', () => {
    const { onSelect } = renderTree(new Set());
    const f1 = screen.getByRole('treeitem', { name: /Гайды/ });
    f1.focus();
    fireEvent.keyDown(f1, { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('F1');
  });

  it('ArrowRight на свёрнутой папке раскрывает её', () => {
    const { onToggleExpand } = renderTree(new Set());
    const f1 = screen.getByRole('treeitem', { name: /Гайды/ });
    f1.focus();
    fireEvent.keyDown(f1, { key: 'ArrowRight' });
    expect(onToggleExpand).toHaveBeenCalledWith('F1');
  });

  it('ArrowLeft на раскрытой папке сворачивает её', () => {
    const { onToggleExpand } = renderTree(new Set(['F1']));
    const f1 = screen.getByRole('treeitem', { name: /Гайды/ });
    f1.focus();
    fireEvent.keyDown(f1, { key: 'ArrowLeft' });
    expect(onToggleExpand).toHaveBeenCalledWith('F1');
  });
});
