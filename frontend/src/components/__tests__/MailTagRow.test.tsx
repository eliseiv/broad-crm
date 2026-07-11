import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MailTagRow } from '@/components/MailTagRow';
import { RULE_TYPE_OPTIONS, ruleLabel } from '@/features/mail/tags';
import type { MailTagFull } from '@/types/api';

/**
 * Строка тега (ADR-047 §1/§2):
 * - **признак «встроенный» упразднён** → подписи «встроенный» нет, кнопка удаления активна
 *   для ЛЮБОГО тега (409 на удаление снят на backend);
 * - **нормативный словарь лейблов типов правил** (08-design-system.md «Вкладка Теги»);
 *   `sender_exact` **убран из списка создания правила**, но существующие правила этого типа
 *   **отображаются** лейблом «Отправитель равен» (TD-055).
 */
const hooks = vi.hoisted(() => ({
  createRule: vi.fn(),
  deleteRule: vi.fn(),
  apply: vi.fn(),
  deleteTag: vi.fn(),
}));

vi.mock('@/features/mail/hooks', () => ({
  useCreateTagRule: () => ({ mutate: hooks.createRule, isPending: false }),
  useDeleteTagRule: () => ({ mutate: hooks.deleteRule, isPending: false }),
  useApplyTag: () => ({ mutate: hooks.apply, isPending: false }),
  useDeleteTag: () => ({ mutate: hooks.deleteTag, isPending: false }),
  useUpdateTag: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTag: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function makeTag(over: Partial<MailTagFull> = {}): MailTagFull {
  return {
    id: 'tag-1',
    name: 'Диспут',
    color: '#dc2626',
    match_mode: 'any',
    rules: [],
    created_at: '2026-07-01T09:00:00Z',
    updated_at: '2026-07-01T09:00:00Z',
    ...over,
  };
}

function renderRow(tag: MailTagFull, canManage = true) {
  return render(
    <table>
      <tbody>
        <MailTagRow tag={tag} canManage={canManage} />
      </tbody>
    </table>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('MailTagRow — удалить можно ЛЮБОЙ тег (ADR-047 §1)', () => {
  it('подписи «встроенный» нет ни у одного тега', () => {
    renderRow(makeTag({ name: 'DPLA.PLA' }));

    expect(screen.queryByText(/встроен/i)).not.toBeInTheDocument();
  });

  it('кнопка удаления активна для любого тега и подтверждение шлёт delete', async () => {
    const user = userEvent.setup();
    renderRow(makeTag({ name: 'DPLA.PLA' }));

    const deleteBtn = screen.getByRole('button', { name: 'Удалить тег DPLA.PLA' });
    expect(deleteBtn).toBeEnabled();

    await user.click(deleteBtn);
    const dialog = within(await screen.findByRole('dialog'));
    await user.click(dialog.getByRole('button', { name: 'Удалить' }));

    expect(hooks.deleteTag).toHaveBeenCalledTimes(1);
    expect(hooks.deleteTag.mock.calls[0][0]).toBe('tag-1');
  });

  it('без mail:tags кнопки управления не рендерятся', () => {
    renderRow(makeTag(), false);

    expect(screen.queryByRole('button', { name: /Удалить тег/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Изменить тег/ })).not.toBeInTheDocument();
  });
});

describe('MailTagRow — лейблы типов правил (ADR-047 §2, нормативный словарь)', () => {
  it('в списке создания правила НЕТ sender_exact, но есть три остальных типа', () => {
    renderRow(makeTag());

    const select = screen.getByLabelText('Тип правила');
    const options = within(select)
      .getAllByRole('option')
      .map((o) => o.textContent);

    expect(options).toEqual(['Тема письма', 'Текст письма', 'Отправитель']);
    expect(options).not.toContain('Отправитель равен');
    // Источник опций — нормативный словарь модуля (не локальная копия в компоненте).
    expect(RULE_TYPE_OPTIONS.map((o) => o.label)).toEqual(options);
    expect(RULE_TYPE_OPTIONS.map((o) => o.value)).not.toContain('sender_exact');
  });

  it('существующее правило sender_exact ОТОБРАЖАЕТСЯ лейблом «Отправитель равен» (TD-055)', () => {
    renderRow(
      makeTag({
        rules: [
          {
            id: 'r-1',
            type: 'sender_exact',
            pattern: 'AppStoreNotices@apple.com',
            created_at: '2026-07-01T09:00:00Z',
          },
        ],
      }),
    );

    // Формат строки правила: `<лейбл типа> «<pattern>»`.
    expect(screen.getByText('Отправитель равен «AppStoreNotices@apple.com»')).toBeInTheDocument();
  });

  it('строки правил остальных типов — по нормативному словарю', () => {
    renderRow(
      makeTag({
        rules: [
          { id: 'r-1', type: 'subject_contains', pattern: 'счёт', created_at: 'x' },
          { id: 'r-2', type: 'body_contains', pattern: 'cancel', created_at: 'x' },
          { id: 'r-3', type: 'sender_contains', pattern: 'apple', created_at: 'x' },
        ],
      }),
    );

    expect(screen.getByText('Тема письма «счёт»')).toBeInTheDocument();
    expect(screen.getByText('Текст письма «cancel»')).toBeInTheDocument();
    expect(screen.getByText('Отправитель «apple»')).toBeInTheDocument();
  });

  it('ruleLabel — единый билдер строки правила (формат `<лейбл> «<pattern>»`)', () => {
    expect(ruleLabel({ id: 'r', type: 'subject_contains', pattern: 'счёт', created_at: 'x' })).toBe(
      'Тема письма «счёт»',
    );
    expect(ruleLabel({ id: 'r', type: 'sender_exact', pattern: 'a@b', created_at: 'x' })).toBe(
      'Отправитель равен «a@b»',
    );
  });
});
