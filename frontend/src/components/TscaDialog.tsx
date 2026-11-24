import { createPortal } from 'react-dom'
import { useEffect, useRef, useState } from 'react'
import { ExternalLink, FileCheck2, LoaderCircle, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { Button } from './ui/button'
import { useDialogFocus } from '../hooks/useDialogFocus'
import { api } from '../api'
import type { TscaCertification, TscaDraft, TscaItem, TscaState } from '../types'

const OPTIONS: { value: Exclude<TscaCertification, ''>; title: string; text: string }[] = [
  { value: 'positive', title: 'Positive certification', text: 'I certify that all chemical substances in this shipment comply with all applicable rules or orders under TSCA and that I am not offering a chemical substance for entry in violation of TSCA or any applicable rule or order thereunder.' },
  { value: 'negative', title: 'Negative certification', text: 'I certify that all chemicals in this shipment are not subject to TSCA.' },
]

/** Same checks as the backend: what still blocks the final certificate. */
function issuesFor(draft: TscaDraft): string[] {
  const issues: string[] = []
  if (!draft.date.trim()) issues.push('Enter the declaration date.')
  if (!draft.certification) issues.push('Choose positive or negative certification after checking applicability.')
  if (!draft.items.length) issues.push('Add at least one product.')
  draft.items.forEach((item, index) => {
    const missing = [['number', 'product number'], ['name', 'name'], ['description', 'description']]
      .filter(([key]) => !item[key as 'number' | 'name' | 'description'].trim()).map(([, label]) => label)
    if (missing.length) issues.push(`Product ${index + 1}: enter its ${missing.join(', ')}.`)
  })
  return issues
}

export function TscaDialog({ orderId, orderNumber, onClose, notify }: {
  orderId: number
  orderNumber: string
  onClose: () => void
  notify: (message: string, error?: boolean) => void
}) {
  const [state, setState] = useState<TscaState>()
  const [draft, setDraft] = useState<TscaDraft>()
  const [confirmed, setConfirmed] = useState(false)
  const [remember, setRemember] = useState<number[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ref = useDialogFocus<HTMLElement>(onClose, busy)

  useEffect(() => {
    let cancelled = false
    api.tsca(orderId).then(loaded => {
      if (cancelled) return
      setState(loaded); setDraft(loaded.draft); setConfirmed(loaded.confirmed)
    }).catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'The declaration could not be loaded.') })
    return () => { cancelled = true }
  }, [orderId])

  // Any edit invalidates the final review.
  const edit = (next: TscaDraft) => { setDraft(next); setConfirmed(false) }
  const editItem = (index: number, patch: Partial<TscaItem>) =>
    draft && edit({ ...draft, items: draft.items.map((item, i) => (i === index ? { ...item, ...patch } : item)) })

  const issues = draft ? issuesFor(draft) : []

  // Live PDF preview of the unsaved draft, refreshed shortly after the user stops typing.
  const [previewUrl, setPreviewUrl] = useState('')
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [previewNonce, setPreviewNonce] = useState(0)
  const refreshNow = useRef(false)
  const previewUrlRef = useRef('')
  useEffect(() => {
    if (!draft) return
    const controller = new AbortController()
    const delay = refreshNow.current ? 0 : 700
    refreshNow.current = false
    const timer = window.setTimeout(async () => {
      setPreviewBusy(true)
      try {
        const blob = await api.tscaPreview(orderId, draft, confirmed, controller.signal)
        if (controller.signal.aborted) return
        const url = URL.createObjectURL(blob)
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
        previewUrlRef.current = url
        setPreviewUrl(url)
        setPreviewError('')
      } catch (err) {
        if (!controller.signal.aborted) setPreviewError(err instanceof Error ? err.message : 'The preview could not be generated.')
      } finally {
        if (!controller.signal.aborted) setPreviewBusy(false)
      }
    }, delay)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [orderId, draft, confirmed, previewNonce])
  useEffect(() => () => { if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current) }, [])
  const refreshPreview = () => { refreshNow.current = true; setPreviewNonce(n => n + 1) }

  const save = async (confirm: boolean) => {
    if (!draft) return
    const saved = await api.saveTsca(orderId, draft, confirm, remember)
    setState(saved); setDraft(saved.draft); setConfirmed(saved.confirmed); setRemember([])
    return saved
  }

  const run = async (action: 'save' | 'draft' | 'final') => {
    setBusy(true); setError('')
    try {
      await save(action === 'final' ? true : confirmed)
      if (action === 'save') notify('TSCA declaration saved.')
      else {
        await api.tscaPdf(orderId, orderNumber, action === 'final')
        notify(action === 'final' ? 'Final TSCA certification downloaded. Review it and sign it before submission.' : 'Draft TSCA certification downloaded.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The declaration could not be saved.')
    } finally { setBusy(false) }
  }

  return createPortal(
    <div className="modal-layer" role="dialog" aria-modal="true" aria-labelledby="tsca-title">
      <button className="modal-backdrop" tabIndex={-1} aria-hidden="true" disabled={busy} onClick={onClose} aria-label="Close" />
      <section ref={ref} className="upload-modal tsca-dialog">
        <header>
          <div><span className="eyebrow">US CUSTOMS · {orderNumber}</span><h2 id="tsca-title">TSCA certification</h2></div>
          <button type="button" className="icon-button" disabled={busy} aria-label="Close" onClick={onClose}><X /></button>
        </header>
        {!draft ? (
          <div className="tsca-body">{error ? <p role="alert" className="form-error">{error}</p> : <p role="status"><LoaderCircle className="spin" size={16} /> Loading declaration…</p>}</div>
        ) : (
          <div className="tsca-layout">
          <div className="tsca-body">
            <p className="tsca-note">Shipment to <b>{state?.destination?.name ?? 'the United States'}</b>. The app does not determine TSCA compliance: choose the certification only after checking your product records.</p>
            <div className="form-grid tsca-fields">
              <label><span>Date on declaration</span><input value={draft.date} maxLength={100} onChange={e => edit({ ...draft, date: e.target.value })} /></label>
              <label><span>Shipment / reference</span><input value={draft.reference} maxLength={200} onChange={e => edit({ ...draft, reference: e.target.value })} /></label>
            </div>

            <fieldset className="tsca-options">
              <legend>Certification statement</legend>
              {OPTIONS.map(option => (
                <label key={option.value} className={`tsca-option${draft.certification === option.value ? ' is-selected' : ''}`}>
                  <input type="radio" name="tsca-certification" checked={draft.certification === option.value}
                    onChange={() => edit({ ...draft, certification: option.value })} />
                  <span><b>{option.title}</b>{option.text}</span>
                </label>
              ))}
            </fieldset>

            <div className="tsca-products">
              <div className="tsca-products__head"><h3>Products covered ({draft.items.length})</h3>
                <Button type="button" className="button button--ghost" disabled={busy || draft.items.length >= 200}
                  onClick={() => edit({ ...draft, items: [...draft.items, { product_id: null, number: '', name: '', description: '' }] })}><Plus size={15} /> Add product</Button>
              </div>
              {draft.items.length === 0 && <p className="tsca-note">No products yet. Add the products included in this shipment.</p>}
              {draft.items.map((item, index) => (
                <div className="tsca-item" key={index}>
                  <span className="tsca-item__index">{index + 1}</span>
                  <label><span>Product number (EAN)</span><input value={item.number} maxLength={150} onChange={e => editItem(index, { number: e.target.value })} /></label>
                  <label><span>Product name</span><input value={item.name} maxLength={500} onChange={e => editItem(index, { name: e.target.value })} /></label>
                  <label className="tsca-item__description"><span>Product description</span><textarea rows={2} value={item.description} maxLength={4000} onChange={e => editItem(index, { description: e.target.value })} /></label>
                  <div className="tsca-item__actions">
                    {item.product_id !== null && <label className="tsca-check"><input type="checkbox" checked={remember.includes(item.product_id)}
                      onChange={e => setRemember(ids => e.target.checked ? [...ids, item.product_id as number] : ids.filter(id => id !== item.product_id))} /> Remember in catalog</label>}
                    <button type="button" className="icon-button" aria-label={`Remove product ${index + 1}`} disabled={busy}
                      onClick={() => edit({ ...draft, items: draft.items.filter((_, i) => i !== index) })}><Trash2 size={16} /></button>
                  </div>
                </div>
              ))}
            </div>

            {issues.length > 0 && <ul className="tsca-issues" aria-label="Before the final certificate">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
            <label className="tsca-check tsca-confirm">
              <input type="checkbox" checked={confirmed} disabled={issues.length > 0} onChange={e => setConfirmed(e.target.checked)} />
              I have checked the products, identifiers, descriptions, date and certification against our compliance records, and am authorised to prepare this declaration.
            </label>
            {error && <p role="alert" className="form-error">{error}</p>}
          </div>
          <aside className="tsca-preview" aria-label="Document preview">
            <div className="tsca-preview__bar">
              <span>Document preview{previewBusy && <LoaderCircle className="spin" size={14} />}</span>
              <span className="tsca-preview__actions">
                <button type="button" className="text-link" onClick={refreshPreview} disabled={previewBusy}><RefreshCw size={14} /> Refresh</button>
                {previewUrl && <a className="text-link" href={previewUrl} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open in new tab</a>}
              </span>
            </div>
            <small className="tsca-preview__hint">{issues.length || !confirmed ? 'Marked DRAFT until every field is complete and the final review is ticked.' : 'Final layout. Download the final PDF to issue it.'}</small>
            {previewError && <p role="alert" className="field-error">{previewError}</p>}
            {previewUrl
              ? <iframe title="TSCA certification preview" src={`${previewUrl}#toolbar=0&navpanes=0&view=FitH`} />
              : <div className="tsca-preview__empty">{previewBusy ? 'Rendering the certificate…' : 'The preview appears here.'}</div>}
          </aside>
          </div>
        )}
        <footer>
          <Button type="button" className="button button--ghost" disabled={busy || !draft} onClick={() => run('save')}>Save</Button>
          <Button type="button" className="button button--ghost" disabled={busy || !draft} onClick={() => run('draft')}>Draft PDF</Button>
          <Button type="button" className="button button--accent" disabled={busy || !draft || issues.length > 0 || !confirmed} onClick={() => run('final')}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <FileCheck2 size={16} />} Final PDF
          </Button>
        </footer>
      </section>
    </div>,
    document.body,
  )
}
