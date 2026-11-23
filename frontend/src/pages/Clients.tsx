import {
  columnVisibilityFeature,
  createColumnHelper,
  createPaginatedRowModel,
  createSortedRowModel,
  rowPaginationFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_text,
  tableFeatures,
  useTable,
  type SortingState,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown, ClipboardList, FileSpreadsheet, LoaderCircle, Map as MapIcon, Pencil, Search, StickyNote, Table2, Trash2 } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { DeleteClientsDialog, EditClientDialog } from '../components/ClientDialogs'
import { ClientRequirements } from '../components/ClientRequirements'
import { WordmarkPagination } from '../components/WordmarkPagination'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table'
import type { Client, ClientInput, ClientRequirement } from '../types'

// Leaflet and the country shapes are only downloaded when the map view is opened.
const ClientsMap = lazy(() => import('../components/ClientsMap'))

const features = tableFeatures({
  columnVisibilityFeature,
  rowPaginationFeature,
  rowSortingFeature,
  paginatedRowModel: createPaginatedRowModel(),
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, text: sortFn_text },
})

const columnHelper = createColumnHelper<typeof features, Client>()

const sortableHeader = (label: string) => function SortableHeader({ column }: { column: { getIsSorted: () => false | 'asc' | 'desc'; toggleSorting: (desc?: boolean) => void } }) {
  const sorted = column.getIsSorted()
  const Icon = sorted === 'asc' ? ArrowUp : sorted === 'desc' ? ArrowDown : ArrowUpDown
  return (
    <Button variant="ghost" size="sm" className="-ml-3 h-8" onClick={() => column.toggleSorting(sorted === 'asc')}>
      {label}<Icon className="size-3.5" aria-hidden="true" />
    </Button>
  )
}

const empty = <span className="text-muted-foreground">—</span>

const buildDataColumns = (noteClientIds: ReadonlySet<number>) => [
  columnHelper.accessor('customer', {
    header: sortableHeader('Customer'),
    cell: ({ row }) => (
      <span className="client-name-cell">
        <span className="block max-w-[240px] truncate font-semibold text-foreground" title={row.original.customer}>{row.original.customer}</span>
        {noteClientIds.has(row.original.id) && <StickyNote className="client-note-icon" size={15} aria-label="Has special requirements" role="img"><title>Has special requirements</title></StickyNote>}
      </span>
    ),
  }),
  columnHelper.accessor('contact_name', {
    header: sortableHeader('Contact name'),
    cell: ({ row }) => row.original.contact_name
      ? <span className="block max-w-[200px] truncate" title={row.original.contact_name}>{row.original.contact_name}</span>
      : empty,
  }),
  columnHelper.accessor('phone_number', {
    header: 'Phone number',
    enableSorting: false,
    cell: ({ row }) => row.original.phone_number
      ? <a className="hover:underline" href={`tel:${row.original.phone_number}`}>{row.original.phone_number}</a>
      : empty,
  }),
  columnHelper.accessor('email', {
    header: sortableHeader('Email'),
    cell: ({ row }) => row.original.email
      ? <a className="lowercase hover:underline" href={`mailto:${row.original.email}`}>{row.original.email}</a>
      : empty,
  }),
  columnHelper.accessor('country', {
    header: sortableHeader('Country'),
    cell: ({ row }) => row.original.country || empty,
  }),
]

const buildColumns = (onEdit: (client: Client) => void, onDelete: (client: Client) => void, noteClientIds: ReadonlySet<number>) => columnHelper.columns([
  ...buildDataColumns(noteClientIds),
  columnHelper.display({
    id: 'actions',
    header: () => <span className="sr-only">Actions</span>,
    enableSorting: false,
    cell: ({ row }) => (
      <div className="order-actions client-actions">
        <Button variant="ghost" size="icon" className="order-action" title="Edit client" aria-label={`Edit ${row.original.customer}`} onClick={() => onEdit(row.original)}><Pencil size={15} /></Button>
        <Button variant="ghost" size="icon" className="order-action order-action--delete" title="Delete client" aria-label={`Delete ${row.original.customer}`} onClick={() => onDelete(row.original)}><Trash2 size={16} /></Button>
      </div>
    ),
  }),
])

export function Clients({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const [clients, setClients] = useState<Client[]>([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'table' | 'map' | 'requirements'>('table')
  const [requirements, setRequirements] = useState<ClientRequirement[]>([])
  const [requirementsLoading, setRequirementsLoading] = useState(true)
  const [sorting, setSorting] = useState<SortingState>([{ id: 'customer', desc: false }])
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 10 })
  const fileRef = useRef<HTMLInputElement>(null)
  const [editing, setEditing] = useState<Client | null>(null)
  // `true` confirms deleting every client; a Client confirms deleting only that one.
  const [deleteTarget, setDeleteTarget] = useState<Client | true | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  // Clients that have a packing note get a small marker next to their name.
  const noteClientIds = useMemo(() => new Set(requirements.flatMap((requirement) => requirement.client_id === null ? [] : [requirement.client_id])), [requirements])
  const columns = useMemo(() => buildColumns(setEditing, (client) => { setDeleteError(''); setDeleteTarget(client) }, noteClientIds), [noteClientIds])

  const load = useCallback(async () => {
    try { setClients(await api.clients()) }
    catch (error) { notify(error instanceof Error ? error.message : 'Clients could not be loaded.', true) }
    finally { setLoading(false) }
  }, [notify])

  const loadRequirements = useCallback(async () => {
    try { setRequirements(await api.clientRequirements()) }
    catch (error) { notify(error instanceof Error ? error.message : 'Special requirements could not be loaded.', true) }
    finally { setRequirementsLoading(false) }
  }, [notify])

  useEffect(() => { load(); loadRequirements() }, [load, loadRequirements])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return clients
    return clients.filter((client) =>
      `${client.customer} ${client.contact_name} ${client.email} ${client.phone_number} ${client.country}`.toLowerCase().includes(needle))
  }, [clients, query])

  const table = useTable({
    features,
    data: filtered,
    columns,
    getRowId: (row) => String(row.id),
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    state: { sorting, pagination },
  })

  const importFile = async (file?: File) => {
    if (!file) return
    setImporting(true)
    try {
      const result = await api.importClients(file)
      notify(`${result.created} clients added, ${result.updated} updated${result.errors.length ? ` · ${result.errors.length} rows skipped` : ''}.`, result.errors.length > 0 && result.imported === 0)
      await load()
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The file could not be imported.', true)
    } finally {
      setImporting(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const saveClient = async (payload: ClientInput) => {
    if (!editing) return
    const updated = await api.updateClient(editing.id, payload)
    setClients((current) => current.map((client) => client.id === updated.id ? updated : client))
    loadRequirements() // a renamed client changes the name shown for its linked requirement
    setEditing(null)
    notify(`${updated.customer} updated.`)
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    setDeleteError('')
    try {
      if (deleteTarget === true) {
        const result = await api.deleteAllClients()
        setClients([])
        notify(`${result.deleted_clients} clients deleted.`)
      } else {
        await api.deleteClient(deleteTarget.id)
        setClients((current) => current.filter((client) => client.id !== deleteTarget.id))
        notify(`${deleteTarget.customer} deleted.`)
      }
      loadRequirements() // deleting a client unlinks its requirements but keeps them
      setDeleteTarget(null)
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : 'The client could not be deleted. Please try again.')
    } finally { setDeleting(false) }
  }

  // Keep the current page valid after rows disappear (e.g. deleting the last client on the last page).
  const lastPageIndex = Math.max(Math.ceil(filtered.length / pagination.pageSize) - 1, 0)
  useEffect(() => {
    if (pagination.pageIndex > lastPageIndex) setPagination((current) => ({ ...current, pageIndex: lastPageIndex }))
  }, [lastPageIndex, pagination.pageIndex])

  const rows = table.getRowModel().rows
  const pageCount = Math.max(table.getPageCount(), 1)

  return (
    <div className="page list-page clients-page">
      <header className="page-header">
        <div><span className="eyebrow">MASTER DATA / CLIENTS</span><h1>Clients</h1><p>Customer contacts and shipping details, imported from Excel.</p></div>
        <div className="catalog-health"><span>CLIENT DIRECTORY</span><strong>{clients.length} records</strong></div>
      </header>
      {view !== 'requirements' && <p className="results-count" role="status">{filtered.length} of {clients.length} clients</p>}
      <div className="catalog-tabs" role="group" aria-label="Clients view">
        <button aria-pressed={view === 'table'} className={ view === 'table' ? 'active' : ''} onClick={() => setView('table')}><Table2 size={18} /> Table</button>
        <button aria-pressed={view === 'map'} className={view === 'map' ? 'active' : ''} onClick={() => setView('map')}><MapIcon size={18} /> Map</button>
        <button aria-pressed={view === 'requirements'} className={view === 'requirements' ? 'active' : ''} onClick={() => setView('requirements')}><ClipboardList size={18} /> Special requirements <b>{requirements.length}</b></button>
      </div>
      <section className="panel data-panel">
        {view === 'requirements' ? (
          <ClientRequirements requirements={requirements} clients={clients} loading={requirementsLoading} reload={loadRequirements} notify={notify} />
        ) : <>
        <div className="table-tools">
          <label><Search size={17} /><Input aria-label="Search clients" type="search" value={query} onChange={(event) => { setQuery(event.target.value); table.setPageIndex(0) }} placeholder="Search customer, contact, email or country…" /></label>
          <div>
            <input ref={fileRef} type="file" accept=".xlsx,.xlsm" hidden onChange={(event) => importFile(event.target.files?.[0])} />
            <button disabled={importing} onClick={() => fileRef.current?.click()}>
              {importing ? <LoaderCircle size={16} className="spin" /> : <FileSpreadsheet size={16} />} {importing ? 'Importing…' : 'Import Excel'}
            </button>
            <button className="clients-delete-all" disabled={!clients.length || importing} onClick={() => { setDeleteError(''); setDeleteTarget(true) }}>
              <Trash2 size={16} /> Delete all
            </button>
          </div>
        </div>
        {view === 'map' ? (
          <Suspense fallback={<p className="clients-map__loading" role="status">Loading map…</p>}>
            {loading ? <p className="clients-map__loading" role="status">Loading clients…</p> : <ClientsMap clients={filtered} />}
          </Suspense>
        ) : <>
        <div className="clients-table">
          <Table aria-label="Clients list">
            <TableHeader>
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id}>
                  {headerGroup.headers.map((header) => (
                    <TableHead key={header.id}>{header.isPlaceholder ? null : <table.FlexRender header={header} />}</TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {rows.length ? rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => <TableCell key={cell.id}><table.FlexRender cell={cell} /></TableCell>)}
                </TableRow>
              )) : (
                <TableRow>
                  <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
                    {loading ? 'Loading clients…' : clients.length ? 'No matches. Try a different search.' : 'No clients yet. Import an Excel file with a Customer column to get started.'}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
        <div className="clients-pagination clients-pagination--wordmark">
          <WordmarkPagination label="Clients pages" pageIndex={pagination.pageIndex} pageCount={pageCount} onPageChange={(page) => table.setPageIndex(page)} />
        </div>
        </>}
        </>}
      </section>
      {/*<p className="clients-hint">Excel columns: Customer (required), Phone Number, Email, Contact Name, Address, Country, Shipping Address. Rows with an existing customer name update that client.</p>*/}
      {editing && <EditClientDialog client={editing} onClose={() => setEditing(null)} onSave={saveClient} />}
      {deleteTarget && <DeleteClientsDialog client={deleteTarget === true ? undefined : deleteTarget} count={clients.length} busy={deleting} error={deleteError} onClose={() => setDeleteTarget(null)} onConfirm={confirmDelete} />}
    </div>
  )
}
