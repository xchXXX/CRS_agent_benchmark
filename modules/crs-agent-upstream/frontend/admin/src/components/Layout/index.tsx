import { Layout, Menu } from 'antd'
import {
  DashboardOutlined,
  SettingOutlined,
  FileSearchOutlined,
  TagsOutlined,
  StarOutlined,
  RocketOutlined
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import './index.css'

const { Sider, Content, Header } = Layout

export default function AdminLayout() {
  const navigate = useNavigate()
  const location = useLocation()

  const menuItems = [
    { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
    { key: '/dimensions', icon: <TagsOutlined />, label: '维度管理' },
    { key: '/logs', icon: <FileSearchOutlined />, label: '系统日志' },
    { key: '/benchmarks', icon: <RocketOutlined />, label: 'Benchmark' },
    { key: '/feedback', icon: <StarOutlined />, label: '用户反馈' },
    { key: '/config', icon: <SettingOutlined />, label: '系统配置' }
  ]

  return (
    <Layout className="admin-layout">
      <Sider width={220} className="admin-sider">
        <div className="logo">DocPilot</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header className="admin-header" />
        <Content className="admin-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
