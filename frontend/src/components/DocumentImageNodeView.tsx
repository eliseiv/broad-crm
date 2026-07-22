import { useEffect, useState } from 'react';
import { NodeViewWrapper } from '@tiptap/react';
import type { NodeViewProps } from '@tiptap/react';
import { ImageOff, Loader2 } from 'lucide-react';
import { cn } from '@/lib/cn';
import { fetchAttachmentBlob } from '@/features/documents/api';
import { attachmentIdFromSrc } from '@/features/documents/attachments';

type ImageState = 'loading' | 'ready' | 'error';

/**
 * NodeView изображения документа (ADR-068 §3, 08-design-system.md §Изображения в
 * `DocumentEditor`). **Голый `<img src="/api/…">` здесь невозможен:** JWT живёт в
 * `localStorage`, браузер отправит запрос картинки **без** `Authorization` и получит `401`,
 * а сделать эндпоинт анонимным ради «просто `<img>`» запрещено — это утечка картинки в
 * обход per-node видимости узла.
 *
 * Поэтому байты забираются **авторизованным `fetch`** и подставляются как
 * `URL.createObjectURL(blob)`; на размонтировании и при смене `src` blob **обязательно**
 * освобождается `URL.revokeObjectURL` — иначе blob'ы копятся в памяти вкладки на всю сессию.
 *
 * Состояния: загрузка (плейсхолдер) → показ; `404`/ошибка сети → плашка «Изображение
 * недоступно» + alt-текст (недоступное вложение не ломает документ — ADR-068 §5, случай
 * перенесённой вручную ссылки или удалённого вложения).
 *
 * Внешний (`https:`) адрес в markdown вложением не является — такую картинку грузит сам
 * браузер (CSP `img-src` включает `https:`), авторизация ей не нужна.
 */
export function DocumentImageNodeView({ node, selected }: NodeViewProps) {
  const src = typeof node.attrs.src === 'string' ? node.attrs.src : '';
  const alt = typeof node.attrs.alt === 'string' ? node.attrs.alt : '';
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<ImageState>(() =>
    attachmentIdFromSrc(src) ? 'loading' : src ? 'ready' : 'error',
  );

  useEffect(() => {
    const attachmentId = attachmentIdFromSrc(src);
    if (!attachmentId) {
      // Не наш контур вложений: внешний адрес отдаём браузеру как есть, пустой — ошибка.
      setObjectUrl(null);
      setStatus(src ? 'ready' : 'error');
      return;
    }

    let active = true;
    let created: string | null = null;
    const controller = new AbortController();
    setStatus('loading');
    setObjectUrl(null);

    fetchAttachmentBlob(attachmentId, controller.signal)
      .then((blob) => {
        if (!active) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
        setStatus('ready');
      })
      .catch(() => {
        // Прерванный запрос (смена документа/размонтирование) состояние не меняет.
        if (active) setStatus('error');
      });

    return () => {
      active = false;
      controller.abort();
      // Обязательное освобождение blob-URL (ADR-068 §3).
      if (created) URL.revokeObjectURL(created);
    };
  }, [src]);

  return (
    <NodeViewWrapper
      className={cn('doc-image', selected && 'doc-image--selected')}
      data-drag-handle
    >
      {status === 'loading' && (
        <span className="doc-image-status" role="status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Загрузка изображения…
        </span>
      )}

      {status === 'error' && (
        <span className="doc-image-status doc-image-status--error">
          <ImageOff className="h-4 w-4" aria-hidden="true" />
          Изображение недоступно
          {alt && <span className="doc-image-alt">{alt}</span>}
        </span>
      )}

      {status === 'ready' && (
        <img
          src={objectUrl ?? src}
          alt={alt}
          className="doc-image-img"
          draggable={false}
          onError={() => setStatus('error')}
        />
      )}
    </NodeViewWrapper>
  );
}
