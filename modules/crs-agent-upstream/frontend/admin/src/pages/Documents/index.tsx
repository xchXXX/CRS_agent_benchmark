import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Table,
  Input,
  Button,
  Popconfirm,
  message,
  Modal,
  Form,
  Tooltip,
  Typography,
  InputNumber,
  Upload,
  Select,
  Switch,
  Progress,
  Alert,
  Space,
  Divider
} from 'antd'
import { PlusOutlined, DeleteOutlined, FileTextOutlined, UploadOutlined } from '@ant-design/icons'
import { docsService } from '../../services/docs'
import dayjs from 'dayjs'
import './index.css'

type ImportPreview = {
  upload_id: string
  filename: string
  columns: string[]
  row_count: number
  sample_rows: Record<string, string>[]
}

type ImportStatus = {
  task_id: string
  state: string
  total: number
  processed: number
  success: number
  failed: number
  skipped: number
  message: string
  errors: Array<{ row: number; error: string }>
}

type DeleteStatus = {
  task_id: string
  state: string
  total: number
  processed: number
  success: number
  failed: number
  skipped: number
  message: string
  errors: Array<{ row: number; error: string }>
}

export default function Documents() {
  const [data, setData] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  const [importModalOpen, setImportModalOpen] = useState(false)
  const [importPreviewLoading, setImportPreviewLoading] = useState(false)
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null)
  const [importMapping, setImportMapping] = useState({
    file_path_col: '',
    filename_col: '',
    ref_file_id_col: '',
    parent_id_col: '',
  })
  const [skipExisting, setSkipExisting] = useState(true)
  const [importTask, setImportTask] = useState<ImportStatus | null>(null)
  const pollTimerRef = useRef<number | null>(null)

  const [deleteModalOpen, setDeleteModalOpen] = useState(false)
  const [deletePreviewLoading, setDeletePreviewLoading] = useState(false)
  const [deletePreview, setDeletePreview] = useState<ImportPreview | null>(null)
  const [deleteMapping, setDeleteMapping] = useState({
    file_path_col: '',
    filename_col: '',
    ref_file_id_col: '',
    parent_id_col: '',
  })
  const [deleteTask, setDeleteTask] = useState<DeleteStatus | null>(null)
  const deletePollTimerRef = useRef<number | null>(null)

  const fetchData = async () => {
    setLoading(true)
    try {
      const res = await docsService.getList({ page, page_size: 20, keyword })
      setData(res.data.items)
      setTotal(res.data.total)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchData() }, [page, keyword])

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) window.clearInterval(pollTimerRef.current)
      if (deletePollTimerRef.current) window.clearInterval(deletePollTimerRef.current)
    }
  }, [])

  const handleDelete = async (id: string) => {
    await docsService.delete(id)
    message.success('删除成功')
    fetchData()
  }

  const handleBatchDelete = async () => {
    await docsService.batchDelete(selectedKeys)
    message.success('批量删除成功')
    setSelectedKeys([])
    fetchData()
  }

  const handleAdd = async () => {
    try {
      const values = await form.validateFields()
      await docsService.add(values)
      message.success('添加成功')
      setModalOpen(false)
      form.resetFields()
      fetchData()
    } catch (err: any) {
      if (err.response?.data?.detail) {
        message.error(err.response.data.detail)
      }
    }
  }

  const resetImportModal = () => {
    setImportPreview(null)
    setImportTask(null)
    setImportPreviewLoading(false)
    setImportMapping({
      file_path_col: '',
      filename_col: '',
      ref_file_id_col: '',
      parent_id_col: '',
    })
    setSkipExisting(true)
    if (pollTimerRef.current) {
      window.clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }

  const resetDeleteModal = () => {
    setDeletePreview(null)
    setDeleteTask(null)
    setDeletePreviewLoading(false)
    setDeleteMapping({
      file_path_col: '',
      filename_col: '',
      ref_file_id_col: '',
      parent_id_col: '',
    })
    if (deletePollTimerRef.current) {
      window.clearInterval(deletePollTimerRef.current)
      deletePollTimerRef.current = null
    }
  }

  const guessColumn = (columns: string[], candidates: string[]) => {
    const lower = columns.map(c => ({ raw: c, lower: c.toLowerCase() }))
    for (const cand of candidates) {
      const c = cand.toLowerCase()
      const exact = lower.find(x => x.lower === c)
      if (exact) return exact.raw
    }
    for (const cand of candidates) {
      const c = cand.toLowerCase()
      const contains = lower.find(x => x.lower.includes(c))
      if (contains) return contains.raw
    }
    return ''
  }

  const handleImportPreview = async (file: File) => {
    setImportPreviewLoading(true)
    try {
      const res = await docsService.importPreview(file)
      const preview = res.data as ImportPreview
      setImportPreview(preview)
      setImportTask(null)
      setImportMapping({
        file_path_col: guessColumn(preview.columns, ['file_path', 'physical_path', '文件路径', '路径']),
        filename_col: guessColumn(preview.columns, ['filename', 'file_name', '文件名', '文档名称', '名称', '标题']),
        ref_file_id_col: guessColumn(preview.columns, ['ref_file_id', '关联文件id', '关联文件ID', 'ref']),
        parent_id_col: guessColumn(preview.columns, ['parent_id', '父文件id', '父文件ID', 'parent']),
      })
    } catch (err: any) {
      message.error(err.response?.data?.detail || '解析失败，请检查文件格式/编码')
    } finally {
      setImportPreviewLoading(false)
    }
  }

  const handleDeletePreview = async (file: File) => {
    setDeletePreviewLoading(true)
    try {
      const res = await docsService.importPreview(file)
      const preview = res.data as ImportPreview
      setDeletePreview(preview)
      setDeleteTask(null)
      setDeleteMapping({
        file_path_col: guessColumn(preview.columns, ['file_path', 'physical_path', '文件路径', '路径']),
        filename_col: guessColumn(preview.columns, ['filename', 'file_name', '文件名', '文档名称', '名称', '标题', '关联文件名称']),
        ref_file_id_col: guessColumn(preview.columns, ['ref_file_id', '关联文件id', '关联文件ID', 'ref']),
        parent_id_col: guessColumn(preview.columns, ['parent_id', '父文件id', '父文件ID', 'parent']),
      })
    } catch (err: any) {
      message.error(err.response?.data?.detail || '解析失败，请检查文件格式/编码')
    } finally {
      setDeletePreviewLoading(false)
    }
  }

  const startPollingImportStatus = (taskId: string) => {
    if (pollTimerRef.current) window.clearInterval(pollTimerRef.current)
    pollTimerRef.current = window.setInterval(async () => {
      try {
        const res = await docsService.importStatus(taskId)
        const status = res.data as ImportStatus
        setImportTask(status)
        if (status.state === 'completed' || status.state === 'failed') {
          if (pollTimerRef.current) window.clearInterval(pollTimerRef.current)
          pollTimerRef.current = null
        }
      } catch (err: any) {
        const detail = err.response?.data?.detail
        setImportTask(prev => prev ? { ...prev, state: 'failed', message: detail || '获取任务状态失败', errors: prev.errors || [] } : null)
        if (pollTimerRef.current) window.clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }, 1000)
  }

  const startPollingDeleteStatus = (taskId: string) => {
    if (deletePollTimerRef.current) window.clearInterval(deletePollTimerRef.current)
    deletePollTimerRef.current = window.setInterval(async () => {
      try {
        const res = await docsService.deleteStatus(taskId)
        const status = res.data as DeleteStatus
        setDeleteTask(status)
        if (status.state === 'completed' || status.state === 'failed') {
          if (deletePollTimerRef.current) window.clearInterval(deletePollTimerRef.current)
          deletePollTimerRef.current = null
        }
      } catch (err: any) {
        const detail = err.response?.data?.detail
        setDeleteTask(prev => prev ? { ...prev, state: 'failed', message: detail || '获取任务状态失败', errors: prev.errors || [] } : null)
        if (deletePollTimerRef.current) window.clearInterval(deletePollTimerRef.current)
        deletePollTimerRef.current = null
      }
    }, 1000)
  }

  const handleImportStart = async () => {
    if (!importPreview) return
    const { file_path_col, filename_col, ref_file_id_col, parent_id_col } = importMapping
    if (!file_path_col || !filename_col || !ref_file_id_col || !parent_id_col) {
      message.error('请先完成列映射（四项都必选）')
      return
    }
    try {
      const res = await docsService.importStart({
        upload_id: importPreview.upload_id,
        file_path_col,
        filename_col,
        ref_file_id_col,
        parent_id_col,
        skip_existing: skipExisting,
      })
      const { task_id, total: totalCount } = res.data as { task_id: string; total: number }
      setImportTask({
        task_id,
        state: 'pending',
        total: totalCount,
        processed: 0,
        success: 0,
        failed: 0,
        skipped: 0,
        message: '任务已创建，等待执行',
        errors: [],
      })
      startPollingImportStatus(task_id)
      message.success('已开始导入任务')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '启动导入失败')
    }
  }

  const handleDeleteStart = async () => {
    if (!deletePreview) return
    const { file_path_col, filename_col, ref_file_id_col, parent_id_col } = deleteMapping
    if (!file_path_col || !filename_col || !ref_file_id_col || !parent_id_col) {
      message.error('请先完成列映射（四项都必选）')
      return
    }
    try {
      const res = await docsService.deleteStart({
        upload_id: deletePreview.upload_id,
        file_path_col,
        filename_col,
        ref_file_id_col,
        parent_id_col,
      })
      const { task_id, total: totalCount } = res.data as { task_id: string; total: number }
      setDeleteTask({
        task_id,
        state: 'pending',
        total: totalCount,
        processed: 0,
        success: 0,
        failed: 0,
        skipped: 0,
        message: '任务已创建，等待执行',
        errors: [],
      })
      startPollingDeleteStatus(task_id)
      message.success('已开始删除任务')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '启动删除失败')
    }
  }

  const importSampleColumns = useMemo(() => {
    if (!importPreview?.columns?.length) return []
    // 预览表最多展示前 8 列，避免横向过宽
    return importPreview.columns.slice(0, 8).map(col => ({
      title: col,
      dataIndex: col,
      ellipsis: true,
      width: 180,
      render: (v: any) => <Tooltip title={v}>{v || '-'}</Tooltip>,
    }))
  }, [importPreview])

  const importProgressPercent = useMemo(() => {
    if (!importTask?.total) return 0
    return Math.min(100, Math.round((importTask.processed / importTask.total) * 100))
  }, [importTask])

  const deleteSampleColumns = useMemo(() => {
    if (!deletePreview?.columns?.length) return []
    return deletePreview.columns.slice(0, 8).map(col => ({
      title: col,
      dataIndex: col,
      ellipsis: true,
      width: 180,
      render: (v: any) => <Tooltip title={v}>{v || '-'}</Tooltip>,
    }))
  }, [deletePreview])

  const deleteProgressPercent = useMemo(() => {
    if (!deleteTask?.total) return 0
    return Math.min(100, Math.round((deleteTask.processed / deleteTask.total) * 100))
  }, [deleteTask])

  return (
    <div className="documents-page">
      <div className="page-header">
        <h2><FileTextOutlined /> 文档管理</h2>
        <p>管理系统中的所有文档资料</p>
      </div>

      <div className="toolbar-card">
        <div className="toolbar">
          <Input.Search
            placeholder="搜索文档名称..."
            allowClear
            onSearch={v => { setKeyword(v); setPage(1) }}
            className="search-input"
          />
          <div className="toolbar-actions">
            {selectedKeys.length > 0 && (
              <Popconfirm title="确定批量删除选中的文档？" onConfirm={handleBatchDelete}>
                <Button danger icon={<DeleteOutlined />}>
                  删除选中 ({selectedKeys.length})
                </Button>
              </Popconfirm>
            )}
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
              新增文档
            </Button>
            <Button icon={<UploadOutlined />} onClick={() => { resetImportModal(); setImportModalOpen(true) }}>
              批量导入
            </Button>
            <Button danger icon={<DeleteOutlined />} onClick={() => { resetDeleteModal(); setDeleteModalOpen(true) }}>
              批量删除（表格）
            </Button>
          </div>
        </div>
      </div>
      {/* 表格部分在下一段 */}
      <div className="table-card">
        <Table
          rowKey="file_id"
          loading={loading}
          dataSource={data}
          rowSelection={{
            selectedRowKeys: selectedKeys,
            onChange: keys => setSelectedKeys(keys as string[])
          }}
          pagination={{
            current: page,
            total,
            pageSize: 20,
            onChange: setPage,
            showTotal: t => `共 ${t} 条`
          }}
          columns={[
            {
              title: '文件名',
              dataIndex: 'filename',
              ellipsis: true,
              sorter: (a: any, b: any) => (a.filename || '').localeCompare(b.filename || ''),
              render: v => v || '-'
            },
            {
              title: '关联文件ID',
              dataIndex: 'ref_file_id',
              width: 220,
              ellipsis: true,
              sorter: (a: any, b: any) => String(a.ref_file_id || '').localeCompare(String(b.ref_file_id || '')),
              render: (v: number | string | null) => {
                const text = v == null ? '' : String(v)
                return (
                  <Tooltip title={text || '-'}>
                    <Typography.Text code={!!text} copyable={text ? { text } : false}>
                      {text || '-'}
                    </Typography.Text>
                  </Tooltip>
                )
              }
            },
            {
              title: '文件路径',
              dataIndex: 'file_path',
              ellipsis: true,
              sorter: (a: any, b: any) => (a.file_path || '').localeCompare(b.file_path || ''),
              render: v => <Tooltip title={v}>{v || '-'}</Tooltip>
            },
            {
              title: '入库时间',
              dataIndex: 'discovered_at',
              width: 180,
              sorter: (a: any, b: any) => (a.discovered_at || '').localeCompare(b.discovered_at || ''),
              defaultSortOrder: 'descend' as const,
              render: (v: string) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss.SSS') : '-'
            },
            {
              title: '操作',
              width: 80,
              render: (_, r: any) => (
                <Popconfirm title="确定删除？" onConfirm={() => handleDelete(r.file_id)}>
                  <Button type="link" danger size="small">删除</Button>
                </Popconfirm>
              )
            }
          ]}
        />
      </div>
      {/* Modal 在下一段 */}
      <Modal
        title="新增文档"
        open={modalOpen}
        onOk={handleAdd}
        onCancel={() => { setModalOpen(false); form.resetFields() }}
        okText="添加"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="filename"
            label="文档名称"
            rules={[{ required: true, message: '请输入文档名称' }]}
          >
            <Input placeholder="请输入文档名称" />
          </Form.Item>
          <Form.Item
            name="file_path"
            label="文件路径"
            rules={[{ required: true, message: '请输入文件路径' }]}
          >
            <Input placeholder="支持 doc_tree 相对路径或层级路径（可用 -> 分隔）；不要求本地文件存在" />
          </Form.Item>
          <Form.Item
            name="ref_file_id"
            label="关联文件ID（ref_file_id）"
            rules={[{ required: true, message: '请输入关联文件ID' }]}
          >
            <InputNumber style={{ width: '100%' }} placeholder="例如：123456" min={0} precision={0} />
          </Form.Item>
          <Form.Item
            name="parent_id"
            label="父文件ID（parent_id）"
            rules={[{ required: true, message: '请输入父文件ID' }]}
          >
            <InputNumber style={{ width: '100%' }} placeholder="例如：789" min={0} precision={0} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="批量导入（CSV/XLS/XLSX）"
        open={importModalOpen}
        width={980}
        destroyOnClose
        onCancel={() => {
          if (importTask && importTask.state !== 'completed' && importTask.state !== 'failed') {
            Modal.confirm({
              title: '导入任务仍在执行',
              content: '关闭窗口只会停止前端进度轮询，后台任务仍会继续执行。确定关闭？',
              okText: '关闭',
              cancelText: '继续查看',
              onOk: () => { resetImportModal(); setImportModalOpen(false) },
            })
            return
          }
          resetImportModal()
          setImportModalOpen(false)
        }}
        footer={null}
      >
        <div className="import-section">
          <div className="import-block">
            <div className="import-block-title">1) 上传表格并解析</div>
            <Upload
              accept=".csv,.xls,.xlsx"
              maxCount={1}
              beforeUpload={(file) => {
                handleImportPreview(file)
                return false
              }}
              showUploadList={{ showRemoveIcon: false }}
            >
              <Button loading={importPreviewLoading} icon={<UploadOutlined />}>
                选择 CSV/XLS/XLSX 文件并解析
              </Button>
            </Upload>
            <div className="import-hint">解析后将展示列名与样例数据，用于映射字段。</div>
          </div>

          <Divider className="import-divider" />

          <div className="import-block">
            <div className="import-block-title">2) 列映射（必填）</div>
            {!importPreview ? (
              <Alert type="info" showIcon message="请先上传表格文件并解析。" />
            ) : (
              <>
                <div className="import-mapping-grid">
                  <div className="import-mapping-item">
                    <div className="import-mapping-label">文件路径</div>
                    <Select
                      value={importMapping.file_path_col || undefined}
                      onChange={(v) => setImportMapping(m => ({ ...m, file_path_col: v }))}
                      options={importPreview.columns.map(c => ({ label: c, value: c }))}
                      placeholder="选择列"
                      showSearch
                      style={{ width: '100%' }}
                    />
                  </div>
                  <div className="import-mapping-item">
                    <div className="import-mapping-label">文件名</div>
                    <Select
                      value={importMapping.filename_col || undefined}
                      onChange={(v) => setImportMapping(m => ({ ...m, filename_col: v }))}
                      options={importPreview.columns.map(c => ({ label: c, value: c }))}
                      placeholder="选择列"
                      showSearch
                      style={{ width: '100%' }}
                    />
                  </div>
                  <div className="import-mapping-item">
                    <div className="import-mapping-label">关联文件ID（ref_file_id）</div>
                    <Select
                      value={importMapping.ref_file_id_col || undefined}
                      onChange={(v) => setImportMapping(m => ({ ...m, ref_file_id_col: v }))}
                      options={importPreview.columns.map(c => ({ label: c, value: c }))}
                      placeholder="选择列"
                      showSearch
                      style={{ width: '100%' }}
                    />
                  </div>
                  <div className="import-mapping-item">
                    <div className="import-mapping-label">父文件ID（parent_id）</div>
                    <Select
                      value={importMapping.parent_id_col || undefined}
                      onChange={(v) => setImportMapping(m => ({ ...m, parent_id_col: v }))}
                      options={importPreview.columns.map(c => ({ label: c, value: c }))}
                      placeholder="选择列"
                      showSearch
                      style={{ width: '100%' }}
                    />
                  </div>
                </div>

                <div className="import-options">
                  <Space size={8}>
                    <Switch checked={skipExisting} onChange={setSkipExisting} />
                    <span>遇到已存在文档时跳过（推荐）</span>
                  </Space>
                </div>

                <div className="import-actions">
                  <Button
                    type="primary"
                    disabled={!!importTask && importTask.state !== 'completed' && importTask.state !== 'failed'}
                    onClick={handleImportStart}
                  >
                    开始录入
                  </Button>
                  <span className="import-meta">
                    {importPreview ? `共 ${importPreview.row_count} 行，文件：${importPreview.filename}` : ''}
                  </span>
                </div>

                <div className="import-preview-table">
                  <div className="import-block-title">样例预览（前 20 行）</div>
                  <Table
                    size="small"
                    rowKey={(_, idx) => String(idx)}
                    dataSource={importPreview.sample_rows}
                    columns={importSampleColumns}
                    pagination={false}
                    scroll={{ x: true, y: 260 }}
                  />
                </div>
              </>
            )}
          </div>

          <Divider className="import-divider" />

          <div className="import-block">
            <div className="import-block-title">3) 进度</div>
            {!importTask ? (
              <Alert type="info" showIcon message="导入任务尚未启动。" />
            ) : (
              <>
                <Progress
                  percent={importProgressPercent}
                  status={importTask.state === 'failed' ? 'exception' : (importTask.state === 'completed' ? 'success' : 'active')}
                />
                <div className="import-status-line">
                  <span>状态：{importTask.state}</span>
                  <span>进度：{importTask.processed}/{importTask.total}</span>
                  <span>成功：{importTask.success}</span>
                  <span>跳过：{importTask.skipped}</span>
                  <span>失败：{importTask.failed}</span>
                </div>
                <div className="import-hint">{importTask.message}</div>

                {importTask.errors?.length > 0 && (
                  <div className="import-errors">
                    <div className="import-block-title">错误列表（最多显示 50 条）</div>
                    <Table
                      size="small"
                      rowKey={(r) => `${r.row}-${r.error}`}
                      dataSource={importTask.errors}
                      pagination={false}
                      columns={[
                        { title: '行号', dataIndex: 'row', width: 90 },
                        { title: '错误', dataIndex: 'error', ellipsis: true },
                      ]}
                      scroll={{ y: 200 }}
                    />
                  </div>
                )}

                {importTask.state === 'completed' && (
                  <div className="import-finish-actions">
                    <Button type="primary" onClick={() => { fetchData(); message.success('已刷新列表') }}>
                      刷新文档列表
                    </Button>
                    <Button onClick={() => { resetImportModal(); setImportModalOpen(false) }}>
                      关闭
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </Modal>

      <Modal
        title="批量删除（CSV/XLS/XLSX）"
        open={deleteModalOpen}
        width={980}
        destroyOnClose
        onCancel={() => {
          if (deleteTask && deleteTask.state !== 'completed' && deleteTask.state !== 'failed') {
            Modal.confirm({
              title: '删除任务仍在执行',
              content: '关闭窗口只会停止前端进度轮询，后台任务仍会继续执行。确定关闭？',
              okText: '关闭',
              cancelText: '继续查看',
              onOk: () => { resetDeleteModal(); setDeleteModalOpen(false) },
            })
            return
          }
          resetDeleteModal()
          setDeleteModalOpen(false)
        }}
        footer={null}
      >
        <div className="import-section">
          <div className="import-block">
            <div className="import-block-title">1) 上传表格并解析</div>
            <Upload
              accept=".csv,.xls,.xlsx"
              maxCount={1}
              beforeUpload={(file) => {
                handleDeletePreview(file)
                return false
              }}
              showUploadList={{ showRemoveIcon: false }}
            >
              <Button loading={deletePreviewLoading} icon={<UploadOutlined />}>
                选择 CSV/XLS/XLSX 文件并解析
              </Button>
            </Upload>
            <div className="import-hint">
              仅当【文件路径+文件名+ref_file_id+parent_id】四字段完全匹配时才会删除；父文件夹节点（ref_file_id 为空且文件名为空）将自动跳过。
            </div>
          </div>

          <Divider className="import-divider" />

          <div className="import-block">
            <div className="import-block-title">2) 列映射（必填）</div>
            {!deletePreview ? (
              <Alert type="info" showIcon message="请先上传表格文件并解析。" />
            ) : (
              <>
                <div className="import-mapping-grid">
                  <div className="import-mapping-item">
                    <div className="import-mapping-label">文件路径</div>
                    <Select
                      value={deleteMapping.file_path_col || undefined}
                      onChange={(v) => setDeleteMapping(m => ({ ...m, file_path_col: v }))}
                      options={deletePreview.columns.map(c => ({ label: c, value: c }))}
                      placeholder="选择列"
                      showSearch
                      style={{ width: '100%' }}
                    />
                  </div>
                  <div className="import-mapping-item">
                    <div className="import-mapping-label">文件名</div>
                    <Select
                      value={deleteMapping.filename_col || undefined}
                      onChange={(v) => setDeleteMapping(m => ({ ...m, filename_col: v }))}
                      options={deletePreview.columns.map(c => ({ label: c, value: c }))}
                      placeholder="选择列"
                      showSearch
                      style={{ width: '100%' }}
                    />
                  </div>
                  <div className="import-mapping-item">
                    <div className="import-mapping-label">关联文件ID（ref_file_id）</div>
                    <Select
                      value={deleteMapping.ref_file_id_col || undefined}
                      onChange={(v) => setDeleteMapping(m => ({ ...m, ref_file_id_col: v }))}
                      options={deletePreview.columns.map(c => ({ label: c, value: c }))}
                      placeholder="选择列"
                      showSearch
                      style={{ width: '100%' }}
                    />
                  </div>
                  <div className="import-mapping-item">
                    <div className="import-mapping-label">父文件ID（parent_id）</div>
                    <Select
                      value={deleteMapping.parent_id_col || undefined}
                      onChange={(v) => setDeleteMapping(m => ({ ...m, parent_id_col: v }))}
                      options={deletePreview.columns.map(c => ({ label: c, value: c }))}
                      placeholder="选择列"
                      showSearch
                      style={{ width: '100%' }}
                    />
                  </div>
                </div>

                <div className="import-actions">
                  <Popconfirm
                    title="确定开始批量删除？"
                    description="该操作会删除数据库与向量库中的匹配记录，无法撤销。"
                    okText="开始删除"
                    cancelText="取消"
                    onConfirm={handleDeleteStart}
                    disabled={!!deleteTask && deleteTask.state !== 'completed' && deleteTask.state !== 'failed'}
                  >
                    <Button
                      danger
                      type="primary"
                      disabled={!!deleteTask && deleteTask.state !== 'completed' && deleteTask.state !== 'failed'}
                    >
                      开始删除
                    </Button>
                  </Popconfirm>
                  <span className="import-meta">
                    {deletePreview ? `共 ${deletePreview.row_count} 行，文件：${deletePreview.filename}` : ''}
                  </span>
                </div>

                <div className="import-preview-table">
                  <div className="import-block-title">样例预览（前 20 行）</div>
                  <Table
                    size="small"
                    rowKey={(_, idx) => String(idx)}
                    dataSource={deletePreview.sample_rows}
                    columns={deleteSampleColumns}
                    pagination={false}
                    scroll={{ x: true, y: 260 }}
                  />
                </div>
              </>
            )}
          </div>

          <Divider className="import-divider" />

          <div className="import-block">
            <div className="import-block-title">3) 进度</div>
            {!deleteTask ? (
              <Alert type="info" showIcon message="删除任务尚未启动。" />
            ) : (
              <>
                <Progress
                  percent={deleteProgressPercent}
                  status={deleteTask.state === 'failed' ? 'exception' : (deleteTask.state === 'completed' ? 'success' : 'active')}
                />
                <div className="import-status-line">
                  <span>状态：{deleteTask.state}</span>
                  <span>进度：{deleteTask.processed}/{deleteTask.total}</span>
                  <span>已删除：{deleteTask.success}</span>
                  <span>跳过：{deleteTask.skipped}</span>
                  <span>失败：{deleteTask.failed}</span>
                </div>
                <div className="import-hint">{deleteTask.message}</div>

                {deleteTask.errors?.length > 0 && (
                  <div className="import-errors">
                    <div className="import-block-title">提示/错误列表（最多显示 50 条）</div>
                    <Table
                      size="small"
                      rowKey={(r) => `${r.row}-${r.error}`}
                      dataSource={deleteTask.errors}
                      pagination={false}
                      columns={[
                        { title: '行号', dataIndex: 'row', width: 90 },
                        { title: '说明', dataIndex: 'error', ellipsis: true },
                      ]}
                      scroll={{ y: 200 }}
                    />
                  </div>
                )}

                {deleteTask.state === 'completed' && (
                  <div className="import-finish-actions">
                    <Button type="primary" onClick={() => { fetchData(); message.success('已刷新列表') }}>
                      刷新文档列表
                    </Button>
                    <Button onClick={() => { resetDeleteModal(); setDeleteModalOpen(false) }}>
                      关闭
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </Modal>
    </div>
  )
}
