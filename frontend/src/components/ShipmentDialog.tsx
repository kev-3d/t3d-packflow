import { LoaderCircle, Save, Trash2, X } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'
import { useDialogFocus } from '../hooks/useDialogFocus'
import { DOC_COLUMNS, FORWARDERS, todayIso } from '../shipments'
import type { DhlDestination, Shipment, ShipmentDocField, ShipmentInput, ShipmentOrderCandidate, ShipmentUpdate } from '../types'
import { Button } from './ui/button'
import { Input } from './ui/input'

type DocState = 'todo' | 'done' | 'na'
const docState = (value: boolean | null): DocState => (value === null ? 'na' : value ? 'done' : 'todo')
const docValue = (state: DocState): boolean | null => (state === 'na' ? null : state === 'done')

interface Props {
  /** Edit this shipment; without it the dialog creates a new one. */
  shipment?: Shipment
  countries: DhlDestination[]
  canDelete: boolean
  onClose: () => void
  onCreate: (payload: ShipmentInput) => Promise<void>
  onUpdate: (id: number, payload: ShipmentUpdate) => Promise<void>
  onDelete: (shipment: Shipment) => Promise<void>
}

const emptyFields = { ship_date: todayIso(), client: '', po: '', destination: '', invoice_number: '', tracking_number: '', forwarder: 'DHL', notes: '' }

export function ShipmentDialog({ shipment, countries, canDelete, onClose, onCreate, onUpdate, onDelete }: Props) {
  const editing = Boolean(shipment)
  const [mode, setMode] = useState<'order' | 'manual'>('order')
  const [candidates, setCandidates] = useState<ShipmentOrderCandidate[] | null>(null)
  const [orderId, setOrderId] = useState<number | null>(null)
  const [fields, setFields] = useState(() => shipment
    ? { ship_date: shipment.ship_date, client: shipment.client, po: shipment.po, destination: shipment.destination,
        invoice_number: shipment.invoice_number, tracking_number: shipment.tracking_number, forwarder: shipment.forwarder, notes: shipment.notes }
    : emptyFields)
  const [docs, setDocs] = useState<Record<ShipmentDocField, DocState> | null>(() => shipment
    ? Object.fromEntries(DOC_COLUMNS.map(({ field }) => [field, docState(shipment[field])])) as Record<ShipmentDocField, DocState>
    : null)
  const [busy, setBusy] = useState(false)
  const [loadingOrder, setLoadingOrder] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState('')
  const dialogRef = useDialogFocus<HTMLFormElement>(onClose, busy)

  useEffect(() => {
    if (editing) return
    api.shipmentOrderCandidates()
      .then(rows => { setCandidates(rows); if (!rows.length) setMode('manual') })
      .catch(() => { setCandidates([]); setMode('manual') })
  }, [editing])

  const set = (name: keyof typeof emptyFields, value: string) => setFields(current => ({ ...current, [name]: value }))

  const pickOrder = async (value: string) => {
    const id = value ? Number(value) : null
    setOrderId(id)
    setError('')
    if (!id) { setFields(current => ({ ...emptyFields, ship_date: current.ship_date })); return }
    setLoadingOrder(true)
    try {
      const defaults = await api.shipmentOrderDefaults(id)
      setFields(current => ({ ...current, client: defaults.client, po: defaults.po, destination: defaults.destination, forwarder: defaults.forwarder || current.forwarder }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The order details could not be loaded.')
    } finally { setLoadingOrder(false) }
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!editing && mode === 'order' && !orderId) { setError('Select an order, or switch to a manual shipment.'); return }
    if (!fields.client.trim()) { setError('Client is required.'); return }
    if (!fields.destination) { setError('Destination is required.'); return }
    setBusy(true)
    setError('')
    try {
      if (shipment && docs) {
        await onUpdate(shipment.id, { ...fields, ...Object.fromEntries(DOC_COLUMNS.map(({ field }) => [field, docValue(docs[field])])) })
      } else {
        await onCreate({ ...fields, order_id: mode === 'order' ? orderId : null })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The shipment could not be saved.')
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!shipment) return
    setBusy(true)
    try { await onDelete(shipment) } catch (err) {
      setError(err instanceof Error ? err.message : 'The shipment could not be deleted.')
      setBusy(false)
    }
  }

  const knownCountry = !fields.destination || countries.some(country => country.code === fields.destination)

  return createPortal(
    <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="shipment-dialog-title">
      <button className="modal-backdrop" tabIndex={-1} aria-hidden="true" disabled={busy} onClick={onClose} aria-label="Close form" />
      <form ref={dialogRef} className="catalog-form client-form shipment-form" onSubmit={submit}>
        <header>
          <div><span className="eyebrow">EXPORT / {editing ? 'EDIT' : 'NEW'}</span><h2 id="shipment-dialog-title">{editing ? `Shipment · ${shipment?.client}` : 'New shipment'}</h2></div>
          <button type="button" disabled={busy} className="icon-button" aria-label="Close form" onClick={onClose}><X /></button>
        </header>
        {!editing && (
          <div className="shipment-mode" role="radiogroup" aria-label="Shipment source">
            <label><input type="radio" name="mode" checked={mode === 'order'} disabled={!candidates?.length} onChange={() => setMode('order')} /> From an order</label>
            <label><input type="radio" name="mode" checked={mode === 'manual'} onChange={() => { setMode('manual'); setOrderId(null) }} /> Manual (Shopify, trade show, sample…)</label>
          </div>
        )}
        <div className="form-grid">
          {!editing && mode === 'order' && (
            <label className="client-form-wide">
              <span>Order *</span>
              <select value={orderId ?? ''} onChange={event => pickOrder(event.target.value)} disabled={candidates === null}>
                <option value="">{candidates === null ? 'Loading orders…' : 'Select an order ready to ship'}</option>
                {candidates?.map(order => <option key={order.id} value={order.id}>{order.order_number} · {order.client}{order.order_date ? ` · ${order.order_date}` : ''}</option>)}
              </select>
              {loadingOrder && <small><LoaderCircle className="spin" size={12} /> Reading client, PO and destination…</small>}
            </label>
          )}
          <label><span>Ship date *</span><Input type="date" required value={fields.ship_date} onChange={event => set('ship_date', event.target.value)} /></label>
          <label><span>Client *</span><Input required value={fields.client} onChange={event => set('client', event.target.value)} /></label>
          <label><span>PO / reference</span><Input value={fields.po} placeholder="PO number, Shopify #, event…" onChange={event => set('po', event.target.value)} /></label>
          <label>
            <span>Destination *</span>
            <select required value={fields.destination} onChange={event => set('destination', event.target.value)}>
              <option value="">Select a country</option>
              {!knownCountry && <option value={fields.destination}>{fields.destination}</option>}
              {countries.map(country => <option key={country.code} value={country.code}>{country.name} ({country.code})</option>)}
            </select>
          </label>
          <label><span>Invoice (INV)</span><Input value={fields.invoice_number} placeholder="e.g. 3881 or na" onChange={event => set('invoice_number', event.target.value)} /></label>
          <label>
            <span>Forwarder</span>
            <select value={fields.forwarder} onChange={event => set('forwarder', event.target.value)}>
              <option value="">—</option>
              {FORWARDERS.map(name => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
          <label className="client-form-wide"><span>Tracking</span><Input value={fields.tracking_number} onChange={event => set('tracking_number', event.target.value)} /></label>
          <label className="client-form-wide"><span>Notes</span><textarea rows={2} value={fields.notes} onChange={event => set('notes', event.target.value)} /></label>
        </div>
        {docs && (
          <fieldset className="shipment-docs">
            <legend>Documents</legend>
            {DOC_COLUMNS.map(({ field, label, title }) => (
              <label key={field} title={title}>
                <span>{label}</span>
                <select value={docs[field]} onChange={event => setDocs({ ...docs, [field]: event.target.value as DocState })}>
                  <option value="todo">To do</option><option value="done">Done</option><option value="na">N/A</option>
                </select>
              </label>
            ))}
          </fieldset>
        )}
        {!editing && <p className="shipment-hint">Documents are set from the destination: TSCA and Section 232 only for the US, SDS for chemical products or when the client asks for it. Stripe can be switched on afterwards.</p>}
        {error && <p role="alert" className="form-error">{error}</p>}
        <footer>
          {editing && canDelete && (confirmDelete
            ? <Button type="button" className="button button--danger-ghost" disabled={busy} onClick={remove}><Trash2 size={16} /> Confirm delete</Button>
            : <Button type="button" className="button button--danger-ghost" disabled={busy} onClick={() => setConfirmDelete(true)}><Trash2 size={16} /> Delete</Button>)}
          <Button type="button" className="button button--ghost" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button className="button button--accent" disabled={busy || loadingOrder}>{busy ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}{busy ? 'Saving…' : editing ? 'Save changes' : 'Create shipment'}</Button>
        </footer>
      </form>
    </div>, document.body,
  )
}
