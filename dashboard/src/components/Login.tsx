import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'

import { api, post, setSession } from '../api'
import type { LoginHint, MemberRef } from '../api'
import { Icon } from './ui'

interface MembersResponse {
  family: string
  members: MemberRef[]
}

type Step =
  | { kind: 'choose' } // pick a way to sign in
  | { kind: 'token' } // demo token (mock mode only)
  | { kind: 'members'; family: MembersResponse } // demo token accepted: who are you?
  | { kind: 'code-member' } // which member to send a code to
  | { kind: 'code-enter'; memberId: string; name?: string; note: string } // type the code

const MEMBER_KEY = 'khayal-member'

function rememberedMember(): string {
  const fromUrl = new URLSearchParams(window.location.search).get('member')
  if (fromUrl) return fromUrl
  try {
    return localStorage.getItem(MEMBER_KEY) ?? ''
  } catch {
    return ''
  }
}

function remember(memberId: string): void {
  try {
    localStorage.setItem(MEMBER_KEY, memberId)
  } catch {
    // private mode: nothing to remember
  }
}

function MemberButton({ m, onPick, busy }: { m: MemberRef; onPick: (m: MemberRef) => void; busy: boolean }) {
  return (
    <button className="member-btn" onClick={() => onPick(m)} disabled={busy}>
      <span className="avatar" aria-hidden="true">
        {m.name.slice(0, 1)}
      </span>
      <span>
        <strong>{m.name}</strong>
        <small>{m.role === 'parent' ? 'Parent: can change what is shared' : 'Family member'}</small>
      </span>
      <Icon name={m.role === 'parent' ? 'lock' : 'user'} />
    </button>
  )
}

export function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [hint, setHint] = useState<LoginHint | null>(null)
  const [step, setStep] = useState<Step>({ kind: 'choose' })
  const [token, setToken] = useState('')
  const [memberId, setMemberId] = useState(rememberedMember)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api<LoginHint>('/login/hint')
      .then((h) => {
        setHint(h)
        if (!h.demo_login) setStep({ kind: 'code-member' })
      })
      .catch(() => setStep({ kind: 'code-member' }))
  }, [])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  function signedIn(session: string, id: string) {
    setSession(session)
    remember(id)
    onSignedIn()
  }

  const checkToken = (e: FormEvent) => {
    e.preventDefault()
    void run(async () => {
      setStep({ kind: 'members', family: await post<MembersResponse>('/login/members', { token }) })
    })
  }

  const pickWithToken = (m: MemberRef) =>
    void run(async () => {
      const res = await post<{ session: string }>('/login', { token, member_id: m.id })
      signedIn(res.session, m.id)
    })

  const sendCode = (id: string, name?: string) =>
    void run(async () => {
      const res = await post<{ message: string }>('/login/code/request', { member_id: id })
      setCode('')
      setStep({ kind: 'code-enter', memberId: id, name, note: res.message })
    })

  const verifyCode = (e: FormEvent, id: string) => {
    e.preventDefault()
    void run(async () => {
      const res = await post<{ session: string }>('/login/code/verify', { member_id: id, code: code.trim() })
      signedIn(res.session, id)
    })
  }

  const back = () => {
    setError(null)
    setStep(hint?.demo_login ? { kind: 'choose' } : { kind: 'code-member' })
  }

  const demoMembers = hint?.demo_members ?? []

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

        {step.kind === 'choose' && (
          <div className="stack">
            <p className="muted">How would you like to sign in?</p>
            <button className="btn btn-primary" onClick={() => setStep({ kind: 'code-member' })}>
              Send me a code
            </button>
            <button className="btn btn-ghost" onClick={() => setStep({ kind: 'token' })}>
              Use the demo access token
            </button>
            <p className="hint">
              Mock demo: a code arrives in the member's chat (the simulator's phones). The demo
              token works only in mock mode.
            </p>
          </div>
        )}

        {step.kind === 'token' && (
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
            {hint?.default_token && (
              <p className="hint">
                Mock demo: the token is <code>change-me</code> unless you set{' '}
                <code>DASHBOARD_DEMO_TOKEN</code> in <code>.env</code>.
              </p>
            )}
            <button className="btn btn-primary" disabled={busy || !token}>
              Continue
            </button>
            <button type="button" className="btn btn-ghost" onClick={back}>
              Back
            </button>
          </form>
        )}

        {step.kind === 'members' && (
          <div className="stack">
            <p className="muted">{step.family.family}: who is signing in?</p>
            <div className="member-list">
              {step.family.members.map((m) => (
                <MemberButton key={m.id} m={m} onPick={pickWithToken} busy={busy} />
              ))}
            </div>
            <button className="btn btn-ghost" onClick={() => setStep({ kind: 'token' })}>
              Back
            </button>
          </div>
        )}

        {step.kind === 'code-member' && (
          <div className="stack">
            {demoMembers.length > 0 ? (
              <>
                <p className="muted">Who is signing in? We will send a code to their chat.</p>
                <div className="member-list">
                  {demoMembers.map((m) => (
                    <MemberButton key={m.id} m={m} onPick={(p) => sendCode(p.id, p.name)} busy={busy} />
                  ))}
                </div>
              </>
            ) : (
              <form
                className="stack"
                onSubmit={(e) => {
                  e.preventDefault()
                  sendCode(memberId.trim())
                }}
              >
                <label htmlFor="member">Your member ID</label>
                <input
                  id="member"
                  autoComplete="username"
                  value={memberId}
                  onChange={(e) => setMemberId(e.target.value)}
                  placeholder="mem_…"
                  required
                />
                <p className="hint">
                  It is in your Khayal welcome message. We send a 6-digit code to your own chat
                  (WhatsApp, Telegram or the simulator).
                </p>
                <button className="btn btn-primary" disabled={busy || !memberId.trim()}>
                  Send me a code
                </button>
              </form>
            )}
            {hint?.demo_login && (
              <button className="btn btn-ghost" onClick={back}>
                Back
              </button>
            )}
          </div>
        )}

        {step.kind === 'code-enter' && (
          <form onSubmit={(e) => verifyCode(e, step.memberId)} className="stack">
            <p className="muted">{step.note}</p>
            <label htmlFor="code">{step.name ? `Code sent to ${step.name}` : 'Sign-in code'}</label>
            <input
              id="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              placeholder="6 digits"
              required
            />
            <button className="btn btn-primary" disabled={busy || code.length !== 6}>
              Sign in
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={busy}
              onClick={() => sendCode(step.memberId, step.name)}
            >
              Send a new code
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setStep({ kind: 'code-member' })}>
              Back
            </button>
          </form>
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
