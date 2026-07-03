import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MailTags } from '@/components/MailTags';
import type { MailTag } from '@/types/api';

function tag(id: number, name: string, color: string): MailTag {
  return { id, name, color };
}

describe('MailTags', () => {
  it('renders nothing when there are no tags', () => {
    const { container } = render(<MailTags tags={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a "+N" overflow badge when tags exceed the max limit', () => {
    const tags = [
      tag(1, 'a', '#EF4444'),
      tag(2, 'b', '#EF4444'),
      tag(3, 'c', '#EF4444'),
      tag(4, 'd', '#EF4444'),
      tag(5, 'e', '#EF4444'),
    ];
    render(<MailTags tags={tags} max={3} />);

    // Видимы первые 3 пилюли, остаток свёрнут в «+2».
    expect(screen.getByText('a')).toBeInTheDocument();
    expect(screen.getByText('c')).toBeInTheDocument();
    expect(screen.queryByText('d')).not.toBeInTheDocument();
    expect(screen.getByText('+2')).toBeInTheDocument();
  });

  it('renders every tag without a "+N" badge when max is not set', () => {
    render(<MailTags tags={[tag(1, 'важное', '#EF4444'), tag(2, 'счёт', '#22C55E')]} />);

    expect(screen.getByText('важное')).toBeInTheDocument();
    expect(screen.getByText('счёт')).toBeInTheDocument();
    expect(screen.queryByText(/^\+\d/)).not.toBeInTheDocument();
  });

  it('applies the pill color from tag.color', () => {
    render(<MailTags tags={[tag(1, 'важное', '#123456')]} />);

    expect(screen.getByText('важное')).toHaveStyle({ color: '#123456' });
  });
});
