// Typed client for the Khayal dashboard API (backend/app/api/dashboard.py).
import { setServerNow } from './time'

export type Role = 'parent' | 'child'
export type Category = 'readings' | 'alerts' | 'medicines'
export type Level = 'amber' | 'red'
export type RefillStatus =
  | 'pending_approval'
  | 'approved'
  | 'rejected'
  | 'sent_to_pharmacy'
  | 'fulfilled'

export interface MemberRef {
  id: string
  name: string
  role: Role
}

export interface SharingInfo {
  parent_id: string
  categories: Record<Category, boolean>
  visible_to_me: Record<Category, boolean>
  updated_by: string | null
  updated_at: string | null
  editable: boolean
  consent: { status: string; at: string | null; text_version: string | null }
}

export interface Me {
  member: MemberRef
  family: string
  parents: { id: string; name: string; sharing: SharingInfo }[]
  app_mode: string
  timezone: string
}

export interface Reading {
  id: string
  type: 'bp' | 'glucose'
  at: string
  systolic: number | null
  diastolic: number | null
  pulse: number | null
  glucose_mgdl: number | null
  context: string | null
  source: string
}

export type Limits = Record<string, number>

export interface Alert {
  id: string
  parent_id: string
  level: Level
  reason: string
  reading_id: string | null
  at: string
  acknowledged_by: string | null
  acknowledged_at: string | null
}

export interface Refill {
  id: string
  parent_id: string
  medicine: { id: string; brand: string; generic: string; strength: string }
  qty: number
  status: RefillStatus
  requested_by: string
  requested_at: string
  decided_by: string | null
  decided_at: string | null
  pharmacy: string | null
  pharmacy_ref: string | null
}

export interface Dose {
  medicine: string
  time: string
  due_at: string
  status: 'taken' | 'missed' | 'pending' | 'upcoming' | 'no_reply'
}

export interface Overview {
  parent: { id: string; name: string }
  latest?: { bp: Reading | null; glucose: Reading | null }
  limits?: Limits
  today_doses?: Dose[]
  pending_refills?: Refill[]
  active_alerts?: Alert[]
  alerts_shared?: boolean // false: the parent shares red alerts only
}

export interface Medicine {
  id: string
  brand: string
  generic: string
  strength: string
  times: string[]
  stock_count: number
  daily_dose: number
  days_left: number | null
  confirmed: boolean
  is_schedule_x: boolean
  week: { taken: number; missed: number }
}

export interface ThresholdRow {
  rule: string
  label: string
  unit: string
  min: number
  max: number
  integer: boolean
  value: number
  default: number
  set_by: 'default' | 'doctor'
}

export interface AuditRow {
  id: string
  at: string
  actor: 'agent' | 'member' | 'system'
  tool: string
  via: 'mcp' | 'dashboard'
  args: unknown
  result: unknown
  error: boolean
  redacted: boolean
}

export interface AuditStats {
  ai_tool_calls: number
  ai_refused_calls: number
  ai_approve_calls: number
  family_approvals: number
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const SESSION_KEY = 'khayal-session'

export function getSession(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY)
  } catch {
    return null
  }
}

export function setSession(session: string | null): void {
  try {
    if (session) localStorage.setItem(SESSION_KEY, session)
    else localStorage.removeItem(SESSION_KEY)
  } catch {
    // private mode: the session lasts until reload
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail))
    return detail
      .map((d) => (typeof d === 'string' ? d : (d as { msg?: string }).msg ?? JSON.stringify(d)))
      .join(' ')
  return 'Something went wrong.'
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const session = getSession()
  if (session) headers.set('Authorization', `Bearer ${session}`)
  if (init.body) headers.set('Content-Type', 'application/json')
  const res = await fetch(`/api${path}`, { ...init, headers })
  const demoNow = res.headers.get('X-Demo-Now')
  if (demoNow) setServerNow(Date.parse(demoNow))
  if (!res.ok) {
    let message = res.statusText
    try {
      message = detailText((await res.json()).detail)
    } catch {
      // not JSON
    }
    if (res.status === 401) setSession(null)
    throw new ApiError(res.status, message)
  }
  return (await res.json()) as T
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const put = <T>(path: string, body: unknown) =>
  api<T>(path, { method: 'PUT', body: JSON.stringify(body) })
