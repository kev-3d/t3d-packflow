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
import { ArrowDown, ArrowUp, ArrowUpDown, FileSpreadsheet, Link2, LoaderCircle, Pencil, Search, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { Client, ClientRequirement, ClientRequirementInput } from '../types'
import { DeleteRequirementsDialog, EditRequirementDialog, requirementToInput } from './ClientRequirementDialogs'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'
import { WordmarkPagination } from './WordmarkPagination'

const features = tableFeatures({
  columnVisibilityFeature,
  rowPaginationFeature,
  rowSortingFeature,
  paginatedRowModel: createPaginatedRowModel(),
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, text: sortFn_text },
})

const columnHelper = createColumnHelper<typeof features, ClientRequirement>()

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
/** Long free text stays on one line; the full value is in the tooltip and the edit form. */
const clamp = (text: string, width: number) => text
  ? <span className="block truncate" style={{ maxWidth: width }} title={text}>{text.replace(/\s*\n\s*/g, ' · ')}</span>
  : empty

interface Actions {
  onLink: (requirement: ClientRequirement) => void
  onEdit: (requirement: ClientRequirement) => void
  onDelete: (requirement: ClientRequirement) => void
  linkingId: number | null
}

const buildColumns = ({ onLink, onEdit, onDelete, linkingId }: Actions) => columnHelper.columns([
  columnHelper.accessor('company', {
    header: sortableHeader('Company'),
    cell: ({ row }) => <span className="block max-w-[220px] truncate font-semibold text-foreground" title={row.original.company}>{row.original.company}</span>,
  }),
  columnHelper.accessor('client_name', {
    header: sortableHeader('Linked client'),
    sortUndefined: 'last',
    cell: ({ row }) => {
      const { client_name, suggested_client } = row.original
      if (client_name) return <span className="requirements-link"><Link2 size={14} aria-hidden="true" /><span className="truncate" title={client_name}>{client_name}</span></span>
      if (suggested_client) {
        return (
          <span className="requirements-suggestion">
            <span className="truncate" title={`Possible client: ${suggested_client.customer}`}>Possible: {suggested_client.customer}</span>
            <Button variant="outline" size="sm" disabled={linkingId === row.original.id} onClick={() => onLink(row.original)} aria-label={`Link ${row.original.company} to ${suggested_client.customer}`}>
              {linkingId === row.original.id ? <LoaderCircle className="spin" size={13} /> : <Link2 size={13} />}Link
            </Button>
          </span>
        )
      }
      return <span className="text-muted-foreground">Not linked</span>
    },
  }),
  columnHelper.accessor('docs_required', { header: 'Docs required', enableSorting: false, cell: ({ row }) => clamp(row.original.docs_required, 220) }),
  columnHelper.accessor('pricing_requirements', { header: 'Pricing / invoice', enableSorting: false, cell: ({ row }) => clamp(row.original.pricing_requirements, 220) }),
  columnHelper.accessor('courier', { header: 'Courier', enableSorting: false, cell: ({ row }) => clamp(row.original.courier, 160) }),
  columnHelper.accessor('comments', { header: 'Comments', enableSorting: false, cell: ({ row }) => clamp(row.original.comments, 220) }),
  columnHelper.display({
    id: 'actions',
    header: () => <span className="sr-only">Actions</span>,
    cell: ({ row }) => (
      <div className="order-actions client-actions">
        <Button variant="ghost" size="icon" className="order-action" title="Edit requirements" aria-label={`Edit requirements for ${row.original.company}`} onClick={() => onEdit(row.original)}><Pencil size={15} /></Button>
        <Button variant="ghost" size="icon" className="order-action order-action--delete" title="Delete requirements" aria-label={`Delete requirements for ${row.original.company}`} onClick={() => onDelete(row.original)}><Trash2 size={16} /></Button>
      </div>
    ),
  }),
])

interface Props {
  requirements: ClientRequirement[]
  clients: Client[]
  loading: boolean
  reload: () => Promise<void>
  notify: (message: string, error?: boolean) => void
}

export function ClientRequirements({ requirements, clients, loading, reload, notify }: Props) {
  const [query, setQuery] = useState('')
  const [onlyUnlinked, setOnlyUnlinked] = useState(false)
  const [sorting, setSorting] = useState<SortingState>([{ id: 'company', desc: false }])
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 10 })
  const [importing, setImporting] = useState(false)
  const [linkingId, setLinkingId] = useState<number | null>(null)
  const [editing, setEditing] = useState<ClientRequirement | null>(null)
  // `true` confirms deleting every requirement; a ClientRequirement confirms deleting only that one.
  const [deleteTarget, setDeleteTarget] = useState<ClientRequirement | true | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const unlinkedCount = useMemo(() => requirements.filter((requirement) => requirement.client_id === null).length, [requirements])

  const link = async (requirement: ClientRequirement) => {
    if (!requirement.suggested_client) return
    setLinkingId(requirement.id)
    try {
      await api.updateClientRequirement(requirement.id, requirementToInput(requirement, { client_id: requirement.suggested_client.id }))
      await reload()
      notify(`${requirement.company} linked to ${requirement.suggested_client.customer}.`)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The requirement could not be linked.', true)
    } finally { setLinkingId(null) }
  }

  const columns = useMemo(() => buildColumns({
    onLink: link, onEdit: setEditing, onDelete: (requirement) => { setDeleteError(''); setDeleteTarget(requirement) }, linkingId,
  }), [linkingId])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return requirements.filter((requirement) =>
      (!onlyUnlinked || requirement.client_id === null)
      && (!needle || `${requirement.company} ${requirement.client_name ?? ''} ${requirement.docs_required} ${requirement.courier} ${requirement.comments} ${requirement.pricing_requirements}`.toLowerCase().includes(needle)))
  }, [requirements, query, onlyUnlinked])

  const table = useTable({
    features,
    data: filtered,
    columns,
    getRowId: (row) => String(row.id),
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    state: { sorting, pagination },
  })

  // Keep the current page valid after rows disappear (filtering, deleting the last row of a page).
  const lastPageIndex = Math.max(Math.ceil(filtered.length / pagination.pageSize) - 1, 0)
  useEffect(() => {
    if (pagination.pageIndex > lastPageIndex) setPagination((current) => ({ ...current, pageIndex: lastPageIndex }))
  }, [lastPageIndex, pagination.pageIndex])

  const importFile = async (file?: File) => {
    if (!file) return
    setImporting(true)
    try {
      const result = await api.importClientRequirements(file)
      const review = result.suggested + result.unlinked
      notify(`${result.created} added, ${result.updated} updated · ${result.linked} linked to a client${review ? `, ${review} to review` : ''}${result.errors.length ? ` · ${result.errors.length} rows skipped` : ''}.`)
      await reload()
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The file could not be imported.', true)
    } finally {
      setImporting(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const saveRequirement = async (payload: ClientRequirementInput) => {
    if (!editing) return
    await api.updateClientRequirement(editing.id, payload)
    await reload()
    notify(`${payload.company} updated.`)
    setEditing(null)
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    setDeleteError('')
    try {
      if (deleteTarget === true) {
        const result = await api.deleteAllClientRequirements()
        notify(`${result.deleted_requirements} special requirements deleted.`)
      } else {
        await api.deleteClientRequirement(deleteTarget.id)
        notify(`Requirements for ${deleteTarget.company} deleted.`)
      }
      await reload()
      setDeleteTarget(null)
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : 'The requirements could not be deleted. Please try again.')
    } finally { setDeleting(false) }
  }

  const rows = table.getRowModel().rows
  const pageCount = Math.max(table.getPageCount(), 1)

  return (
    <>
      <div className="table-tools">
        <label><Search size={17} /><Input aria-label="Search special requirements" type="search" value={query} onChange={(event) => { setQuery(event.target.value); table.setPageIndex(0) }} placeholder="Search company, docs, courier or comments…" /></label>
        <div>
          <input ref={fileRef} type="file" accept=".xlsx,.xlsm" hidden onChange={(event) => importFile(event.target.files?.[0])} />
          <button aria-pressed={onlyUnlinked} className={onlyUnlinked ? 'is-active' : undefined} disabled={!unlinkedCount && !onlyUnlinked} onClick={() => { setOnlyUnlinked((value) => !value); table.setPageIndex(0) }}>
            Not linked ({unlinkedCount})
          </button>
          <button disabled={importing} onClick={() => fileRef.current?.click()}>
            {importing ? <LoaderCircle size={16} className="spin" /> : <FileSpreadsheet size={16} />} {importing ? 'Importing…' : 'Import requirements'}
          </button>
          <button className="clients-delete-all" disabled={!requirements.length || importing} onClick={() => { setDeleteError(''); setDeleteTarget(true) }}>
            <Trash2 size={16} /> Delete all
          </button>
        </div>
      </div>
      <p className="clients-hint requirements-hint">Orders whose customer matches a company below show these as a packing note. Link each company to its client so the match survives name differences.</p>
      <div className="clients-table">
        <Table aria-label="Special requirements list">
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
                  {loading ? 'Loading special requirements…' : requirements.length ? 'No matches. Try a different search.' : 'No special requirements yet. Import the “Special clients” Excel sheet to get started.'}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <div className="clients-pagination clients-pagination--wordmark">
        <span role="status">{filtered.length} of {requirements.length} companies</span>
        <WordmarkPagination label="Special requirements pages" pageIndex={pagination.pageIndex} pageCount={pageCount} onPageChange={(page) => table.setPageIndex(page)} />
      </div>
      {editing && <EditRequirementDialog requirement={editing} clients={clients} onClose={() => setEditing(null)} onSave={saveRequirement} />}
      {deleteTarget && <DeleteRequirementsDialog requirement={deleteTarget === true ? undefined : deleteTarget} count={requirements.length} busy={deleting} error={deleteError} onClose={() => setDeleteTarget(null)} onConfirm={confirmDelete} />}
    </>
  )
}
