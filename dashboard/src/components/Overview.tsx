import type { Limits, Overview, Reading } from '../api'
import { ago, hhmm12, istWhen } from '../time'
import { AlertItem } from './Alerts'
import { PendingRefill } from './Refills'
import { Card, DoseChip, Empty, Icon, NotShared, Skeleton } from './ui'

type Tab = 'trends' | 'alerts' | 'refills' | 'medicines'

function bpStatus(r: Reading, l: Limits): { tone: string; text: string } {
  const s = r.systolic ?? 0
  const d = r.diastolic ?? 0
  if (s >= l.bp_severe_systolic || d >= l.bp_severe_diastolic)
    return { tone: 'bad', text: `At or above the severe limit (${l.bp_severe_systolic}/${l.bp_severe_diastolic})` }
  if (s >= l.bp_limit_systolic || d >= l.bp_limit_diastolic)
    return { tone: 'warn', text: `Above the limit (${l.bp_limit_systolic}/${l.bp_limit_diastolic})` }
  return { tone: 'good', text: `Within the limit (${l.bp_limit_systolic}/${l.bp_limit_diastolic})` }
}

function glucoseStatus(v: number, l: Limits): { tone: string; text: string } {
  if (v < l.glucose_red_below) return { tone: 'bad', text: `Below ${l.glucose_red_below} mg/dL` }
  if (v < l.glucose_amber_below) return { tone: 'warn', text: `Below ${l.glucose_amber_below} mg/dL` }
  if (v > l.glucose_amber_above) return { tone: 'warn', text: `Above ${l.glucose_amber_above} mg/dL` }
  return { tone: 'good', text: `Within ${l.glucose_amber_below}–${l.glucose_amber_above} mg/dL` }
}

const CONTEXT: Record<string, string> = {
  fasting: 'Fasting',
  post_meal: 'After a meal',
  random: 'Random',
}

function ReadingTile({ reading, limits, kind }: { reading: Reading | null; limits: Limits; kind: 'bp' | 'glucose' }) {
  if (!reading)
    return (
      <div className="tile">
        <span className="tile-label">{kind === 'bp' ? 'Blood pressure' : 'Glucose'}</span>
        <Empty>No confirmed reading yet.</Empty>
      </div>
    )
  const status =
    kind === 'bp' ? bpStatus(reading, limits) : glucoseStatus(reading.glucose_mgdl ?? 0, limits)
  return (
    <div className="tile">
      <span className="tile-label">{kind === 'bp' ? 'Blood pressure' : 'Glucose'}</span>
      <p className="tile-value">
        {kind === 'bp' ? (
          <>
            {reading.systolic}/{reading.diastolic}
            <small>mmHg</small>
          </>
        ) : (
          <>
            {reading.glucose_mgdl}
            <small>mg/dL</small>
          </>
        )}
      </p>
      <p className="tile-meta">
        {kind === 'bp' && reading.pulse ? `Pulse ${reading.pulse} · ` : ''}
        {kind === 'glucose' && reading.context ? `${CONTEXT[reading.context] ?? reading.context} · ` : ''}
        <time dateTime={reading.at} title={istWhen(reading.at)}>
          {ago(reading.at)}
        </time>
      </p>
      <p className={`tile-status tone-${status.tone}`}>
        <span className="dot" aria-hidden="true" />
        {status.text}
      </p>
    </div>
  )
}

export function OverviewPage({
  data,
  parent,
  onChange,
  go,
}: {
  data: Overview | null
  parent: string
  onChange: () => void
  go: (tab: Tab) => void
}) {
  if (!data) return <Skeleton lines={6} />
  const pending = data.pending_refills ?? []
  const alerts = data.active_alerts ?? []
  const doses = data.today_doses ?? []
  const taken = doses.filter((d) => d.status === 'taken').length

  return (
    <div className="page overview">
      {pending.length > 0 && (
        <Card
          className="card-attention"
          title={`Refill ${pending.length > 1 ? 'requests' : 'request'} waiting for you`}
        >
          <div className="stack">
            {pending.map((r) => (
              <PendingRefill key={r.id} refill={r} onChange={onChange} />
            ))}
          </div>
        </Card>
      )}

      {data.active_alerts === undefined ? (
        <Card title="Alerts">
          <NotShared parent={parent} category="alerts" />
        </Card>
      ) : (
        <Card
          title={alerts.length ? `Open alerts (${alerts.length})` : 'Alerts'}
          action={
            <button className="link" onClick={() => go('alerts')}>
              All alerts
            </button>
          }
        >
          {alerts.length ? (
            <ul className="alert-list">
              {alerts.slice(0, 3).map((a) => (
                <AlertItem key={a.id} alert={a} onChange={onChange} />
              ))}
            </ul>
          ) : (
            <p className="all-clear">
              <Icon name="check" /> No open alerts in the last 7 days.
            </p>
          )}
        </Card>
      )}

      {data.latest === undefined || !data.limits ? (
        <Card title="Latest readings">
          <NotShared parent={parent} category="readings" />
        </Card>
      ) : (
        <Card
          title="Latest readings"
          action={
            <button className="link" onClick={() => go('trends')}>
              14-day trends
            </button>
          }
        >
          <div className="tiles">
            <ReadingTile kind="bp" reading={data.latest.bp} limits={data.limits} />
            <ReadingTile kind="glucose" reading={data.latest.glucose} limits={data.limits} />
          </div>
          <p className="footnote">Limits are set by {parent}'s doctor. Readings are confirmed by {parent} before they are saved.</p>
        </Card>
      )}

      {data.today_doses === undefined ? (
        <Card title="Today's medicines">
          <NotShared parent={parent} category="medicines" />
        </Card>
      ) : (
        <Card
          title="Today's medicines"
          action={
            <button className="link" onClick={() => go('medicines')}>
              Medicines
            </button>
          }
        >
          {doses.length ? (
            <>
              <p className="muted small">
                {taken} of {doses.length} doses marked taken today
              </p>
              <ul className="dose-list">
                {doses.map((d) => (
                  <li key={`${d.medicine}-${d.time}`}>
                    <span className="dose-time">{hhmm12(d.time)}</span>
                    <span className="dose-name">{d.medicine}</span>
                    <DoseChip status={d.status} />
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <Empty>No medicine reminders set.</Empty>
          )}
        </Card>
      )}
    </div>
  )
}
