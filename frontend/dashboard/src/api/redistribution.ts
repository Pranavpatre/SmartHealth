import { apiClient } from './client'

export interface RedistributionPlan {
  id: string
  status: 'PENDING' | 'APPROVED' | 'DEFERRED'
  total_items: number
  estimated_saving_inr: number
  created_at: string
  line_items: LineItem[]
}

export interface LineItem {
  id: string
  from_facility_name: string
  to_facility_name: string
  medicine_name: string
  quantity: number
  distance_km: number
  urgency: string
  estimated_saving_inr: number
}

// ── Raw backend shapes (RedistributionPlanResponse / LineItemResponse) ───────
interface RawItem {
  id: string
  from_facility_name?: string | null
  to_facility_name?: string | null
  medicine_name?: string | null
  quantity: number
  distance_km?: string | number | null
  estimated_saving?: string | number | null
  status?: string
}
interface RawPlan {
  id: string
  status?: string
  generated_at: string
  total_savings?: string | number | null
  items?: RawItem[]
}

const num = (v: unknown): number => (v == null ? 0 : Number(v))

// Map the backend response shape to what the dashboard renders.
function mapPlan(p: RawPlan): RedistributionPlan {
  return {
    id: p.id,
    status: (p.status as RedistributionPlan['status']) ?? 'PENDING',
    total_items: p.items?.length ?? 0,
    estimated_saving_inr: num(p.total_savings),
    created_at: p.generated_at,
    line_items: (p.items ?? []).map((i) => ({
      id: i.id,
      from_facility_name: i.from_facility_name ?? '',
      to_facility_name: i.to_facility_name ?? '',
      medicine_name: i.medicine_name ?? '',
      quantity: i.quantity,
      distance_km: num(i.distance_km),
      urgency: '',
      estimated_saving_inr: num(i.estimated_saving),
    })),
  }
}

// district_id is required for admins (no home district); ignored for scoped users.
const dparams = (districtId?: number) => (districtId ? { district_id: districtId } : {})

export const getPlans = async (districtId?: number) => {
  const { data } = await apiClient.get<{ plans: RawPlan[] }>('/redistribution/plans', {
    params: dparams(districtId),
  })
  return (data.plans ?? []).map(mapPlan)
}

export const createPlan = async (districtId?: number) => {
  const { data } = await apiClient.post<RawPlan>('/redistribution/plans', null, {
    params: dparams(districtId),
  })
  return mapPlan(data)
}

export const approvePlan = async (planId: string, districtId?: number) => {
  const { data } = await apiClient.post<RawPlan>(`/redistribution/plans/${planId}/approve`, null, {
    params: dparams(districtId),
  })
  return mapPlan(data)
}

export const deferPlan = async (planId: string, reason: string, districtId?: number) => {
  const { data } = await apiClient.post<RawPlan>(`/redistribution/plans/${planId}/defer`, { reason }, {
    params: dparams(districtId),
  })
  return mapPlan(data)
}
