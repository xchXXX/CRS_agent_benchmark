import { useEffect, useState } from 'react'
import { Card, Form, Input, Button, message, Tabs, Switch, Popconfirm, Typography, Space, Alert } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { configService } from '../../services/config'
import './index.css'

type ConfigItem = {
  key: string
  value: string
  type: string
  description?: string
  is_sensitive?: boolean
}

const categoryNames: Record<string, string> = {
  frontend: '前端展示',
  external_service: '外部服务',
  llm: 'LLM配置',
  search: '检索参数',
  session: '会话配置',
  clarify: '澄清模块',
  intent: '意图识别',
  runtime: '运行时配置',
  parameter_query: '参数查询'
}

const externalServiceGroups: Array<{
  key: string
  label: string
  match: (item: ConfigItem) => boolean
  description?: string
}> = [
  {
    key: 'circuit_body_search',
    label: '电路图内搜索',
    match: item => item.key.startsWith('circuit_diagram_body_search_'),
    description: '配置外部电路图内搜索接口和解析库 PostgreSQL 连接。搜索接口通过 pdf_id 和 keyword 查询 PDF 内部命中元素，并返回页码与坐标。'
  },
  {
    key: 'diagnosis',
    label: '故障诊断',
    match: item => item.key.startsWith('diagnosis_'),
  },
  {
    key: 'oss',
    label: '图片 OSS',
    match: item => item.key.startsWith('aliyun_oss_'),
  },
]

export default function Config() {
  const [configs, setConfigs] = useState<any>({})
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()
  const [clearingLog, setClearingLog] = useState(false)

  const handleClearLog = async () => {
    setClearingLog(true)
    try {
      const res = await configService.clearLogFile()
      message.success(res.data?.message || '日志文件已清空')
    } catch {
      message.error('清除日志文件失败')
    } finally {
      setClearingLog(false)
    }
  }

  useEffect(() => {
    configService.getAll().then(res => {
      setConfigs(res.data)
    })
  }, [])

  const handleSave = async (itemsToSave: ConfigItem[]) => {
    setLoading(true)
    try {
      const values = form.getFieldsValue()
      const items = itemsToSave.map((c: ConfigItem) => ({
        key: c.key,
        value: c.type === 'bool'
          ? String(values[c.key] ?? false)
          : String(values[c.key] ?? c.value),
        type: c.type
      }))
      await configService.update(items)
      message.success('保存成功')
      const res = await configService.getAll()
      setConfigs(res.data)
      form.resetFields()
    } finally {
      setLoading(false)
    }
  }

  const renderConfigItems = (items: ConfigItem[]) => {
    return items.map((c: ConfigItem) => {
      let formControl
      if (c.type === 'bool') {
        formControl = (
          <Switch
            disabled={c.is_sensitive}
            checkedChildren="开启"
            unCheckedChildren="关闭"
          />
        )
      } else if (c.key.includes('prompt')) {
        formControl = <Input.TextArea rows={10} disabled={c.is_sensitive} />
      } else {
        formControl = <Input disabled={c.is_sensitive} />
      }

      return (
        <Form.Item
          key={c.key}
          name={c.key}
          label={c.description || c.key}
          initialValue={c.type === 'bool' ? String(c.value).toLowerCase() === 'true' : c.value}
          valuePropName={c.type === 'bool' ? 'checked' : 'value'}
        >
          {formControl}
        </Form.Item>
      )
    })
  }

  const renderConfigCard = (items: ConfigItem[], options?: { description?: string }) => {
    return (
      <Card className="config-card">
        <Space direction="vertical" size="middle" className="config-card-content">
          {options?.description && (
            <Alert
              type="info"
              showIcon
              message={options.description}
            />
          )}
          <div>{renderConfigItems(items)}</div>
          <Button type="primary" loading={loading} onClick={() => handleSave(items)}>
            保存
          </Button>
        </Space>
      </Card>
    )
  }

  const renderExternalServiceConfig = () => {
    const externalItems = (configs.external_service || []) as ConfigItem[]
    const groupedKeys = new Set<string>()
    const tabItems = externalServiceGroups.map(group => {
      const groupItems = externalItems.filter(item => group.match(item))
      groupItems.forEach(item => groupedKeys.add(item.key))
      return {
        key: group.key,
        label: group.label,
        children: renderConfigCard(groupItems, { description: group.description })
      }
    }).filter(item => {
      const group = externalServiceGroups.find(groupItem => groupItem.key === item.key)
      return externalItems.some(configItem => group?.match(configItem))
    })

    const otherItems = externalItems.filter(item => !groupedKeys.has(item.key))
    if (otherItems.length > 0) {
      tabItems.push({
        key: 'other',
        label: '其他',
        children: renderConfigCard(otherItems)
      })
    }

    if (tabItems.length === 0) {
      return renderConfigCard(externalItems)
    }

    return (
      <div className="external-service-panel">
        <Tabs
          className="external-service-tabs"
          tabPosition="top"
          items={tabItems}
        />
      </div>
    )
  }

  return (
    <div className="config">
      <h2>系统配置</h2>
      <Form form={form} layout="vertical">
        <Tabs
          items={[
            ...Object.keys(configs).filter(cat => cat !== 'system').map(cat => ({
              key: cat,
              label: categoryNames[cat] || cat,
              children: cat === 'external_service'
                ? renderExternalServiceConfig()
                : renderConfigCard(configs[cat] || [])
            })),
            {
              key: '_system',
              label: '系统',
              children: (
                <Card className="config-card">
                  <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                    <div>
                      <Typography.Title level={5} style={{ color: '#e6e6e6', marginBottom: 8 }}>用户端鉴权</Typography.Title>
                      <Typography.Text type="secondary">
                        新项目正式环境固定要求携带 App Token，后台不再提供关闭鉴权的开关。
                      </Typography.Text>
                    </div>
                    <Alert
                      type="info"
                      showIcon
                      message="鉴权固定开启"
                      description="资料搜索、文件访问和相关兼容接口都按必须鉴权处理；如需联调，请传入有效 token。"
                    />

                    <div style={{ borderTop: '1px solid #303030', margin: '8px 0' }} />

                    <div>
                      <Typography.Title level={5} style={{ color: '#e6e6e6', marginBottom: 8 }}>日志管理</Typography.Title>
                      <Typography.Text type="secondary">
                        清空后端日志文件（logs/app.log）。日志级别可在后端 logging_config.json 中修改，修改后需重启后端生效。
                      </Typography.Text>
                    </div>
                    <Popconfirm
                      title="确认清除日志"
                      description="确定要清空日志文件吗？此操作不可撤销。"
                      onConfirm={handleClearLog}
                      okText="确认"
                      cancelText="取消"
                    >
                      <Button danger icon={<DeleteOutlined />} loading={clearingLog}>
                        清除日志文件
                      </Button>
                    </Popconfirm>
                  </Space>
                </Card>
              )
            }
          ]}
        />
      </Form>
    </div>
  )
}
