import { useCallback, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  FilePlus,
  FileText,
  Folder,
  FolderPlus,
  RefreshCw,
  Upload,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { TreeView } from '@/components/ui/TreeView';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { DocumentCreateModal } from '@/components/DocumentCreateModal';
import { DocumentDeleteDialog } from '@/components/DocumentDeleteDialog';
import { DocumentEditor } from '@/components/DocumentEditor';
import { DocumentNodeMenu } from '@/components/DocumentNodeRow';
import { DocumentRenameModal } from '@/components/DocumentRenameModal';
import { DocumentVisibilityModal } from '@/components/DocumentVisibilityModal';
import { UploadMdModal } from '@/components/UploadMdModal';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useCopyNode, useDocumentNode, useDocumentTree } from '@/features/documents/hooks';
import { buildTree, flattenVisible } from '@/features/documents/tree';
import type { DocumentNode } from '@/types/api';

/** Заглушка по центру правой панели (пусто / загрузка / ошибка). */
function CenteredState({
  icon,
  title,
  hint,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 py-16 text-center">
      {icon}
      <div>
        <p className="text-base font-semibold text-text-primary">{title}</p>
        {hint && <p className="mt-1 text-[13px] text-text-secondary">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

/** Page-level view-guard (documents:view). Единственный хук до раннего возврата — гейт. */
export function DocumentsPage() {
  const canView = useCanViewPage('documents');
  if (!canView) {
    return (
      <div className="w-full px-6 py-8">
        <InsufficientPermissions />
      </div>
    );
  }
  return <DocumentsWorkspace />;
}

function DocumentsWorkspace() {
  const treeQuery = useDocumentTree();
  const nodes = useMemo<DocumentNode[]>(() => treeQuery.data ?? [], [treeQuery.data]);
  const tree = useMemo(() => buildTree(nodes), [nodes]);

  const canCreate = useCan('documents', 'create');
  const canEdit = useCan('documents', 'edit');
  const canDelete = useCan('documents', 'delete');
  const canShare = useCan('documents', 'share');

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileDetail, setMobileDetail] = useState(false);
  // Только что созданный узел — фолбэк правой панели до прихода рефетча дерева (см. selectedNode).
  // `treeUpdatedAt` — отметка снимка дерева на момент создания: фолбэк действует, только пока
  // рефетч не пришёл, и перестаёт действовать с приходом ЛЮБОГО нового снимка.
  const [justCreated, setJustCreated] = useState<{
    node: DocumentNode;
    treeUpdatedAt: number;
  } | null>(null);

  // Модальное состояние (single-instance модалки на всю страницу).
  const [createKind, setCreateKind] = useState<'folder' | 'document' | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [renameNode, setRenameNode] = useState<DocumentNode | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DocumentNode | null>(null);
  const [visibilityNode, setVisibilityNode] = useState<DocumentNode | null>(null);

  const copyMutation = useCopyNode();

  const rows = useMemo(() => flattenVisible(tree, expanded), [tree, expanded]);
  const selectedNode = useMemo(() => {
    const fromTree = nodes.find((n) => n.id === selectedId);
    if (fromTree) return fromTree;
    // Локальный фолбэк на только что созданный узел (08-design-system.md §Страница «Документы»,
    // ADR-063): рефетч дерева ещё в полёте, но узел уже должен быть открыт справа — без
    // промежуточного мигания заглушкой «Выберите документ или папку».
    //
    // Окно фолбэка — строго «узел ещё не приходил с сервера»: он самоочищается приходом рефетча
    // (снимок дерева обновился ⇒ отметка разошлась), а не только появлением узла в дереве. Иначе
    // фолбэк залипал бы на узле, которого в свежем дереве УЖЕ НЕТ — например, после каскадного
    // удаления родительской папки, — и рендерил бы полностью редактируемый удалённый документ.
    if (!justCreated || justCreated.node.id !== selectedId) return null;
    return justCreated.treeUpdatedAt === treeQuery.dataUpdatedAt ? justCreated.node : null;
  }, [nodes, selectedId, justCreated, treeQuery.dataUpdatedAt]);

  // Ленивая догрузка контента выбранного документа (content_md не входит в дерево).
  const isDocumentSelected = selectedNode?.node_type === 'document';
  const nodeQuery = useDocumentNode(selectedId, Boolean(isDocumentSelected));

  // Родитель по умолчанию для создания/загрузки: выбранная папка / родитель документа / корень.
  const defaultParentId = useMemo(() => {
    if (!selectedNode) return null;
    return selectedNode.node_type === 'folder' ? selectedNode.id : selectedNode.parent_id;
  }, [selectedNode]);

  const toggleExpand = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  /**
   * Раскрыть цепочку папок ОТ РОДИТЕЛЯ вверх до корня (08-design-system.md §Страница «Документы»,
   * ADR-063). Параметризация именно по `parent_id`, а не по id созданного узла: родитель
   * существовал до создания, поэтому присутствует даже в ещё не обновлённом снимке дерева —
   * раскрытие не ждёт рефетча и созданный узел сразу виден, в т.ч. внутри свёрнутой папки.
   */
  const expandFrom = useCallback(
    (parentId: string | null) => {
      if (!parentId) return;
      const byId = new Map(nodes.map((n) => [n.id, n]));
      const chain = new Set<string>([parentId]);
      let current = byId.get(parentId);
      while (current?.parent_id && !chain.has(current.parent_id)) {
        chain.add(current.parent_id);
        current = byId.get(current.parent_id);
      }
      setExpanded((prev) => new Set([...prev, ...chain]));
    },
    [nodes],
  );

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
    setMobileDetail(true);
  }, []);

  const handleTreeSelect = useCallback(
    (id: string) => {
      const node = nodes.find((n) => n.id === id);
      // Клик по папке в дереве раскрывает/сворачивает её и показывает обзор справа.
      if (node?.node_type === 'folder') toggleExpand(id);
      handleSelect(id);
    },
    [nodes, toggleExpand, handleSelect],
  );

  /** Создание папки/документа и загрузка .md — общий путь: узел сразу виден и открыт. */
  const openCreated = useCallback(
    (node: DocumentNode) => {
      expandFrom(node.parent_id);
      // Отметка текущего снимка дерева: фолбэк действует, пока рефетч (инвалидация после создания)
      // не принесёт новый снимок — тогда `dataUpdatedAt` разойдётся и фолбэк снимется.
      setJustCreated({ node, treeUpdatedAt: treeQuery.dataUpdatedAt });
      setSelectedId(node.id);
      setMobileDetail(true);
    },
    [expandFrom, treeQuery.dataUpdatedAt],
  );

  const handleCopy = useCallback(
    (node: DocumentNode) => {
      copyMutation.mutate(
        { id: node.id, payload: { target_parent_id: node.parent_id } },
        {
          onSuccess: () => toast.success('Копия создана'),
          onError: (err) => {
            if (
              err instanceof ApiError &&
              err.status === 422 &&
              err.code === 'document_copy_cycle'
            ) {
              toast.error('Нельзя скопировать узел внутрь самого себя');
              return;
            }
            if (err instanceof ApiError && err.status === 403) {
              toast.error('Недостаточно прав');
              return;
            }
            toast.error(err instanceof ApiError ? err.message : 'Не удалось создать копию');
          },
        },
      );
    },
    [copyMutation],
  );

  const handleDeleted = useCallback(
    (node: DocumentNode) => {
      // Удаление — каскадный soft-delete поддерева: фолбэк-узел мог быть среди удалённых потомков
      // (удаляют папку F, а залип фолбэк на её ребёнке X — id не совпадут, поэтому снимаем его
      // безусловно). Мостик «создание → рефетч» после удаления в любом случае не нужен: удаление
      // инвалидирует дерево и рефетч всё равно придёт.
      setJustCreated(null);
      if (selectedId === node.id) {
        setSelectedId(null);
        setMobileDetail(false);
      }
    },
    [selectedId],
  );

  const renderActions = useCallback(
    (row: { node: DocumentNode }) => (
      <DocumentNodeMenu
        node={row.node}
        canEdit={canEdit}
        canCreate={canCreate}
        canShare={canShare}
        canDelete={canDelete}
        onRename={setRenameNode}
        onCopy={handleCopy}
        onVisibility={setVisibilityNode}
        onDelete={setDeleteTarget}
      />
    ),
    [canEdit, canCreate, canShare, canDelete, handleCopy],
  );

  // --- Левый сайдбар (дерево) ---
  const sidebar = (
    <div
      className={cn(
        'flex min-h-0 flex-col border-border-subtle md:w-[30%] md:flex-none md:border-r',
        mobileDetail ? 'hidden md:flex' : 'flex',
      )}
    >
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border-subtle px-3 py-2">
        <span className="text-sm font-semibold text-text-primary">Документы</span>
        {canCreate && (
          <div className="flex items-center gap-0.5">
            <Button
              variant="ghost"
              size="sm"
              aria-label="Новая папка"
              onClick={() => setCreateKind('folder')}
            >
              <FolderPlus className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-label="Новый документ"
              onClick={() => setCreateKind('document')}
            >
              <FilePlus className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-label="Загрузить .md"
              onClick={() => setUploadOpen(true)}
            >
              <Upload className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        )}
      </div>

      <div className="scrollbar-none min-h-0 flex-1 overflow-y-auto px-2">
        {treeQuery.isLoading ? (
          <div className="flex flex-col gap-2 p-3">
            {[0, 1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="h-8 animate-pulse rounded-md border border-border-subtle bg-surface-2"
              />
            ))}
          </div>
        ) : treeQuery.error ? (
          <TreeError error={treeQuery.error} onRetry={() => treeQuery.refetch()} />
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
            <Folder className="h-8 w-8 text-text-tertiary" aria-hidden="true" />
            <p className="text-sm font-medium text-text-primary">Документов пока нет</p>
            {canCreate && (
              <p className="text-[12px] text-text-secondary">
                Создайте папку или документ кнопками выше.
              </p>
            )}
          </div>
        ) : (
          <TreeView
            rows={rows}
            selectedId={selectedId}
            onSelect={handleTreeSelect}
            onToggleExpand={toggleExpand}
            renderActions={renderActions}
            ariaLabel="Дерево документов"
          />
        )}
      </div>
    </div>
  );

  // --- Правая панель (обзор папки / редактор документа / пусто) ---
  let detail: React.ReactNode;
  if (!selectedNode) {
    detail = (
      <CenteredState
        icon={<FileText className="h-9 w-9 text-text-tertiary" aria-hidden="true" />}
        title="Выберите документ или папку"
        hint="Слева — дерево папок и документов."
      />
    );
  } else if (selectedNode.node_type === 'folder') {
    detail = (
      <FolderOverview
        folder={selectedNode}
        nodes={nodes}
        onOpen={handleSelect}
        onBack={() => setMobileDetail(false)}
      />
    );
  } else if (nodeQuery.isLoading) {
    detail = (
      <CenteredState
        icon={<FileText className="h-9 w-9 text-text-tertiary" aria-hidden="true" />}
        title="Загрузка документа…"
      />
    );
  } else if (nodeQuery.error) {
    const notFound = nodeQuery.error instanceof ApiError && nodeQuery.error.status === 404;
    detail = (
      <CenteredState
        icon={<AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />}
        title={notFound ? 'Документ недоступен' : 'Не удалось загрузить документ'}
        hint={
          notFound
            ? 'Документ удалён или больше не виден вашей роли.'
            : 'Проверьте соединение и попробуйте снова.'
        }
        action={
          !notFound ? (
            <Button variant="outline" onClick={() => nodeQuery.refetch()}>
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              Повторить
            </Button>
          ) : undefined
        }
      />
    );
  } else if (nodeQuery.data) {
    // Ключ ремоунта — только id узла (ADR-063 §B): редактор пересобирается при СМЕНЕ документа и
    // ни при каком другом событии. Прежний составной ключ `id:content_version` отменён — он делал
    // собственное сохранение неотличимым от внешнего изменения (курсор в начало, сброс скролла).
    // Внешние правки приходят в редактор ресинком по расхождению версий, а не ремоунтом.
    detail = (
      <DocumentEditor
        key={nodeQuery.data.id}
        node={nodeQuery.data}
        canEdit={canEdit}
        onBack={() => setMobileDetail(false)}
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-row">
      {sidebar}
      <div className={cn('min-h-0 flex-1 md:block', mobileDetail ? 'block' : 'hidden md:block')}>
        {detail}
      </div>

      {/* Модалки (single-instance). key-ремоунт → чистый стейт на каждое открытие. */}
      {createKind && (
        <DocumentCreateModal
          key={`create-${createKind}-${defaultParentId ?? 'root'}`}
          open={Boolean(createKind)}
          onOpenChange={(open) => !open && setCreateKind(null)}
          kind={createKind}
          nodes={nodes}
          defaultParentId={defaultParentId}
          onCreated={openCreated}
        />
      )}
      {uploadOpen && (
        <UploadMdModal
          key={`upload-${defaultParentId ?? 'root'}`}
          open={uploadOpen}
          onOpenChange={setUploadOpen}
          nodes={nodes}
          defaultParentId={defaultParentId}
          onUploaded={openCreated}
        />
      )}
      {renameNode && (
        <DocumentRenameModal
          key={`rename-${renameNode.id}`}
          open={Boolean(renameNode)}
          onOpenChange={(open) => !open && setRenameNode(null)}
          node={renameNode}
        />
      )}
      {deleteTarget && (
        <DocumentDeleteDialog
          key={`delete-${deleteTarget.id}`}
          open={Boolean(deleteTarget)}
          onOpenChange={(open) => !open && setDeleteTarget(null)}
          node={deleteTarget}
          nodes={nodes}
          onDeleted={handleDeleted}
        />
      )}
      {visibilityNode && (
        <DocumentVisibilityModal
          key={`visibility-${visibilityNode.id}`}
          open={Boolean(visibilityNode)}
          onOpenChange={(open) => !open && setVisibilityNode(null)}
          node={visibilityNode}
        />
      )}
    </div>
  );
}

/** Ошибка загрузки дерева: 403 — заглушка прав; иначе — общий ретрай. */
function TreeError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const forbidden = error instanceof ApiError && error.status === 403;
  if (forbidden) {
    return (
      <div className="px-2 py-6">
        <InsufficientPermissions />
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-3 px-4 py-10 text-center">
      <AlertTriangle className="h-8 w-8 text-status-red" aria-hidden="true" />
      <p className="text-sm font-medium text-text-primary">Не удалось загрузить документы</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
        Повторить
      </Button>
    </div>
  );
}

/** Обзор папки в правой панели: имя + прямые дети (кликом открываются). */
function FolderOverview({
  folder,
  nodes,
  onOpen,
  onBack,
}: {
  folder: DocumentNode;
  nodes: DocumentNode[];
  onOpen: (id: string) => void;
  onBack: () => void;
}) {
  const children = useMemo(
    () => nodes.filter((n) => n.parent_id === folder.id),
    [nodes, folder.id],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-3 py-2">
        <Button variant="ghost" size="sm" onClick={onBack} className="md:hidden" aria-label="Назад">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        </Button>
        <Folder className="h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-text-primary">
          {folder.name}
        </h2>
      </div>
      <div className="scrollbar-none min-h-0 flex-1 overflow-y-auto p-3">
        {children.length === 0 ? (
          <p className="px-1 py-6 text-center text-[13px] text-text-secondary">Папка пуста</p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {children.map((child) => (
              <li key={child.id}>
                <button
                  type="button"
                  onClick={() => onOpen(child.id)}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  {child.node_type === 'folder' ? (
                    <Folder className="h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />
                  ) : (
                    <FileText className="h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />
                  )}
                  <span className="min-w-0 flex-1 truncate">{child.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
