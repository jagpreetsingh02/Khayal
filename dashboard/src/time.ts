// Every time on the dashboard is shown in Asia/Kolkata, whatever the viewer's device says.
export const TZ = 'Asia/Kolkata'

const fmt = (opts: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat('en-IN', { timeZone: TZ, ...opts })

const time = fmt({ hour: 'numeric', minute: '2-digit', hour12: true })
const day = fmt({ day: 'numeric', month: 'short' })
const dayKey = fmt({ year: 'numeric', month: '2-digit', day: '2-digit' })
const weekday = fmt({ weekday: 'short', day: 'numeric', month: 'short' })

export const istTime = (iso: string) => time.format(new Date(iso))
export const istDay = (iso: string | number) => day.format(new Date(iso))
export const istWeekday = (iso: string | number) => weekday.format(new Date(iso))

/** "Today, 8:15 am" / "Yesterday, 7:30 pm" / "18 Nov, 8:15 am" (IST). */
export function istWhen(iso: string): string {
  const d = new Date(iso)
  const today = dayKey.format(new Date())
  const yesterday = dayKey.format(new Date(Date.now() - 86_400_000))
  const key = dayKey.format(d)
  const label = key === today ? 'Today' : key === yesterday ? 'Yesterday' : day.format(d)
  return `${label}, ${time.format(d)}`
}

/** "5 min ago", "3 h ago", "2 days ago". */
export function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  const d = Math.floor(s / 86400)
  return d === 1 ? '1 day ago' : `${d} days ago`
}

/** "08:30" (24 h reminder time) -> "8:30 am". */
export function hhmm12(t: string): string {
  const [h, m] = t.split(':').map(Number)
  const suffix = h < 12 ? 'am' : 'pm'
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${suffix}`
}
