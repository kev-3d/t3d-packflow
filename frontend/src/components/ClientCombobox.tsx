import { findClient, matchClients } from '../manualOrder'
import type { Client } from '../types'
import { SearchCombobox, type ComboboxInputProps } from './SearchCombobox'

interface Props extends ComboboxInputProps {
  value: string
  clients: Client[]
  onChange: (text: string) => void
  onSelect: (client: Client) => void
}

const details = (client: Client) => [client.country, client.contact_name].filter(Boolean).join(' · ')

/** Customer field with a dropdown of saved clients; orders are linked to clients by this exact name. */
export function ClientCombobox({ value, clients, ...props }: Props) {
  return <SearchCombobox {...props} value={value} options={matchClients(clients, value)}
    matched={clients.length ? Boolean(findClient(clients, value)) : null}
    optionKey={client => client.id} optionLabel={client => client.customer} optionMeta={details} listLabel="Saved clients"
    matchText={{ label: 'Known client', title: 'Matches a saved client' }}
    missingText={{ label: 'New client', title: 'Not in Clients — check the spelling or pick from the list. New clients are allowed.' }} />
}
