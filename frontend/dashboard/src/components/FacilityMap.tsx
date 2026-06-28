import { MapContainer, TileLayer, CircleMarker, Popup } from 'react-leaflet'
import { useNavigate } from 'react-router-dom'
import type { Facility } from '../api/facilities'
import 'leaflet/dist/leaflet.css'

const TRAFFIC_COLORS: Record<string, string> = {
  GREEN: '#15803D',
  YELLOW: '#B85E00',
  RED: '#B91C1C',
}

interface Props {
  facilities: Facility[]
}

export default function FacilityMap({ facilities }: Props) {
  const navigate = useNavigate()
  // Default center: Pune district
  const center: [number, number] = [18.52, 73.86]

  return (
    <MapContainer center={center} zoom={10} style={{ height: '420px', width: '100%', borderRadius: '8px' }}>
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>'
      />
      {facilities
        .filter((f) => Number.isFinite(f.lat) && Number.isFinite(f.lng))
        .map((f) => (
        <CircleMarker
          key={f.id}
          center={[f.lat, f.lng]}
          radius={10}
          pathOptions={{
            fillColor: TRAFFIC_COLORS[f.traffic_light] ?? '#6B7280',
            color: '#fff',
            weight: 2,
            fillOpacity: 0.85,
          }}
          eventHandlers={{ click: () => navigate(`/facilities/${f.id}`) }}
        >
          <Popup>
            <strong>{f.name}</strong>
            <br />
            Score: {f.health_score}/100
            <br />
            Alerts: {f.active_alerts}
            <br />
            <button
              className="text-teal-600 underline text-xs mt-1"
              onClick={() => navigate(`/facilities/${f.id}`)}
            >
              View detail &rarr;
            </button>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  )
}
