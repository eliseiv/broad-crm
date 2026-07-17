import { describe, expect, it } from 'vitest';
import {
  buildTree,
  descendantCount,
  flattenVisible,
  folderOptions,
  subtreeIds,
} from '@/features/documents/tree';
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

// Дерево: F1 (папка) → [D1 (doc), F2 (папка) → [D2 (doc)]], F3 (папка, пустая).
const flat: DocumentNode[] = [
  node({ id: 'F1', node_type: 'folder' }),
  node({ id: 'D1', node_type: 'document', parent_id: 'F1' }),
  node({ id: 'F2', node_type: 'folder', parent_id: 'F1' }),
  node({ id: 'D2', node_type: 'document', parent_id: 'F2' }),
  node({ id: 'F3', node_type: 'folder' }),
];

describe('buildTree', () => {
  it('строит вложенное дерево по parent_id, сохраняя порядок сервера', () => {
    const roots = buildTree(flat);
    expect(roots.map((r) => r.id)).toEqual(['F1', 'F3']);
    const f1 = roots[0];
    expect(f1.children.map((c) => c.id)).toEqual(['D1', 'F2']);
    expect(f1.children[1].children.map((c) => c.id)).toEqual(['D2']);
  });

  it('оборванный parent_id (родитель не виден) поднимается в корень', () => {
    const orphan = [node({ id: 'X', parent_id: 'MISSING' })];
    expect(buildTree(orphan).map((r) => r.id)).toEqual(['X']);
  });
});

describe('descendantCount', () => {
  it('считает всех потомков рекурсивно, сам узел не входит', () => {
    expect(descendantCount(flat, 'F1')).toBe(3); // D1, F2, D2
    expect(descendantCount(flat, 'F2')).toBe(1); // D2
    expect(descendantCount(flat, 'F3')).toBe(0);
    expect(descendantCount(flat, 'D1')).toBe(0);
  });
});

describe('subtreeIds', () => {
  it('возвращает id узла и всех потомков (недопустимые цели копирования)', () => {
    expect(subtreeIds(flat, 'F1')).toEqual(new Set(['F1', 'D1', 'F2', 'D2']));
    expect(subtreeIds(flat, 'F3')).toEqual(new Set(['F3']));
  });
});

describe('flattenVisible', () => {
  it('свёрнутая папка скрывает потомков; уровень/позиция для ARIA проставлены', () => {
    const tree = buildTree(flat);
    const collapsed = flattenVisible(tree, new Set());
    expect(collapsed.map((r) => r.node.id)).toEqual(['F1', 'F3']);
    const f1Row = collapsed[0];
    expect(f1Row.level).toBe(1);
    expect(f1Row.hasChildren).toBe(true);
    expect(f1Row.expanded).toBe(false);
    expect(f1Row.posInSet).toBe(1);
    expect(f1Row.setSize).toBe(2);
  });

  it('раскрытая папка показывает детей со следующим уровнем', () => {
    const tree = buildTree(flat);
    const rows = flattenVisible(tree, new Set(['F1', 'F2']));
    expect(rows.map((r) => r.node.id)).toEqual(['F1', 'D1', 'F2', 'D2', 'F3']);
    const d2 = rows.find((r) => r.node.id === 'D2')!;
    expect(d2.level).toBe(3);
    expect(d2.hasChildren).toBe(false);
  });

  it('пустая папка не имеет hasChildren даже в expanded', () => {
    const tree = buildTree(flat);
    const rows = flattenVisible(tree, new Set(['F3']));
    const f3 = rows.find((r) => r.node.id === 'F3')!;
    expect(f3.hasChildren).toBe(false);
  });
});

describe('folderOptions', () => {
  it('строит опции папок с путями через « / », первая — «Корень»', () => {
    const opts = folderOptions(flat);
    expect(opts[0]).toEqual({ value: '', label: 'Корень' });
    const labels = Object.fromEntries(opts.map((o) => [o.value, o.label]));
    expect(labels['F2']).toBe('F1 / F2');
    // Документы не попадают в опции родительских папок.
    expect(opts.some((o) => o.value === 'D1')).toBe(false);
  });

  it('exclude скрывает поддерево (цель копирования не может быть циклической)', () => {
    const opts = folderOptions(flat, subtreeIds(flat, 'F1'));
    const values = opts.map((o) => o.value);
    expect(values).not.toContain('F1');
    expect(values).not.toContain('F2');
    expect(values).toContain('F3');
  });
});
