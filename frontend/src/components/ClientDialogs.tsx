import { AlertTriangle, LoaderCircle, Save, Trash2, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { useDialogFocus } from '../hooks/useDialogFocus'
import type { Client, ClientInput } from '../types'
import { Button } from './ui/button'
import { Input } from './ui/input'

const fields: { name: keyof ClientInput; label: string; type?: string; multiline?: boolean; required?: boolean }[] = [
  { name: 'customer', label: 'Customer', required: true },
  { name: 'contact_name', label: 'Contact name' },
  { name: 'phone_number', label: 'Phone number', type: 'tel' },
  { name: 'email', label: 'Email', type: 'email' },
  { name: 'country', label: 'Country' },
  { name: 'address', label: 'Address', multiline: true },
  { name: 'shipping_address', label: 'Shipping address', multiline: true },
]

interface EditProps {
  client: Client
  onClose: () => void
  onSave: (payload: ClientInput) => Promise<void>
}

export function EditClientDialog({ client, onClose, onSave }: EditProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dialogRef = useDialogFocus<HTMLFormElement>(onClose, busy)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const payload = Object.fromEntries(fields.map(({ name }) => [name, String(data.get(name) ?? '').trim()])) as ClientInput
    if (!payload.customer) { setError('Customer is required.'); return }
    setBusy(true)
    setError('')
    try {
      await onSave(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The client could not be saved.')
      setBusy(false)
    }
  }

  return createPortal(
    <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="edit-client-title">
      <button className="modal-backdrop" tabIndex={-1} aria-hidden="true" disabled={busy} onClick={onClose} aria-label="Close form" />
      <form ref={dialogRef} className="catalog-form client-form" onSubmit={submit}>
        <header>
          <div><span className="eyebrow">CLIENTS / EDIT</span><h2 id="edit-client-title">Edit client</h2></div>
          <button type="button" disabled={busy} className="icon-button" aria-label="Close form" onClick={onClose}><X /></button>
        </header>
        <div className="form-grid">
          {fields.map(({ name, label, type, multiline, required }) => (
            <label key={name} className={multiline ? 'client-form-wide' : undefined}>
              <span>{label}{required && ' *'}</span>
              {multiline
                ? <textarea name={name} rows={3} defaultValue={client[name]} />
                : <Input name={name} type={type ?? 'text'} required={required} defaultValue={client[name]} />}
            </label>
          ))}
        </div>
        {error && <p role="alert" className="form-error">{error}</p>}
        <footer>
          <Button type="button" className="button button--ghost" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button className="button button--accent" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}{busy ? 'Saving…' : 'Save changes'}</Button>
        </footer>
      </form>
    </div>, document.body,
  )
}

interface DeleteProps {
  /** When given, confirms deleting only this client instead of the whole directory. */
  client?: Client
  count: number
  busy: boolean
  error: string
  onClose: () => void
  onConfirm: () => void
}

export function DeleteClientsDialog({ client, count, busy, error, onClose, onConfirm }: DeleteProps) {
  const dialogRef = useDialogFocus(onClose, busy)
  const title = client ? `Delete ${client.customer}?` : 'Delete all clients?'
  const description = client
    ? `This will permanently delete ${client.customer} and its contact and shipping details. Other clients are kept. This cannot be undone.`
    : `This will permanently delete all ${count} clients. Orders, products and boxes are kept. This cannot be undone.`
  return createPortal(
    <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="delete-clients-title" aria-describedby="delete-clients-description">
      <button className="modal-backdrop" tabIndex={-1} aria-hidden="true" disabled={busy} onClick={onClose} aria-label="Cancel deletion" />
      <section className="upload-modal delete-orders-dialog" ref={dialogRef}>
        <header><div><span className="eyebrow">{client ? 'CLIENT' : 'CLIENT DIRECTORY'}</span><h2 id="delete-clients-title">{title}</h2></div><button className="icon-button" disabled={busy} onClick={onClose} aria-label="Close dialog"><X size={20} /></button></header>
        <div className="delete-orders-body"><AlertTriangle size={28} /><p id="delete-clients-description">{description}</p></div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <footer>
          <Button className="button button--ghost" disabled={busy} onClick={onClose}>{client ? 'Keep client' : 'Keep clients'}</Button>
          <Button className="button button--danger" disabled={busy} onClick={onConfirm}>{busy ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}{busy ? 'Deleting…' : client ? 'Delete client' : 'Delete all clients'}</Button>
        </footer>
      </section>
    </div>, document.body,
  )
}
