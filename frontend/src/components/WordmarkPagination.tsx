import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { MouseEvent } from 'react'
import { Pagination, PaginationContent, PaginationItem, PaginationLink } from './ui/pagination'

/** Pages shown at once: wide screens get ten, phones five (the extra ones are hidden by CSS). */
const WIDE_WINDOW = 10
const COMPACT_WINDOW = 5

function pageWindow(current: number, count: number, size: number) {
  const length = Math.min(size, count)
  const start = Math.min(Math.max(current - Math.floor(size / 2), 0), count - length)
  return { start, end: start + length - 1 }
}

// Reset the shadcn button look so each link is a bare column: letter on top, label below.
const columnLink = 'size-auto h-auto min-w-0 flex-col gap-1 rounded-md border-0 bg-transparent px-1 py-1 shadow-none hover:bg-transparent focus-visible:ring-2'

interface Props {
  /** Zero-based current page. */
  pageIndex: number
  pageCount: number
  onPageChange: (pageIndex: number) => void
  label: string
}

/**
 * Pagination drawn as the PackFlow wordmark with one "o" per page ("PackFloooow"), each page number
 * under its letter. The current page's "o" is orange; Previous/Next sit at both ends.
 */
export function WordmarkPagination({ pageIndex, pageCount, onPageChange, label }: Props) {
  const count = Math.max(pageCount, 1)
  const current = Math.min(Math.max(pageIndex, 0), count - 1)
  const wide = pageWindow(current, count, WIDE_WINDOW)
  const compact = pageWindow(current, count, COMPACT_WINDOW)
  const pages = Array.from({ length: wide.end - wide.start + 1 }, (_, offset) => wide.start + offset)

  const go = (target: number) => (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()
    if (target !== current && target >= 0 && target < count) onPageChange(target)
  }

  return (
    <Pagination aria-label={label} className="wordmark-pagination">
      <p className="sr-only" role="status">Page {current + 1} of {count}</p>
      <PaginationContent className="wordmark-pagination__row gap-0">
        <PaginationItem className="wordmark-pagination__edge">
          {current > 0 && (
            <PaginationLink href={`#page-${current}`} onClick={go(current - 1)} className={columnLink} aria-label="Go to previous page">
              <span className="wordmark-pagination__glyph" aria-hidden="true"><ChevronLeft /></span>
              <span className="wordmark-pagination__label">Previous</span>
            </PaginationLink>
          )}
        </PaginationItem>
        <PaginationItem className="wordmark-pagination__static" aria-hidden="true">
          <span className="wordmark-pagination__letters">PackFl</span>
          <span className="wordmark-pagination__label">&nbsp;</span>
        </PaginationItem>
        {pages.map((page) => {
          const active = page === current
          return (
            <PaginationItem key={page} data-compact-hidden={page < compact.start || page > compact.end || undefined}>
              <PaginationLink
                href={`#page-${page + 1}`}
                onClick={go(page)}
                isActive={active}
                aria-label={active ? `Page ${page + 1}, current page` : `Go to page ${page + 1}`}
                className={columnLink}
              >
                <span className={`wordmark-pagination__o ${active ? 'is-current' : ''}`} aria-hidden="true">o</span>
                <span className={`wordmark-pagination__label ${active ? 'is-current' : ''}`}>{page + 1}</span>
              </PaginationLink>
            </PaginationItem>
          )
        })}
        <PaginationItem className="wordmark-pagination__static" aria-hidden="true">
          <span className="wordmark-pagination__letters">w</span>
          <span className="wordmark-pagination__label">&nbsp;</span>
        </PaginationItem>
        <PaginationItem className="wordmark-pagination__edge">
          {current < count - 1 && (
            <PaginationLink href={`#page-${current + 2}`} onClick={go(current + 1)} className={columnLink} aria-label="Go to next page">
              <span className="wordmark-pagination__glyph" aria-hidden="true"><ChevronRight /></span>
              <span className="wordmark-pagination__label">Next</span>
            </PaginationLink>
          )}
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  )
}
