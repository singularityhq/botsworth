import { describe, expect, it } from 'vitest'
import { fmt, modelFamily, modelShort } from './format'

/** Epoch ms of an Asia/Kolkata wall-clock time (UTC+5:30, no DST). */
const ist = (y: number, mo: number, d: number, h: number, mi: number) => Date.UTC(y, mo - 1, d, h - 5, mi - 30)

describe('fmt.money (SYS-29)', () => {
  it('prints 2 decimals and en-US thousands separators at every size', () => {
    expect(fmt.money(2123.3)).toBe('$2,123.30')
    expect(fmt.money(549.34)).toBe('$549.34')
    expect(fmt.money(0.06)).toBe('$0.06')
    expect(fmt.money(1234567.891)).toBe('$1,234,567.89')
  })

  it('prints a measured zero as $0.00 and a tiny cost that rounds to zero as $0.00', () => {
    expect(fmt.money(0)).toBe('$0.00')
    expect(fmt.money(0.004)).toBe('$0.00')
  })

  it('appends + for a lower-bound cost (SYS-32), and only then', () => {
    expect(fmt.money(12.4, { lowerBound: true })).toBe('$12.40+')
    expect(fmt.money(12.4, { lowerBound: false })).toBe('$12.40')
    expect(fmt.moneyParts(12.4, { lowerBound: true })).toEqual({ text: '$12.40', plus: true })
  })

  it('prints nothing for a null cost, with or without the lower-bound flag', () => {
    expect(fmt.money(null)).toBe('')
    expect(fmt.money(null, { lowerBound: true })).toBe('')
  })

  it('puts the sign before the dollar sign, and never prints -$0.00', () => {
    expect(fmt.money(-1.5)).toBe('-$1.50')
    expect(fmt.money(-0.001)).toBe('$0.00')
  })

  it('rejects NaN instead of printing it', () => {
    expect(() => fmt.money(Number.NaN)).toThrow()
  })
})

describe('fmt.tokens (SYS-29)', () => {
  it('prints the integer below 1,000', () => {
    expect(fmt.tokens(0)).toBe('0')
    expect(fmt.tokens(290)).toBe('290')
    expect(fmt.tokens(999)).toBe('999')
  })

  it('uses 0 decimals from 100, 1 from 10, else 2, and strips trailing zeros', () => {
    expect(fmt.tokens(97_000)).toBe('97k')
    expect(fmt.tokens(12_400_000)).toBe('12.4M')
    expect(fmt.tokens(8_690_000_000)).toBe('8.69B')
    expect(fmt.tokens(1_500_000_000)).toBe('1.5B')
    expect(fmt.tokens(1000)).toBe('1k')
    expect(fmt.tokens(146_000_000)).toBe('146M')
    expect(fmt.tokens(100_000)).toBe('100k')
  })

  it('rounds at the digit boundaries without printing 100.0k', () => {
    expect(fmt.tokens(99_949)).toBe('99.9k')
    expect(fmt.tokens(99_950)).toBe('100k')
  })

  it('moves to the next unit when rounding reaches 1,000 of the current one', () => {
    expect(fmt.tokens(999_499)).toBe('999k')
    expect(fmt.tokens(999_999)).toBe('1M')
    expect(fmt.tokens(999_999_999)).toBe('1B')
  })

  it('handles null and negative values', () => {
    expect(fmt.tokens(null)).toBe('')
    expect(fmt.tokens(-1500)).toBe('-1.5k')
  })
})

describe('fmt.dur (SYS-29)', () => {
  it('prints <1s, seconds, minutes, hours and days in the specified shapes', () => {
    expect(fmt.dur(0)).toBe('<1s')
    expect(fmt.dur(499)).toBe('<1s')
    expect(fmt.dur(45_000)).toBe('45s')
    expect(fmt.dur(13 * 60_000)).toBe('13m')
    expect(fmt.dur(65 * 60_000)).toBe('1h05m')
    expect(fmt.dur((2 * 24 + 3) * 3_600_000)).toBe('2d03h')
  })

  it('switches units at the boundaries', () => {
    expect(fmt.dur(59_499)).toBe('59s')
    expect(fmt.dur(59_500)).toBe('1m') // 60 s rounds into the minute branch
    expect(fmt.dur(3_569_499)).toBe('59m')
    expect(fmt.dur(3_570_000)).toBe('1h00m') // 59.5 min rounds to 60 min
    expect(fmt.dur(47 * 3_600_000 + 59 * 60_000)).toBe('47h59m')
    expect(fmt.dur(48 * 3_600_000)).toBe('2d00h')
  })

  it('handles null and negative values', () => {
    expect(fmt.dur(null)).toBe('')
    expect(fmt.dur(-45_000)).toBe('-45s')
  })
})

describe('fmt.clock, fmt.range (SYS-29)', () => {
  it('prints HH:MM in the given zone, 24 h, zero-padded', () => {
    expect(fmt.clock(ist(2026, 10, 12, 13, 44), 'Asia/Kolkata')).toBe('13:44')
    expect(fmt.clock(ist(2026, 10, 12, 0, 5), 'Asia/Kolkata')).toBe('00:05')
    expect(fmt.clock(Date.UTC(2026, 9, 12, 8, 14), 'Asia/Kolkata')).toBe('13:44')
    expect(fmt.clock(Date.UTC(2026, 9, 12, 8, 14), 'UTC')).toBe('08:14')
  })

  it('adds +Nd for each local midnight between start and end', () => {
    const tz = 'Asia/Kolkata'
    expect(fmt.range(ist(2026, 10, 7, 21, 43), ist(2026, 10, 9, 11, 41), tz)).toBe('21:43–11:41 +2d')
    expect(fmt.range(ist(2026, 10, 7, 22, 0), ist(2026, 10, 8, 1, 0), tz)).toBe('22:00–01:00 +1d')
    expect(fmt.range(ist(2026, 10, 7, 9, 0), ist(2026, 10, 7, 17, 30), tz)).toBe('09:00–17:30')
  })

  it('counts midnights in the given zone, not in UTC', () => {
    // 23:30 to 00:30 IST is one midnight; in UTC both fall on the same date.
    expect(fmt.range(ist(2026, 10, 7, 23, 30), ist(2026, 10, 8, 0, 30), 'Asia/Kolkata')).toBe('23:30–00:30 +1d')
    expect(fmt.range(ist(2026, 10, 7, 23, 30), ist(2026, 10, 8, 0, 30), 'UTC')).toBe('18:00–19:00')
  })

  it('prints nothing when an end is missing', () => {
    expect(fmt.range(null, 5, 'UTC')).toBe('')
    expect(fmt.range(5, null, 'UTC')).toBe('')
  })
})

describe('fmt.date, fmt.dateRange (SYS-29)', () => {
  it('prints Ddd Mmm D for a Day and for an epoch ms in a zone', () => {
    expect(fmt.date('2026-10-12')).toBe('Mon Oct 12')
    expect(fmt.date('2026-01-04')).toBe('Sun Jan 4')
    expect(fmt.date(Date.UTC(2026, 9, 11, 20, 0), 'Asia/Kolkata')).toBe('Mon Oct 12')
  })

  it('requires a zone for an epoch ms', () => {
    expect(() => fmt.date(0)).toThrow()
  })

  it('prints Mmm D – Mmm D with an en dash, and one date for a one-day range', () => {
    expect(fmt.dateRange('2026-10-06', '2026-10-12')).toBe('Oct 6 – Oct 12')
    expect(fmt.dateRange('2026-10-06', '2026-10-06')).toBe('Oct 6')
  })
})

describe('fmt.pct (SYS-29)', () => {
  it('prints an integer percent and <1% below 1 %', () => {
    expect(fmt.pct(0.824)).toBe('82%')
    expect(fmt.pct(1)).toBe('100%')
    expect(fmt.pct(0.0049)).toBe('<1%')
    expect(fmt.pct(0.005)).toBe('1%')
    expect(fmt.pct(0)).toBe('0%')
    expect(fmt.pct(null)).toBe('')
  })
})

describe('model names (SYS-41)', () => {
  it('maps ids to family and short name', () => {
    expect(modelFamily('claude-opus-5-5')).toBe('opus')
    expect(modelShort('claude-opus-5-5')).toBe('opus 5.5')
    expect(modelShort('claude-opus-5-5-20261001')).toBe('opus 5.5')
    expect(modelShort('claude-sonnet-5')).toBe('sonnet 5')
    expect(modelShort('claude-sonnet-4-20250514')).toBe('sonnet 4')
    expect(modelShort('claude-haiku-4-5')).toBe('haiku 4.5')
    expect(modelShort('claude-fable-5-1')).toBe('fable 5.1')
    expect(modelFamily('gpt-5.4')).toBe('gpt')
    expect(modelShort('gpt-5.4')).toBe('gpt 5.4')
  })

  it('treats any other id as "other" and shows it as is', () => {
    expect(modelFamily('deepseek-v3')).toBe('other')
    expect(modelShort('deepseek-v3')).toBe('deepseek-v3')
    expect(modelFamily('codex-mini')).toBe('other')
  })

  it('treats a missing model as "other" named unknown', () => {
    expect(modelFamily(null)).toBe('other')
    expect(modelShort(null)).toBe('unknown')
  })
})
