import { Gauge } from '../components/charts/gauge'
import { Input } from '../components/ui/input'
import { Button } from '../components/ui/button'
import { AlertTriangle, ArrowLeft, Box, Check, ChevronDown, Download, FileCheck2, LoaderCircle, Package, Pencil, Plus, Save, Scale, Sparkles, Tag, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api'
import { BoxNavigation } from '../components/BoxNavigation'
import { boundedBoxIndex } from '../resultPresentation'
import { SourceDocument } from '../components/SourceDocument'
import { RecommendationPreview } from '../components/RecommendationPreview'
import { prioritySuggestions, type RecommendationPreview as Preview } from '../workflow'
import { BoxVisual } from '../components/BoxVisual'
import { DHL_PANEL_ID, DhlShippingPanel, type DhlRateStatus } from '../components/DhlShippingPanel'
import { colorForProduct } from '../components/box3d/sceneBuilder'
import { ReviewProductsTable } from '../components/ReviewProductsTable'
import { fieldId, makeDraft, reviewPayload, serverIssues, validateReview, type DraftItem, type ReviewIssue } from '../review'
import type { OrderDetailData, OrderItem, PackingResult, Product } from '../types'

interface DetailProps {
  order: OrderDetailData
  onBack: () => void
  onRefresh: () => Promise<void>
  onSaved: () => Promise<void>
  notify: (message: string, error?: boolean) => void
}

export function OrderDetail({ order, onBack, onRefresh, onSaved, notify }: DetailProps) {
  const [editing, setEditing] = useState(!order.plan)
  const [busy, setBusy] = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)
  const [savingPlan, setSavingPlan] = useState(false)
  const [boxIndex, setBoxIndex] = useState(0)
  const [draft, setDraft] = useState(makeDraft(order))
  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [undo, setUndo] = useState<{ id: number; quantity: number; planId: number } | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [highlightedProduct, setHighlightedProduct] = useState('')
  const exportMenuRef = useRef<HTMLDivElement>(null)
  const [dhlRate, setDhlRate] = useState<DhlRateStatus>({ state: 'loading' })
  const [operation, setOperation] = useState('')
  const [matchedItems, setMatchedItems] = useState(order.items)
  const [catalog, setCatalog] = useState<Product[]>()
  const [catalogError, setCatalogError] = useState(false)
  const [attempted, setAttempted] = useState(false)
  const [remoteIssues, setRemoteIssues] = useState<ReviewIssue[]>([])
  const [reviewMessage, setReviewMessage] = useState('')
  const summaryRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    setDraft(makeDraft(order)); setMatchedItems(order.items); setEditing(!order.plan); setBoxIndex(0)
    setAttempted(false); setRemoteIssues([]); setReviewMessage(''); setPreview(null); setUndo(null); setExportOpen(false); setHighlightedProduct('')
    // Reset only on navigation to another order, not on unrelated parent refreshes.
  }, [order.id])
  useEffect(() => {
    if (!exportOpen) return
    const onPointerDown = (event: MouseEvent) => { if (!exportMenuRef.current?.contains(event.target as Node)) setExportOpen(false) }
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setExportOpen(false) }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('mousedown', onPointerDown); document.removeEventListener('keydown', onKeyDown) }
  }, [exportOpen])
  useEffect(() => {
    let cancelled = false
    api.products().then(products => { if (!cancelled) { setCatalog(products); setCatalogError(false) } })
      .catch(() => { if (!cancelled) setCatalogError(true) })
    return () => { cancelled = true }
  }, [order.id])
  const validationIssues = validateReview(draft, matchedItems, catalog)
  const issues = [...validationIssues.filter(issue => issue.kind === 'warning' || attempted || issue.packingOnly), ...remoteIssues]
  const errors = issues.filter(issue => issue.kind === 'error')
  const focusSummary = () => requestAnimationFrame(() => summaryRef.current?.focus())
  const inputAttributes = (field: string) => ({ id: fieldId(field), 'aria-invalid': errors.some(e => e.field === field) || undefined, 'aria-describedby': errors.some(e => e.field === field) ? `${fieldId(field)}-error` : undefined })
  const editDraft: typeof setDraft = update => { setDraft(update); setRemoteIssues([]); setReviewMessage('') }
  const updateItem = (index: number, patch: Partial<DraftItem>) => editDraft(current => ({ ...current, items: current.items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item) }))
  const toggleEditing = () => {
    if (!editing) { setDraft(makeDraft(order)); setMatchedItems(order.items) }
    setAttempted(false); setRemoteIssues([]); setReviewMessage(''); setEditing(!editing)
  }
  const focusPackingResult = () => requestAnimationFrame(() => document.getElementById('packing-performance-title')?.focus({ preventScroll: true }))
  const saveReview = async (analyze = false) => {
    setAttempted(true); setRemoteIssues([]); setReviewMessage('')
    const blocking = validateReview(draft, matchedItems, catalog).filter(issue => issue.kind === 'error' && (analyze || !issue.packingOnly))
    if (blocking.length) { focusSummary(); return }
    setBusy(true)
    setOperation('Saving details and matching products to the catalog…')
    let saved = false
    try {
      const updated = await api.reviewOrder(order.id, reviewPayload(draft))
      saved = true
      setMatchedItems(updated.items)
      // Keep the user's draft and supplied commercial fields; only adopt server item identities/matches.
      const checkedDraft = { ...draft, items: draft.items.map((item, index) => ({ ...item, id: updated.items[index]?.id })) }
      setDraft(checkedDraft)
      let freshCatalog: Product[] | undefined
      try { freshCatalog = await api.products(); setCatalog(freshCatalog); setCatalogError(false) }
      catch { setCatalog(undefined); setCatalogError(true) }
      const physicalErrors = validateReview(checkedDraft, updated.items, freshCatalog).filter(issue => issue.kind === 'error')
      if (analyze && physicalErrors.length) {
        setReviewMessage('Order details saved. Packing has not been calculated: correct the catalog issues below.')
        await onRefresh(); setEditing(true); focusSummary(); return
      }
      if (analyze) { setOperation('Calculating the packing plan…'); await api.analyzeOrder(order.id) }
      await onRefresh()
      setEditing(!analyze)
      setAttempted(false)
      if (analyze) {
        window.scrollTo({ top: 0, behavior: 'smooth' })
        focusPackingResult()
      }
      notify(analyze ? 'Packing plan calculated.' : 'Order details saved and catalog matches rechecked. Packing has not been calculated.')
    } catch (error) {
      setRemoteIssues(error instanceof ApiError ? serverIssues(error.details, error.status) : [])
      const message = error instanceof Error ? error.message : 'The order could not be saved.'
      setReviewMessage(saved ? `Order details were saved, but the remaining operation failed. ${message}` : message)
      if (saved) { try { await onRefresh() } catch { /* Keep the draft available for recovery. */ } }
      setEditing(true); focusSummary(); notify(message, true)
    } finally { setBusy(false); setOperation('') }
  }

  const saveAndFinish = async () => {
    setBusy(true)
    setSavingPlan(true); setOperation('Saving the packing plan and PDF…')
    try {
      await api.savePackingPlan(order.id)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The packing plan could not be saved.', true)
      setBusy(false); setSavingPlan(false); setOperation('')
      return
    }
    setBusy(false); setSavingPlan(false); setOperation('')
    await onSaved()
  }

  const exportExcel = async () => {
    setBusy(true); setOperation('Preparing the Excel export…')
    try {
      await api.exportOrder(order.id, order.order_number)
      notify('Excel download started.')
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The plan could not be exported.', true)
    } finally { setBusy(false); setOperation('') }
  }

  const exportPdf = async () => {
    setBusy(true)
    setExportingPdf(true); setOperation('Preparing the PDF export…')
    try {
      await api.exportOrderPdf(order.id, order.order_number)
      notify('PDF download started.')
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The PDF could not be exported.', true)
    } finally { setBusy(false); setExportingPdf(false); setOperation('') }
  }

  const exportShippingDoc = async (kind: 'packing-list' | 'labels', box?: number) => {
    const what = kind === 'packing-list' ? 'packing list' : box ? `label for box ${box}` : 'box labels'
    setBusy(true)
    setExportingPdf(true); setOperation(`Preparing the ${what}…`)
    try {
      if (kind === 'packing-list') await api.packingListPdf(order.id, order.order_number)
      else await api.boxLabelsPdf(order.id, order.order_number, box)
      notify(`${what.charAt(0).toUpperCase()}${what.slice(1)} download started.`)
    } catch (error) {
      notify(error instanceof Error ? error.message : `The ${what} could not be generated.`, true)
    } finally { setBusy(false); setExportingPdf(false); setOperation('') }
  }

  const previewRecommendation = async (recommendation: PackingResult['product_recommendations'][number]) => {
    if (!order.plan) return
    setBusy(true); setOperation('Evaluating this addition in the packing engine…'); setPreviewError('')
    try { setPreview(await api.previewRecommendation(order.id, recommendation.source_item_id, recommendation.quantity, order.plan.id)) }
    catch (error) { notify(error instanceof Error ? error.message : 'Preview unavailable. Refresh and try again.', true) }
    finally { setBusy(false); setOperation('') }
  }
  const applyPreview = async () => {
    if (!preview) return
    setBusy(true); setOperation('Applying the addition and recalculating…'); setPreviewError('')
    try {
      const updated = await api.addSuggestedItem(order.id, preview.source_item_id, preview.quantity, preview.plan_id)
      const source = order.items.find(item => item.id === preview.source_item_id)
      const addition = updated.items.find(item => item.is_suggested && item.product_id === source?.product_id && item.description === source?.description && item.unit_price === source?.unit_price)
      setPreview(null)
      setUndo(addition?.id && updated.plan ? { id: addition.id, quantity: preview.quantity, planId: updated.plan.id } : null)
      await onRefresh()
      focusPackingResult()
      notify('Addition applied. Recommendations and totals have been recalculated.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to confirm the addition. Refresh the order before trying again.'
      setPreviewError(message); notify(message, true)
    }
    finally { setBusy(false); setOperation('') }
  }
  const undoAddition = async () => {
    if (!undo || undo.planId !== order.plan?.id) return
    setBusy(true)
    try { await api.removeSuggestedItem(order.id, undo.id, undo.quantity); setUndo(null); await onRefresh(); focusPackingResult(); notify('Last addition undone. Earlier added units have been kept.') }
    catch (error) { notify(error instanceof Error ? error.message : 'Undo failed. Refresh the order.', true) }
    finally { setBusy(false); setOperation('') }
  }

  const removeSuggestedItem = async (item: OrderItem) => {
    if (!item.id) return
    setBusy(true)
    try {
      await api.removeSuggestedItem(order.id, item.id)
      await onRefresh()
      focusPackingResult()
      notify('Added product removed. Its value was deducted from the total.')
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The added product could not be removed.', true)
    } finally { setBusy(false); setOperation('') }
  }

  const result = order.plan?.result
  const selectedIndex = boundedBoxIndex(boxIndex, result?.boxes.length || 0)
  const selectedBox = result?.boxes[selectedIndex]
  const totalValue = order.total_amount || order.items.reduce((sum, item) => sum + item.quantity * item.unit_price, 0)
  const addedItems = order.items.filter((item) => Boolean(item.is_suggested))

  return (
    <div className="page order-detail-pag">
      {preview && <RecommendationPreview preview={preview} currency={order.currency} busy={busy} error={previewError} onClose={() => setPreview(null)} onApply={applyPreview} />}
      <header className="detail-header">
        <button className="back-button" onClick={onBack}><ArrowLeft size={18} /> Orders</button>
        <div className="detail-title"><span className="eyebrow">PACKING PLAN / {order.order_number}</span><h1>{order.client}</h1><p className="detail-order-value"><span>Order value</span><strong>{order.currency} {totalValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></p></div>
        <div className="detail-actions">
          {order.plan && <Button className="button button--ghost" disabled={busy} onClick={toggleEditing}><Pencil size={16} /> {editing ? 'Cancel editing' : 'Edit details'}</Button>}
          {order.plan && <div className="export-menu" ref={exportMenuRef}>
            <Button className="button button--ghost" aria-haspopup="menu" aria-expanded={exportOpen} disabled={busy || editing} onClick={() => setExportOpen(open => !open)}>{exportingPdf ? <LoaderCircle className="spin" size={16} /> : <Download size={16} />}{exportingPdf ? 'Exporting…' : 'Export'}<ChevronDown size={15} /></Button>
            {exportOpen && <div className="export-menu__list" role="menu" aria-label="Export format">
              <button type="button" role="menuitem" disabled={result?.status !== 'complete'} onClick={() => { setExportOpen(false); exportPdf() }}><span>PDF</span><small>Printable packing plan</small></button>
              <button type="button" role="menuitem" onClick={() => { setExportOpen(false); exportExcel() }}><span>Excel</span><small>Editable workbook</small></button>
              <button type="button" role="menuitem" disabled={result?.status !== 'complete'} onClick={() => { setExportOpen(false); exportShippingDoc('packing-list') }}><span>Packing list</span><small>Shipment document (PDF)</small></button>
              <button type="button" role="menuitem" disabled={result?.status !== 'complete'} onClick={() => { setExportOpen(false); exportShippingDoc('labels') }}><span>Box labels</span><small>One 10×10 cm label per box (PDF)</small></button>
            </div>}
          </div>}
          {order.plan && !editing && <Button className="button button--accent" disabled={busy || result?.status !== 'complete'} onClick={saveAndFinish}>{savingPlan ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}{savingPlan ? 'Saving…' : 'Save & Finish'}</Button>}
        </div>
      </header>

      {busy && <p role="status" className="operation-status">{operation || 'Completing your request…'}</p>}
      {order.extraction_warnings?.length ? <div className="form-error" role="alert">{order.extraction_warnings.join(' ')}</div> : null}


      {editing ? (
        <section className="review-layout ">
          <article className="panel review-form-panel">
            <header className="panel-header"><div><span className="panel-index">01</span><div><h2>Order information</h2><p>Confirm the order details and products before calculating</p></div></div><span className="review-badge"><FileCheck2 size={15} /> {errors.length ? `${errors.length} issues to review` : 'Review required'}</span></header>
            {/* The review notice only appears when there is something to act on: blocking issues, a save result, or a catalog load failure. */}
            {(errors.length > 0 || reviewMessage || catalogError) && <div ref={summaryRef} tabIndex={-1} className={`review-validation ${errors.length || reviewMessage ? 'review-validation--error' : ''}`} aria-labelledby="review-validation-title">
              <h3 id="review-validation-title">{errors.length ? `${errors.length} blocking ${errors.length === 1 ? 'issue' : 'issues'}` : 'Review the order details'}</h3>
              {reviewMessage && <p role="alert">{reviewMessage}</p>}
              {!!errors.length && <ul>{errors.map((issue, index) => <li key={`${issue.field}-${index}`}><a href={`#${fieldId(issue.field)}`} onClick={event => { event.preventDefault(); document.getElementById(fieldId(issue.field))?.focus() }}>{issue.field.startsWith('items.') ? `Product ${Number(issue.field.split('.')[1]) + 1}: ` : ''}{issue.message}</a></li>)}</ul>}
              {catalogError && <p>Live catalog could not be loaded. Only previously returned packing data is shown; rotation and availability may be unknown. Save to retry.</p>}
            </div>}
            <div className="order-fields">
              <label><span>Order number</span><Input {...inputAttributes('order_number')} disabled={busy} value={draft.order_number} onChange={(e) => editDraft({ ...draft, order_number: e.target.value })} /></label>
              <label className="wide"><span>Customer</span><Input {...inputAttributes('client')} disabled={busy} value={draft.client} onChange={(e) => editDraft({ ...draft, client: e.target.value })} /></label>
              <label><span>Supplier</span><Input {...inputAttributes('vendor')} disabled={busy} value={draft.vendor} onChange={(e) => editDraft({ ...draft, vendor: e.target.value })} /></label>
              <label><span>Order date</span><Input {...inputAttributes('order_date')} disabled={busy} type="date" value={draft.order_date} onChange={(e) => editDraft({ ...draft, order_date: e.target.value })} /></label>
              <label><span>Requested date</span><Input {...inputAttributes('requested_date')} disabled={busy} type="date" value={draft.requested_date} onChange={(e) => editDraft({ ...draft, requested_date: e.target.value })} /></label>
              <label><span>Currency</span><select {...inputAttributes('currency')} disabled={busy} value={draft.currency} onChange={(e) => editDraft({ ...draft, currency: e.target.value })}><option>USD</option><option>EUR</option><option>GBP</option></select></label>
            </div>
            {errors.filter(issue => !issue.field.startsWith('items')).map((issue, index) => <p className="field-error" id={`${fieldId(issue.field)}-error`} key={`${issue.field}-${index}`}>{issue.message}</p>)}
            <div className="commercial-fields">
              <label><span>Delivery address</span><textarea {...inputAttributes('delivery_address')} disabled={busy} value={draft.delivery_address} onChange={(e) => editDraft({ ...draft, delivery_address: e.target.value })} /></label>
              <label><span>Billing address</span><textarea {...inputAttributes('billing_address')} disabled={busy} value={draft.billing_address} onChange={(e) => editDraft({ ...draft, billing_address: e.target.value })} /></label>
              <label><span>Terms</span><textarea {...inputAttributes('terms')} disabled={busy} value={draft.terms} onChange={e => editDraft({ ...draft, terms: e.target.value })} /></label>
              <label><span>Notes</span><textarea {...inputAttributes('notes')} disabled={busy} value={draft.notes} onChange={e => editDraft({ ...draft, notes: e.target.value })} /></label>
            </div>
            <div className="order-totals-edit">
              <label><span>SUBTOTAL</span><div><small>{draft.currency}</small><Input {...inputAttributes('subtotal')} disabled={busy} type="number" min="0" step="0.01" value={draft.subtotal} onChange={(e) => editDraft({ ...draft, subtotal: Number(e.target.value) })} /></div></label>
              <label><span>TAXES</span><div><small>{draft.currency}</small><Input {...inputAttributes('tax_amount')} disabled={busy} type="number" min="0" step="0.01" value={draft.tax_amount} onChange={(e) => editDraft({ ...draft, tax_amount: Number(e.target.value) })} /></div></label>
              <label><span>TOTAL</span><div><small>{draft.currency}</small><Input {...inputAttributes('total_amount')} disabled={busy} type="number" min="0" step="0.01" value={draft.total_amount} onChange={(e) => editDraft({ ...draft, total_amount: Number(e.target.value) })} /></div></label>
            </div>
            <div className="review-items-head" id={fieldId('items')} tabIndex={-1}><div><h3>Products to pack</h3><p>Check descriptions, quantities and prices before calculating.</p></div><button disabled={busy} onClick={() => editDraft({ ...draft, items: [...draft.items, { description: '', quantity: 1, unit_price: 0, requested_date: '', tax_label: '', discount_percent: 0, amount: 0 }] })}><Plus size={15} /> Add line item</button></div>
            <p className="packing-data-note">Catalog measurements may include provisional values from the reference workbook. Confirm them before using this plan for shipping.</p>
            <ReviewProductsTable items={draft.items} issues={issues} busy={busy} onChange={updateItem} onRemove={index => editDraft(current => ({ ...current, items: current.items.filter((_, i) => i !== index) }))} />
            <footer className="review-footer"><span><AlertTriangle size={17} /> {errors.length ? 'Resolve blocking issues before calculation.' : 'Confirm the catalog measurements and quantities.'}</span><div><Button className="button button--ghost" disabled={busy} onClick={() => saveReview(false)}><Save size={16} /> Save and recheck</Button><Button className="button button--accent" disabled={busy} onClick={() => saveReview(true)}>{busy ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />} Validate and calculate</Button></div></footer>
          </article>
          <aside className="source-card">{order.source_type === 'pdf' ? <SourceDocument key={order.id} orderId={order.id} filename={order.source_filename} /> : <section className="source-document"><h3>{order.source_type === 'email' ? 'Received email' : 'Manual entry'}</h3><p>{order.source_type === 'email' ? 'The original pasted email is kept in Notes.' : 'This order was entered manually.'}</p></section>}</aside>
        </section>
      ) : result && selectedBox ? (
        <>
          {/* <h2 id="packing-performance-title" tabIndex={-1} className="results-heading">Packing performance</h2> */}
          <section className="plan-kpis" aria-labelledby="packing-performance-title">
            <article><span className="kpi-title"><Package aria-hidden="true" /> BOXES REQUIRED</span><strong>{result.box_count}<small> {result.box_count === 1 ? 'box' : 'boxes'}</small></strong></article>
            <article><span className="kpi-title"><Box aria-hidden="true" /> TOTAL UTILIZATION <details className="metric-help"><summary aria-label="How total utilization is calculated">?</summary><p>Total packed product volume divided by the total volume of selected boxes. This is volume-weighted, not an average of box percentages. Unassigned units are excluded.</p></details></span><strong>{result.average_utilization}<small>%</small></strong></article>
            <article><span className="kpi-title"><Scale aria-hidden="true" /> GROSS WEIGHT</span><strong>{result.total_weight_kg}<small> kg</small></strong></article>
            <article className="dhl-rate-card">
              <button type="button" aria-controls={DHL_PANEL_ID} title="Show the DHL shipping details" onClick={() => {
                const panel = document.getElementById(DHL_PANEL_ID) as HTMLDetailsElement | null
                if (panel) { panel.open = true; panel.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
              }}>
                <span className="kpi-title">
                  <img src="/dhl-logo.png" alt="" className="kpi-title__logo" />
                  DHL RATE
                  {dhlRate.state === 'ready' && dhlRate.destination && <b className="kpi-title__meta">- {dhlRate.destination}</b>}
                </span>
                {dhlRate.state === 'ready' && dhlRate.total !== undefined
                  ? <strong><small>{dhlRate.currency} </small>{dhlRate.total.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>
                  : <strong className="dhl-rate-card__pending">{dhlRate.state === 'loading' || dhlRate.state === 'calculating' ? <LoaderCircle className="spin" size={20} /> : '—'}</strong>}
                {dhlRate.state !== 'ready' && <em>{dhlRate.state === 'loading' || dhlRate.state === 'calculating' ? 'Calculating…' : dhlRate.message}</em>}
              </button>
            </article>

          </section>
          <section id="selected-box-details" className="packing-workbench pb-3" aria-labelledby={`select-box-${selectedBox.sequence}`}>
            <article className="panel box-selector-panel">
              <div className="box-switcher box-switcher--cards"><BoxNavigation boxes={result.boxes} selectedIndex={selectedIndex} onSelect={index => { setBoxIndex(index); setHighlightedProduct('') }} /></div>
              <BoxVisual key={`${order.plan?.id}-${selectedBox.sequence}`} box={selectedBox} product={highlightedProduct} onProductChange={setHighlightedProduct} />
            </article>
            <aside className="box-inspector">
              <div className="inspector-head"><span>BOX {String(selectedBox.sequence).padStart(2, '0')}</span><strong>{selectedBox.box_code}</strong><em>{selectedBox.box_name}</em></div>
              <div className="utilization-readout"><div className="box-gauge" role="img" aria-label={`${selectedBox.utilization}% volume utilization`}><Gauge value={selectedBox.utilization} width={90} height={90} totalNotches={40} spacing={45} activeFill="#389D9B" inactiveFill="#E5E5E3" /><strong>{selectedBox.utilization}%</strong></div><div><b>Volume utilization</b><p>{selectedBox.remaining_volume_l} L available</p></div></div>
              <div className="inspector-metrics"><span><small>GROSS WEIGHT</small><b>{selectedBox.gross_weight_kg} kg</b></span><span><small>CAPACITY</small><b>{selectedBox.max_weight_kg} kg</b></span><span><small>UNITS</small><b>{selectedBox.placements.length}</b></span><span><small>DIMENSIONS</small><b>{selectedBox.dimensions_cm.join('×')}</b></span></div>
              <div className="contents-list"><h3 id="box-contents-title">Contents</h3><div className="box-legend box-legend--inspector" role="group" aria-labelledby="box-contents-title">{selectedBox.items.map(item => <button key={item.sku} type="button" aria-pressed={highlightedProduct === item.sku} title="Highlight this product in the 3D view" onClick={() => setHighlightedProduct(current => current === item.sku ? '' : item.sku)}><i style={{ background: colorForProduct(item.sku) }} /><span>{item.name}</span><b>×{item.quantity}</b></button>)}</div></div>
              {result.status === 'complete' && <Button className="button button--ghost box-label-button" disabled={busy} onClick={() => exportShippingDoc('labels', selectedBox.sequence)}><Tag size={16} /> Print label for box {selectedBox.sequence}</Button>}
            </aside>
          </section>

          <DhlShippingPanel orderId={order.id} planId={order.plan?.id ?? null} orderCurrency={order.currency} onStatus={setDhlRate} />


          <section className="plan-bottom-grid">
            <article className="panel recommendation-panel">
              <header className="panel-header">
                <div>
                  <span className="panel-index">02</span>
                    <div>
                      <h2>Optimize unused space</h2>
                      <p>Products that fit in the selected boxes. Apply one alternative at a time.</p>
                    </div>
                </div>
              </header>
              {undo && undo.planId === order.plan?.id && <div className="undo-addition" role="status"><span>The last addition can be undone.</span><button disabled={busy} onClick={undoAddition}>Undo last addition</button></div>}
              {addedItems.length > 0 && <div className="added-products">
                <div className="added-products__title"><span>ADDED TO THE INVOICE</span><b>{addedItems.length}</b></div>
                {addedItems.map((item) => <div className="added-product" key={item.id}>
                  <div><strong>{item.description}</strong><span>{item.quantity} × {order.currency} {item.unit_price.toFixed(2)}</span></div>
                  <b>+{order.currency} {(item.amount || item.quantity * item.unit_price).toFixed(2)}</b>
                  <button disabled={busy} onClick={() => removeSuggestedItem(item)} aria-label={`Remove ${item.description}`}><Trash2 size={15} /></button>
                </div>)}
              </div>}
              {(result.product_recommendations || []).map((recommendation) => <div className="product-recommendation" key={`${recommendation.source_item_id}-${recommendation.quantity}`}>
                <i><Plus size={17} /></i>
                <div><strong>{recommendation.description}</strong><p>Add {recommendation.quantity} units · utilization {result.average_utilization}% → {recommendation.projected_utilization}%</p></div>
                <div className="recommendation-price"><span>{recommendation.unit_price ? `+${order.currency} ${recommendation.amount.toFixed(2)}` : 'Pricing pending'}</span><small>{recommendation.unit_price ? `${order.currency} ${recommendation.unit_price.toFixed(2)} / unit` : 'Complete during review'}</small></div>
                <button disabled={busy} onClick={() => previewRecommendation(recommendation)}>Add items</button>
              </div>)}
              {!(result.product_recommendations || []).length && <div className="recommendation-empty"><Sparkles size={18} /><span>No additional units of the ordered products fit in the selected boxes.</span></div>}
              <div className="operational-notes">{prioritySuggestions(result.suggestions).map((suggestion, index) => <div className={`recommendation recommendation--${suggestion.type}`} key={`${suggestion.title}-${index}`}><i><Sparkles size={18} /></i><div><strong>{suggestion.type === 'warning' ? 'Warning' : suggestion.type === 'weight' ? 'Weight warning' : suggestion.type === 'space' ? 'Space suggestion' : suggestion.type === 'success' ? 'Success' : 'Information'} · {suggestion.title}</strong><p>{suggestion.detail}</p>{suggestion.type === 'space' && <p>No automatic alternative-layout comparison is available. Review the selected boxes or preview an additional product below.</p>}</div><span>{String(index + 1).padStart(2, '0')}</span></div>)}</div>
            </article>
            <article className="panel item-summary-panel"><header className="panel-header"><div><span className="panel-index">03</span><div><h2>Order summary</h2><p>{result.unit_count} units across {order.items.length} line items</p></div></div></header><div className="simple-items simple-items--products"><div><span>PRODUCT</span><span>QTY.</span><span>UNIT WEIGHT</span></div>{order.items.map((item) => <div key={item.id}><span>{item.description}</span><span>{item.quantity}</span><span>{item.weight_kg?.toFixed(2) || '—'} kg</span></div>)}</div></article>
          </section>
        </>
      ) : <div className="empty-state"><Package size={42} /><h2>This order does not have a packing plan yet.</h2><p>Review its line items to calculate a plan.</p><Button className="button button--accent" onClick={() => setEditing(true)}>Review order</Button></div>}
    </div>
  )
}
