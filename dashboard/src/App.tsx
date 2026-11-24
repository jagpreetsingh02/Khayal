import { Suspense, lazy, useEffect, useRef, useState } from 'react'

import { getSession, logout } from './api'
import type { Me, Overview } from './api'
import { AlertsPage } from './components/Alerts'
import { AuditPage } from './components/AuditLog'
import { Login } from './components/Login'
import { MedicinesPage } from './components/Medicines'
import { OverviewPage } from './components/Overview'
import { RefillsPage } from './components/Refills'
import { SettingsPage } from './components/Settings'
import { ErrorNote, NotShared, Skeleton } from './components/ui'
import { usePoll } from './hooks'
import { istWhen } from './time'

// Charts (Recharts) load only when the Trends tab opens.
const TrendsPage = lazy(() => import('./components/Trends').then((m) => ({ default: m.TrendsPage })))

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'trends', label: 'Trends' },
  { id: 'alerts', label: 'Alerts' },
  { id: 'refills', label: 'Refills' },
  { id: 'medicines', label: 'Medicines' },
  { id: 'settings', label: 'Limits & sharing' },
  { id: 'audit', label: 'What the AI did' },
] as const
type TabId = (typeof TABS)[number]['id']

const INTENDED_USE =
  'Khayal is a medication-adherence and self-recorded-readings logbook with family ' +
  'notifications. It does not diagnose, treat or recommend dosing. Alert limits are set by the ' +
  "parent's doctor. Not an emergency service: call 108/112."

function tabFromHash(): TabId {
  const id = window.location.hash.slice(1)
  return (TABS.find((t) => t.id === id)?.id ?? 'overview') as TabId
}

function Shell({ onSignOut }: { onSignOut: () => void }) {
  const [tab, setTab] = useState<TabId>(tabFromHash)
  const me = usePoll<Me>('/me', 8000)
  const parent = me.data?.parents[0]
  const overview = usePoll<Overview>(parent ? `/parents/${parent.id}/overview` : null)
  const [toast, setToast] = useState<string | null>(null)
  const seen = useRef<Set<string> | null>(null)

  useEffect(() => {
    const onHash = () => setTab(tabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    if (me.error && !getSession()) onSignOut()
  }, [me.error, onSignOut])

  // A gentle toast when a new alert or refill request arrives while the page is open.
  useEffect(() => {
    const o = overview.data
    if (!o) return
    const items = [
      ...(o.active_alerts ?? []).map((a) => ({ id: a.id, text: `${a.level.toUpperCase()} alert: ${a.reason}` })),
      ...(o.pending_refills ?? []).map((r) => ({ id: r.id, text: `Refill request: ${r.medicine.brand} × ${r.qty}` })),
    ]
    if (seen.current) {
      const fresh = items.find((i) => !seen.current!.has(i.id))
      if (fresh) setToast(fresh.text)
    }
    seen.current = new Set(items.map((i) => i.id))
  }, [overview.data])

  useEffect(() => {
    if (!toast) return
    const id = window.setTimeout(() => setToast(null), 6000)
    return () => window.clearTimeout(id)
  }, [toast])

  function go(id: TabId) {
    window.location.hash = id
    setTab(id)
    window.scrollTo({ top: 0 })
  }

  if (!me.data || !parent)
    return (
      <main className="shell">
        <ErrorNote error={me.error} />
        <Skeleton lines={8} />
      </main>
    )

  const s = parent.sharing
  const badge: Partial<Record<TabId, number>> = {
    alerts: overview.data?.active_alerts?.length ?? 0,
    refills: overview.data?.pending_refills?.length ?? 0,
  }
  const consent = s.consent.status

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              K
            </span>
            <div>
              <strong>{parent.name}</strong>
              <small>
                <span className={`consent-dot consent-${consent}`} aria-hidden="true" />
                {consent === 'granted' ? 'Sharing with family' : `Consent ${consent}`}
              </small>
            </div>
          </div>
          <div className="who">
            <span>{me.data.member.name.split(' ')[0]}</span>
            <button className="btn btn-small btn-ghost" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              className="tab"
              aria-current={tab === t.id ? 'page' : undefined}
              onClick={() => go(t.id)}
            >
              {t.label}
              {badge[t.id] ? <span className="tab-badge">{badge[t.id]}</span> : null}
            </button>
          ))}
        </nav>
      </header>

      <main className="shell">
        {consent !== 'granted' && (
          <p className="banner">
            {consent === 'paused'
              ? `${parent.name} has paused Khayal. Nothing new is logged or shared until they reply START.`
              : consent === 'revoked'
                ? `${parent.name} has withdrawn consent. Nothing is shared.`
                : `${parent.name} has not agreed yet. Nothing is logged until they reply “Haan / Yes”.`}
          </p>
        )}
        {tab === 'overview' && (
          <OverviewPage data={overview.data} parent={parent.name} onChange={overview.reload} go={go} />
        )}
        {tab === 'trends' &&
          (s.visible_to_me.readings ? (
            <Suspense fallback={<Skeleton lines={8} />}>
              <TrendsPage parentId={parent.id} parent={parent.name} />
            </Suspense>
          ) : (
            <NotShared parent={parent.name} category="readings" />
          ))}
        {tab === 'alerts' && (
          <AlertsPage parentId={parent.id} parent={parent.name} shared={s.visible_to_me.alerts} />
        )}
        {tab === 'refills' &&
          (s.visible_to_me.medicines ? (
            <RefillsPage parentId={parent.id} />
          ) : (
            <NotShared parent={parent.name} category="medicines" />
          ))}
        {tab === 'medicines' &&
          (s.visible_to_me.medicines ? (
            <MedicinesPage parentId={parent.id} demo={me.data.app_mode === 'mock'} />
          ) : (
            <NotShared parent={parent.name} category="medicines" />
          ))}
        {tab === 'settings' && (
          <SettingsPage parentId={parent.id} parent={parent.name} sharing={s} onSaved={me.reload} />
        )}
        {tab === 'audit' && <AuditPage parentId={parent.id} />}
      </main>

      <footer className="footer">
        <p>{INTENDED_USE}</p>
        <p className="muted">
          All times in India Standard Time (IST). Updates every few seconds. Signed in until{' '}
          {istWhen(me.data.session.expires_at)}.
        </p>
      </footer>

      {toast && (
        <div className="toast" role="status" onClick={() => setToast(null)}>
          {toast}
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [signedIn, setSignedIn] = useState(() => Boolean(getSession()))
  if (!signedIn) return <Login onSignedIn={() => setSignedIn(true)} />
  return (
    <Shell
      onSignOut={() => {
        void logout().finally(() => setSignedIn(false))
      }}
    />
  )
}
