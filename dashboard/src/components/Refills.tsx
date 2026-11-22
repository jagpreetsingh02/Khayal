import { useState } from 'react'

import { post } from '../api'
import type { Refill } from '../api'
import { usePoll } from '../hooks'
import { ago, istWhen } from '../time'
import { Card, Empty, ErrorNote, Icon, RefillChip, Skeleton } from './ui'

export function PendingRefill({ refill, onChange }: { refill: Refill; onChange: () => void }) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function decide(action: 'approve' | 'reject') {
    setBusy(action)
    setError(null)
    try {
      await post(`/refills/${refill.id}/${action}`)
      onChange()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      onChange()
    } finally {
      setBusy(null)
    }
  }

  const m = refill.medicine
  return (
    <div className="refill-pending">
      <div className="refill-head">
        <span className="refill-icon" aria-hidden="true">
          <Icon name="pill" size={20} />
        </span>
        <div>
          <strong>
            {m.brand} × {refill.qty}
          </strong>
          <small>
            {m.generic} {m.strength} · requested by the AI assistant {ago(refill.requested_at)}
          </small>
        </div>
      </div>
      <p className="refill-note">
        Approving sends it to the partner pharmacy, which checks the prescription. In this demo no
        real order is placed.
      </p>
      <div className="btn-row">
        <button
          className="btn btn-primary"
          onClick={() => decide('approve')}
          disabled={busy !== null}
        >
          {busy === 'approve' ? 'Approving…' : 'Approve'}
        </button>
        <button className="btn" onClick={() => decide('reject')} disabled={busy !== null}>
          {busy === 'reject' ? 'Rejecting…' : 'Reject'}
        </button>
      </div>
      <ErrorNote error={error} />
    </div>
  )
}

export function RefillsPage({ parentId }: { parentId: string }) {
  const { data, error, reload } = usePoll<{ refills: Refill[] }>(`/refills?parent_id=${parentId}`)
  const pending = data?.refills.filter((r) => r.status === 'pending_approval') ?? []
  const history = data?.refills.filter((r) => r.status !== 'pending_approval') ?? []
  return (
    <div className="page">
      <ErrorNote error={error} />
      <Card title="Waiting for your approval">
        {!data ? (
          <Skeleton />
        ) : pending.length ? (
          <div className="stack">
            {pending.map((r) => (
              <PendingRefill key={r.id} refill={r} onChange={reload} />
            ))}
          </div>
        ) : (
          <Empty>
            No refill requests. When a medicine runs low, the AI asks here, and only a family
            member can approve. (Demo: use “Simulate low stock” under Medicines.)
          </Empty>
        )}
      </Card>
      <Card title="History">
        {history.length ? (
          <ul className="history">
            {history.map((r) => (
              <li key={r.id}>
                <div className="history-head">
                  <strong>
                    {r.medicine.brand} × {r.qty}
                  </strong>
                  <RefillChip status={r.status} />
                </div>
                <p className="muted small">
                  Requested by the AI · {istWhen(r.requested_at)}
                  {r.decided_by && r.decided_at
                    ? ` · ${r.status === 'rejected' ? 'Not approved' : 'Approved'} by ${r.decided_by}, ${istWhen(r.decided_at)}`
                    : ''}
                </p>
                {r.pharmacy_ref && (
                  <p className="small">
                    Pharmacy ref <code>{r.pharmacy_ref}</code> · demo partner pharmacy, no real
                    order placed
                  </p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No decisions yet.</Empty>
        )}
      </Card>
      <p className="footnote">
        The AI can only <em>request</em> a refill. Approval happens only here, by a family member;
        the AI has no approve tool. Orders never happen inside WhatsApp.
      </p>
    </div>
  )
}
