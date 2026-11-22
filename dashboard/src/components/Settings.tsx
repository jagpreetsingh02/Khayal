import { useState } from 'react'
import type { FormEvent } from 'react'

import { put } from '../api'
import type { Category, SharingInfo, ThresholdRow } from '../api'
import { usePoll } from '../hooks'
import { istWhen } from '../time'
import { Card, ErrorNote, Icon, NotShared, Skeleton } from './ui'

const GROUPS: { title: string; rules: string[] }[] = [
  {
    title: 'Blood pressure',
    rules: [
      'bp_limit_systolic',
      'bp_limit_diastolic',
      'bp_severe_systolic',
      'bp_severe_diastolic',
      'bp_weekly_over_limit_count',
    ],
  },
  { title: 'Glucose', rules: ['glucose_red_below', 'glucose_amber_below', 'glucose_amber_above'] },
  { title: 'Medicines', rules: ['missed_doses_per_day'] },
]

function ThresholdEditor({ parentId, parent }: { parentId: string; parent: string }) {
  const { data, error, reload } = usePoll<{ label: string; thresholds: ThresholdRow[] }>(
    `/parents/${parentId}/thresholds`,
    15000,
  )
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [confirmed, setConfirmed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const rows = new Map(data?.thresholds.map((t) => [t.rule, t]))
  const changed = Object.entries(draft).filter(
    ([rule, v]) => v !== '' && Number(v) !== rows.get(rule)?.value,
  )

  async function save(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setMessage(null)
    try {
      const values = Object.fromEntries(changed.map(([k, v]) => [k, Number(v)]))
      await put(`/parents/${parentId}/thresholds`, { values, doctor_confirmed: true })
      setDraft({})
      setConfirmed(false)
      setMessage({ ok: true, text: 'Saved. New readings are checked against these limits.' })
      void reload()
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) })
    } finally {
      setSaving(false)
    }
  }

  if (!data) return error ? <ErrorNote error={error} /> : <Skeleton lines={6} />
  return (
    <form onSubmit={save} className="stack">
      <p className="doctor-label">
        <Icon name="lock" size={16} /> Set by {parent}'s doctor. Enter only what the doctor wrote
        down; Care Circle does not choose limits.
      </p>
      {GROUPS.map((g) => (
        <fieldset key={g.title} className="limits">
          <legend>{g.title}</legend>
          {g.rules.map((rule) => {
            const t = rows.get(rule)
            if (!t) return null
            return (
              <label key={rule} className="limit-row">
                <span>
                  {t.label}
                  <small className="block muted">
                    {t.set_by === 'doctor' ? 'Set by doctor' : `Default ${t.default}`}
                  </small>
                </span>
                <span className="limit-input">
                  <input
                    type="number"
                    inputMode="decimal"
                    min={t.min}
                    max={t.max}
                    step={t.integer ? 1 : 'any'}
                    value={draft[rule] ?? String(t.value)}
                    onChange={(e) => setDraft({ ...draft, [rule]: e.target.value })}
                  />
                  <small>{t.unit}</small>
                </span>
              </label>
            )
          })}
        </fieldset>
      ))}
      <label className="check">
        <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        These values come from {parent}'s doctor.
      </label>
      <div className="btn-row">
        <button className="btn btn-primary" disabled={!confirmed || changed.length === 0 || saving}>
          {saving ? 'Saving…' : `Save ${changed.length || ''} change${changed.length === 1 ? '' : 's'}`}
        </button>
        {changed.length > 0 && (
          <button type="button" className="btn btn-ghost" onClick={() => setDraft({})}>
            Undo
          </button>
        )}
      </div>
      {message && (
        <p className={message.ok ? 'ok-note' : 'error-note'} role="status">
          {message.text}
        </p>
      )}
    </form>
  )
}

const SHARE_ROWS: { key: Category; title: string; text: string }[] = [
  { key: 'readings', title: 'Readings', text: 'BP and glucose numbers, charts and the doctor’s limits' },
  { key: 'alerts', title: 'Alerts', text: 'Amber and red alerts on the dashboard and in family chats' },
  { key: 'medicines', title: 'Medicines', text: 'Medicines, reminders, doses and refill requests' },
]

const CONSENT_TEXT: Record<string, string> = {
  granted: 'Agreed',
  paused: 'Paused by the parent (nothing new is logged or shared)',
  revoked: 'Withdrawn (nothing is shared)',
  pending: 'Not agreed yet',
}

function SharingPanel({ parentId, initial, parent, onSaved }: {
  parentId: string
  initial: SharingInfo
  parent: string
  onSaved: () => void
}) {
  const [values, setValues] = useState(initial.categories)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function toggle(key: Category) {
    const next = { ...values, [key]: !values[key] }
    setValues(next)
    setSaving(true)
    setError(null)
    try {
      await put(`/parents/${parentId}/sharing`, next)
      onSaved()
    } catch (e) {
      setValues(values)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const c = initial.consent
  return (
    <div className="stack">
      <div className={`consent consent-${c.status}`}>
        <Icon name={c.status === 'granted' ? 'check' : 'lock'} />
        <div>
          <strong>Consent: {CONSENT_TEXT[c.status] ?? c.status}</strong>
          <small className="block muted">
            {c.at ? `${istWhen(c.at)} · ` : ''}
            {c.text_version ? `Notice ${c.text_version} · ` : ''}
            {parent} can reply STOP in chat at any time.
          </small>
        </div>
      </div>
      <ul className="share-list">
        {SHARE_ROWS.map((row) => (
          <li key={row.key}>
            <div>
              <strong>{row.title}</strong>
              <small className="block muted">{row.text}</small>
            </div>
            {initial.editable ? (
              <button
                role="switch"
                aria-checked={values[row.key]}
                aria-label={`Share ${row.title}`}
                className="switch"
                onClick={() => toggle(row.key)}
                disabled={saving}
              >
                <span />
              </button>
            ) : (
              <span className={`chip ${values[row.key] ? 'chip-good' : 'chip-muted'}`}>
                {values[row.key] ? 'Shared' : 'Not shared'}
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="muted small">
        {initial.editable
          ? 'You decide what your family sees. Changes apply at once.'
          : `Only ${parent} can change this (parent-first).`}
        {initial.updated_by && initial.updated_at
          ? ` Last changed by ${initial.updated_by}, ${istWhen(initial.updated_at)}.`
          : ''}
      </p>
      <ErrorNote error={error} />
    </div>
  )
}

export function SettingsPage({ parentId, parent, sharing, onSaved }: {
  parentId: string
  parent: string
  sharing: SharingInfo
  onSaved: () => void
}) {
  return (
    <div className="page">
      <Card title="Sharing and consent">
        <SharingPanel
          key={`${sharing.updated_at}-${sharing.consent.status}`}
          parentId={parentId}
          initial={sharing}
          parent={parent}
          onSaved={onSaved}
        />
      </Card>
      <Card title="Alert limits">
        {sharing.visible_to_me.readings ? (
          <ThresholdEditor parentId={parentId} parent={parent} />
        ) : (
          <NotShared parent={parent} category="readings" />
        )}
      </Card>
    </div>
  )
}
