import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/authStore'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import FacilitiesPage from './pages/FacilitiesPage'
import FacilityDetailPage from './pages/FacilityDetailPage'
import RedistributionPage from './pages/RedistributionPage'
import AssistantPage from './pages/AssistantPage'
import MyFacilityPage from './pages/MyFacilityPage'
import Layout from './components/Layout'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function IndexRedirect() {
  // PHC_ADMIN is scoped to a single facility and can't query the
  // district-wide dashboard endpoints — land them on their facility view.
  const role = useAuthStore((s) => s.role)
  return <Navigate to={role === 'PHC_ADMIN' ? '/my-facility' : '/dashboard'} replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<IndexRedirect />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="facilities" element={<FacilitiesPage />} />
        <Route path="facilities/:id" element={<FacilityDetailPage />} />
        <Route path="redistribution" element={<RedistributionPage />} />
        <Route path="assistant" element={<AssistantPage />} />
        <Route path="my-facility" element={<MyFacilityPage />} />
      </Route>
    </Routes>
  )
}
