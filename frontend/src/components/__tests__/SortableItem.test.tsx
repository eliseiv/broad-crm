import { DndContext } from '@dnd-kit/core';
import { SortableContext } from '@dnd-kit/sortable';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SortableItem } from '@/components/SortableItem';

/**
 * Полноценный drag в jsdom не воспроизводится (нет реального pointer/layout) — см.
 * ограничение в summary. Здесь проверяется базовый рендер SortableItem внутри
 * DndContext/SortableContext без падений (@dnd-kit hooks корректно инициализируются).
 */
describe('SortableItem', () => {
  it('renders children inside DndContext/SortableContext without crashing', () => {
    render(
      <DndContext>
        <SortableContext items={['a', 'b']}>
          <SortableItem id="a">
            <div>Card A</div>
          </SortableItem>
          <SortableItem id="b">
            <div>Card B</div>
          </SortableItem>
        </SortableContext>
      </DndContext>,
    );

    expect(screen.getByText('Card A')).toBeInTheDocument();
    expect(screen.getByText('Card B')).toBeInTheDocument();
  });
});
