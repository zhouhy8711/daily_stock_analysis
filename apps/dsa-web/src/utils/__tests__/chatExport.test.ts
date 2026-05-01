import { afterEach, describe, expect, it, vi } from 'vitest';
import { formatSessionAsMarkdown } from '../chatExport';

describe('formatSessionAsMarkdown', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('exports generation time in 24-hour format', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-30T21:05:00+08:00'));

    const markdown = formatSessionAsMarkdown([]);

    expect(markdown).toContain('21:05');
    expect(markdown).not.toMatch(/上午|下午|AM|PM/i);
  });
});
