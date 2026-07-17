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
  Italic,
  Link2,
  List,
  ListOrdered,
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
import { documentNodeKey, useUpdateNode } from '@/features/documents/hooks';
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

function Toolbar({ editor }: { editor: Editor }) {
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
    </div>
  );
}

/**
 * WYSIWYG-редактор документа на TipTap (ADR-062, 08-design-system.md «Компонент
 * DocumentEditor»). Хранение — markdown в `content_md`: при открытии `content_md` парсится
 * в ProseMirror (tiptap-markdown), при сохранении сериализуется обратно в markdown и уходит
 * в PATCH /nodes/{id}. Компонент keyed родителем по `id:content_version` — на новую версию
 * (в т.ч. после собственного сохранения или 409-refetch) перемонтируется со свежим контентом.
 *
 * Optimistic-lock (TD-064, опц.): PATCH шлётся с `expected_version = node.content_version`;
 * конфликт → 409 document_node_conflict → тост «документ изменён» + рефетч свежей версии.
 *
 * Тулбар покрывает возможности StarterKit (заголовки/жирный/курсив/зачёркнутый/код/списки/
 * цитата/блок кода) плюс «ссылка» — через @tiptap/extension-link (ADR-062 §2, поправка
 * 2026-07-18: граница зависимости расширена этим официальным расширением TipTap). Markdown-
 * ссылки `[text](url)` открываются кликабельными и сохраняются при round-trip (URL не теряется).
 */
export function DocumentEditor({ node, canEdit, onBack }: DocumentEditorProps) {
  const queryClient = useQueryClient();
  const updateMutation = useUpdateNode();
  const [dirty, setDirty] = useState(false);
  // Базовая версия для optimistic-lock. Инициализируется версией узла; после успешного
  // сохранения родитель перемонтирует компонент (ключ по content_version), поэтому ref
  // достаточно как снимок на текущий маунт.
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
      Markdown.configure({ html: false, transformPastedText: true, transformCopiedText: true }),
    ],
    content: node.content_md ?? '',
    editorProps: {
      attributes: {
        class: 'doc-prose min-h-full max-w-3xl focus:outline-none',
        'aria-label': `Содержимое документа «${node.name}»`,
      },
    },
    onUpdate: () => setDirty(true),
  });

  // Синхронизация editable при смене прав (напр. обновление /me).
  useEffect(() => {
    editor?.setEditable(canEdit);
  }, [editor, canEdit]);

  const handleSave = useCallback(() => {
    if (!editor || !canEdit) return;
    const markdown = editor.storage.markdown.getMarkdown() as string;
    updateMutation.mutate(
      { id: node.id, payload: { content_md: markdown, expected_version: baseVersionRef.current } },
      {
        onSuccess: () => {
          setDirty(false);
          toast.success('Документ сохранён');
          // content_version инкрементнулся на сервере; инвалидация узла (в хуке) обновит
          // кэш, родитель перемонтирует редактор со свежей версией.
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) {
            toast.error('Документ изменён другим пользователем — загружена актуальная версия.');
            // Рефетч свежего узла: новая content_version → родитель перемонтирует редактор
            // со свежим контентом (ключ по id:content_version).
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

      {canEdit && editor && <Toolbar editor={editor} />}

      <div className="scrollbar-none min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {editor ? (
          <EditorContent editor={editor} />
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
