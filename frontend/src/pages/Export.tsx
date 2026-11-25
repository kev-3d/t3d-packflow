import 'flag-icons/css/flag-icons.min.css'
import { Check, ClipboardList, ExternalLink, FileDown, FileSearch, LoaderCircle, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useCurrentUser } from '../auth'
import { ClientRequirementsNote } from '../components/ClientRequirementsNote'
import { ShipmentDialog } from '../components/ShipmentDialog'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { DOC_COLUMNS, EMAIL_COLUMNS, FORWARDERS, sheetDate, trackingUrl } from '../shipments'
import type { DhlDestination, Shipment, ShipmentFilters, ShipmentUpdate } from '../types'

interface Props {
  notify: (message: string, error?: boolean) => void
  onOpenOrder: (id: number) => void
}

/** Text cell edited in place; saves when it loses focus or on Enter. */
function InlineText({ value, label, onSave }: { value: string; label: string; onSave: (value: string) => void }) {
  return <Input key={value} className="export-inline" aria-label={label} defaultValue={value}
    onBlur={event => { const next = event.target.value.trim(); if (next !== value) onSave(next) }}
    onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') { event.currentTarget.value = value; event.currentTarget.blur() } }} />
}

export function Export({ notify, onOpenOrder }: Props) {
  const { is_admin: isAdmin } = useCurrentUser()
  const [shipments, setShipments] = useState<Shipment[] | null>(null)
  const [countries, setCountries] = useState<DhlDestination[]>([])
  const [filters, setFilters] = useState<ShipmentFilters>({})
  const [query, setQuery] = useState('')
  const [dialog, setDialog] = useState<Shipment | 'new' | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [saving, setSaving] = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)

  const load = useCallback(async (next: ShipmentFilters) => {
    try { setShipments(await api.shipments(next)) } catch (error) {
      notify(error instanceof Error ? error.message : 'Shipments could not be loaded.', true)
      setShipments(current => current ?? [])
    }
  }, [notify])

  // The search box waits for a pause in typing before asking the server.
  useEffect(() => {
    const timer = window.setTimeout(() => load({ ...filters, q: query.trim() || undefined }), query ? 250 : 0)
    return () => window.clearTimeout(timer)
  }, [filters, query, load])

  useEffect(() => { api.dhlConfig().then(config => setCountries([...config.destinations].sort((a, b) => a.name.localeCompare(b.name)))).catch(() => setCountries([])) }, [])

  const countryName = useMemo(() => new Map(countries.map(country => [country.code, country.name])), [countries])
  const groups = useMemo(() => {
    const byDate = new Map<string, Shipment[]>()
    for (const shipment of shipments ?? []) byDate.set(shipment.ship_date, [...(byDate.get(shipment.ship_date) ?? []), shipment])
    return [...byDate.entries()]
  }, [shipments])
  const pendingCount = (shipments ?? []).filter(shipment => !shipment.complete).length

  const replace = (updated: Shipment) => setShipments(current => current?.map(row => (row.id === updated.id ? updated : row)) ?? null)

  const update = async (shipment: Shipment, payload: ShipmentUpdate) => {
    setSaving(shipment.id)
    try { replace(await api.updateShipment(shipment.id, payload)) } catch (error) {
      notify(error instanceof Error ? error.message : 'The shipment could not be updated.', true)
    } finally { setSaving(null) }
  }

  const remove = async (shipment: Shipment) => {
    setSaving(shipment.id)
    try {
      await api.deleteShipment(shipment.id)
      setShipments(current => current?.filter(row => row.id !== shipment.id) ?? null)
      notify(`Shipment for ${shipment.client} deleted.`)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The shipment could not be deleted.', true)
    } finally { setSaving(null); setConfirmDelete(null) }
  }

  const setFilter = (key: keyof ShipmentFilters, value: string | boolean) => setFilters(current => ({ ...current, [key]: value || undefined }))

  const downloadPackingList = async (shipment: Shipment) => {
    if (!shipment.order_id) return
    try { await api.packingListPdf(shipment.order_id, shipment.order_number || shipment.po) } catch (error) {
      notify(error instanceof Error ? error.message : 'The packing list could not be generated.', true)
    }
  }

  return (
    <div className="page list-page export-page">
      <header className="page-header">
        <div><span className="eyebrow">OPERATIONS / EXPORT</span><h1>Export shipments</h1><p>Documents, tracking and emails for every outgoing shipment.</p></div>
        <div className="orders-header-actions">
          <div className="summary-stamp"><b>{pendingCount.toString().padStart(2, '0')}</b><span>SHIPMENTS<br />PENDING</span></div>
          <Button className="button button--accent" onClick={() => setDialog('new')}><Plus size={16} /> New shipment</Button>
        </div>
      </header>
      <section className="panel data-panel">
        <div className="table-tools export-tools">
          <label><Search size={17} /><Input aria-label="Search shipments" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search client, PO, invoice or tracking…" /></label>
          <div className="export-filters">
            <label>From<Input type="date" value={filters.from ?? ''} onChange={event => setFilter('from', event.target.value)} /></label>
            <label>To<Input type="date" value={filters.to ?? ''} onChange={event => setFilter('to', event.target.value)} /></label>
            <label>Forwarder<select value={filters.forwarder ?? ''} onChange={event => setFilter('forwarder', event.target.value)}><option value="">All</option>{FORWARDERS.map(name => <option key={name}>{name}</option>)}</select></label>
            <label>Country<select value={filters.destination ?? ''} onChange={event => setFilter('destination', event.target.value)}><option value="">All</option>{countries.map(country => <option key={country.code} value={country.code}>{country.name}</option>)}</select></label>
            <label className="export-pending"><input type="checkbox" checked={Boolean(filters.pending)} onChange={event => setFilter('pending', event.target.checked)} /> Pending only</label>
          </div>
        </div>
        <div className="export-table-wrap" role="region" aria-label="Shipments" tabIndex={0}>
          <table className="export-table">
            <thead>
              <tr>
                <th>Client</th><th>PO</th><th>Dest.</th><th>INV</th>
                {DOC_COLUMNS.map(({ field, label, title }) => <th key={field} title={title} className="export-check-col">{label}</th>)}
                <th>Tracking</th><th>Forwarder</th>
                {EMAIL_COLUMNS.map(({ field, label, title }) => <th key={field} title={title} className="export-check-col">{label}</th>)}
                <th>Progress</th><th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {shipments === null && <tr><td colSpan={16} className="empty-row"><LoaderCircle className="spin" size={16} /> Loading shipments…</td></tr>}
              {shipments?.length === 0 && <tr><td colSpan={16} className="empty-row">{Object.values(filters).some(Boolean) || query ? 'No shipments match these filters.' : 'No shipments yet. Select “New shipment” to add the first one.'}</td></tr>}
              {groups.map(([day, rows]) => (
                <Fragment key={day}>
                  <tr className="export-date-row"><th colSpan={16} scope="colgroup">{sheetDate(day)}<span>{rows.length} shipment{rows.length === 1 ? '' : 's'}</span></th></tr>
                  {rows.map(shipment => {
                    const url = trackingUrl(shipment.forwarder, shipment.tracking_number)
                    const { done, total } = shipment.progress
                    return (
                      <Fragment key={shipment.id}>
                        <tr className={`export-row ${shipment.complete ? 'export-row--complete' : ''} ${saving === shipment.id ? 'export-row--saving' : ''}`}>
                          <td className="export-client">
                            <b>{shipment.client}</b>
                            {shipment.requirements && <button type="button" className="export-note-toggle" aria-expanded={expanded === shipment.id} title="Client special requirements" aria-label={`Special requirements for ${shipment.client}`} onClick={() => setExpanded(expanded === shipment.id ? null : shipment.id)}><ClipboardList size={14} /></button>}
                            {shipment.notes && <small>{shipment.notes}</small>}
                          </td>
                          <td>{shipment.order_id
                            ? <button type="button" className="export-link" title="Open order" onClick={() => onOpenOrder(shipment.order_id!)}><FileSearch size={13} />{shipment.po}</button>
                            : shipment.po || '—'}</td>
                          <td title={countryName.get(shipment.destination) ?? shipment.destination}>{shipment.destination
                            ? <span className="export-country"><span className={`fi fi-${shipment.destination.toLowerCase()} fis export-flag`} aria-hidden="true" />{shipment.destination}</span>
                            : '—'}</td>
                          <td><InlineText value={shipment.invoice_number} label={`Invoice for ${shipment.client}`} onSave={value => update(shipment, { invoice_number: value })} /></td>
                          {DOC_COLUMNS.map(({ field, label }) => {
                            const value = shipment[field]
                            return <td key={field} className="export-check-col">{value === null
                              ? <button type="button" className="export-na" title={`${label} not applicable. Select to require it.`} aria-label={`${label}: not applicable for ${shipment.client}. Require it`} onClick={() => update(shipment, { [field]: false })}>n/a</button>
                              : <input type="checkbox" checked={value} aria-label={`${label} for ${shipment.client}`} onChange={event => update(shipment, { [field]: event.target.checked })} />}</td>
                          })}
                          <td className="export-tracking">
                            <InlineText value={shipment.tracking_number} label={`Tracking for ${shipment.client}`} onSave={value => update(shipment, { tracking_number: value })} />
                            {url && <a href={url} target="_blank" rel="noreferrer" title="Track shipment" aria-label={`Track ${shipment.tracking_number}`}><ExternalLink size={13} /></a>}
                          </td>
                          <td>
                            <select className="export-inline" aria-label={`Forwarder for ${shipment.client}`} value={shipment.forwarder} onChange={event => update(shipment, { forwarder: event.target.value })}>
                              <option value="">—</option>
                              {!FORWARDERS.includes(shipment.forwarder as typeof FORWARDERS[number]) && shipment.forwarder && <option>{shipment.forwarder}</option>}
                              {FORWARDERS.map(name => <option key={name}>{name}</option>)}
                            </select>
                          </td>
                          {EMAIL_COLUMNS.map(({ field, label }) => {
                            const sentAt = shipment[`${field}_at` as 'email_client_sent_at' | 'email_forwarder_sent_at']
                            return <td key={field} className="export-check-col" title={sentAt ? `Marked ${new Date(sentAt).toLocaleString('en-GB')}` : undefined}>
                              <input type="checkbox" checked={shipment[field]} aria-label={`${label} for ${shipment.client}`} onChange={event => update(shipment, { [field]: event.target.checked })} />
                            </td>
                          })}
                          <td><span className={`export-progress ${shipment.complete ? 'export-progress--done' : ''}`} title={`${done} of ${total} steps done`}><i><b style={{ width: `${total ? (done / total) * 100 : 0}%` }} /></i>{done}/{total}</span></td>
                          <td className="export-actions">
                            {shipment.order_id && <Button variant="ghost" size="icon" title="Packing list PDF" aria-label={`Packing list for ${shipment.client}`} onClick={() => downloadPackingList(shipment)}><FileDown size={16} /></Button>}
                            {confirmDelete === shipment.id ? (
                              <span className="export-confirm" role="group" aria-label={`Delete shipment for ${shipment.client}?`}>
                                <span>Delete?</span>
                                <Button variant="ghost" size="icon" className="export-confirm-yes" title="Confirm delete" aria-label={`Confirm deleting shipment for ${shipment.client}`} disabled={saving === shipment.id} onClick={() => remove(shipment)}><Check size={16} /></Button>
                                <Button variant="ghost" size="icon" title="Cancel" aria-label="Cancel delete" onClick={() => setConfirmDelete(null)}><X size={16} /></Button>
                              </span>
                            ) : (
                              <>
                                <Button variant="ghost" size="icon" title="Edit shipment" aria-label={`Edit shipment for ${shipment.client}`} onClick={() => setDialog(shipment)}><Pencil size={16} /></Button>
                                {isAdmin && <Button variant="ghost" size="icon" className="order-action--delete" title="Delete shipment" aria-label={`Delete shipment for ${shipment.client}`} onClick={() => setConfirmDelete(shipment.id)}><Trash2 size={16} /></Button>}
                              </>
                            )}
                          </td>
                        </tr>
                        {expanded === shipment.id && shipment.requirements && (
                          <tr className="export-note-row"><td colSpan={16}><ClientRequirementsNote note={shipment.requirements} /></td></tr>
                        )}
                      </Fragment>
                    )
                  })}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {dialog && (
        <ShipmentDialog
          shipment={dialog === 'new' ? undefined : dialog}
          countries={countries}
          canDelete={isAdmin}
          onClose={() => setDialog(null)}
          onCreate={async payload => {
            const created = await api.createShipment(payload)
            setDialog(null)
            await load({ ...filters, q: query.trim() || undefined })
            notify(`Shipment for ${created.client} added.`)
          }}
          onUpdate={async (id, payload) => {
            replace(await api.updateShipment(id, payload))
            setDialog(null)
            notify('Shipment updated.')
          }}
          onDelete={async target => {
            await api.deleteShipment(target.id)
            setShipments(current => current?.filter(row => row.id !== target.id) ?? null)
            setDialog(null)
            notify(`Shipment for ${target.client} deleted.`)
          }}
        />
      )}
    </div>
  )
}
