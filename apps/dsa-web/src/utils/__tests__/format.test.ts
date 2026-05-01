import { describe, expect, it } from 'vitest';
import { formatDateTime } from '../format';

describe('formatDateTime', () => {
  it('uses 24-hour time for report and market review timestamps', () => {
    const formatted = formatDateTime('2026-04-30T21:05:00+08:00');

    expect(formatted).toContain('21:05');
    expect(formatted).not.toMatch(/上午|下午|AM|PM/i);
  });
});
