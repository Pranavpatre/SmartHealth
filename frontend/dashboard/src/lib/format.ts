// Locale-aware formatting keyed to the active i18n language.
// Non-English languages render native numerals (Devanagari for hi/mr, etc.)
// per the "native numerals" product choice.

import i18n from '../i18n'
import { enIN, hi } from 'date-fns/locale'
import { formatDistanceToNow } from 'date-fns'
import type { Locale } from 'date-fns'

// BCP-47 locale (with numbering-system extension) per UI language.
const NUMBER_LOCALE: Record<string, string> = {
  en: 'en-IN',
  hi: 'hi-IN-u-nu-deva',
  mr: 'mr-IN-u-nu-deva',
  gu: 'gu-IN-u-nu-gujr',
  pa: 'pa-IN-u-nu-guru',
  ta: 'ta-IN',
  ml: 'ml-IN',
  te: 'te-IN-u-nu-telu',
  kn: 'kn-IN-u-nu-knda',
  bn: 'bn-IN-u-nu-beng',
}

// date-fns ships hi but not mr; mr reuses hi (same Devanagari script).
const DATE_LOCALE: Record<string, Locale> = { hi, mr: hi }

const lang = () => (i18n.language || 'en').split('-')[0]
const numLocale = () => NUMBER_LOCALE[lang()] ?? 'en-IN'

/** Integer / grouped number in the active locale's numerals (e.g. ५२,३३९). */
export function formatNumber(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return new Intl.NumberFormat(numLocale()).format(n)
}

/** Fixed-decimal number in the active locale's numerals. */
export function formatDecimal(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return '—'
  return new Intl.NumberFormat(numLocale(), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n)
}

/** INR currency in the active locale's numerals (e.g. ₹१२,३४५). */
export function formatCurrencyINR(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return new Intl.NumberFormat(numLocale(), {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(n)
}

/** Relative time ("2 hours ago") localized where date-fns has the locale. */
export function formatRelativeTime(date: Date): string {
  return formatDistanceToNow(date, { addSuffix: true, locale: DATE_LOCALE[lang()] ?? enIN })
}

/** Clock time (HH:MM) in the active locale. */
export function formatClock(date: Date): string {
  return date.toLocaleTimeString(numLocale(), { hour: '2-digit', minute: '2-digit' })
}
