const MONEY = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function money(amount: number, currency?: string): string {
  const text = MONEY.format(amount)
  return currency ? `${text} ${currency}` : text
}

export function signedMoney(amount: number, currency?: string): string {
  return amount > 0 ? `+${money(amount, currency)}` : money(amount, currency)
}

export function dateTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 19)
}

export function label(snake: string): string {
  const text = snake.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// Never interpolate an unknown value into a string: format each kind explicitly.
export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return JSON.stringify(value)
}

export function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2) ?? ''
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
