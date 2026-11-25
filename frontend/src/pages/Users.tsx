import { LoaderCircle, Mail, UserPlus } from 'lucide-react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api } from '../api'
import { useCurrentUser } from '../auth'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import type { AppUser, Role } from '../types'

const dateTime = (value: string | null) => value ? new Date(value).toLocaleString() : 'Never'

export function Users({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const me = useCurrentUser()
  const [users, setUsers] = useState<AppUser[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState('')

  const load = useCallback(async () => {
    try { setUsers(await api.users()) }
    catch (error) { notify(error instanceof Error ? error.message : 'Users could not be loaded.', true) }
    finally { setLoading(false) }
  }, [notify])
  useEffect(() => { void load() }, [load])

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    setCreating(true); setFormError('')
    try {
      const result = await api.createUser({
        email: String(data.get('email') ?? ''),
        full_name: String(data.get('full_name') ?? ''),
        role: data.get('role') === 'admin' ? 'admin' : 'user',
        send_invitation: data.get('send_invitation') === 'on',
      })
      form.reset()
      await load()
      if (result.invitation === 'failed') notify(`${result.user.email} can sign in, but the invitation email failed: ${result.invitation_error}`, true)
      else notify(result.invitation === 'sent' ? `Invitation sent to ${result.user.email}.` : `${result.user.email} can now sign in.`)
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'The user could not be added.')
    } finally { setCreating(false) }
  }

  const update = async (user: AppUser, change: { role?: Role; is_active?: boolean }, message: string) => {
    setBusyId(user.id)
    try {
      const updated = await api.updateUser(user.id, change)
      setUsers((current) => current.map((item) => item.id === updated.id ? updated : item))
      notify(message)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The user could not be updated.', true)
    } finally { setBusyId(null) }
  }

  const invite = async (user: AppUser) => {
    setBusyId(user.id)
    try {
      const result = await api.resendInvitation(user.id)
      if (result.invitation === 'sent') notify(`Invitation sent to ${user.email}.`)
      else notify(`The invitation could not be sent: ${result.invitation_error}`, true)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The invitation could not be sent.', true)
    } finally { setBusyId(null) }
  }

  const activeAdmins = users.filter((user) => user.role === 'admin' && user.is_active).length

  return (
    <div className="page list-page users-page">
      <header className="page-header">
        <div><span className="eyebrow">ADMINISTRATION / ACCESS</span><h1>Users</h1><p>Who can use PackFlow and with which role. Sign-in and passwords are handled by Clerk.</p></div>
        <div className="catalog-health"><span>ACTIVE USERS</span><strong>{users.filter((user) => user.is_active).length} of {users.length}</strong></div>
      </header>
      <section className="panel data-panel">
        <form className="users-form" onSubmit={create} aria-label="Add a user">
          <label>Email<input name="email" type="email" required autoComplete="off" placeholder="name@company.com" /></label>
          <label>Full name<input name="full_name" type="text" maxLength={200} placeholder="Optional" /></label>
          <label>Role<select name="role" defaultValue="user"><option value="user">User</option><option value="admin">Administrator</option></select></label>
          <Button className="button button--accent" disabled={creating}>{creating ? <LoaderCircle className="spin" size={16} /> : <UserPlus size={16} />}{creating ? 'Adding…' : 'Add user'}</Button>
          <label className="users-invite"><input name="send_invitation" type="checkbox" defaultChecked /> Send a Clerk invitation email</label>
          {formError && <p className="form-error" role="alert">{formError}</p>}
        </form>
        <div className="users-table-wrap">
          <table className="users-table" aria-label="Users">
            <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Last sign-in</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {loading && <tr><td colSpan={5}>Loading users…</td></tr>}
              {!loading && !users.length && <tr><td colSpan={5}>No users yet.</td></tr>}
              {users.map((user) => {
                const isMe = user.id === me.id
                const lastAdmin = user.role === 'admin' && user.is_active && activeAdmins <= 1
                const locked = isMe || lastAdmin
                const busy = busyId === user.id
                return (
                  <tr key={user.id}>
                    <td><b>{user.full_name || user.email}</b>{isMe && ' (you)'}<small>{user.email}</small></td>
                    <td>
                      <select aria-label={`Role of ${user.email}`} value={user.role} disabled={busy || locked}
                        title={locked ? 'You cannot remove your own or the last administrator access.' : undefined}
                        onChange={(event) => update(user, { role: event.target.value as Role }, `${user.email} is now ${event.target.value === 'admin' ? 'an administrator' : 'a user'}.`)}>
                        <option value="user">User</option><option value="admin">Administrator</option>
                      </select>
                    </td>
                    <td>
                      <Badge className={`status-pill ${user.is_active ? 'status-pill--packed' : 'status-pill--needs_review'}`}><i />{user.is_active ? 'Active' : 'Disabled'}</Badge>
                      {!user.linked && <small>Not signed in yet</small>}
                    </td>
                    <td>{dateTime(user.last_login_at)}</td>
                    <td>
                      <div className="users-actions">
                        {!user.linked && user.is_active && <Button variant="outline" size="sm" disabled={busy} onClick={() => invite(user)}><Mail size={14} /> Resend invitation</Button>}
                        <Button variant="outline" size="sm" disabled={busy || (user.is_active && locked)}
                          onClick={() => update(user, { is_active: !user.is_active }, `${user.email} ${user.is_active ? 'disabled' : 'enabled'}.`)}>
                          {busy ? <LoaderCircle className="spin" size={14} /> : null}{user.is_active ? 'Disable' : 'Enable'}
                        </Button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
