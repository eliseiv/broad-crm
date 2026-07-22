import Image from '@tiptap/extension-image';
import { ReactNodeViewRenderer } from '@tiptap/react';
import type { MarkdownSerializerState } from '@tiptap/pm/markdown';
import type { Node as ProseMirrorNode } from '@tiptap/pm/model';
import { DocumentImageNodeView } from '@/components/DocumentImageNodeView';
import { imageUploadPlaceholder } from '@/features/documents/imageUploadPlaceholder';

/**
 * Изображение документа — `@tiptap/extension-image` (ADR-068 §4, 02-tech-stack.md; версия в
 * lockstep с ядром TipTap 2.27.2).
 *
 * - **`allowBase64: false`** — требование модели, а не настройка вкуса: иначе вставка из
 *   буфера положила бы `data:`-URI прямо в `content_md`, съедая `DOCUMENTS_MAX_MD_BYTES`
 *   base64-мусором в обход хранилища вложений и засоряя корпус RAG. **Любая** картинка
 *   проходит через upload вложения.
 * - **`inline: false`** — изображение блочный узел: `tiptap-markdown` сериализует его
 *   отдельной строкой `![alt](/api/documents/attachments/{id})`, тогда как инлайн-вариант
 *   вклеивает картинку в абзац и ломает round-trip markdown ↔ ProseMirror.
 *
 * Отрисовка — собственный NodeView (авторизованный `fetch` + `blob:`), т.к. `<img src>` на
 * защищённый эндпоинт уходит без `Authorization`.
 */
export const DocumentImage = Image.extend({
  /**
   * Сериализация в markdown. Дефолт `tiptap-markdown` (`defaultMarkdownSerializer.nodes.image`)
   * рассчитан на **инлайновую** картинку и не закрывает блок — следующий абзац приклеился бы
   * к ссылке в той же строке. Наш узел блочный, поэтому после записи вызывается `closeBlock`.
   */
  addStorage() {
    return {
      markdown: {
        serialize(state: MarkdownSerializerState, node: ProseMirrorNode) {
          const alt = typeof node.attrs.alt === 'string' ? node.attrs.alt : '';
          const rawSrc = typeof node.attrs.src === 'string' ? node.attrs.src : '';
          const title = typeof node.attrs.title === 'string' ? node.attrs.title : '';
          const src = rawSrc.replace(/[()]/g, '\\$&');
          const titlePart = title ? ` "${title.replace(/"/g, '\\"')}"` : '';
          state.write(`![${state.esc(alt)}](${src}${titlePart})`);
          state.closeBlock(node);
        },
        parse: {
          // markdown-it отдаёт `<img>`; разбор — штатным `parseHTML` расширения.
        },
      },
    };
  },

  addNodeView() {
    return ReactNodeViewRenderer(DocumentImageNodeView);
  },

  addProseMirrorPlugins() {
    return [imageUploadPlaceholder];
  },
}).configure({ inline: false, allowBase64: false });
