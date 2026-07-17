import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import {
  copyNode,
  createDocument,
  createFolder,
  deleteNode,
  getNode,
  getNodeVisibility,
  getTree,
  listRoleRefs,
  reorderNodes,
  setVisibility,
  updateNode,
  uploadMd,
} from '@/features/documents/api';
import type {
  DocumentCopyRequest,
  DocumentCreateRequest,
  DocumentFolderCreateRequest,
  DocumentNode,
  DocumentNodeUpdateRequest,
  DocumentOrderRequest,
  DocumentRoleRef,
  DocumentVisibility,
} from '@/types/api';

export const documentsTreeKey = ['documents', 'tree'] as const;
export const documentNodeKey = ['documents', 'node'] as const;
export const documentVisibilityKey = ['documents', 'visibility'] as const;
export const documentRoleRefsKey = ['documents', 'role-refs'] as const;

/**
 * Всё видимое дерево документов (GET /api/documents/tree) — единый источник структуры
 * сайдбара. Клиент строит вложенность по `parent_id` (04-api.md). Мутации инвалидируют
 * весь префикс `['documents','tree']`.
 */
export function useDocumentTree(): UseQueryResult<DocumentNode[]> {
  return useQuery({
    queryKey: documentsTreeKey,
    queryFn: ({ signal }) => getTree(signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/**
 * Полный узел с `content_md` (GET /api/documents/nodes/{id}). Ленивая догрузка контента
 * документа при его открытии (`enabled` — образец `useTeamMailboxes`): контент не входит
 * в дерево, поэтому запрашивается отдельно только для выбранного документа.
 */
export function useDocumentNode(id: string | null, enabled: boolean): UseQueryResult<DocumentNode> {
  return useQuery({
    queryKey: [...documentNodeKey, id] as const,
    queryFn: ({ signal }) => getNode(id as string, signal),
    enabled: enabled && Boolean(id),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/** Собственная видимость узла для предзаполнения модалки (ленивая — только при открытии). */
export function useNodeVisibility(
  id: string | null,
  enabled: boolean,
): UseQueryResult<DocumentVisibility> {
  return useQuery({
    queryKey: [...documentVisibilityKey, id] as const,
    queryFn: ({ signal }) => getNodeVisibility(id as string, signal),
    enabled: enabled && Boolean(id),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/** Роли для модалки видимости (GET /api/documents/role-refs, гейт documents:share). */
export function useRoleRefs(enabled: boolean): UseQueryResult<DocumentRoleRef[]> {
  return useQuery({
    queryKey: documentRoleRefsKey,
    queryFn: ({ signal }) => listRoleRefs(signal),
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/** Инвалидация дерева после любой структурной/именной мутации (04-api.md). */
function invalidateTree(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: documentsTreeKey });
}

export function useCreateFolder() {
  const queryClient = useQueryClient();
  return useMutation<DocumentNode, unknown, DocumentFolderCreateRequest>({
    mutationFn: (payload) => createFolder(payload),
    onSuccess: () => invalidateTree(queryClient),
  });
}

export function useCreateDocument() {
  const queryClient = useQueryClient();
  return useMutation<DocumentNode, unknown, DocumentCreateRequest>({
    mutationFn: (payload) => createDocument(payload),
    onSuccess: () => invalidateTree(queryClient),
  });
}

export function useUploadMd() {
  const queryClient = useQueryClient();
  return useMutation<DocumentNode, unknown, FormData>({
    mutationFn: (form) => uploadMd(form),
    onSuccess: () => invalidateTree(queryClient),
  });
}

/**
 * PATCH /api/documents/nodes/{id} (rename/content). Инвалидирует дерево (имя/позиция)
 * и кэш конкретного узла (контент/версия). `expected_version` опц. → 409 при конфликте.
 */
export function useUpdateNode() {
  const queryClient = useQueryClient();
  return useMutation<DocumentNode, unknown, { id: string; payload: DocumentNodeUpdateRequest }>({
    mutationFn: ({ id, payload }) => updateNode(id, payload),
    onSuccess: (data) => {
      invalidateTree(queryClient);
      queryClient.setQueryData([...documentNodeKey, data.id], data);
    },
  });
}

export function useCopyNode() {
  const queryClient = useQueryClient();
  return useMutation<DocumentNode, unknown, { id: string; payload: DocumentCopyRequest }>({
    mutationFn: ({ id, payload }) => copyNode(id, payload),
    onSuccess: () => invalidateTree(queryClient),
  });
}

export function useSetVisibility() {
  const queryClient = useQueryClient();
  return useMutation<DocumentNode, unknown, { id: string; payload: DocumentVisibility }>({
    mutationFn: ({ id, payload }) => setVisibility(id, payload),
    onSuccess: (data) => {
      invalidateTree(queryClient);
      void queryClient.invalidateQueries({ queryKey: [...documentVisibilityKey, data.id] });
    },
  });
}

export function useReorderNodes() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, DocumentOrderRequest>({
    mutationFn: (payload) => reorderNodes(payload),
    onSuccess: () => invalidateTree(queryClient),
  });
}

export function useDeleteNode() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => deleteNode(id),
    onSuccess: () => invalidateTree(queryClient),
  });
}
