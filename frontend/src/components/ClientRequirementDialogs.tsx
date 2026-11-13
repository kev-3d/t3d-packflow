import { AlertTriangle, LoaderCircle, Save, Trash2, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { useDialogFocus } from '../hooks/useDialogFocus'
import type { Client, ClientRequirement, ClientRequirementInput } from '../types'
import { Button } from './ui/button'
import { Input } from './ui/input'

type TextField = 'docs_required' | 'pricing_requirements' | 'courier' | 'comments' | 'invoice_email' | 'payment'

/** The first four are shown to the packer on the order; the last two are billing data kept for reference. */
const noteFields: { name: TextField; label: string; rows: number }[] = [
  { name: 'docs_required', label: 'Docs required', rows: 3 },
  { name: 'pricing_requirements', label: 'Pricing / invoice requirements', rows: 4 },
  { name: 'courier', label: 'Courier', rows: 2 },
  { name: 'comments', label: 'Comments', rows: 3 },
]
const billingFields: { name: TextField; label: string; rows: number }[] = [
  { name: 'invoice_email', label: 'Invoice email (not shown on orders)', rows: 2 },
  { name: 'payment', label: 'Payment (not shown on orders)', rows: 2 },
]

export const requirementToInput = (requirement: ClientRequirement, overrides: Partial<ClientRequirementInput> = {}): ClientRequirementInput => ({
  company: requirement.company, aliases: requirement.aliases, client_id: requirement.client_id,
  invoice_email: requirement.invoice_email, payment: requirement.payment,
  pricing_requirements: requirement.pricing_requirements, docs_required: requirement.docs_required,
  courier: requirement.courier, comments: requirement.comments, ...overrides,
})

interface EditProps {
  requirement: ClientRequirement
  clients: Client[]
  onClose: () => void
  onSave: (payload: ClientRequirementInput) => Promise<void>
}

export function EditRequirementDialog({ requirement, clients, onClose, onSave }: EditProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dialogRef = useDialogFocus<HTMLFormElement>(onClose, busy)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const text = (name: string) => String(data.get(name) ?? '').trim()
    const company = text('company')
    if (!company) { setError('Company is required.'); return }
    const clientName = text('client')
    const client = clientName ? clients.find((candidate) => candidate.customer.toLowerCase() === clientName.toLowerCase()) : null
    if (clientName && !client) { setError('Choose a client from the list, or leave it empty to keep this requirement unlinked.'); return }
    const payload: ClientRequirementInput = {
      company, client_id: client?.id ?? null,
      aliases: text('aliases').split('\n').map((alias) => alias.trim()).filter(Boolean),
      invoice_email: text('invoice_email'), payment: text('payment'),
      pricing_requirements: text('pricing_requirements'), docs_required: text('docs_required'),
      courier: text('courier'), comments: text('comments'),
    }
    setBusy(true)
    setError('')
    try {
      await onSave(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The requirement could not be saved.')
      setBusy(false)
    }
  }

  return createPortal(
    <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="edit-requirement-title">
      <button className="modal-backdrop" tabIndex={-1} aria-hidden="true" disabled={busy} onClick={onClose} aria-label="Close form" />
      <form ref={dialogRef} className="catalog-form client-form" onSubmit={submit}>
        <header>
          <div><span className="eyebrow">CLIENTS / SPECIAL REQUIREMENTS</span><h2 id="edit-requirement-title">Edit requirements</h2></div>
          <button type="button" disabled={busy} className="icon-button" aria-label="Close form" onClick={onClose}><X /></button>
        </header>
        <div className="form-grid">
          <label><span>Company *</span><Input name="company" required defaultValue={requirement.company} /></label>
          <label>
            <span>Linked client</span>
            <Input name="client" list="requirement-client-options" placeholder="Not linked" defaultValue={requirement.client_name ?? ''} />
            <datalist id="requirement-client-options">{clients.map((client) => <option key={client.id} value={client.customer} />)}</datalist>
          </label>
          <label className="client-form-wide">
            <span>Other names (one per line)</span>
            <textarea name="aliases" rows={2} defaultValue={requirement.aliases.join('\n')} placeholder="Names this customer may use on a purchase order" />
          </label>
          {noteFields.map(({ name, label, rows }) => (
            <label key={name} className="client-form-wide"><span>{label}</span><textarea name={name} rows={rows} defaultValue={requirement[name]} /></label>
          ))}
          {billingFields.map(({ name, label, rows }) => (
            <label key={name}><span>{label}</span><textarea name={name} rows={rows} defaultValue={requirement[name]} /></label>
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
  /** When given, confirms deleting only this requirement instead of all of them. */
  requirement?: ClientRequirement
  count: number
  busy: boolean
  error: string
  onClose: () => void
  onConfirm: () => void
}

export function DeleteRequirementsDialog({ requirement, count, busy, error, onClose, onConfirm }: DeleteProps) {
  const dialogRef = useDialogFocus(onClose, busy)
  const title = requirement ? `Delete requirements for ${requirement.company}?` : 'Delete all special requirements?'
  const description = requirement
    ? `Orders for ${requirement.company} will no longer show a packing note. The client itself is kept. This cannot be undone.`
    : `This will permanently delete all ${count} special requirements, so no order will show a packing note. Your clients are kept. This cannot be undone.`
  return createPortal(
    <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="delete-requirements-title" aria-describedby="delete-requirements-description">
      <button className="modal-backdrop" tabIndex={-1} aria-hidden="true" disabled={busy} onClick={onClose} aria-label="Cancel deletion" />
      <section className="upload-modal delete-orders-dialog" ref={dialogRef}>
        <header><div><span className="eyebrow">{requirement ? 'SPECIAL REQUIREMENT' : 'SPECIAL REQUIREMENTS'}</span><h2 id="delete-requirements-title">{title}</h2></div><button className="icon-button" disabled={busy} onClick={onClose} aria-label="Close dialog"><X size={20} /></button></header>
        <div className="delete-orders-body"><AlertTriangle size={28} /><p id="delete-requirements-description">{description}</p></div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <footer>
          <Button className="button button--ghost" disabled={busy} onClick={onClose}>Keep</Button>
          <Button className="button button--danger" disabled={busy} onClick={onConfirm}>{busy ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}{busy ? 'Deleting…' : requirement ? 'Delete requirements' : 'Delete all'}</Button>
        </footer>
      </section>
    </div>, document.body,
  )
}
