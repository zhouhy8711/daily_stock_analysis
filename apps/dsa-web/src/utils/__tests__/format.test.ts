import { describe, expect, it } from 'vitest';
import { formatDateTime, getOneYearAgoInShanghai } from '../format';

describe('formatDateTime', () => {
  it('uses 24-hour time for report and market review timestamps', () => {
    const formatted = formatDateTime('2026-04-30T21:05:00+08:00');

    expect(formatted).toContain('21:05');
    expect(formatted).not.toMatch(/上午|下午|AM|PM/i);
  });
});

describe('getOneYearAgoInShanghai', () => {
  it('keeps the same month and day by defaulting to one calendar year ago', () => {
    expect(getOneYearAgoInShanghai(new Date('2026-05-03T12:00:00+08:00'))).toBe('2025-05-03');
  });

  it('clamps leap day to February 28 in non-leap previous years', () => {
    expect(getOneYearAgoInShanghai(new Date('2024-02-29T12:00:00+08:00'))).toBe('2023-02-28');
  });
});
