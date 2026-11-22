import { useState } from 'react'

import { post } from '../api'
import type { Dose, Medicine } from '../api'
import { usePoll } from '../hooks'
import { hhmm12 } from '../time'
import { Card, DoseChip, Empty, ErrorNote, Skeleton } from './ui'

function StockBar({ med }: { med: Medicine }) {
  const days = med.days_left ?? 0
  const tone = days <= 5 ? 'bad' : days <= 10 ? 'warn' : 'good'
  const pct = Math.min(100, (days / 30) * 100)
  return (
    <div className="stock">
      <div className="stock-label">
        <span>
          {med.stock_count} left · <strong>{days} days</strong>
        </span>
        {days <= 5 && <span className={`chip chip-${tone === 'bad' ? 'bad' : 'warn'}`}>Low</span>}
      </div>
      <div className="meter" role="img" aria-label={`${days} days of stock left`}>
        <span className={`meter-fill tone-bg-${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export function MedicinesPage({ parentId, demo }: { parentId: string; demo: boolean }) {
  const { data, error, reload } = usePoll<{ medicines: Medicine[]; today: Dose[] }>(
    `/parents/${parentId}/medicines`,
  )
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  async function lowStock(med: Medicine) {
    setBusy(med.id)
    setNote(null)
    try {
      await post(`/parents/${parentId}/demo/low-stock`, { medicine_id: med.id })
      setNote({
        ok: true,
        text: `${med.brand} is now low. The AI called request_refill over MCP; the request is waiting under Refills.`,
      })
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
      reload()
    }
  }

  if (!data) return <Skeleton lines={6} />
  return (
    <div className="page">
      <ErrorNote error={error} />
      {note && (
        <p className={note.ok ? 'ok-note' : 'error-note'} role="status">
          {note.text}
        </p>
      )}
      <Card title="Today (IST)">
        {data.today.length ? (
          <ul className="dose-list">
            {data.today.map((d) => (
              <li key={`${d.medicine}-${d.time}`}>
                <span className="dose-time">{hhmm12(d.time)}</span>
                <span className="dose-name">{d.medicine}</span>
                <DoseChip status={d.status} />
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No reminders today.</Empty>
        )}
      </Card>
      <Card title="Medicines and reminders">
        {data.medicines.length ? (
          <ul className="med-list">
            {data.medicines.map((m) => (
              <li key={m.id} className="med">
                <div className="med-head">
                  <div>
                    <strong>{m.brand}</strong>
                    <small className="block muted">
                      {m.generic} {m.strength}
                    </small>
                  </div>
                  {!m.confirmed && <span className="chip chip-muted">Not confirmed yet</span>}
                </div>
                <div className="med-times" aria-label="Reminder times">
                  {m.times.length ? (
                    m.times.map((t) => (
                      <span key={t} className="time-pill">
                        {hhmm12(t)}
                      </span>
                    ))
                  ) : (
                    <span className="muted small">No reminder set</span>
                  )}
                </div>
                <StockBar med={m} />
                <p className="muted small">
                  Last 7 days: {m.week.taken} taken · {m.week.missed} missed
                </p>
                {demo && !m.is_schedule_x && (
                  <button
                    className="btn btn-small btn-ghost"
                    onClick={() => lowStock(m)}
                    disabled={busy !== null}
                  >
                    {busy === m.id ? 'Asking the AI…' : 'Simulate low stock (demo)'}
                  </button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty>
            No medicines yet. The parent adds one by sending a photo of the strip and confirming it.
          </Empty>
        )}
      </Card>
      <p className="footnote">
        Reminder times are in India time. Care Circle never suggests doses or changes to
        medicines.
      </p>
    </div>
  )
}
