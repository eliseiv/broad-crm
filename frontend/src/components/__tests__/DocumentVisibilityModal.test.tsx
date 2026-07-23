import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentVisibilityModal } from '@/components/DocumentVisibilityModal';
import { useNodeVisibility, useRoleRefs, useSetVisibility } from '@/features/documents/hooks';
import type { DocumentNode } from '@/types/api';

vi.mock('@/features/documents/hooks', () => ({
  useNodeVisibility: vi.fn(),
  useRoleRefs: vi.fn(),
  useSetVisibility: vi.fn(),
}));

const mockNodeVisibility = vi.mocked(useNodeVisibility);
const mockRoleRefs = vi.mocked(useRoleRefs);
const mockSetVisibility = vi.mocked(useSetVisibility);

const NODE: DocumentNode = {
  id: 'n1',
  node_type: 'folder',
  parent_id: null,
  name: 'Гайды',
  content_md: null,
  owner_id: 'o',
  visibility_mode: 'restricted',
  content_version: 1,
  position: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const ROLE_REFS = [
  { id: 'r1', name: 'Оператор' },
  { id: 'r2', name: 'Менеджер' },
];

function setHooks(visibility: {
  visibility_mode: 'inherit' | 'restricted';
  role_ids: string[];
  rag_exclude?: boolean;
}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  mockNodeVisibility.mockReturnValue({ data: visibility, isLoading: false, error: null } as any);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  mockRoleRefs.mockReturnValue({ data: ROLE_REFS, isLoading: false, error: null } as any);
  const mutate = vi.fn();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  mockSetVisibility.mockReturnValue({ mutate, isPending: false } as any);
  return { mutate };
}

describe('DocumentVisibilityModal (предзаполнение + inherit↔restricted, ADR-059)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('предзаполняет restricted + собственные роли из GET visibility; опции из role-refs', () => {
    setHooks({ visibility_mode: 'restricted', role_ids: ['r1'] });
    render(<DocumentVisibilityModal open onOpenChange={vi.fn()} node={NODE} />);

    // Режим restricted выбран.
    const radios = screen.getAllByRole('radio');
    const restricted = radios.find((r) => (r as HTMLInputElement).value === 'restricted')!;
    expect(restricted).toBeChecked();
    // MultiSelect с ролями из role-refs; предвыбрана r1.
    expect(screen.getByRole('checkbox', { name: 'Оператор' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Менеджер' })).not.toBeChecked();
  });

  it('inherit прячет выбор ролей; переключение на restricted показывает MultiSelect', async () => {
    const user = userEvent.setup();
    setHooks({ visibility_mode: 'inherit', role_ids: [] });
    render(<DocumentVisibilityModal open onOpenChange={vi.fn()} node={NODE} />);

    // В режиме inherit чекбоксов ролей нет.
    expect(screen.queryByRole('checkbox', { name: 'Оператор' })).not.toBeInTheDocument();

    // Переключаем на restricted → появляется MultiSelect.
    const radios = screen.getAllByRole('radio');
    const restricted = radios.find((r) => (r as HTMLInputElement).value === 'restricted')!;
    await user.click(restricted);
    expect(screen.getByRole('checkbox', { name: 'Оператор' })).toBeInTheDocument();

    // Обратно на inherit → MultiSelect скрывается.
    const inherit = radios.find((r) => (r as HTMLInputElement).value === 'inherit')!;
    await user.click(inherit);
    expect(screen.queryByRole('checkbox', { name: 'Оператор' })).not.toBeInTheDocument();
  });

  it('Сохранить шлёт mutate с режимом и набором ролей', async () => {
    const user = userEvent.setup();
    const { mutate } = setHooks({ visibility_mode: 'restricted', role_ids: ['r1'] });
    render(<DocumentVisibilityModal open onOpenChange={vi.fn()} node={NODE} />);

    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(mutate).toHaveBeenCalledTimes(1);
    const [args] = mutate.mock.calls[0];
    expect(args).toEqual({
      id: 'n1',
      payload: { visibility_mode: 'restricted', role_ids: ['r1'], rag_exclude: false },
    });
  });

  it('чекбокс «Не включать в RAG» предзаполняется и уходит в payload', async () => {
    const user = userEvent.setup();
    const { mutate } = setHooks({ visibility_mode: 'inherit', role_ids: [], rag_exclude: true });
    render(<DocumentVisibilityModal open onOpenChange={vi.fn()} node={NODE} />);

    const checkbox = (await screen.findByLabelText('Не включать в RAG')) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);

    await user.click(checkbox);
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(mutate).toHaveBeenCalledTimes(1);
    const [args] = mutate.mock.calls[0];
    expect(args).toEqual({
      id: 'n1',
      payload: { visibility_mode: 'inherit', role_ids: [], rag_exclude: false },
    });
  });
});
