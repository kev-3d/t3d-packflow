import { Button } from './ui/button'
import { AlertTriangle, LoaderCircle, Trash2, X } from 'lucide-react'
import { createPortal } from 'react-dom'
import { useDialogFocus } from '../hooks/useDialogFocus'
import type { OrderSummary } from '../types'

interface Props {
  count: number
  /** When given, the dialog confirms deleting only this order instead of the whole history. */
  order?: Pick<OrderSummary, 'order_number' | 'client'>
  busy: boolean
  error: string
  onClose: () => void
  onConfirm: () => void
}

export function DeleteOrdersDialog({ count, order, busy, error, onClose, onConfirm }: Props) {
  const dialogRef = useDialogFocus(onClose, busy)
  const title = order ? `Delete order ${order.order_number}?` : 'Delete all orders?'
  const description = order
    ? `This will permanently delete order ${order.order_number}${order.client ? ` for ${order.client}` : ''}, its packing plan and saved PDF reports. Other orders and your product and box catalogs will be kept. This cannot be undone.`
    : `This will permanently delete all ${count} orders, their packing plans and saved PDF reports. Your product and box catalogs will be kept. This cannot be undone.`
  return createPortal(
    <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="delete-orders-title" aria-describedby="delete-orders-description">
      <button className="modal-backdrop" tabIndex={-1} aria-hidden="true" disabled={busy} onClick={onClose} aria-label="Cancel deletion" />
      <section className="upload-modal delete-orders-dialog" ref={dialogRef}>
        <header><div><span className="eyebrow">{order ? 'PURCHASE ORDER' : 'ORDER HISTORY'}</span><h2 id="delete-orders-title">{title}</h2></div><button className="icon-button" disabled={busy} onClick={onClose} aria-label="Close dialog"><X size={20} /></button></header>
        <div className="delete-orders-body"><AlertTriangle size={28} /><p id="delete-orders-description">{description}</p></div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <footer><Button className="button button--ghost" disabled={busy} onClick={onClose}>{order ? 'Keep order' : 'Keep orders'}</Button><Button className="button button--danger" disabled={busy} onClick={onConfirm}>{busy ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}{busy ? 'Deleting…' : order ? 'Delete order' : 'Delete all orders'}</Button></footer>
      </section>
    </div>, document.body,
  )
}
