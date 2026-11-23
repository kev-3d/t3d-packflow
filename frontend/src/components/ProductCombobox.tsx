import { findCatalogProduct, matchProducts } from '../manualOrder'
import type { Product } from '../types'
import { SearchCombobox, type ComboboxInputProps } from './SearchCombobox'

interface Props extends ComboboxInputProps {
  value: string
  products: Product[]
  onChange: (text: string) => void
  onSelect: (product: Product) => void
}

const price = (product: Product) => product.price_eur > 0 ? `EUR ${product.price_eur.toFixed(2)}` : 'No price'

/** Product field with a catalog dropdown; picking an option fills the exact catalog name so it matches on save. */
export function ProductCombobox({ value, products, ...props }: Props) {
  return <SearchCombobox {...props} value={value} options={matchProducts(products, value)}
    matched={products.length ? Boolean(findCatalogProduct(products, value)) : null}
    optionKey={product => product.id} optionLabel={product => product.name} optionMeta={price} listLabel="Catalog products"
    matchText={{ label: 'In catalog', title: 'Matches a catalog product' }}
    missingText={{ label: 'Not in catalog', title: 'Not found in the catalog — check the spelling or pick from the list' }} />
}
