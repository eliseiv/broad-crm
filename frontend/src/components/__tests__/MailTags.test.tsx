import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MailTags } from '@/components/MailTags';
import type { MailTag } from '@/types/api';

function tag(id: string, name: string, color: string): MailTag {
  return { id, name, color };
}

describe('MailTags', () => {
  it('renders nothing when there are no tags', () => {
    const { container } = render(<MailTags tags={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a "+N" overflow badge when tags exceed the max limit', () => {
    const tags = [
      tag('t1', 'a', '#EF4444'),
      tag('t2', 'b', '#EF4444'),
      tag('t3', 'c', '#EF4444'),
      tag('t4', 'd', '#EF4444'),
      tag('t5', 'e', '#EF4444'),
    ];
    render(<MailTags tags={tags} max={3} />);

    // Видимы первые 3 пилюли, остаток свёрнут в «+2».
    expect(screen.getByText('a')).toBeInTheDocument();
    expect(screen.getByText('c')).toBeInTheDocument();
    expect(screen.queryByText('d')).not.toBeInTheDocument();
    expect(screen.getByText('+2')).toBeInTheDocument();
  });

  it('renders every tag without a "+N" badge when max is not set', () => {
    render(<MailTags tags={[tag('t1', 'важное', '#EF4444'), tag('t2', 'счёт', '#22C55E')]} />);

    expect(screen.getByText('важное')).toBeInTheDocument();
    expect(screen.getByText('счёт')).toBeInTheDocument();
    expect(screen.queryByText(/^\+\d/)).not.toBeInTheDocument();
  });

  it('renders each tag as a MailTagChip (единый элемент, не сырой HEX как текст)', () => {
    render(<MailTags tags={[tag('t1', 'важное', '#123456')]} />);

    // Текст тега в чипе; цвет — тема-зависимый color-mix, НЕ плоский tag.color (WCAG AA).
    const chip = screen.getByText('важное').closest('span[style]') as HTMLElement;
    expect(chip).not.toBeNull();
    expect(chip.style.color).not.toBe('#123456');
    expect(chip.getAttribute('style') ?? '').toContain('color-mix');
    // В ленте/списке чип без точки-свотча (dot только на вкладке «Теги»).
    expect(chip.querySelector('[aria-hidden="true"]')).toBeNull();
  });
});
