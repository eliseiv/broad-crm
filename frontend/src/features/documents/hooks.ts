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
  uploadAttachment,
  uploadMd,
} from '@/features/documents/api';
import type {
  DocumentAttachment,
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
 * PATCH /api/documents/nodes/{id} (rename/content). Инвалидирует дерево (имя/позиция) и
 * **сливает** ответ в кэш узла. `expected_version` опц. → 409 при конфликте.
 *
 * Кэш узла (ADR-063 §A, 04-api.md §PATCH /api/documents/nodes/{id}): ответ этого PATCH несёт
 * `content_md: null` — контент отдаёт только GET /nodes/{id}. Поэтому класть ответ в кэш узла
 * целиком ЗАПРЕЩЕНО: это затирает контент и даёт видимую потерю текста на экране (данные на
 * сервере целы). Ответ сливается поверх предыдущего значения:
 *
 * - предыдущего значения нет → частичная запись НЕ сеется (следующий GET заполнит кэш целиком);
 * - в теле запроса был `content_md` (строго `!== undefined`; `''` — валидный контент нового
 *   документа) → в кэш идёт отправленное клиентом значение: сервер подтвердил его записью;
 * - иначе (rename и любая другая мутация без `content_md`) → контент берётся из прежнего кэша;
 * - остальные поля (`name`, `content_version`, `updated_at`, `position`) — из ответа сервера.
 *
 * Правило действует для ВСЕХ потребителей этого PATCH — и сохранения контента в редакторе, и
 * переименования (общий хук; иначе rename открытого документа опустошает его). Инвалидация узла
 * на успешном пути не применяется (лишний round-trip GET) — только в ветке 409, где нужны чужие
 * данные.
 */
export function useUpdateNode() {
  const queryClient = useQueryClient();
  return useMutation<DocumentNode, unknown, { id: string; payload: DocumentNodeUpdateRequest }>({
    mutationFn: ({ id, payload }) => updateNode(id, payload),
    onSuccess: (data, variables) => {
      invalidateTree(queryClient);
      queryClient.setQueryData<DocumentNode>([...documentNodeKey, data.id], (previous) => {
        if (!previous) return undefined;
        const sent = variables.payload.content_md;
        return {
          ...previous,
          ...data,
          content_md: sent !== undefined ? sent : previous.content_md,
        };
      });
    },
  });
}

/**
 * Загрузка изображения в документ (POST /nodes/{id}/attachments, ADR-068). Дерево НЕ
 * инвалидируется: вложение не меняет ни структуру, ни имя узла. Кэш узла тоже не трогаем —
 * ссылка попадает в `content_md` только вместе с сохранением документа (PATCH), а вложение
 * иммутабельно (замена картинки = новая загрузка = новый `id`).
 *
 * ⚠️ **Вызывать только через `mutateAsync` и обрабатывать результат в `try/catch/finally`
 * вызывающего.** Загрузки бывают ПАРАЛЛЕЛЬНЫМИ (Ctrl+V может дать несколько картинок сразу),
 * а хук даёт один `MutationObserver` на компонент: `mutate()` при каждом вызове перецепляет
 * observer и перетирает его per-call `onSuccess`/`onError`/`onSettled`, из-за чего колбэки
 * первого из параллельных вызовов не срабатывают никогда. Промис `mutateAsync` приходит от
 * самой мутации и от observer не зависит. Общее `isPending` по той же причине непригодно как
 * индикатор конкретной загрузки — счётчик держит вызывающий.
 */
export function useUploadAttachment() {
  return useMutation<DocumentAttachment, unknown, { nodeId: string; file: File }>({
    mutationFn: ({ nodeId, file }) => uploadAttachment(nodeId, file),
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
