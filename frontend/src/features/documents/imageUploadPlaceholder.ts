import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { EditorState } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import type { EditorView } from '@tiptap/pm/view';

/**
 * Плейсхолдер загрузки изображения (08-design-system.md §Изображения в `DocumentEditor`:
 * «Во время загрузки — плейсхолдер/спиннер на месте будущей картинки»).
 *
 * Реализован **декорацией ProseMirror**, а не временным узлом документа: декорация живёт
 * вне документа ⇒ не попадает ни в `content_md`, ни в markdown-сериализацию, а её позиция
 * автоматически переносится при правках, сделанных пока файл летит на сервер. Временный
 * узел пришлось бы удалять руками и он мог бы утечь в сохранение при `Ctrl+S` в этот момент.
 */

interface PlaceholderMeta {
  add?: { id: string; pos: number };
  remove?: { id: string };
}

export const imageUploadPlaceholderKey = new PluginKey<DecorationSet>('documentImageUpload');

function createPlaceholderElement(): HTMLElement {
  const el = document.createElement('span');
  el.className = 'doc-image-status';
  el.setAttribute('role', 'status');
  el.textContent = 'Загрузка изображения…';
  return el;
}

export const imageUploadPlaceholder = new Plugin<DecorationSet>({
  key: imageUploadPlaceholderKey,
  state: {
    init: () => DecorationSet.empty,
    apply(tr, set) {
      let next = set.map(tr.mapping, tr.doc);
      const meta = tr.getMeta(imageUploadPlaceholderKey) as PlaceholderMeta | undefined;
      if (meta?.add) {
        const widget = Decoration.widget(meta.add.pos, createPlaceholderElement(), {
          id: meta.add.id,
        });
        next = next.add(tr.doc, [widget]);
      }
      if (meta?.remove) {
        const { id } = meta.remove;
        next = next.remove(next.find(undefined, undefined, (spec) => spec.id === id));
      }
      return next;
    },
  },
  props: {
    decorations(state) {
      return imageUploadPlaceholderKey.getState(state);
    },
  },
});

/** Показать плейсхолдер в позиции `pos` (обычно — текущая каретка). */
export function addImageUploadPlaceholder(view: EditorView, id: string, pos: number): void {
  view.dispatch(view.state.tr.setMeta(imageUploadPlaceholderKey, { add: { id, pos } }));
}

/** Убрать плейсхолдер (успех, ошибка или отмена загрузки). */
export function removeImageUploadPlaceholder(view: EditorView, id: string): void {
  view.dispatch(view.state.tr.setMeta(imageUploadPlaceholderKey, { remove: { id } }));
}

/**
 * Актуальная позиция плейсхолдера (документ мог измениться, пока шла загрузка) или `null`,
 * если его уже нет — тогда вставлять картинку в запомненную позицию нельзя.
 */
export function findImageUploadPlaceholder(state: EditorState, id: string): number | null {
  const set = imageUploadPlaceholderKey.getState(state);
  const found = set?.find(undefined, undefined, (spec) => spec.id === id);
  return found && found.length > 0 ? found[0].from : null;
}
