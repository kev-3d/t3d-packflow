import type { Feature, FeatureCollection, MultiPolygon, Polygon, Position } from 'geojson'
import { numericToAlpha2 } from 'i18n-iso-countries'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, GeoJSON, MapContainer, Pane, Tooltip, useMap } from 'react-leaflet'
import { feature } from 'topojson-client'
import type { GeometryCollection, Topology } from 'topojson-specification'
import countries50m from 'world-atlas/countries-50m.json'
import type { Client } from '../types'
import './clients-map.css'

type CountryShape = Feature<Polygon | MultiPolygon, { name: string }>

interface CountryGroup {
  code: string
  name: string
  center: L.LatLngTuple
  bounds: L.LatLngBounds
  clients: Client[]
}

// Natural Earth 1:50m country shapes (public domain), bundled so the map needs no external service.
const topology = countries50m as unknown as Topology<{ countries: GeometryCollection<{ name: string }> }>
const ANTARCTICA = '010'

/** Rings that cross the antimeridian jump from +180 to -180; keep them continuous so Leaflet does not draw a band across the world. */
function unwrapRing(ring: Position[]): Position[] {
  let shift = 0
  return ring.map(([lng, lat], index) => {
    if (index > 0) {
      const previous = ring[index - 1][0]
      if (lng - previous > 180) shift -= 360
      else if (previous - lng > 180) shift += 360
    }
    return [lng + shift, lat]
  })
}

function unwrap(shape: CountryShape): CountryShape {
  const geometry = shape.geometry.type === 'Polygon'
    ? { type: 'Polygon' as const, coordinates: shape.geometry.coordinates.map(unwrapRing) }
    : { type: 'MultiPolygon' as const, coordinates: shape.geometry.coordinates.map((polygon) => polygon.map(unwrapRing)) }
  return { ...shape, geometry }
}

const shapes = (feature(topology, topology.objects.countries) as FeatureCollection<Polygon | MultiPolygon, { name: string }>).features
  .filter((shape) => shape.id !== ANTARCTICA)
  .map((shape) => unwrap(shape as CountryShape))

function ringArea(ring: Position[]) {
  let sum = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) sum += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]
  return sum / 2
}

function ringCentroid(ring: Position[]): L.LatLngTuple {
  const area = ringArea(ring)
  if (!area) return [ring[0][1], ring[0][0]]
  let x = 0, y = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const cross = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]
    x += (ring[j][0] + ring[i][0]) * cross
    y += (ring[j][1] + ring[i][1]) * cross
  }
  return [y / (6 * area), x / (6 * area)]
}

/** Centre of the country's largest landmass, so the US marker sits on the mainland, not between it and Alaska. */
function mainCenter(shape: CountryShape): L.LatLngTuple {
  const polygons = shape.geometry.type === 'Polygon' ? [shape.geometry.coordinates] : shape.geometry.coordinates
  const largest = polygons.reduce((best, polygon) => Math.abs(ringArea(polygon[0])) > Math.abs(ringArea(best[0])) ? polygon : best)
  return ringCentroid(largest[0])
}

const shapesByCode = new Map<string, { shape: CountryShape; center: L.LatLngTuple; bounds: L.LatLngBounds }>()
for (const shape of shapes as CountryShape[]) {
  const code = shape.id != null ? numericToAlpha2(String(shape.id)) : undefined
  if (code && !shapesByCode.has(code)) shapesByCode.set(code, { shape, center: mainCenter(shape), bounds: L.geoJSON(shape).getBounds() })
}

const SCALE = [
  { min: 25, color: '#174746', label: '25+' },
  { min: 10, color: '#256967', label: '10–24' },
  { min: 5, color: '#4E8F8C', label: '5–9' },
  { min: 2, color: '#8DBDB9', label: '2–4' },
  { min: 1, color: '#C6E0DD', label: '1' },
]
const EMPTY_FILL = '#E9E9E6'
const fillFor = (count: number) => SCALE.find((step) => count >= step.min)?.color ?? EMPTY_FILL

const WORLD_BOUNDS = L.latLngBounds([-58, -170], [78, 180])

function FitTo({ bounds }: { bounds: L.LatLngBounds | null }) {
  const map = useMap()
  useEffect(() => {
    map.flyToBounds(bounds ?? WORLD_BOUNDS, { padding: [24, 24], maxZoom: bounds ? 6 : 2, duration: 0.6 })
  }, [map, bounds])
  return null
}

export default function ClientsMap({ clients }: { clients: Client[] }) {
  const [selected, setSelected] = useState<string | null>(null)

  const groups = useMemo(() => {
    const byCode = new Map<string, CountryGroup>()
    for (const client of clients) {
      const code = client.country_code
      const place = code ? shapesByCode.get(code) : undefined
      if (!code || !place) continue
      const group = byCode.get(code) ?? { code, name: place.shape.properties.name, center: place.center, bounds: place.bounds, clients: [] }
      group.clients.push(client)
      byCode.set(code, group)
    }
    return [...byCode.values()].sort((a, b) => b.clients.length - a.clients.length || a.name.localeCompare(b.name))
  }, [clients])

  const located = groups.reduce((sum, group) => sum + group.clients.length, 0)
  const unlocated = clients.length - located
  const counts = useMemo(() => new Map(groups.map((group) => [group.code, group.clients.length])), [groups])
  const selectedGroup = groups.find((group) => group.code === selected) ?? null
  const maxCount = groups[0]?.clients.length ?? 0

  // A search that hides the selected country drops the selection instead of zooming to an empty place.
  useEffect(() => { if (selected && !counts.has(selected)) setSelected(null) }, [counts, selected])

  const geoData = useMemo<FeatureCollection>(() => ({ type: 'FeatureCollection', features: shapes }), [])
  const styleKey = useMemo(() => [...counts].map(([code, count]) => `${code}${count}`).join() + selected, [counts, selected])

  return (
    <div className="clients-map">
      <div className="clients-map__stage">
        <MapContainer
          className="clients-map__canvas"
          bounds={WORLD_BOUNDS}
          minZoom={1}
          maxZoom={8}
          worldCopyJump={false}
          maxBounds={[[-85, -200], [85, 200]]}
          attributionControl={false}
          zoomSnap={0.25}
        >
          <GeoJSON
            key={styleKey}
            data={geoData}
            style={(shape) => {
              const code = shape?.id != null ? numericToAlpha2(String(shape.id)) : undefined
              const count = (code && counts.get(code)) || 0
              const active = code === selected
              return { fillColor: fillFor(count), fillOpacity: 1, color: active ? '#171717' : '#FFFFFF', weight: active ? 1.5 : 0.6 }
            }}
            onEachFeature={(shape, layer) => {
              const code = shape.id != null ? numericToAlpha2(String(shape.id)) : undefined
              if (!code || !counts.has(code)) return
              layer.on('click', () => setSelected((current) => current === code ? null : code))
            }}
          />
          {/* Markers live in their own pane so the country layer, re-created on every selection, never covers them. */}
          <Pane name="client-markers" style={{ zIndex: 450 }}>
          {groups.map((group) => (
            <CircleMarker
              key={group.code}
              center={group.center}
              radius={5 + 13 * Math.sqrt(group.clients.length / Math.max(maxCount, 1))}
              pathOptions={{ color: '#FFFFFF', weight: 1.5, fillColor: group.code === selected ? '#9A4613' : '#171717', fillOpacity: 0.85 }}
              eventHandlers={{ click: () => setSelected((current) => current === group.code ? null : group.code) }}
            >
              <Tooltip direction="top" offset={[0, -6]}>
                <strong>{group.name}</strong> · {group.clients.length} {group.clients.length === 1 ? 'client' : 'clients'}
              </Tooltip>
            </CircleMarker>
          ))}
          </Pane>
          <FitTo bounds={selectedGroup?.bounds ?? null} />
        </MapContainer>
        <div className="clients-map__legend" aria-hidden="true">
          <span>Clients per country</span>
          <div>{[...SCALE].reverse().map((step) => <i key={step.label} style={{ background: step.color }}>{step.label}</i>)}</div>
        </div>
        <p className="clients-map__attribution">Map data: Natural Earth</p>
      </div>

      <aside className="clients-map__side" aria-label="Clients by country">
        <div className="clients-map__summary">
          <strong>{located}</strong> on the map · {groups.length} {groups.length === 1 ? 'country' : 'countries'}
          {unlocated > 0 && <span>{unlocated} without a recognised country are not shown</span>}
        </div>
        {selectedGroup ? (
          <div className="clients-map__detail">
            <button type="button" className="clients-map__back" onClick={() => setSelected(null)}>← All countries</button>
            <h3>{selectedGroup.name} <b>{selectedGroup.clients.length}</b></h3>
            <ul>
              {selectedGroup.clients.map((client) => (
                <li key={client.id}>
                  <strong title={client.customer}>{client.customer}</strong>
                  {client.contact_name && client.contact_name !== client.customer && <span>{client.contact_name}</span>}
                </li>
              ))}
            </ul>
          </div>
        ) : groups.length ? (
          <ol className="clients-map__ranking">
            {groups.map((group) => (
              <li key={group.code}>
                <button type="button" onClick={() => setSelected(group.code)}>
                  <span>{group.name}</span>
                  <i style={{ width: `${(group.clients.length / Math.max(maxCount, 1)) * 100}%` }} />
                  <b>{group.clients.length}</b>
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className="clients-map__empty">No clients with a recognised country match this search.</p>
        )}
      </aside>
    </div>
  )
}
