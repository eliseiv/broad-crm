import { describe, expect, it } from 'vitest';
import { usageToZone } from '@/lib/zones';

describe('usageToZone', () => {
  it('maps documented zone boundaries', () => {
    expect(usageToZone(79.9)).toBe('green');
    expect(usageToZone(80)).toBe('yellow');
    expect(usageToZone(90)).toBe('yellow');
    expect(usageToZone(90.1)).toBe('red');
  });
});
