import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { useAuthStore } from './stores/auth'
import AdminLayout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Dimensions from './pages/Dimensions'
import Config from './pages/Config'
import Logs from './pages/Logs'
import Feedback from './pages/Feedback'
import Benchmarks from './pages/Benchmarks'

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore(state => state.token)
  return token ? <>{children}</> : <Navigate to="/login" />
}

export default function App() {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#00d4aa',
          borderRadius: 8
        }
      }}
    >
      <BrowserRouter basename="/chat/admin">
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={
            <PrivateRoute><AdminLayout /></PrivateRoute>
          }>
            <Route index element={<Dashboard />} />
            <Route path="documents" element={<Navigate to="/dimensions" replace />} />
            <Route path="dimensions" element={<Dimensions />} />
            <Route path="config" element={<Config />} />
            <Route path="logs" element={<Logs />} />
            <Route path="benchmarks" element={<Benchmarks />} />
            <Route path="feedback" element={<Feedback />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
