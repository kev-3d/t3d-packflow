import { ClerkProvider, SignIn, useAuth } from '@clerk/react'
import { AlertCircle, LoaderCircle, LogOut } from 'lucide-react'
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, configureAuth } from './api'
import { Button } from './components/ui/button'
import type { AuthConfig, CurrentUser } from './types'

const CurrentUserContext = createContext<CurrentUser | null>(null)

/** The signed-in user. Only use it to adapt the UI: the server enforces every permission. */
export function useCurrentUser(): CurrentUser {
  const user = useContext(CurrentUserContext)
  if (!user) throw new Error('useCurrentUser must be used inside <AuthGate>.')
  return user
}

function Loading({ label }: { label: string }) {
  return <div className="app-loader" role="status"><LoaderCircle className="spin" /><span>{label}</span></div>
}

function Notice({ title, message, action }: { title: string; message: string; action?: ReactNode }) {
  return <div className="auth-screen"><div className="connection-error"><AlertCircle size={36} /><h1>{title}</h1><p>{message}</p>{action}</div></div>
}

/** Loads the sign-in configuration, then requires a Clerk session and a PackFlow account. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => { api.authConfig().then(setConfig).catch(() => setUnreachable(true)) }, [])

  if (unreachable) return <Notice title="We could not reach PackFlow" message="Check that the server is available and try again." action={<Button className="button button--accent" onClick={() => window.location.reload()}>Try again</Button>} />
  if (!config) return <Loading label="Starting PackFlow…" />
  if (!config.configured) return <Notice title="Sign-in is not configured" message="An administrator must set CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY on the server." />
  return (
    <ClerkProvider publishableKey={config.publishable_key} afterSignOutUrl="/">
      <SessionGate>{children}</SessionGate>
    </ClerkProvider>
  )
}

function SessionGate({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn, userId, getToken, signOut } = useAuth()
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [problem, setProblem] = useState<{ title: string; message: string } | null>(null)
  // Keep the latest Clerk callbacks without re-running the session effect on every render.
  const clerk = useRef({ getToken, signOut })
  clerk.current = { getToken, signOut }

  useEffect(() => {
    setUser(null)
    setProblem(null)
    if (!isLoaded || !isSignedIn) { configureAuth(null, null); return }
    let cancelled = false
    configureAuth(() => clerk.current.getToken(), null)
    api.me()
      .then((me) => {
        if (cancelled) return
        // From now on a 401 means the session ended: return to the sign-in screen.
        configureAuth(() => clerk.current.getToken(), () => { void clerk.current.signOut() })
        setUser(me)
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (error instanceof ApiError && error.status === 403) setProblem({ title: 'No access to PackFlow', message: error.message })
        else setProblem({ title: 'We could not verify your session', message: error instanceof Error ? error.message : 'Try again in a moment.' })
      })
    return () => { cancelled = true }
  }, [isLoaded, isSignedIn, userId])

  if (!isLoaded) return <Loading label="Checking your session…" />
  if (!isSignedIn) {
    return (
      <div className="auth-screen">
        <div className="auth-brand"><img src="/logo.jpg" width={40} height={40} alt="" /><strong>PackFlow<span>.</span></strong><small>by Thought3D</small></div>
        <SignIn routing="hash" />
      </div>
    )
  }
  if (problem) {
    return <Notice title={problem.title} message={problem.message} action={
      <div className="auth-actions">
        <Button className="button button--ghost" onClick={() => window.location.reload()}>Try again</Button>
        <Button className="button button--accent" onClick={() => void signOut()}><LogOut size={16} /> Sign out</Button>
      </div>
    } />
  }
  if (!user) return <Loading label="Loading your account…" />
  return <CurrentUserContext.Provider value={user}>{children}</CurrentUserContext.Provider>
}
