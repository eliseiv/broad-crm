import type { DocumentAttachmentMime } from '@/types/api';

/**
 * Константы и хелперы вложений-изображений документа (04-api.md §Вложения, ADR-068).
 *
 * Клиентские проверки здесь — **подсказка UX, а не граница безопасности**: авторитет —
 * сервер (`422 document_attachment_invalid`; тип определяется по magic bytes, а не по
 * заявленному `Content-Type`). Смысл проверок — не гонять по сети файл, который заведомо
 * будет отвергнут, и дать понятную причину сразу.
 */

/** Whitelist ровно из четырёх типов (ADR-068 §2.3; SVG исключён нормативно — XSS-вектор). */
export const ATTACHMENT_MIMES: readonly DocumentAttachmentMime[] = [
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
];

/** Значение `accept` для file-picker'а (08-design-system.md §Изображения в DocumentEditor). */
export const ATTACHMENT_ACCEPT = ATTACHMENT_MIMES.join(',');

/**
 * `DOCUMENTS_MAX_IMAGE_BYTES` — серверный env, default 5 МБ (04-api.md §POST attachments).
 * Клиент повторяет дефолт; расхождение с сервером безопасно — отказ всё равно придёт `422`.
 */
export const ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024;

/** Канонический префикс адреса вложения — его формирует **сервер** (поле `url`, ADR-068 §2). */
const ATTACHMENT_URL_PREFIX = '/api/documents/attachments/';

/**
 * Причина отказа клиентской предпроверки или `null`, если файл похож на допустимый.
 * Тексты — по 04-api.md (лимит `DOCUMENTS_MAX_IMAGE_BYTES` и whitelist четырёх типов).
 */
export function validateAttachmentFile(file: File): string | null {
  if (!(ATTACHMENT_MIMES as readonly string[]).includes(file.type)) {
    return 'Поддерживаются только изображения PNG, JPEG, WebP и GIF';
  }
  if (file.size > ATTACHMENT_MAX_BYTES) {
    return 'Изображение больше 5 МБ — уменьшите файл';
  }
  return null;
}

/**
 * `id` вложения из `src` узла изображения — или `null`, если ссылка ведёт не в наш
 * контур вложений (внешний `https:`-адрес в markdown; такую картинку грузит сам браузер).
 * Разбор идёт **по адресу, который отдал сервер**; клиент URL не конструирует.
 */
export function attachmentIdFromSrc(src: string): string | null {
  if (!src.startsWith(ATTACHMENT_URL_PREFIX)) return null;
  const id = src.slice(ATTACHMENT_URL_PREFIX.length);
  // Хвост вида `<id>?x=1` или пустой id — не наш адрес.
  return id.length > 0 && !id.includes('/') && !id.includes('?') ? id : null;
}
