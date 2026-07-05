import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { formatNumber } from '../lib/format'
import L from 'leaflet'
import 'leaflet.markercluster'
import type { MapMarker } from '../api/facilities'
import 'leaflet/dist/leaflet.css'
import 'leaflet.markercluster/dist/MarkerCluster.css'
import 'leaflet.markercluster/dist/MarkerCluster.Default.css'

const TRAFFIC_COLORS: Record<string, string> = {
  GREEN: '#15803D',
  YELLOW: '#B85E00',
  RED: '#B91C1C',
}

interface Props {
  markers: MapMarker[]
}

// Imperative cluster layer — adds all markers straight to a Leaflet
// markerClusterGroup (no per-marker React element), so it scales to 50k+ points.
function ClusterLayer({ markers }: { markers: MapMarker[] }) {
  const map = useMap()
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()

  useEffect(() => {
    const valid = markers.filter((m) => Number.isFinite(m.lat) && Number.isFinite(m.lng))
    if (valid.length === 0) return   // skip the initial empty render (avoids invalid bounds)

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const group = (L as any).markerClusterGroup({
      maxClusterRadius: 55,
      showCoverageOnHover: false,
    })

    const layers = valid.map((m) => {
      const color = (m.traffic_light && TRAFFIC_COLORS[m.traffic_light]) || '#6B7280'
      const marker = L.circleMarker([m.lat, m.lng], {
        radius: 6, fillColor: color, color: '#fff', weight: 1.5, fillOpacity: 0.9,
      })
      marker.bindPopup(
        `<strong>${m.name}</strong><br/>${t('map.score')}: ${formatNumber(m.health_score)}/100` +
        `<br/><a href="/facilities/${m.id}" style="color:#0d9488">${t('map.view_detail')}</a>`,
      )
      marker.on('click', () => navigate(`/facilities/${m.id}`))
      return marker
    })
    group.addLayers(layers)   // bulk add — fast + synchronous
    map.addLayer(group)

    const bounds = L.latLngBounds(valid.map((m) => [m.lat, m.lng] as [number, number]))
    if (bounds.isValid()) {
      try { map.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 }) } catch { /* ignore */ }
    }
    return () => {
      try { map.removeLayer(group) } catch { /* ignore */ }
    }
  }, [markers, map, navigate, t, i18n.language])

  return null
}

export default function FacilityMap({ markers }: Props) {
  // Fallback center: geographic centre of India (until bounds are fitted).
  const center: [number, number] = [22.0, 79.0]

  return (
    <MapContainer center={center} zoom={5} style={{ height: '420px', width: '100%', borderRadius: '8px' }}>
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>'
      />
      <ClusterLayer markers={markers} />
    </MapContainer>
  )
}
