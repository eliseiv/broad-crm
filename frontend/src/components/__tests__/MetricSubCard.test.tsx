import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MetricSubCard } from '@/components/MetricSubCard';
import type { Metric } from '@/types/api';

function metric(value: number | null, total: number | null, unit: string): Metric {
  return {
    usage_percent: 65,
    zone: 'green',
    detail: { value, total, unit },
  };
}

describe('MetricSubCard', () => {
  it('renders CPU cores with Russian pluralization', () => {
    const { rerender } = render(<MetricSubCard kind="cpu" metric={metric(null, 1, 'cores')} />);

    expect(screen.getByText('1 ядро')).toBeInTheDocument();
    rerender(<MetricSubCard kind="cpu" metric={metric(null, 2, 'cores')} />);
    expect(screen.getByText('2 ядра')).toBeInTheDocument();
    rerender(<MetricSubCard kind="cpu" metric={metric(null, 5, 'cores')} />);
    expect(screen.getByText('5 ядер')).toBeInTheDocument();
    rerender(<MetricSubCard kind="cpu" metric={metric(null, 8, 'cores')} />);
    expect(screen.getByText('8 ядер')).toBeInTheDocument();
  });

  it('renders RAM and SSD details in gigabytes', () => {
    const { rerender } = render(<MetricSubCard kind="ram" metric={metric(11.5, 16, 'GB')} />);

    expect(screen.getByText('11.5/16 ГБ')).toBeInTheDocument();
    rerender(<MetricSubCard kind="ssd" metric={metric(238, 500, 'GB')} />);
    expect(screen.getByText('238/500 ГБ')).toBeInTheDocument();
  });

  it('hides absolute details when both value and total are null', () => {
    render(<MetricSubCard kind="ram" metric={metric(null, null, 'GB')} />);

    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
