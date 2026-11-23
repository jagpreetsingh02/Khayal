import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'

import { api, post, setSession } from '../api'
import type { MemberRef } from '../api'
import { Icon } from './ui'

interface MembersResponse {
  family: string
  members: MemberRef[]
}

export function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [token, setToken] = useState('')
  const [family, setFamily] = useState<MembersResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState(false)

  useEffect(() => {
    api<{ default_token: boolean }>('/login/hint')
      .then((h) => setHint(h.default_token))
      .catch(() => setHint(false))
  }, [])

  async function checkToken(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setFamily(await post<MembersResponse>('/login/members', { token }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function pick(member: MemberRef) {
    setBusy(true)
    try {
      const res = await post<{ session: string }>('/login', { token, member_id: member.id })
      setSession(res.session)
      onSignedIn()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <div className="login-card">
        <div className="brand brand-lg">
          <span className="brand-mark" aria-hidden="true">
            K
          </span>
          <div>
            <h1>Khayal</h1>
            <p>Family dashboard</p>
          </div>
        </div>

        {!family ? (
          <form onSubmit={checkToken} className="stack">
            <label htmlFor="token">Demo access token</label>
            <input
              id="token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="DASHBOARD_DEMO_TOKEN"
              required
            />
            {hint && (
              <p className="hint">
                Mock demo: the token is <code>change-me</code> unless you set{' '}
                <code>DASHBOARD_DEMO_TOKEN</code> in <code>.env</code>.
              </p>
            )}
            <button className="btn btn-primary" disabled={busy || !token}>
              Continue
            </button>
          </form>
        ) : (
          <div className="stack">
            <p className="muted">{family.family}: who is signing in?</p>
            <div className="member-list">
              {family.members.map((m) => (
                <button
                  key={m.id}
                  className="member-btn"
                  onClick={() => pick(m)}
                  disabled={busy}
                >
                  <span className="avatar" aria-hidden="true">
                    {m.name.slice(0, 1)}
                  </span>
                  <span>
                    <strong>{m.name}</strong>
                    <small>
                      {m.role === 'parent' ? 'Parent: can change what is shared' : 'Family member'}
                    </small>
                  </span>
                  <Icon name={m.role === 'parent' ? 'lock' : 'user'} />
                </button>
              ))}
            </div>
            <button className="btn btn-ghost" onClick={() => setFamily(null)}>
              Back
            </button>
          </div>
        )}
        {error && (
          <p className="error-note" role="alert">
            {error}
          </p>
        )}
      </div>
    </main>
  )
}
