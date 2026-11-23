import { useState } from 'react'

import type { AuditRow, AuditStats } from '../api'
import { usePoll } from '../hooks'
import { istWhen } from '../time'
import { Card, Empty, ErrorNote, Icon, Skeleton } from './ui'

type Obj = Record<string, unknown>
const obj = (v: unknown): Obj => (v && typeof v === 'object' ? (v as Obj) : {})

function summarize(row: AuditRow): string {
  if (row.redacted) return 'Details hidden: the parent has not shared this category.'
  const a = obj(row.args)
  const r = obj(row.result)
  if (row.error) return `Refused by the server: ${String(r.error ?? 'error')}`
  switch (row.tool) {
    case 'log_reading': {
      const v = obj(a.values)
      const value =
        a.type === 'bp' ? `${v.systolic}/${v.diastolic}` : `${v.glucose_value} ${v.glucose_unit ?? ''}`
      return r.stored
        ? `Saved ${value} as unconfirmed; waits for the parent's “yes”.`
        : `Did not save ${value}: asked for a retake.`
    }
    case 'alert_family':
      if (r.sent) return `Alerted the family: ${String(r.level).toUpperCase()} (server re-checked the limits).`
      if (r.not_shared) return 'Not sent: the parent does not share alerts.'
      if (r.already_sent) return 'Not sent again: this reading was already alerted.'
      return 'Not sent (rate cap).'
    case 'request_refill':
      return `Requested a refill: ${r.brand} × ${r.qty}. Waiting for a family member to approve.`
    case 'create_reminder':
      return `Set reminders for ${r.brand}: ${(r.times as string[] | undefined)?.join(', ')}.`
    case 'get_history':
      return `Read ${r.count ?? 0} confirmed readings (${a.range ?? '7d'}).`
    case 'get_meds':
      return 'Read the medicine list.'
    case 'weekly_summary':
      return 'Wrote the weekly summary (numbers only).'
    case 'dashboard:approve_refill':
      return `${a.by} approved the refill: ${a.medicine} × ${a.qty}.`
    case 'dashboard:reject_refill':
      return `${a.by} did not approve the refill: ${a.medicine}.`
    case 'pharmacy:demo_order':
      return `Handed to the demo partner pharmacy (${r.pharmacy_ref}). Demo, no real order.`
    case 'demo:simulate_low_stock':
      return `Demo: ${a.by} set ${a.medicine} stock low (${r.stock_count} left).`
    case 'dashboard:update_thresholds':
      return `${a.by} entered new limits from the doctor: ${Object.entries(obj(a.values))
        .map(([k, v]) => `${k.replaceAll('_', ' ')} ${v}`)
        .join(', ')}.`
    case 'dashboard:update_sharing':
      return `${a.by} changed what the family can see.`
    case 'dashboard:acknowledge_alert':
      return `${a.by} marked an alert as seen.`
    default:
      return row.tool
  }
}

const ACTOR = {
  agent: { label: 'AI via MCP', icon: 'bot' },
  member: { label: 'Family', icon: 'user' },
  system: { label: 'System', icon: 'gear' },
} as const

function Entry({ row }: { row: AuditRow }) {
  const [open, setOpen] = useState(false)
  const actor = ACTOR[row.actor]
  const name = row.actor === 'agent' ? `${row.tool}()` : row.tool.split(':')[1]?.replaceAll('_', ' ')
  return (
    <li className={`audit-item actor-${row.actor}${row.error ? ' is-error' : ''}`}>
      <button className="audit-row" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="actor-badge">
          <Icon name={actor.icon} size={14} />
          {actor.label}
        </span>
        <span className="audit-main">
          <code>{name}</code>
          <span className="audit-sum">{summarize(row)}</span>
        </span>
        <time dateTime={row.at}>{istWhen(row.at)}</time>
      </button>
      {open && !row.redacted && (
        <pre className="audit-json">
          {`args:\n${JSON.stringify(row.args, null, 2)}\n\nresult:\n${JSON.stringify(row.result, null, 2)}`}
        </pre>
      )}
    </li>
  )
}

export function AuditPage({ parentId }: { parentId: string }) {
  const { data, error } = usePoll<{ entries: AuditRow[]; stats: AuditStats }>(
    `/audit?parent_id=${parentId}&limit=150`,
  )
  const tools = usePoll<{ tools: string[]; live: boolean; has_approve_tool: boolean }>(
    '/mcp/tools',
    30000,
  )
  const [filter, setFilter] = useState<'all' | 'agent' | 'people'>('all')
  const rows =
    data?.entries.filter((e) =>
      filter === 'all' ? true : filter === 'agent' ? e.actor === 'agent' : e.actor !== 'agent',
    ) ?? []

  return (
    <div className="page">
      <ErrorNote error={error} />
      {data && (
        <div className="stats">
          <div className="stat">
            <span className="stat-value">{data.stats.ai_tool_calls}</span>
            <span className="stat-label">AI tool calls</span>
          </div>
          <div className="stat">
            <span className="stat-value">{data.stats.ai_refused_calls}</span>
            <span className="stat-label">Refused by guardrails</span>
          </div>
          <div className="stat stat-key">
            <span className="stat-value">{data.stats.ai_approve_calls}</span>
            <span className="stat-label">Approvals by the AI</span>
          </div>
          <div className="stat">
            <span className="stat-value">{data.stats.family_approvals}</span>
            <span className="stat-label">Approvals by family</span>
          </div>
        </div>
      )}
      <Card title="Tools the AI can call">
        {tools.data ? (
          <>
            <div className="tool-chips">
              {tools.data.tools.map((t) => (
                <code key={t}>{t}</code>
              ))}
              <code className="tool-missing">approve_refill: does not exist</code>
            </div>
            <p className="muted small">
              {tools.data.live ? 'Listed live from the Khayal MCP server. ' : 'MCP server not reachable; showing the agent allowlist. '}
              The AI can log, remind and request. It cannot approve, prescribe or diagnose.
            </p>
          </>
        ) : (
          <Skeleton lines={2} />
        )}
      </Card>
      <Card
        title="What the AI did"
        action={
          <div className="segmented" role="group" aria-label="Filter">
            {(['all', 'agent', 'people'] as const).map((f) => (
              <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {f === 'all' ? 'All' : f === 'agent' ? 'AI' : 'People'}
              </button>
            ))}
          </div>
        }
      >
        {!data ? (
          <Skeleton lines={6} />
        ) : rows.length ? (
          <ul className="audit-list">
            {rows.map((r) => (
              <Entry key={r.id} row={r} />
            ))}
          </ul>
        ) : (
          <Empty>Nothing yet. Every MCP tool call the AI makes is listed here, refusals included.</Empty>
        )}
      </Card>
    </div>
  )
}
