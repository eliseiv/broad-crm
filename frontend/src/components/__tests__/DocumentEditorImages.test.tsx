import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from 'tiptap-markdown';
import type { PropsWithChildren } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentEditor } from '@/components/DocumentEditor';
import { DocumentImage } from '@/features/documents/imageExtension';
import { ApiError } from '@/lib/api';
import type { DocumentNode } from '@/types/api';

/**
 * Изображения в `DocumentEditor` (ADR-068 §3/§4, 08-design-system.md §Изображения).
 *
 * Обязательные кейсы 06-testing-strategy.md:
 * - **две картинки одним `Ctrl+V`** — обе вставлены, ОБА плейсхолдера сняты, кнопка не
 *   «залипает» (счётчик загрузок вернулся к нулю). Это регресс-гейт нормы «только
 *   `mutateAsync`, без per-call колбэков»: на `mutate()` колбэки первого из параллельных
 *   вызовов не сработали бы никогда — висящий плейсхолдер и вечный спиннер;
 * - **`data:`-URI отсутствует** в сериализованном `content_md` (гейт `allowBase64:false`):
 *   иначе base64-мусор съедал бы `DOCUMENTS_MAX_MD_BYTES` в обход хранилища вложений;
 * - **drag-and-drop не грузит** (`handleDrop` намеренно не переопределён);
 * - **`revokeObjectURL` на размонтировании** — иначе blob'ы копятся на всю сессию вкладки;
 * - **ошибка загрузки** → плейсхолдер снят, счётчик декрементирован.
 */

// --- Моки границ -----------------------------------------------------------------------

const uploadAttachment = vi.hoisted(() => vi.fn());
const updateNode = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const fetchAttachmentBlob = vi.hoisted(() => vi.fn());

vi.mock('@/features/documents/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/documents/hooks')>();
  return {
    ...actual,
    useUpdateNode: () => updateNode,
    // Один хук на все загрузки — ровно как в проде (общий MutationObserver).
    useUploadAttachment: () => ({
      mutateAsync: uploadAttachment,
      isPending: false,
    }),
  };
});

vi.mock('@/features/documents/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/documents/api')>();
  return { ...actual, fetchAttachmentBlob };
});

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// --- Фикстуры --------------------------------------------------------------------------

const DOC: DocumentNode = {
  id: 'doc-1',
  node_type: 'document',
  parent_id: null,
  name: 'Регламент',
  content_md: 'Текст документа',
  owner_id: 'owner',
  visibility_mode: 'inherit',
  content_version: 3,
  position: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function wrapper({ children }: PropsWithChildren) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function pngFile(name: string): File {
  return new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], name, { type: 'image/png' });
}

function attachment(id: string, filename: string) {
  return {
    id,
    document_node_id: DOC.id,
    filename,
    mime: 'image/png',
    size_bytes: 4,
    checksum: 'c'.repeat(64),
    url: `/api/documents/attachments/${id}`,
    created_at: '2026-01-01T00:00:00Z',
  };
}

/**
 * Фейк `DataTransfer`: jsdom его не реализует (проверено — `typeof DataTransfer ===
 * 'undefined'`). Несёт ровно то, что читают `clipboardImageFiles` и prosemirror-view
 * (`items`/`files`/`getData`/`types`).
 */
function fakeDataTransfer(files: File[]): DataTransfer {
  return {
    items: files.map((file) => ({
      kind: 'file' as const,
      type: file.type,
      getAsFile: () => file,
    })),
    files,
    types: ['Files'],
    getData: () => '',
  } as unknown as DataTransfer;
}

function dispatchPaste(target: Element, files: File[]): void {
  const event = new Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'clipboardData', { value: fakeDataTransfer(files) });
  target.dispatchEvent(event);
}

function dispatchDrop(target: Element, files: File[]): void {
  const event = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: fakeDataTransfer(files) });
  target.dispatchEvent(event);
}

const editorSurface = () => document.querySelector('.ProseMirror') as HTMLElement;
const imageButton = () => screen.getByRole('button', { name: 'Изображение' });

/** Сериализованный `content_md`, каким он уйдёт в PATCH (кнопка «Сохранить»). */
function savedMarkdown(): string {
  expect(updateNode.mutate).toHaveBeenCalled();
  const call = updateNode.mutate.mock.calls.at(-1)!;
  return call[0].payload.content_md as string;
}

let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  // Дефолт отрисовки: вставленная картинка сразу же запрашивается NodeView'ом. Без
  // реализации мок вернул бы `undefined`, и `.then()` уронил бы дерево компонентов —
  // тесты вставки провалились бы по причине, к вставке отношения не имеющей.
  fetchAttachmentBlob.mockResolvedValue(new Blob([new Uint8Array([1, 2, 3])]));
  // jsdom не реализует Object-URL API — ставим наблюдаемые заглушки.
  createObjectURL = vi.fn((_blob: Blob) => `blob:mock/${Math.random().toString(36).slice(2)}`);
  revokeObjectURL = vi.fn();
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true });
  Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true });
});

afterEach(() => vi.restoreAllMocks());

// --- Ctrl+V: единственный путь загрузки -------------------------------------------------

describe('DocumentEditor — вставка изображения через Ctrl+V (ADR-068)', () => {
  it('одна картинка: загружается и вставляется с `src` из поля `url` ОТВЕТА сервера', async () => {
    uploadAttachment.mockResolvedValue(attachment('att-1', 'pic.png'));
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    dispatchPaste(editorSurface(), [pngFile('pic.png')]);

    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(1));
    expect(uploadAttachment).toHaveBeenCalledWith({
      nodeId: DOC.id,
      file: expect.any(File),
    });

    await waitFor(() => expect(document.querySelector('.doc-image')).toBeTruthy());

    // Клиент URL НЕ конструирует — он берёт его из поля `url` ответа.
    const save = await screen.findByRole('button', { name: /Сохранить/ });
    save.click();
    await waitFor(() => expect(savedMarkdown()).toContain('/api/documents/attachments/att-1'));
  });

  it('ОБЯЗАТЕЛЬНЫЙ: две картинки одним Ctrl+V — обе загружены и обе вставлены', async () => {
    uploadAttachment
      .mockResolvedValueOnce(attachment('att-1', 'first.png'))
      .mockResolvedValueOnce(attachment('att-2', 'second.png'));
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    dispatchPaste(editorSurface(), [pngFile('first.png'), pngFile('second.png')]);

    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(2));

    await waitFor(() => expect(document.querySelectorAll('.doc-image')).toHaveLength(2));

    const save = await screen.findByRole('button', { name: /Сохранить/ });
    save.click();
    const md = savedMarkdown();
    expect(md).toContain('/api/documents/attachments/att-1');
    expect(md).toContain('/api/documents/attachments/att-2');
  });

  it('ОБЯЗАТЕЛЬНЫЙ: после двух параллельных загрузок оба плейсхолдера сняты', async () => {
    uploadAttachment
      .mockResolvedValueOnce(attachment('att-1', 'first.png'))
      .mockResolvedValueOnce(attachment('att-2', 'second.png'));
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    dispatchPaste(editorSurface(), [pngFile('first.png'), pngFile('second.png')]);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(2));

    // Плейсхолдер загрузки — декорация с текстом «Загрузка изображения…».
    await waitFor(() => {
      const stuck = Array.from(document.querySelectorAll('.doc-image-status')).filter(
        (el) => el.getAttribute('role') === 'status' && !el.closest('.doc-image'),
      );
      expect(stuck).toHaveLength(0);
    });
  });

  it('ОБЯЗАТЕЛЬНЫЙ: кнопка тулбара не «залипает» — aria-busy снят после обеих загрузок', async () => {
    uploadAttachment
      .mockResolvedValueOnce(attachment('att-1', 'first.png'))
      .mockResolvedValueOnce(attachment('att-2', 'second.png'));
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    dispatchPaste(editorSurface(), [pngFile('first.png'), pngFile('second.png')]);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(2));

    await waitFor(() => expect(imageButton()).toHaveAttribute('aria-busy', 'false'));
  });

  it('невалидный файл отвергается клиентом и на сервер не уходит', async () => {
    const { toast } = await import('sonner');
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    const svg = new File(['<svg/>'], 'evil.svg', { type: 'image/svg+xml' });
    dispatchPaste(editorSurface(), [svg]);

    // SVG вне whitelist — вставка вообще не перехватывается как картинка.
    await waitFor(() => expect(uploadAttachment).not.toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('read-only документ (canEdit=false): Ctrl+V картинкой ничего не грузит', async () => {
    render(<DocumentEditor node={DOC} canEdit={false} />, { wrapper });
    await waitFor(() => expect(screen.getByText(/Режим просмотра/)).toBeInTheDocument());

    const surface = editorSurface();
    if (surface) dispatchPaste(surface, [pngFile('pic.png')]);

    await waitFor(() => expect(uploadAttachment).not.toHaveBeenCalled());
  });
});

// --- Ошибки загрузки ---------------------------------------------------------------------

describe('DocumentEditor — ошибка загрузки изображения', () => {
  it('ОБЯЗАТЕЛЬНЫЙ: 422 → плейсхолдер снят и счётчик декрементирован (кнопка свободна)', async () => {
    uploadAttachment.mockRejectedValue(
      new ApiError(422, 'document_attachment_invalid', 'Файл не прошёл проверку'),
    );
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    dispatchPaste(editorSurface(), [pngFile('pic.png')]);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(1));

    await waitFor(() => {
      // Плейсхолдера нет…
      expect(document.querySelectorAll('.doc-image-status[role="status"]')).toHaveLength(0);
      // …и счётчик вернулся к нулю (иначе спиннер горел бы вечно).
      expect(imageButton()).toHaveAttribute('aria-busy', 'false');
    });
  });

  it('после ошибки следующая загрузка проходит штатно (счётчик не «протёк»)', async () => {
    uploadAttachment
      .mockRejectedValueOnce(new ApiError(500, 'internal_error', 'Ошибка'))
      .mockResolvedValueOnce(attachment('att-9', 'ok.png'));
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    dispatchPaste(editorSurface(), [pngFile('bad.png')]);
    await waitFor(() => expect(imageButton()).toHaveAttribute('aria-busy', 'false'));

    dispatchPaste(editorSurface(), [pngFile('ok.png')]);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(imageButton()).toHaveAttribute('aria-busy', 'false'));
  });

  it('каждая из двух параллельных загрузок обрабатывается независимо (одна упала, вторая вставлена)', async () => {
    uploadAttachment
      .mockRejectedValueOnce(new ApiError(422, 'document_attachment_invalid', 'Плохой файл'))
      .mockResolvedValueOnce(attachment('att-ok', 'good.png'));
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    dispatchPaste(editorSurface(), [pngFile('bad.png'), pngFile('good.png')]);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(2));

    await waitFor(() => expect(document.querySelector('.doc-image')).toBeTruthy());

    const save = await screen.findByRole('button', { name: /Сохранить/ });
    save.click();
    expect(savedMarkdown()).toContain('/api/documents/attachments/att-ok');
    await waitFor(() => expect(imageButton()).toHaveAttribute('aria-busy', 'false'));
  });
});

// --- Drag-and-drop не поддерживается ------------------------------------------------------

describe('DocumentEditor — drag-and-drop файлов (ADR-068: не поддерживается)', () => {
  it('ОБЯЗАТЕЛЬНЫЙ: drop файла-картинки НЕ запускает загрузку', async () => {
    render(<DocumentEditor node={DOC} canEdit={true} />, { wrapper });
    await screen.findByRole('toolbar', { name: 'Форматирование' });

    dispatchDrop(editorSurface(), [pngFile('dropped.png')]);

    await waitFor(() => expect(uploadAttachment).not.toHaveBeenCalled());
  });
});

// --- Гейт allowBase64: false ---------------------------------------------------------------

describe('DocumentImage — `data:`-URI не попадает в content_md (allowBase64:false)', () => {
  function roundTrip(markdown: string): string {
    const editor = new Editor({
      extensions: [
        StarterKit,
        DocumentImage,
        Markdown.configure({ html: false, transformPastedText: true, transformCopiedText: true }),
      ],
      content: markdown,
    });
    const out = editor.storage.markdown.getMarkdown() as string;
    editor.destroy();
    return out;
  }

  it('ОБЯЗАТЕЛЬНЫЙ: base64-картинка не сериализуется как `data:` в markdown', () => {
    const base64 =
      'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
    const out = roundTrip(`![скрин](${base64})`);

    expect(out).not.toContain('data:');
    expect(out).not.toContain('base64');
  });

  it('ссылка на вложение сохраняется при round-trip и остаётся отдельным блоком', () => {
    const url = '/api/documents/attachments/11111111-2222-3333-4444-555555555555';
    const out = roundTrip(`Текст\n\n![подпись](${url})\n\nСледующий абзац`);

    expect(out).toContain(`![подпись](${url})`);
    // Блочный узел закрывает блок — следующий абзац не приклеивается к ссылке.
    expect(out).not.toMatch(/\)Следующий абзац/);
  });

  it('внешний https-адрес картинки не ломается (его грузит сам браузер)', () => {
    const out = roundTrip('![внешняя](https://cdn.example.com/pic.png)');
    expect(out).toContain('https://cdn.example.com/pic.png');
  });
});

// --- Отрисовка: авторизованный fetch + blob, обязательный revokeObjectURL --------------------

describe('DocumentImageNodeView — blob-жизненный цикл (ADR-068 §3)', () => {
  const IMG_URL = '/api/documents/attachments/att-render';
  const docWithImage: DocumentNode = { ...DOC, content_md: `![подпись](${IMG_URL})` };

  it('картинка забирается авторизованным fetch и подставляется как blob:-URL', async () => {
    fetchAttachmentBlob.mockResolvedValue(new Blob([new Uint8Array([1, 2, 3])]));
    render(<DocumentEditor node={docWithImage} canEdit={true} />, { wrapper });

    await waitFor(() => expect(fetchAttachmentBlob).toHaveBeenCalledWith('att-render', expect.anything()));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));

    const img = await waitFor(() => {
      const el = document.querySelector('img.doc-image-img') as HTMLImageElement | null;
      expect(el).toBeTruthy();
      return el!;
    });
    expect(img.getAttribute('src')).toMatch(/^blob:mock\//);
    expect(img.getAttribute('alt')).toBe('подпись');
  });

  it('ОБЯЗАТЕЛЬНЫЙ: на размонтировании вызывается revokeObjectURL с тем же адресом', async () => {
    fetchAttachmentBlob.mockResolvedValue(new Blob([new Uint8Array([1, 2, 3])]));
    const view = render(<DocumentEditor node={docWithImage} canEdit={true} />, { wrapper });

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    const created = createObjectURL.mock.results[0].value as string;
    expect(revokeObjectURL).not.toHaveBeenCalled();

    view.unmount();

    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith(created));
  });

  it('404 вложения → плашка «Изображение недоступно» + alt, редактор не падает', async () => {
    fetchAttachmentBlob.mockRejectedValue(
      new ApiError(404, 'document_attachment_not_found', 'Нет вложения'),
    );
    render(<DocumentEditor node={docWithImage} canEdit={true} />, { wrapper });

    expect(await screen.findByText('Изображение недоступно')).toBeInTheDocument();
    expect(screen.getByText('подпись')).toBeInTheDocument();
    // Редактор остался работоспособным.
    expect(screen.getByRole('toolbar', { name: 'Форматирование' })).toBeInTheDocument();
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
