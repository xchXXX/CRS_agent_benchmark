import { useEffect, useMemo, useState } from 'react'
import {
  Input,
  Button,
  Popconfirm,
  message,
  Form,
  Select,
  InputNumber,
  Empty,
  Spin,
  Tag,
  Modal
} from 'antd'
import {
  TagsOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ReloadOutlined,
  WarningOutlined,
  SearchOutlined
} from '@ant-design/icons'
import { dimensionService, DimValue, DimStats } from '../../services/dimension'
import './index.css'

// 维度配置映射
const FACET_CONFIG: Record<string, { label: string; icon: string }> = {
  brand: { label: '品牌', icon: '🏭' },
  series: { label: '系列', icon: '📦' },
  model: { label: '型号', icon: '🔧' }
}

export default function Dimensions() {
  // 数据状态
  const [allValues, setAllValues] = useState<DimValue[]>([])
  const [stats, setStats] = useState<DimStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  // 选中状态
  const [selectedBrand, setSelectedBrand] = useState<DimValue | null>(null)
  const [selectedSeries, setSelectedSeries] = useState<DimValue | null>(null)
  const [selectedModel, setSelectedModel] = useState<DimValue | null>(null)

  // 搜索状态
  const [brandSearch, setBrandSearch] = useState('')
  const [seriesSearch, setSeriesSearch] = useState('')
  const [modelSearch, setModelSearch] = useState('')

  // Modal 状态
  const [modalOpen, setModalOpen] = useState(false)
  const [editingItem, setEditingItem] = useState<DimValue | null>(null)
  const [addingFacet, setAddingFacet] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [form] = Form.useForm()

  // 加载数据
  const fetchData = async () => {
    setLoading(true)
    try {
      const [valuesRes, statsRes] = await Promise.all([
        dimensionService.getValues(),
        dimensionService.getStats()
      ])
      setAllValues(valuesRes.data)
      setStats(statsRes.data)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '加载数据失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  // 刷新缓存
  const handleRefreshCache = async () => {
    setRefreshing(true)
    try {
      await dimensionService.refreshCache()
      await fetchData()
      message.success('缓存刷新成功')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '刷新缓存失败')
    } finally {
      setRefreshing(false)
    }
  }

  // 级联筛选逻辑
  const brands = useMemo(() =>
    allValues
      .filter(v => v.facet_key === 'brand')
      .filter(v => !brandSearch || v.value.toLowerCase().includes(brandSearch.toLowerCase()))
      .sort((a, b) => b.sort_order - a.sort_order)
  , [allValues, brandSearch])

  const series = useMemo(() =>
    allValues
      .filter(v => v.facet_key === 'series')
      .filter(v => !selectedBrand || v.parent_value_id === selectedBrand.id)
      .filter(v => !seriesSearch || v.value.toLowerCase().includes(seriesSearch.toLowerCase()))
      .sort((a, b) => b.sort_order - a.sort_order)
  , [allValues, selectedBrand, seriesSearch])

  const models = useMemo(() =>
    allValues
      .filter(v => v.facet_key === 'model')
      .filter(v => !selectedSeries || v.parent_value_id === selectedSeries.id)
      .filter(v => !modelSearch || v.value.toLowerCase().includes(modelSearch.toLowerCase()))
      .sort((a, b) => b.sort_order - a.sort_order)
  , [allValues, selectedSeries, modelSearch])

  // 计算子项数量
  const getChildCount = (item: DimValue) =>
    allValues.filter(v => v.parent_value_id === item.id).length

  // 检查是否未绑定
  const isUnbound = (item: DimValue) => {
    if (item.facet_key === 'brand') return false
    return !item.parent_value_id
  }

  // 单击品牌 - 仅选中
  const handleBrandClick = (item: DimValue) => {
    if (selectedBrand?.id === item.id) {
      setSelectedBrand(null)
    } else {
      setSelectedBrand(item)
    }
    setSelectedSeries(null)
    setSelectedModel(null)
  }

  // 单击系列 - 仅选中
  const handleSeriesClick = (item: DimValue) => {
    if (selectedSeries?.id === item.id) {
      setSelectedSeries(null)
    } else {
      setSelectedSeries(item)
    }
    setSelectedModel(null)
  }

  // 单击型号 - 仅选中
  const handleModelClick = (item: DimValue) => {
    if (selectedModel?.id === item.id) {
      setSelectedModel(null)
    } else {
      setSelectedModel(item)
    }
  }

  // 双击打开编辑弹窗
  const handleItemDoubleClick = (item: DimValue) => {
    setEditingItem(item)
    setAddingFacet(null)
    form.setFieldsValue({
      value: item.value,
      match_patterns: item.match_patterns,
      parent_value_id: item.parent_value_id,
      sort_order: item.sort_order
    })
    setModalOpen(true)
  }

  // 新增项 - 打开弹窗
  const handleAdd = (facetKey: string) => {
    setEditingItem(null)
    setAddingFacet(facetKey)
    form.resetFields()

    // 自动关联父级
    if (facetKey === 'series' && selectedBrand) {
      form.setFieldsValue({ parent_value_id: selectedBrand.id })
    } else if (facetKey === 'model' && selectedSeries) {
      form.setFieldsValue({ parent_value_id: selectedSeries.id })
    }
    form.setFieldsValue({ sort_order: 0 })
    setModalOpen(true)
  }

  // 保存
  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)

      if (addingFacet) {
        // 新增
        await dimensionService.createValue({
          facet_key: addingFacet,
          value: values.value,
          match_patterns: values.match_patterns,
          parent_value_id: values.parent_value_id,
          sort_order: values.sort_order || 0
        })
        message.success('新增成功')
      } else if (editingItem) {
        // 更新
        await dimensionService.updateValue(editingItem.id, {
          value: values.value,
          match_patterns: values.match_patterns,
          parent_value_id: values.parent_value_id,
          sort_order: values.sort_order
        })
        message.success('保存成功')
      }

      setModalOpen(false)
      setEditingItem(null)
      setAddingFacet(null)
      form.resetFields()
      await fetchData()
    } catch (err: any) {
      if (err.response?.data?.detail) {
        message.error(err.response.data.detail)
      }
    } finally {
      setSaving(false)
    }
  }

  // 删除
  const handleDelete = async () => {
    if (!editingItem) return
    try {
      await dimensionService.deleteValue(editingItem.id)
      message.success('删除成功')

      // 清理选中状态
      if (selectedBrand?.id === editingItem.id) setSelectedBrand(null)
      if (selectedSeries?.id === editingItem.id) setSelectedSeries(null)
      if (selectedModel?.id === editingItem.id) setSelectedModel(null)

      setModalOpen(false)
      setEditingItem(null)
      form.resetFields()
      await fetchData()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除失败')
    }
  }

  // 关闭弹窗
  const handleModalClose = () => {
    setModalOpen(false)
    setEditingItem(null)
    setAddingFacet(null)
    form.resetFields()
  }

  // 获取父级选项
  const getParentOptions = () => {
    if (addingFacet === 'series' || editingItem?.facet_key === 'series') {
      return allValues
        .filter(v => v.facet_key === 'brand')
        .map(b => ({ label: b.value, value: b.id }))
    }
    if (addingFacet === 'model' || editingItem?.facet_key === 'model') {
      return allValues
        .filter(v => v.facet_key === 'series')
        .map(s => ({ label: s.value, value: s.id }))
    }
    return []
  }

  // 渲染面板
  const renderPanel = (
    title: string,
    icon: string,
    items: DimValue[],
    selectedItem: DimValue | null,
    onItemClick: (item: DimValue) => void,
    search: string,
    onSearch: (value: string) => void,
    facetKey: string
  ) => (
    <div className="cascade-panel">
      <div className="panel-header">
        <span className="panel-title">
          {icon} {title}
          <span className="panel-count">{items.length}</span>
        </span>
      </div>
      <div className="panel-toolbar">
        <Input
          className="panel-search"
          placeholder="搜索..."
          prefix={<SearchOutlined style={{ color: '#8a94a6' }} />}
          value={search}
          onChange={e => onSearch(e.target.value)}
          allowClear
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          size="small"
          onClick={() => handleAdd(facetKey)}
        />
      </div>
      <div className="panel-list">
        {loading ? (
          <div className="panel-loading">
            <Spin />
          </div>
        ) : items.length === 0 ? (
          <div className="panel-empty">
            <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        ) : (
          items.map(item => (
            <div
              key={item.id}
              className={`panel-item ${selectedItem?.id === item.id ? 'selected' : ''} ${isUnbound(item) ? 'unbound' : ''}`}
              onClick={() => onItemClick(item)}
              onDoubleClick={() => handleItemDoubleClick(item)}
            >
              <div className="item-content">
                {isUnbound(item) && <WarningOutlined className="warn-icon" />}
                <span className="item-name" title={item.value}>{item.value}</span>
              </div>
              {facetKey !== 'model' && (
                <span className="item-count">{getChildCount(item)}</span>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )

  // 获取弹窗标题
  const getModalTitle = () => {
    if (addingFacet) {
      const config = FACET_CONFIG[addingFacet]
      return `新增${config?.label || addingFacet}`
    }
    if (editingItem) {
      const config = FACET_CONFIG[editingItem.facet_key]
      return `编辑${config?.label || editingItem.facet_key}：${editingItem.value}`
    }
    return ''
  }

  // 获取父级标签
  const getParentLabel = () => {
    const facetKey = addingFacet || editingItem?.facet_key
    if (facetKey === 'series') return '父级品牌'
    if (facetKey === 'model') return '父级系列'
    return '父级'
  }

  // 是否需要显示父级选择
  const showParentSelect = () => {
    const facetKey = addingFacet || editingItem?.facet_key
    return facetKey === 'series' || facetKey === 'model'
  }

  return (
    <div className="dimensions-page">
      <div className="page-header">
        <h2><TagsOutlined /> 维度管理</h2>
        <p>管理品牌、系列、型号等维度的层级关系和匹配规则</p>
      </div>

      <div className="toolbar-card">
        <div className="toolbar">
          <div className="stats-info">
            {stats && (
              <>
                <Tag color="cyan">{stats.facet_count} 维度</Tag>
                <Tag color="green">{stats.value_count} 个值</Tag>
                <Tag color={stats.cache_loaded ? 'blue' : 'orange'}>
                  缓存{stats.cache_loaded ? '已加载' : '未加载'}
                </Tag>
              </>
            )}
          </div>
          <div className="toolbar-actions">
            <Button
              icon={<ReloadOutlined spin={refreshing} />}
              onClick={handleRefreshCache}
              loading={refreshing}
            >
              刷新缓存
            </Button>
          </div>
        </div>
      </div>

      {/* 级联面板区域 */}
      <div className="cascade-panels">
        {renderPanel(
          '品牌',
          FACET_CONFIG.brand.icon,
          brands,
          selectedBrand,
          handleBrandClick,
          brandSearch,
          setBrandSearch,
          'brand'
        )}
        {renderPanel(
          '系列',
          FACET_CONFIG.series.icon,
          series,
          selectedSeries,
          handleSeriesClick,
          seriesSearch,
          setSeriesSearch,
          'series'
        )}
        {renderPanel(
          '型号',
          FACET_CONFIG.model.icon,
          models,
          selectedModel,
          handleModelClick,
          modelSearch,
          setModelSearch,
          'model'
        )}
      </div>

      {/* 编辑/新增弹窗 */}
      <Modal
        title={
          <span className="modal-title">
            {addingFacet ? <PlusOutlined className="modal-icon add" /> : <EditOutlined className="modal-icon edit" />}
            {getModalTitle()}
          </span>
        }
        open={modalOpen}
        onCancel={handleModalClose}
        footer={
          <div className="modal-footer">
            {editingItem && (
              <Popconfirm
                title="确定删除此项？"
                description="删除后相关子项的父级绑定将失效"
                onConfirm={handleDelete}
                okText="删除"
                cancelText="取消"
              >
                <Button danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            )}
            <div className="modal-footer-right">
              <Button onClick={handleModalClose}>取消</Button>
              <Button type="primary" onClick={handleSave} loading={saving}>
                保存
              </Button>
            </div>
          </div>
        }
        destroyOnClose
        width={480}
      >
        <Form form={form} layout="vertical" className="modal-form">
          <Form.Item
            name="value"
            label="主值"
            rules={[{ required: true, message: '请输入主值' }]}
          >
            <Input placeholder="如：东风、天锦、DFL1160..." />
          </Form.Item>
          {showParentSelect() && (
            <Form.Item name="parent_value_id" label={getParentLabel()}>
              <Select
                placeholder={`选择${getParentLabel()}`}
                options={getParentOptions()}
                allowClear
                showSearch
                optionFilterProp="label"
              />
            </Form.Item>
          )}
          <Form.Item
            name="match_patterns"
            label="匹配别名"
            tooltip="多个别名用英文逗号分隔，用于搜索匹配"
          >
            <Input placeholder="如：天锦,tianjin,TJ,KR（逗号分隔）" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序权重" tooltip="数值越大排序越靠前">
            <InputNumber style={{ width: '100%' }} placeholder="0" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
