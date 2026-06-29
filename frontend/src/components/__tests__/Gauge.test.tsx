import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Gauge } from '@/components/Gauge';

describe('Gauge', () => {
  it('clamps value to 0..100 and exposes meter aria attributes', () => {
    render(<Gauge value={125} label="CPU" />);

    const meter = screen.getByRole('meter', { name: 'Загрузка CPU 100 процентов' });
    expect(meter).toHaveAttribute('aria-valuenow', '100');
    expect(meter).toHaveAttribute('aria-valuemin', '0');
    expect(meter).toHaveAttribute('aria-valuemax', '100');
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
    expect(screen.queryByText('Usage')).not.toBeInTheDocument();
    expect(screen.queryByText('%')).not.toBeInTheDocument();
  });

  it('renders placeholder for unavailable metrics', () => {
    render(<Gauge value={null} label="RAM" />);

    const placeholder = screen.getByRole('img', { name: 'Загрузка RAM недоступна' });
    expect(placeholder).toBeInTheDocument();
    expect(placeholder).not.toHaveAttribute('aria-valuenow');
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('uses the same geometry for all zone boundaries', () => {
    const { rerender, container } = render(<Gauge value={79.9} label="SSD" />);
    const greenPath = container.querySelector('path[stroke^="url"]');

    rerender(<Gauge value={80} label="SSD" />);
    const yellowPath = container.querySelector('path[stroke^="url"]');
    rerender(<Gauge value={90.1} label="SSD" />);
    const redPath = container.querySelector('path[stroke^="url"]');

    expect(greenPath?.getAttribute('d')).toBe(yellowPath?.getAttribute('d'));
    expect(yellowPath?.getAttribute('d')).toBe(redPath?.getAttribute('d'));
  });
});
