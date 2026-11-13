import { columnVisibilityFeature, createColumnHelper, tableFeatures, useTable } from '@tanstack/react-table'
import { Trash2 } from 'lucide-react'
import { useMemo, useRef } from 'react'
import { fieldId, type DraftItem, type ReviewIssue } from '../review'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'

const NA = 'N.A'
const INTERNAL_CODE = /^PRODUCT-[0-9A-F]{10}$/
const features = tableFeatures({ columnVisibilityFeature })
const columnHelper = createColumnHelper<typeof features, DraftItem>()

interface Props {
  items: DraftItem[]
  issues: ReviewIssue[]
  busy: boolean
  onChange: (index: number, patch: Partial<DraftItem>) => void
  onRemove: (index: number) => void
}

type NumericField = 'quantity' | 'unit_price' | 'amount'

/** Editable shadcn data table for the order's line items. Empty values show "N.A". */
export function ReviewProductsTable({ items, issues, busy, onChange, onRemove }: Props) {
  // Columns are built once so cell inputs keep focus while typing; the latest props are read from this ref.
  const latest = useRef({ items, issues, busy, onChange, onRemove })
  latest.current = { items, issues, busy, onChange, onRemove }

  const columns = useMemo(() => {
    const invalid = (index: number, field: string) => latest.current.issues.some(issue => issue.kind === 'error' && issue.field === `items.${index}.${field}`)
    const inputProps = (index: number, field: string) => ({
      id: fieldId(`items.${index}.${field}`),
      'aria-invalid': invalid(index, field) || undefined,
      disabled: latest.current.busy,
    })
    const setNumber = (index: number, field: NumericField, value: string) => {
      const item = latest.current.items[index]
      const patch: Partial<DraftItem> = { [field]: Number(value) }
      if (field !== 'amount') {
        const next = { ...item, ...patch }
        patch.amount = next.quantity * next.unit_price * (1 - (next.discount_percent || 0) / 100)
      }
      latest.current.onChange(index, patch)
    }
    return columnHelper.columns([
      columnHelper.display({
        id: 'description',
        header: 'Description',
        cell: ({ row }) => <Input {...inputProps(row.index, 'description')} aria-label={`Description, line ${row.index + 1}`} placeholder={NA} value={row.original.description} onChange={e => latest.current.onChange(row.index, { description: e.target.value })} />,
      }),
      columnHelper.display({
        id: 'quantity',
        header: 'Quantity',
        cell: ({ row }) => <Input {...inputProps(row.index, 'quantity')} aria-label={`Quantity, line ${row.index + 1}`} type="number" min="1" step="1" placeholder={NA} value={row.original.quantity || ''} onChange={e => setNumber(row.index, 'quantity', e.target.value)} />,
      }),
      columnHelper.display({
        id: 'unit_price',
        header: 'Unit price',
        cell: ({ row }) => <Input {...inputProps(row.index, 'unit_price')} aria-label={`Unit price, line ${row.index + 1}`} type="number" min="0" step="0.01" placeholder={NA} value={row.original.unit_price || ''} onChange={e => setNumber(row.index, 'unit_price', e.target.value)} />,
      }),
      columnHelper.display({
        id: 'amount',
        header: 'Line amount',
        cell: ({ row }) => <Input {...inputProps(row.index, 'amount')} aria-label={`Line amount, line ${row.index + 1}`} type="number" min="0" step="0.01" placeholder={NA} value={row.original.amount ? Number(row.original.amount.toFixed(2)) : ''} onChange={e => setNumber(row.index, 'amount', e.target.value)} />,
      }),
      columnHelper.display({
        id: 'sku',
        header: 'SKU',
        cell: ({ row }) => {
          const catalogSku = row.original.sku?.trim()
          // Catalog records without a real SKU get a generated internal code; that is not shown as a SKU.
          const sku = row.original.sku_raw?.trim() || (catalogSku && !INTERNAL_CODE.test(catalogSku) ? catalogSku : '')
          return sku ? <span className="review-products-sku">{sku}</span> : <span className="review-products-na">{NA}</span>
        },
      }),
      columnHelper.display({
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => <Button type="button" variant="ghost" size="icon" className="row-delete-button" disabled={latest.current.busy} title="Remove line" aria-label={`Remove line ${row.index + 1}`} onClick={() => latest.current.onRemove(row.index)}><Trash2 size={16} /></Button>,
      }),
    ])
  }, [])

  const table = useTable({ features, data: items, columns, getRowId: (_row, index) => String(index) })
  const rows = table.getRowModel().rows

  return (
    <div className="review-products-table">
      <Table aria-label="Products to pack">
        <TableHeader>
          {table.getHeaderGroups().map(headerGroup => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map(header => <TableHead key={header.id} className={`review-products-col--${header.column.id}`}>{header.isPlaceholder ? null : <table.FlexRender header={header} />}</TableHead>)}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {rows.length ? rows.map(row => (
            <TableRow key={row.id}>
              {row.getVisibleCells().map(cell => <TableCell key={cell.id} className={`review-products-col--${cell.column.id}`}><table.FlexRender cell={cell} /></TableCell>)}
            </TableRow>
          )) : (
            <TableRow><TableCell colSpan={columns.length} className="h-20 text-center text-muted-foreground">No line items. Add at least one product.</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
}
