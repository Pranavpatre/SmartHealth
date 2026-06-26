import { Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { useAuthStore } from './stores/authStore'
import OfflineBanner from './components/OfflineBanner'
import BottomNav from './components/BottomNav'
import LoginPage from './pages/LoginPage'
import DailyEntryPage from './pages/DailyEntryPage'
import StockEntryPage from './pages/StockEntryPage'
import NotificationsPage from './pages/NotificationsPage'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function AppLayout() {
  return (
    <div className="pb-16">
      <Outlet />
    </div>
  )
}

export default function App() {
  return (
    <>
      <OfflineBanner />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Navigate to="/daily" replace />} />
          <Route path="daily" element={<DailyEntryPage />} />
          <Route path="stock" element={<StockEntryPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
        </Route>
        {/* Catch-all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <BottomNav />
    </>
  )
}
