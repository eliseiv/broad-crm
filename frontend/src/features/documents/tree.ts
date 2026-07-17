import type { DocumentNode } from '@/types/api';

/** Узел дерева с уже разрешёнными детьми (строится клиентом из плоского `GET /tree`). */
export interface DocumentTreeNode extends DocumentNode {
  children: DocumentTreeNode[];
}

/**
 * Строит вложенное дерево из плоского массива `GET /api/documents/tree` по `parent_id`
 * (04-api.md: «клиент строит дерево по parent_id»). Порядок детей СОХРАНЯЕТСЯ как пришёл
 * с сервера (`position ASC, created_at DESC, id`) — массив не пересортировывается. Узлы с
 * «оборванным» `parent_id` (родитель не виден текущему пользователю — теоретически
 * невозможно при консистентном фильтре, но защищаемся) поднимаются в корень.
 */
export function buildTree(nodes: DocumentNode[]): DocumentTreeNode[] {
  const byId = new Map<string, DocumentTreeNode>();
  for (const n of nodes) byId.set(n.id, { ...n, children: [] });

  const roots: DocumentTreeNode[] = [];
  for (const n of nodes) {
    const node = byId.get(n.id) as DocumentTreeNode;
    const parent = n.parent_id ? byId.get(n.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

/**
 * Число потомков узла (рекурсивно, все уровни) по плоскому массиву — для подтверждения
 * удаления папки («…и N вложенных элементов»). Считает по `parent_id`, сам узел не входит.
 */
export function descendantCount(nodes: DocumentNode[], nodeId: string): number {
  const childrenOf = new Map<string | null, DocumentNode[]>();
  for (const n of nodes) {
    const list = childrenOf.get(n.parent_id) ?? [];
    list.push(n);
    childrenOf.set(n.parent_id, list);
  }
  let count = 0;
  const stack: string[] = [nodeId];
  while (stack.length > 0) {
    const current = stack.pop() as string;
    for (const child of childrenOf.get(current) ?? []) {
      count += 1;
      stack.push(child.id);
    }
  }
  return count;
}

/**
 * `id` узла и всех его потомков — множество недопустимых целей копирования/перемещения
 * (цель = сам узел или его потомок → цикл, 04-api.md `document_copy_cycle`). Используется
 * для отсеивания опций в селекторе целевой папки (сервер — источник истины, это UX-гейт).
 */
export function subtreeIds(nodes: DocumentNode[], nodeId: string): Set<string> {
  const childrenOf = new Map<string | null, DocumentNode[]>();
  for (const n of nodes) {
    const list = childrenOf.get(n.parent_id) ?? [];
    list.push(n);
    childrenOf.set(n.parent_id, list);
  }
  const ids = new Set<string>([nodeId]);
  const stack: string[] = [nodeId];
  while (stack.length > 0) {
    const current = stack.pop() as string;
    for (const child of childrenOf.get(current) ?? []) {
      ids.add(child.id);
      stack.push(child.id);
    }
  }
  return ids;
}

/** Видимая (с учётом раскрытия) строка дерева — плоская, с уровнем для ARIA/отступа. */
export interface FlatTreeRow {
  node: DocumentTreeNode;
  /** Уровень вложенности (корень = 1) — `aria-level` и левый отступ. */
  level: number;
  /** Есть ли дети (папка может быть пустой — тогда `false`). */
  hasChildren: boolean;
  /** Раскрыт ли узел (только для папок с детьми). */
  expanded: boolean;
  /** Позиция среди сиблингов (1-based) и размер набора — `aria-posinset`/`aria-setsize`. */
  posInSet: number;
  setSize: number;
}

/**
 * Разворачивает дерево в плоский список ВИДИМЫХ строк (дети свёрнутой папки пропускаются)
 * с уровнем/позицией — модель для `TreeView` (плоский DOM с `aria-level`, валидный ARIA-tree)
 * и для линейной клавиатурной навигации.
 */
export function flattenVisible(
  tree: DocumentTreeNode[],
  expanded: ReadonlySet<string>,
): FlatTreeRow[] {
  const rows: FlatTreeRow[] = [];
  const walk = (siblings: DocumentTreeNode[], level: number) => {
    siblings.forEach((node, index) => {
      const hasChildren = node.node_type === 'folder' && node.children.length > 0;
      const isExpanded = hasChildren && expanded.has(node.id);
      rows.push({
        node,
        level,
        hasChildren,
        expanded: isExpanded,
        posInSet: index + 1,
        setSize: siblings.length,
      });
      if (isExpanded) walk(node.children, level + 1);
    });
  };
  walk(tree, 1);
  return rows;
}

/** Опция выбора родительской папки: `value=''` = корень; путь строится через « / ». */
export interface FolderOption {
  value: string;
  label: string;
}

/**
 * Плоский список папок с путями («Гайды / Онбординг») для селекторов «родительская папка»
 * (создание/загрузка) и «целевая папка» (копирование). `exclude` — id, которые нужно скрыть
 * (напр. поддерево копируемого узла, чтобы не предлагать циклическую цель). Первая опция —
 * «Корень» (`value=''`).
 */
export function folderOptions(nodes: DocumentNode[], exclude?: Set<string>): FolderOption[] {
  const byId = new Map<string, DocumentNode>();
  for (const n of nodes) byId.set(n.id, n);

  const pathOf = (node: DocumentNode): string => {
    const parts: string[] = [];
    let current: DocumentNode | undefined = node;
    const guard = new Set<string>();
    while (current && !guard.has(current.id)) {
      guard.add(current.id);
      parts.unshift(current.name);
      current = current.parent_id ? byId.get(current.parent_id) : undefined;
    }
    return parts.join(' / ');
  };

  const options: FolderOption[] = [{ value: '', label: 'Корень' }];
  for (const n of nodes) {
    if (n.node_type !== 'folder') continue;
    if (exclude?.has(n.id)) continue;
    options.push({ value: n.id, label: pathOf(n) });
  }
  return options;
}
