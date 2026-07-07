import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';

// Нормативные строки — ЕДИНЫЙ источник в 08-design-system.md «Заглушки „Недостаточно
// прав“» / ADR-021 §6. Заголовок общий; подсказка зависит от scope. Строки сверены с
// таблицей-источником docs (побуквенно), поэтому здесь литералы, а не импорт констант.
const TITLE = 'Недостаточно прав';
const PAGE_HINT =
  'У вашей учётной записи нет доступа к этому разделу. Обратитесь к администратору.';
const GLOBAL_HINT =
  'У вашей учётной записи нет доступа ни к одному разделу. Обратитесь к администратору.';

describe('InsufficientPermissions (ADR-021 §6, 08-design-system.md «Заглушки»)', () => {
  it('defaults to page scope: common title + page-scoped hint', () => {
    render(<InsufficientPermissions />);

    expect(screen.getByText(TITLE)).toBeInTheDocument();
    expect(screen.getByText(PAGE_HINT)).toBeInTheDocument();
    // Global-подсказка НЕ показывается для page-scope.
    expect(screen.queryByText(GLOBAL_HINT)).not.toBeInTheDocument();
  });

  it('scope="page": common title + page-scoped hint («нет доступа к этому разделу»)', () => {
    render(<InsufficientPermissions scope="page" />);

    expect(screen.getByText(TITLE)).toBeInTheDocument();
    expect(screen.getByText(PAGE_HINT)).toBeInTheDocument();
    expect(screen.queryByText(GLOBAL_HINT)).not.toBeInTheDocument();
  });

  it('scope="global": common title + global hint («нет доступа ни к одному разделу»)', () => {
    render(<InsufficientPermissions scope="global" />);

    expect(screen.getByText(TITLE)).toBeInTheDocument();
    expect(screen.getByText(GLOBAL_HINT)).toBeInTheDocument();
    // Page-подсказка НЕ показывается для global-scope.
    expect(screen.queryByText(PAGE_HINT)).not.toBeInTheDocument();
  });

  it('title is identical across page and global scope (only the hint differs)', () => {
    const { unmount } = render(<InsufficientPermissions scope="page" />);
    expect(screen.getByText(TITLE)).toBeInTheDocument();
    unmount();

    render(<InsufficientPermissions scope="global" />);
    expect(screen.getByText(TITLE)).toBeInTheDocument();
  });
});
