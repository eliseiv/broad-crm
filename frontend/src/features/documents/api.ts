import { apiRequest, apiRequestBlob } from '@/lib/api';
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

/**
 * Тонкий слой над `apiRequest` для модуля «Документы» (04-api.md §Documents,
 * префикс `/api/documents`, JWT). Типы request/response — строго по контракту
 * (форма `DocumentNode`). Внешний контур `/api/external/*` здесь не представлен
 * (машинный, не для UI).
 */

/** GET /api/documents/tree — всё видимое дерево (без `content_md`), порядок сервера. */
export function getTree(signal?: AbortSignal): Promise<DocumentNode[]> {
  return apiRequest<DocumentNode[]>('/documents/tree', { signal });
}

/**
 * GET /api/documents/nodes?parent_id= — дети узла (без `content_md`). `parentId`
 * не задан/`null` → узлы корня. Часть контрактной поверхности (лениво-раскрываемые
 * уровни); страница строит дерево из `getTree`, а `getNode` догружает контент.
 */
export function listNodes(parentId?: string | null, signal?: AbortSignal): Promise<DocumentNode[]> {
  const qs = new URLSearchParams();
  if (parentId) qs.set('parent_id', parentId);
  const suffix = qs.toString();
  return apiRequest<DocumentNode[]>(`/documents/nodes${suffix ? `?${suffix}` : ''}`, { signal });
}

/** GET /api/documents/nodes/{id} — один узел (+`content_md` для документа). */
export function getNode(id: string, signal?: AbortSignal): Promise<DocumentNode> {
  return apiRequest<DocumentNode>(`/documents/nodes/${id}`, { signal });
}

/** POST /api/documents/folders → 201 DocumentNode (folder). */
export function createFolder(payload: DocumentFolderCreateRequest): Promise<DocumentNode> {
  return apiRequest<DocumentNode>('/documents/folders', { method: 'POST', body: payload });
}

/** POST /api/documents/documents → 201 DocumentNode (document). */
export function createDocument(payload: DocumentCreateRequest): Promise<DocumentNode> {
  return apiRequest<DocumentNode>('/documents/documents', { method: 'POST', body: payload });
}

/**
 * POST /api/documents/upload — загрузка `.md`-файла как документа (multipart).
 * `FormData` уходит как есть; `Content-Type`/boundary ставит браузер (lib/api.ts).
 * Не-`.md`/размер/битый UTF-8 → 422 document_upload_invalid.
 */
export function uploadMd(form: FormData): Promise<DocumentNode> {
  return apiRequest<DocumentNode>('/documents/upload', { method: 'POST', body: form });
}

/**
 * PATCH /api/documents/nodes/{id} — rename и/или content (presence-семантика).
 * `expected_version` опц.: mismatch → 409 document_node_conflict (optimistic-lock).
 */
export function updateNode(id: string, payload: DocumentNodeUpdateRequest): Promise<DocumentNode> {
  return apiRequest<DocumentNode>(`/documents/nodes/${id}`, { method: 'PATCH', body: payload });
}

/** POST /api/documents/nodes/{id}/copy → 201 корневой DocumentNode копии. */
export function copyNode(id: string, payload: DocumentCopyRequest): Promise<DocumentNode> {
  return apiRequest<DocumentNode>(`/documents/nodes/${id}/copy`, { method: 'POST', body: payload });
}

/** GET /api/documents/nodes/{id}/visibility — предзаполнение модалки (собственные роли узла). */
export function getNodeVisibility(id: string, signal?: AbortSignal): Promise<DocumentVisibility> {
  return apiRequest<DocumentVisibility>(`/documents/nodes/${id}/visibility`, { signal });
}

/** PATCH /api/documents/nodes/{id}/visibility → 200 DocumentNode. */
export function setVisibility(id: string, payload: DocumentVisibility): Promise<DocumentNode> {
  return apiRequest<DocumentNode>(`/documents/nodes/${id}/visibility`, {
    method: 'PATCH',
    body: payload,
  });
}

/** PATCH /api/documents/order — полная перестановка уровня → 204. */
export function reorderNodes(payload: DocumentOrderRequest): Promise<void> {
  return apiRequest<void>('/documents/order', { method: 'PATCH', body: payload });
}

/** DELETE /api/documents/nodes/{id} → 204 (soft-delete; папка — каскад поддерева). */
export function deleteNode(id: string): Promise<void> {
  return apiRequest<void>(`/documents/nodes/${id}`, { method: 'DELETE' });
}

/** GET /api/documents/role-refs — роли для модалки видимости (гейт documents:share). */
export function listRoleRefs(signal?: AbortSignal): Promise<DocumentRoleRef[]> {
  return apiRequest<DocumentRoleRef[]>('/documents/role-refs', { signal });
}

/**
 * POST /api/documents/nodes/{id}/attachments — загрузка изображения (multipart `file`).
 * Гейт `documents:edit` + видимость узла (ADR-068 §2). Валидация — серверная: размер по
 * потоку и тип по magic bytes; отказ → `422 document_attachment_invalid`.
 * В ответе `url` — канонический адрес вложения, который клиент подставляет в `src`.
 */
export function uploadAttachment(nodeId: string, file: File): Promise<DocumentAttachment> {
  const form = new FormData();
  form.append('file', file);
  return apiRequest<DocumentAttachment>(`/documents/nodes/${nodeId}/attachments`, {
    method: 'POST',
    body: form,
  });
}

/**
 * GET /api/documents/attachments/{id} — байты картинки под JWT (ADR-068 §3).
 * `<img src="/api/…">` напрямую НЕ работает: браузер отправит запрос без `Authorization`
 * и получит `401`, а анонимная раздача означала бы утечку в обход видимости узла. Поэтому
 * байты забираются авторизованным `fetch` и подставляются как `blob:`-URL.
 * Нет / невидим / soft-deleted → единый `404 document_attachment_not_found`.
 */
export function fetchAttachmentBlob(id: string, signal?: AbortSignal): Promise<Blob> {
  return apiRequestBlob(`/documents/attachments/${id}`, { signal });
}
