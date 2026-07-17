import { Copy, Eye, Pencil, Trash2 } from 'lucide-react';
import { DropdownMenu } from '@/components/ui/DropdownMenu';
import type { DropdownMenuItem } from '@/components/ui/DropdownMenu';
import type { DocumentNode } from '@/types/api';

interface DocumentNodeMenuProps {
  node: DocumentNode;
  canEdit: boolean;
  canCreate: boolean;
  canShare: boolean;
  canDelete: boolean;
  onRename: (node: DocumentNode) => void;
  onCopy: (node: DocumentNode) => void;
  onVisibility: (node: DocumentNode) => void;
  onDelete: (node: DocumentNode) => void;
}

/**
 * kebab-меню строки узла дерева (08-design-system.md «Страница Документы»): «Переименовать»
 * (edit) / «Создать копию» (create) / «Сменить видимость» (share) / «Удалить» (delete).
 * Пункт рендерится ⇔ есть соответствующее право (UX-гейт; безопасность — сервер 403).
 * Пустой набор (нет ни одного права на действия) → меню не рендерится (DropdownMenu).
 */
export function DocumentNodeMenu({
  node,
  canEdit,
  canCreate,
  canShare,
  canDelete,
  onRename,
  onCopy,
  onVisibility,
  onDelete,
}: DocumentNodeMenuProps) {
  const items: DropdownMenuItem[] = [];
  if (canEdit) {
    items.push({ label: 'Переименовать', icon: Pencil, onSelect: () => onRename(node) });
  }
  if (canCreate) {
    items.push({ label: 'Создать копию', icon: Copy, onSelect: () => onCopy(node) });
  }
  if (canShare) {
    items.push({ label: 'Сменить видимость', icon: Eye, onSelect: () => onVisibility(node) });
  }
  if (canDelete) {
    items.push({
      label: 'Удалить',
      icon: Trash2,
      tone: 'danger',
      onSelect: () => onDelete(node),
    });
  }

  return <DropdownMenu items={items} triggerAriaLabel={`Действия: ${node.name}`} />;
}
