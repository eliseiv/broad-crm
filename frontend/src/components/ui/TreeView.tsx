import { useRef } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { ChevronRight, FileText, Folder, FolderOpen } from 'lucide-react';
import type { FlatTreeRow } from '@/features/documents/tree';
import { cn } from '@/lib/cn';

interface TreeViewProps {
  /** Плоский список видимых строк (см. `flattenVisible`) — уже упорядочен и отфильтрован. */
  rows: FlatTreeRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** Раскрыть/свернуть папку (папка с детьми). */
  onToggleExpand: (id: string) => void;
  /** Действия строки (kebab-меню) — рендерятся справа, видимы на hover/focus строки. */
  renderActions?: (row: FlatTreeRow) => ReactNode;
  ariaLabel: string;
}

/**
 * Рекурсивное дерево папок/документов (08-design-system.md «Компонент TreeView», ADR-061).
 * DOM плоский (строки со `aria-level`) — валидный ARIA-tree и простая линейная клавиатура:
 * ↑/↓ — перемещение фокуса; → — раскрыть папку / войти в первого ребёнка; ← — свернуть /
 * к родителю; Enter/Space — выбрать; Home/End — край. Активная строка — акцент; видимый
 * focus-ring. Иконки folder/folder-open/file-text. Своя реализация без новой зависимости.
 */
export function TreeView({
  rows,
  selectedId,
  onSelect,
  onToggleExpand,
  renderActions,
  ariaLabel,
}: TreeViewProps) {
  const itemRefs = useRef<(HTMLDivElement | null)[]>([]);

  const focusRow = (index: number) => {
    const clamped = Math.max(0, Math.min(index, rows.length - 1));
    itemRefs.current[clamped]?.focus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>, index: number) => {
    const row = rows[index];
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        focusRow(index + 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        focusRow(index - 1);
        break;
      case 'Home':
        e.preventDefault();
        focusRow(0);
        break;
      case 'End':
        e.preventDefault();
        focusRow(rows.length - 1);
        break;
      case 'ArrowRight':
        e.preventDefault();
        if (row.hasChildren && !row.expanded) onToggleExpand(row.node.id);
        else if (row.hasChildren && row.expanded) focusRow(index + 1);
        break;
      case 'ArrowLeft':
        e.preventDefault();
        if (row.hasChildren && row.expanded) onToggleExpand(row.node.id);
        else {
          // К ближайшему предку (меньший level выше по списку).
          for (let i = index - 1; i >= 0; i -= 1) {
            if (rows[i].level < row.level) {
              focusRow(i);
              break;
            }
          }
        }
        break;
      case 'Enter':
      case ' ':
        e.preventDefault();
        onSelect(row.node.id);
        break;
      default:
        break;
    }
  };

  return (
    <div role="tree" aria-label={ariaLabel} className="flex flex-col py-1">
      {rows.map((row, index) => {
        const { node } = row;
        const isFolder = node.node_type === 'folder';
        const isSelected = node.id === selectedId;
        const FolderIcon = row.expanded ? FolderOpen : Folder;
        return (
          <div
            key={node.id}
            ref={(el) => {
              itemRefs.current[index] = el;
            }}
            role="treeitem"
            aria-level={row.level}
            aria-selected={isSelected}
            aria-posinset={row.posInSet}
            aria-setsize={row.setSize}
            aria-expanded={row.hasChildren ? row.expanded : undefined}
            tabIndex={isSelected || (selectedId === null && index === 0) ? 0 : -1}
            onKeyDown={(e) => handleKeyDown(e, index)}
            onClick={() => onSelect(node.id)}
            style={{ paddingLeft: `${(row.level - 1) * 16 + 8}px` }}
            className={cn(
              'group/row relative flex cursor-pointer items-center gap-1.5 rounded-md py-1.5 pr-1.5 text-[13px] transition-colors',
              'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent',
              isSelected
                ? 'bg-accent/10 text-accent'
                : 'text-text-secondary hover:bg-surface-2 hover:text-text-primary',
            )}
          >
            {/* Chevron раскрытия — только у папок с детьми; иначе распорка для выравнивания. */}
            {row.hasChildren ? (
              <button
                type="button"
                tabIndex={-1}
                aria-hidden="true"
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleExpand(node.id);
                }}
                className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded text-text-tertiary hover:text-text-primary"
              >
                <ChevronRight
                  className={cn('h-3.5 w-3.5 transition-transform', row.expanded && 'rotate-90')}
                />
              </button>
            ) : (
              <span className="inline-block h-4 w-4 shrink-0" aria-hidden="true" />
            )}
            {isFolder ? (
              <FolderIcon className="h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />
            ) : (
              <FileText className="h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />
            )}
            <span className="min-w-0 flex-1 truncate">{node.name}</span>
            {renderActions && (
              <span className="shrink-0 opacity-0 transition-opacity group-focus-within/row:opacity-100 group-hover/row:opacity-100">
                {renderActions(row)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
