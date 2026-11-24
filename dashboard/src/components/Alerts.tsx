import { useState } from 'react'

import { post } from '../api'
import type { Alert } from '../api'
import { usePoll } from '../hooks'
import { ago, istWhen } from '../time'
import { Card, Empty, ErrorNote, LevelBadge, RedOnlyNote, Skeleton } from './ui'

export function AlertItem({ alert, onChange }: { alert: Alert; onChange: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function acknowledge() {
    setBusy(true)
    try {
      await post(`/alerts/${alert.id}/acknowledge`)
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className={`alert-item alert-${alert.level}${alert.acknowledged_at ? ' is-done' : ''}`}>
      <div className="alert-top">
        <LevelBadge level={alert.level} />
        <time dateTime={alert.at} title={istWhen(alert.at)}>
          {istWhen(alert.at)} · {ago(alert.at)}
        </time>
        {alert.delivery === 'pending' && (
          <span className="chip chip-warn" title="Messages to the family are being retried">
            Not delivered yet
          </span>
        )}
        {alert.delivery === 'failed' && (
          <span className="chip chip-bad" title="Sending to the family failed">
            Not delivered
          </span>
        )}
      </div>
      <p className="alert-reason">{alert.reason}</p>
      {alert.acknowledged_at ? (
        <p className="alert-ack">
          Seen by {alert.acknowledged_by ?? 'family'} · {istWhen(alert.acknowledged_at)}
        </p>
      ) : (
        <button className="btn btn-small" onClick={acknowledge} disabled={busy}>
          I've seen this
        </button>
      )}
      <ErrorNote error={error} />
    </li>
  )
}

export function AlertsPage({
  parentId,
  parent,
  shared,
}: {
  parentId: string
  parent: string
  shared: boolean
}) {
  const { data, error, reload } = usePoll<{ alerts: Alert[] }>(
    `/alerts?parent_id=${parentId}&days=30`,
  )
  const open = data?.alerts.filter((a) => !a.acknowledged_at) ?? []
  const seen = data?.alerts.filter((a) => a.acknowledged_at) ?? []
  return (
    <div className="page">
      <ErrorNote error={error} />
      {!shared && <RedOnlyNote parent={parent} />}
      <Card title={`Needs a look${open.length ? ` (${open.length})` : ''}`}>
        {!data ? (
          <Skeleton />
        ) : open.length ? (
          <ul className="alert-list">
            {open.map((a) => (
              <AlertItem key={a.id} alert={a} onChange={reload} />
            ))}
          </ul>
        ) : (
          <Empty>No open alerts. Alerts appear here when a limit set by the doctor is crossed.</Empty>
        )}
      </Card>
      <Card title="Seen in the last 30 days">
        {seen.length ? (
          <ul className="alert-list">
            {seen.map((a) => (
              <AlertItem key={a.id} alert={a} onChange={reload} />
            ))}
          </ul>
        ) : (
          <Empty>Nothing yet.</Empty>
        )}
      </Card>
      <p className="footnote">
        Amber: a limit was crossed. Red: a serious limit was crossed (BP only after a confirmed
        repeat reading). Alerts describe numbers against the doctor's limits; they are not a
        diagnosis.
      </p>
    </div>
  )
}
