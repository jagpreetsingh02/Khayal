import type { ReactNode } from 'react'

import type { Category, Level, RefillStatus } from '../api'

type IconName = 'alert' | 'siren' | 'check' | 'lock' | 'pill' | 'bot' | 'user' | 'gear' | 'clock'

const PATHS: Record<IconName, string> = {
  alert: 'M12 3 2 20h20L12 3Zm0 6v5m0 3v.01',
  siren: 'M12 3a7 7 0 0 0-7 7v7h14v-7a7 7 0 0 0-7-7Zm-9 17h18M12 8v5',
  check: 'M4 12.5 9 17.5 20 6.5',
  lock: 'M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3',
  pill: 'M10.5 4.5a4.95 4.95 0 0 1 7 7l-6 6a4.95 4.95 0 0 1-7-7l6-6Zm-3 3 7 7',
  bot: 'M5 9h14v10H5zM12 5v4M9 14h.01M15 14h.01M2 13v3M22 13v3',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 9a7 7 0 0 1 14 0',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.5 7.5 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7.5 7.5 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.5 7.5 0 0 0 2 1.2l.4 2.6h5l.4-2.6a7.5 7.5 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4l3 2',
}

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="icon"
    >
      <path d={PATHS[name]} />
    </svg>
  )
}

export function Card({
  title,
  action,
  children,
  className = '',
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  )
}

export function LevelBadge({ level }: { level: Level }) {
  return (
    <span className={`badge badge-${level}`}>
      <Icon name={level === 'red' ? 'siren' : 'alert'} size={14} />
      {level === 'red' ? 'Red' : 'Amber'}
    </span>
  )
}

const REFILL_LABEL: Record<RefillStatus, string> = {
  pending_approval: 'Waiting for approval',
  approved: 'Approved',
  rejected: 'Not approved',
  sent_to_pharmacy: 'Sent to pharmacy (demo)',
  fulfilled: 'Delivered',
}

export function RefillChip({ status }: { status: RefillStatus }) {
  const tone =
    status === 'pending_approval' ? 'warn' : status === 'rejected' ? 'muted' : 'good'
  return <span className={`chip chip-${tone}`}>{REFILL_LABEL[status]}</span>
}

const DOSE_LABEL = {
  taken: 'Taken',
  missed: 'Missed',
  pending: 'Reminder sent',
  upcoming: 'Later today',
  no_reply: 'No reply yet',
} as const

export function DoseChip({ status }: { status: keyof typeof DOSE_LABEL }) {
  const tone =
    status === 'taken' ? 'good' : status === 'missed' ? 'bad' : status === 'upcoming' ? 'muted' : 'warn'
  return (
    <span className={`chip chip-${tone}`}>
      {status === 'taken' && <Icon name="check" size={13} />}
      {DOSE_LABEL[status]}
    </span>
  )
}

const CATEGORY_LABEL: Record<Category, string> = {
  readings: 'readings and charts',
  alerts: 'alerts',
  medicines: 'medicines and refills',
}

export function NotShared({ parent, category }: { parent: string; category: Category }) {
  return (
    <div className="not-shared">
      <Icon name="lock" />
      <div>
        <strong>
          {parent} has not shared {CATEGORY_LABEL[category]} with the family.
        </strong>
        <p>Care Circle is parent-first: only {parent} can change what the family sees.</p>
      </div>
    </div>
  )
}

export function RedOnlyNote({ parent }: { parent: string }) {
  return (
    <p className="red-only">
      <Icon name="lock" size={16} />
      {parent} has not shared alerts. Red alerts always reach the family, so only those are
      shown here.
    </p>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? (
    <p className="error-note" role="alert">
      {error}
    </p>
  ) : null
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} />
      ))}
    </div>
  )
}
