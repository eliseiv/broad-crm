import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { EditorContent, useEditor } from '@tiptap/react';
import type { Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import { Markdown } from 'tiptap-markdown';
import {
  ArrowLeft,
  Bold,
  Code,
  Heading1,
  Heading2,
  Heading3,
  Image as ImageIcon,
  Italic,
  Link2,
  List,
  ListOrdered,
  Loader2,
  Quote,
  Save,
  SquareCode,
  Strikethrough,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { ATTACHMENT_ACCEPT, validateAttachmentFile } from '@/features/documents/attachments';
import { documentNodeKey, useUpdateNode, useUploadAttachment } from '@/features/documents/hooks';
import { DocumentImage } from '@/features/documents/imageExtension';
import {
  addImageUploadPlaceholder,
  findImageUploadPlaceholder,
  removeImageUploadPlaceholder,
} from '@/features/documents/imageUploadPlaceholder';
import type { DocumentNode } from '@/types/api';

interface DocumentEditorProps {
  /** Полный узел документа (с `content_md`, из GET /nodes/{id}). */
  node: DocumentNode;
  canEdit: boolean;
  /** Показать кнопку «Назад» (узкие вьюпорты — одна колонка). */
  onBack?: () => void;
}

interface ToolbarButton {
  label: string;
  icon: LucideIcon;
  isActive: () => boolean;
  run: () => void;
}

/** Кнопка тулбара форматирования (WYSIWYG). */
function ToolbarAction({
  label,
  icon: Icon,
  active,
  onClick,
}: {
  label: string;
  icon: LucideIcon;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        active
          ? 'bg-accent/15 text-accent'
          : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
      )}
    >
      <Icon className="h-4 w-4" aria-hidden={true} />
    </button>
  );
}

/**
 * Список изображений из буфера обмена. Сначала `items` (Ctrl+V скриншота даёт именно их),
 * затем — фолбэк на `files`. Не-картиночная вставка сюда не попадает и обрабатывается
 * редактором по умолчанию.
 */
function clipboardImageFiles(data: DataTransfer | null): File[] {
  if (!data) return [];
  const files: File[] = [];
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }
  if (files.length > 0) return files;
  return Array.from(data.files ?? []).filter((file) => file.type.startsWith('image/'));
}

function Toolbar({
  editor,
  onInsertImage,
  imageUploading,
}: {
  editor: Editor;
  /** Открыть выбор файла для вставки изображения (гейт `documents:edit` — как весь тулбар). */
  onInsertImage: () => void;
  imageUploading: boolean;
}) {
  const groups: ToolbarButton[][] = [
    [
      {
        label: 'Заголовок 1',
        icon: Heading1,
        isActive: () => editor.isActive('heading', { level: 1 }),
        run: () => editor.chain().focus().toggleHeading({ level: 1 }).run(),
      },
      {
        label: 'Заголовок 2',
        icon: Heading2,
        isActive: () => editor.isActive('heading', { level: 2 }),
        run: () => editor.chain().focus().toggleHeading({ level: 2 }).run(),
      },
      {
        label: 'Заголовок 3',
        icon: Heading3,
        isActive: () => editor.isActive('heading', { level: 3 }),
        run: () => editor.chain().focus().toggleHeading({ level: 3 }).run(),
      },
    ],
    [
      {
        label: 'Жирный',
        icon: Bold,
        isActive: () => editor.isActive('bold'),
        run: () => editor.chain().focus().toggleBold().run(),
      },
      {
        label: 'Курсив',
        icon: Italic,
        isActive: () => editor.isActive('italic'),
        run: () => editor.chain().focus().toggleItalic().run(),
      },
      {
        label: 'Зачёркнутый',
        icon: Strikethrough,
        isActive: () => editor.isActive('strike'),
        run: () => editor.chain().focus().toggleStrike().run(),
      },
      {
        label: 'Моноширинный код',
        icon: Code,
        isActive: () => editor.isActive('code'),
        run: () => editor.chain().focus().toggleCode().run(),
      },
    ],
    [
      {
        label: 'Маркированный список',
        icon: List,
        isActive: () => editor.isActive('bulletList'),
        run: () => editor.chain().focus().toggleBulletList().run(),
      },
      {
        label: 'Нумерованный список',
        icon: ListOrdered,
        isActive: () => editor.isActive('orderedList'),
        run: () => editor.chain().focus().toggleOrderedList().run(),
      },
      {
        label: 'Цитата',
        icon: Quote,
        isActive: () => editor.isActive('blockquote'),
        run: () => editor.chain().focus().toggleBlockquote().run(),
      },
      {
        label: 'Блок кода',
        icon: SquareCode,
        isActive: () => editor.isActive('codeBlock'),
        run: () => editor.chain().focus().toggleCodeBlock().run(),
      },
    ],
    [
      {
        label: 'Ссылка',
        icon: Link2,
        isActive: () => editor.isActive('link'),
        run: () => {
          // Внутри ссылки — кнопка снимает её (toggle).
          if (editor.isActive('link')) {
            editor.chain().focus().unsetLink().run();
            return;
          }
          const previous = editor.getAttributes('link').href as string | undefined;
          const input = window.prompt('Адрес ссылки (URL)', previous ?? 'https://');
          if (input === null) return; // отмена — ничего не меняем
          const url = input.trim();
          // Пустой ввод — снять ссылку с выделения (если была).
          if (url === '') {
            editor.chain().focus().extendMarkRange('link').unsetLink().run();
            return;
          }
          editor.chain().focus().extendMarkRange('link').setLink({ href: url }).run();
        },
      },
    ],
  ];

  return (
    <div
      role="toolbar"
      aria-label="Форматирование"
      className="flex flex-wrap items-center gap-0.5 border-b border-border-subtle px-2 py-1.5"
    >
      {groups.map((group, gi) => (
        <div key={gi} className="flex items-center gap-0.5">
          {gi > 0 && <span className="mx-1 h-5 w-px bg-border-subtle" aria-hidden="true" />}
          {group.map((btn) => (
            <ToolbarAction
              key={btn.label}
              label={btn.label}
              icon={btn.icon}
              active={btn.isActive()}
              onClick={btn.run}
            />
          ))}
        </div>
      ))}

      {/* Изображение (ADR-068): кнопка открывает скрытый file-picker; второй жест — Ctrl+V
          (editorProps.handlePaste). Drag-and-drop файлов не поддерживается — решение
          владельца, `handleDrop` намеренно НЕ переопределяется. */}
      <div className="flex items-center gap-0.5">
        <span className="mx-1 h-5 w-px bg-border-subtle" aria-hidden="true" />
        {/* Кнопка НЕ блокируется во время загрузки: каждая загрузка независима (свой промис
            mutateAsync, свой плейсхолдер), а Ctrl+V и без того позволяет отправить несколько
            картинок сразу — блокировка только у кнопки создавала бы асимметрию жестов.
            Спиннер здесь — индикатор занятости, а не запрет. */}
        <button
          type="button"
          aria-label="Изображение"
          aria-busy={imageUploading}
          onClick={onInsertImage}
          className={cn(
            'inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors',
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
          )}
        >
          {imageUploading ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden={true} />
          ) : (
            <ImageIcon className="h-4 w-4" aria-hidden={true} />
          )}
        </button>
      </div>
    </div>
  );
}

/**
 * WYSIWYG-редактор документа на TipTap (ADR-062, 08-design-system.md «Компонент
 * DocumentEditor»). Хранение — markdown в `content_md`: при открытии `content_md` парсится
 * в ProseMirror (tiptap-markdown), при сохранении сериализуется обратно в markdown и уходит
 * в PATCH /nodes/{id}.
 *
 * Жизненный цикл (ADR-063 §B; прежняя норма «keyed родителем по `id:content_version`» ОТМЕНЕНА —
 * она делала собственное сохранение неотличимым от внешнего изменения и пересобирала редактор:
 * курсор в начало, скролл сброшен). Действующая норма: ключ ремоунта — только `id` узла, а
 * контент ресинкается по расхождению `content_version` с базовой версией текущего маунта
 * (внешняя правка / рефетч после 409). Собственное сохранение обновляет базовую версию в колбэке
 * мутации ⇒ ресинк на него не срабатывает, курсор и позиция скролла сохраняются.
 *
 * Optimistic-lock (TD-064, опц.): PATCH шлётся с `expected_version = node.content_version`;
 * конфликт → 409 document_node_conflict → тост «документ изменён» + рефетч свежей версии.
 *
 * Тулбар покрывает возможности StarterKit (заголовки/жирный/курсив/зачёркнутый/код/списки/
 * цитата/блок кода) плюс «ссылка» — через @tiptap/extension-link (ADR-062 §2, поправка
 * 2026-07-18: граница зависимости расширена этим официальным расширением TipTap). Markdown-
 * ссылки `[text](url)` открываются кликабельными и сохраняются при round-trip (URL не теряется).
 *
 * Изображения (ADR-068): @tiptap/extension-image с `inline:false`/`allowBase64:false`; два
 * жеста вставки — кнопка тулбара и Ctrl+V (`handlePaste`), drag-and-drop файлов НЕ
 * поддерживается (`handleDrop` не переопределяется). Оба жеста ведут в один путь
 * `POST /nodes/{id}/attachments`; отрисовка — авторизованный `fetch` + `blob:` (NodeView),
 * т.к. `<img src="/api/…">` ушёл бы без `Authorization`.
 */
export function DocumentEditor({ node, canEdit, onBack }: DocumentEditorProps) {
  const queryClient = useQueryClient();
  const updateMutation = useUpdateNode();
  const uploadAttachmentMutation = useUploadAttachment();
  const [dirty, setDirty] = useState(false);
  // Число картинок «в полёте» (Ctrl+V может дать несколько сразу): >0 — спиннер на кнопке
  // тулбара. Загрузки независимы, поэтому счётчик ничего не запрещает — только индицирует.
  const [uploadsInFlight, setUploadsInFlight] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // `editorProps` фиксируются при создании редактора, поэтому handlePaste дёргает загрузку
  // через ref — иначе он навсегда захватил бы колбэк первого рендера (устаревший `node.id`).
  const uploadImageRef = useRef<(file: File) => void>(() => {});
  // Базовая версия текущего маунта (ADR-063 §B): версия, с которой смонтирован или последний раз
  // синхронизирован контент редактора. Служит и признаком внешнего изменения (расхождение с
  // серверной content_version → ресинк), и `expected_version` для optimistic-lock (TD-064).
  const baseVersionRef = useRef(node.content_version);

  const editor = useEditor({
    editable: canEdit,
    extensions: [
      StarterKit,
      // Гиперссылки (ADR-062 §2, поправка 2026-07-18). openOnClick:false — клик по ссылке в
      // режиме редактирования ставит курсор, а не открывает URL (не ломает редактирование);
      // markdown-ссылки `[text](url)` парсятся как кликабельные и сериализуются обратно.
      Link.configure({
        openOnClick: false,
        autolink: true,
        linkOnPaste: true,
        HTMLAttributes: { rel: 'noopener noreferrer nofollow', target: '_blank' },
      }),
      // Изображения (ADR-068): inline:false + allowBase64:false, отрисовка через
      // авторизованный fetch + blob: (NodeView). Конфигурация — imageExtension.ts.
      DocumentImage,
      Markdown.configure({ html: false, transformPastedText: true, transformCopiedText: true }),
    ],
    content: node.content_md ?? '',
    editorProps: {
      attributes: {
        // mx-auto — колонка контента центрируется в области редактирования, поля симметричны
        // (ADR-063 §C). Выравнивание самого текста не меняется: он остаётся по левому краю.
        class: 'doc-prose mx-auto min-h-full max-w-3xl focus:outline-none',
        'aria-label': `Содержимое документа «${node.name}»`,
      },
      /**
       * Второй жест вставки картинки — Ctrl+V (ADR-068). Перехватываем, только если в
       * буфере есть файл-изображение; любая другая вставка идёт штатным путём.
       *
       * `handleDrop` НЕ переопределяется — drag-and-drop файлов не поддерживается (решение
       * владельца); перетаскивание ВНУТРИ документа остаётся дефолтным ProseMirror.
       */
      handlePaste: (view, event) => {
        if (!view.editable) return false;
        const files = clipboardImageFiles(event.clipboardData);
        if (files.length === 0) return false;
        event.preventDefault();
        for (const file of files) uploadImageRef.current(file);
        return true;
      },
    },
    onUpdate: () => setDirty(true),
  });

  // Синхронизация editable при смене прав (напр. обновление /me).
  useEffect(() => {
    editor?.setEditable(canEdit);
  }, [editor, canEdit]);

  // Ресинк контента по расхождению версий (ADR-063 §B.2): пришедшая с сервера content_version
  // ≠ базовой ⇒ контент изменён ИЗВНЕ (рефетч после 409, правка другим пользователем) —
  // содержимое редактора заменяется серверным, базовая версия обновляется, флаг несохранённых
  // изменений сбрасывается. Второй аргумент setContent — emitUpdate=false: замена контента не
  // должна порождать событие правки, иначе документ немедленно помечается изменённым.
  // Команда setContent переопределена расширением tiptap-markdown (парсит markdown-строку).
  useEffect(() => {
    if (!editor) return;
    if (node.content_version === baseVersionRef.current) return;
    baseVersionRef.current = node.content_version;
    editor.commands.setContent(node.content_md ?? '', false);
    setDirty(false);
  }, [editor, node.content_version, node.content_md]);

  /**
   * Загрузка картинки и вставка узла изображения (ADR-068 §2–§4). Оба жеста — кнопка тулбара
   * и Ctrl+V — ведут сюда: единственный путь появления картинки в документе.
   *
   * Порядок: клиентская предпроверка (размер/тип — подсказка, граница на сервере) →
   * плейсхолдер на месте будущей картинки → `POST /nodes/{id}/attachments` → вставка ноды с
   * `src` из поля **`url` ответа сервера** (клиент URL не конструирует) и `alt` = `filename`.
   * Ссылка попадает в `content_md` только при сохранении документа — вставка помечает его
   * изменённым через штатный `onUpdate`.
   *
   * ⚠️ **Только `mutateAsync`, без per-call `onSuccess`/`onError`/`onSettled`.** Все загрузки
   * идут через ОДИН хук ⇒ один `MutationObserver`, а `mutate()` в `@tanstack/query-core`
   * 5.59.16 при каждом вызове перецепляет observer и перетирает его `mutateOptions`: колбэки
   * ПЕРВОГО из параллельных вызовов не вызвались бы никогда. Ctrl+V с несколькими картинками
   * даёт ровно такой параллелизм, и цена была бы не косметической — висящий плейсхолдер,
   * невставленная (осиротевшая на volume) картинка и навсегда занятый счётчик загрузок.
   * Промис `mutateAsync` приходит от самой мутации, поэтому от observer не зависит; весь
   * посткондишен живёт в `try/catch/finally` этого вызова.
   */
  const uploadImage = useCallback(
    async (file: File) => {
      if (!editor || editor.isDestroyed || !canEdit) return;
      const invalid = validateAttachmentFile(file);
      if (invalid) {
        toast.error(invalid);
        return;
      }

      const placeholderId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      addImageUploadPlaceholder(editor.view, placeholderId, editor.state.selection.from);
      setUploadsInFlight((n) => n + 1);

      // Плейсхолдер снимается ровно один раз: на успешном пути — до вставки картинки
      // (иначе оба элемента мелькнули бы рядом), иначе — в finally.
      let placeholderCleared = false;
      const clearPlaceholder = () => {
        if (placeholderCleared || editor.isDestroyed) return;
        placeholderCleared = true;
        removeImageUploadPlaceholder(editor.view, placeholderId);
      };

      try {
        const attachment = await uploadAttachmentMutation.mutateAsync({ nodeId: node.id, file });
        if (!editor.isDestroyed) {
          // Позиция берётся у плейсхолдера: пока файл летел, документ мог измениться.
          const pos = findImageUploadPlaceholder(editor.state, placeholderId);
          clearPlaceholder();
          const at = pos ?? editor.state.selection.from;
          editor
            .chain()
            .insertContentAt(at, {
              type: 'image',
              attrs: { src: attachment.url, alt: attachment.filename },
            })
            .focus()
            .run();
        }
        toast.success('Изображение загружено');
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 422) {
            // document_attachment_invalid: тип вне whitelist / размер (04-api.md).
            toast.error(
              err.message || 'Изображение не прошло проверку: PNG, JPEG, WebP или GIF до 5 МБ',
            );
          } else if (err.status === 403) {
            toast.error('Недостаточно прав для загрузки изображения');
          } else if (err.status === 404) {
            toast.error('Документ недоступен');
          } else {
            toast.error(err.message);
          }
        } else {
          toast.error('Не удалось загрузить изображение');
        }
      } finally {
        clearPlaceholder();
        setUploadsInFlight((n) => Math.max(0, n - 1));
      }
    },
    [editor, canEdit, node.id, uploadAttachmentMutation],
  );

  // Актуальная версия загрузчика для handlePaste (см. комментарий у `uploadImageRef`).
  useEffect(() => {
    uploadImageRef.current = uploadImage;
  }, [uploadImage]);

  const handlePickImage = useCallback(() => fileInputRef.current?.click(), []);

  const handleImageFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const picked = e.target.files?.[0];
      // Сброс значения — иначе повторный выбор ТОГО ЖЕ файла не даёт события change.
      e.target.value = '';
      // Ошибки обрабатываются внутри uploadImage (тосты) — промис намеренно не ожидается.
      if (picked) void uploadImage(picked);
    },
    [uploadImage],
  );

  const handleSave = useCallback(() => {
    if (!editor || !canEdit) return;
    const markdown = editor.storage.markdown.getMarkdown() as string;
    updateMutation.mutate(
      { id: node.id, payload: { content_md: markdown, expected_version: baseVersionRef.current } },
      {
        onSuccess: (data) => {
          // ADR-063 §B.3: собственное сохранение — не внешнее изменение. Базовая версия
          // обновляется значением из ответа PATCH ⇒ ресинк не срабатывает, курсор и позиция
          // скролла сохраняются (в т.ч. при Ctrl+S посреди длинного документа).
          baseVersionRef.current = data.content_version;
          // Правки, набранные ПОКА PATCH был в полёте, этим ответом не сохранены: сбрасывать
          // флаг изменений в этом случае нельзя — кнопка погасла бы, Ctrl+S стал бы no-op, и
          // пользователь ушёл бы со страницы, считая текст сохранённым (молчаливая потеря).
          const current = editor.storage.markdown.getMarkdown() as string;
          if (current === markdown) setDirty(false);
          toast.success('Документ сохранён');
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) {
            toast.error('Документ изменён другим пользователем — загружена актуальная версия.');
            // Инвалидация уместна только здесь: нужны ЧУЖИЕ данные (ADR-063 §A). Свежий контент
            // попадёт в редактор ресинком по расхождению версий, а не ремоунтом.
            void queryClient.invalidateQueries({ queryKey: [...documentNodeKey, node.id] });
            return;
          }
          if (err instanceof ApiError && err.status === 403) {
            toast.error('Недостаточно прав для сохранения');
            return;
          }
          if (err instanceof ApiError && (err.status === 422 || err.status === 400)) {
            const detail = err.details?.[0]?.message;
            toast.error(detail ?? 'Не удалось сохранить: проверьте содержимое документа');
            return;
          }
          toast.error(err instanceof ApiError ? err.message : 'Не удалось сохранить документ');
        },
      },
    );
  }, [editor, canEdit, node.id, updateMutation, queryClient]);

  /**
   * Фокус по клику в пустое место области документа (ADR-063 §C). Клик ниже последнего блока или
   * сбоку от колонки текста ставит каретку в ближайшую к точке клика позицию.
   *
   * onMouseDown, а не onClick: к моменту click браузер уже выставил selection и preventDefault
   * бесполезен. Клик ВНУТРИ редактируемого текста отдаётся ProseMirror без preventDefault —
   * обычный клик, drag-выделение и клик по ссылке не изменяются. В режиме просмотра (нет
   * documents:edit либо редактор не готов) фокус не ставится.
   *
   * Трейд-офф: выделение мышью, НАЧАТОЕ с полей вне колонки и протянутое в текст, не работает.
   * Выделение изнутри текста не затрагивается.
   */
  const handleSurfaceMouseDown = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!editor || !canEdit) return;
      if (editor.view.dom.contains(e.target as Node)) return;
      e.preventDefault();
      // Координаты зажимаются в прямоугольник области редактирования: клик правее колонки
      // ставит курсор в конец ТОЙ ЖЕ строки, а не в конец документа.
      const rect = editor.view.dom.getBoundingClientRect();
      const left = Math.min(Math.max(e.clientX, rect.left + 1), rect.right - 1);
      const top = Math.min(Math.max(e.clientY, rect.top + 1), rect.bottom - 1);
      const coords = editor.view.posAtCoords({ left, top });
      // Позиция не определяется → фолбэк «конец документа».
      if (coords) editor.chain().focus().setTextSelection(coords.pos).run();
      else editor.chain().focus('end').run();
    },
    [editor, canEdit],
  );

  // Ctrl/Cmd+S — сохранить (только при праве и наличии изменений).
  useEffect(() => {
    if (!canEdit) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        if (dirty) handleSave();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [canEdit, dirty, handleSave]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-3 py-2">
        {onBack && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onBack}
            className="md:hidden"
            aria-label="Назад"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          </Button>
        )}
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-text-primary">
          {node.name}
        </h2>
        {canEdit && (
          <Button
            size="sm"
            onClick={handleSave}
            loading={updateMutation.isPending}
            disabled={!dirty || updateMutation.isPending}
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            Сохранить
          </Button>
        )}
      </div>

      {canEdit && editor && (
        <Toolbar
          editor={editor}
          onInsertImage={handlePickImage}
          imageUploading={uploadsInFlight > 0}
        />
      )}

      {/* Скрытый file-picker кнопки «Изображение». accept — ровно whitelist контракта
          (png/jpeg/webp/gif; SVG исключён нормативно). Валидация — на сервере по magic bytes. */}
      {canEdit && (
        <input
          ref={fileInputRef}
          type="file"
          accept={ATTACHMENT_ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={handleImageFileChange}
        />
      )}

      {/* Обработчик только проксирует клик в пустоту на редактор — клавиатурного эквивалента не
          требует: вся клавиатурная работа идёт внутри contenteditable, доступного по Tab. */}
      <div
        className="scrollbar-none min-h-0 flex-1 overflow-y-auto px-4 py-4"
        onMouseDown={handleSurfaceMouseDown}
      >
        {editor ? (
          // h-full — иначе промежуточный wrapper EditorContent остаётся height:auto и min-h-full
          // на contenteditable резолвится относительно родителя неопределённой высоты, не давая
          // высоты вообще: клик ниже последнего блока не попадал бы в редактируемую область.
          // @tiptap/react пробрасывает className на этот wrapper (EditorContentProps extends
          // HTMLProps<HTMLDivElement>, render спредит ...rest на div).
          <EditorContent editor={editor} className="h-full" />
        ) : (
          <p className="text-[13px] text-text-tertiary">Загрузка редактора…</p>
        )}
      </div>

      {!canEdit && (
        <p className="shrink-0 border-t border-border-subtle px-4 py-2 text-[12px] text-text-tertiary">
          Режим просмотра — у вас нет права на редактирование документов.
        </p>
      )}
    </div>
  );
}
