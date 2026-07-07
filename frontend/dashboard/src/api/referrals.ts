import { apiClient } from './client'

export interface ReferralPatient {
  id?: string
  name: string
  phone: string
  sex?: string | null
  year_of_birth?: number | null
}

export interface Referral {
  id: string
  code: string
  status: string
  created_at: string | null
  expires_at: string | null
  reason: string | null
  from_facility: string | null
  to_facility: string | null
  patient: ReferralPatient
  consent_required: boolean
  clinical_summary?: Record<string, unknown>
  visit_notes?: { id: string; note: Record<string, string>; created_at: string | null; facility: string | null }[]
  message?: string
}

export interface CreateReferralPayload {
  patient: { name: string; phone: string; sex?: string; year_of_birth?: number }
  reason?: string
  clinical_summary?: Record<string, string>
  to_facility_id?: string
}

export interface CreateReferralResult {
  id: string
  code: string
  retrieve_path: string
  whatsapp_delivered: boolean
  patient: { name: string; phone: string }
  to_facility: string | null
  expires_at: string
}

export async function createReferral(payload: CreateReferralPayload): Promise<CreateReferralResult> {
  return (await apiClient.post('/referrals', payload)).data
}

export async function searchReferrals(params: { q?: string; phone?: string }): Promise<{ count: number; results: Referral[] }> {
  return (await apiClient.get('/referrals/search', { params })).data
}

export async function getReferralByCode(code: string, reason?: string): Promise<Referral> {
  return (await apiClient.get(`/referrals/by-code/${encodeURIComponent(code)}`, { params: reason ? { reason } : {} })).data
}

export async function requestPatientOtp(phone: string): Promise<{ message: string }> {
  return (await apiClient.post('/referrals/lookup/otp/request', { phone })).data
}

export async function verifyPatientOtp(phone: string, otp: string): Promise<{ count: number; results: Referral[] }> {
  return (await apiClient.post('/referrals/lookup/otp/verify', { phone, otp })).data
}

export async function addVisitNote(
  referralId: string,
  note: { diagnosis?: string; action?: string; follow_up?: string; notes?: string },
): Promise<{ ok: boolean }> {
  return (await apiClient.post(`/referrals/${referralId}/visit-note`, note)).data
}
