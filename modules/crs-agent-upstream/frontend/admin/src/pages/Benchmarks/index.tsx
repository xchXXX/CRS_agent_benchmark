import { useEffect, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  List,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import {
  DownloadOutlined,
  EyeOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import type { ColumnsType } from 'antd/es/table'
import {
  BenchmarkDataset,
  BenchmarkEvent,
  BenchmarkPrediction,
  BenchmarkRunSummary,
  benchmarksService,
} from '../../services/benchmarks'
import './index.css'

const { Text } = Typography
type BenchmarkTrack = 'production_flow' | 'raw_retrieval' | 'final_list'

function normalizeTrackValue(value: string): BenchmarkTrack {
  const normalized = String(value || '').trim().toLowerCase().replace(/\s+/g, '')
  if (['raw_retrieval', 'rawretrieval', 'raw-retrieval', 'raw'].includes(normalized)) {
    return 'raw_retrieval'
  }
  if (['final_list', 'finallist', 'final-list', 'list'].includes(normalized)) {
    return 'final_list'
  }
  return 'production_flow'
}

function formatPercent(value?: number | null) {
  if (typeof value !== 'number') return '-'
  return `${(value * 100).toFixed(1)}%`
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  return dayjs(value).format('MM-DD HH:mm:ss')
}

function renderStatus(status?: string) {
  const map: Record<string, { color: string; text: string }> = {
    queued: { color: 'default', text: '排队中' },
    running: { color: 'processing', text: '运行中' },
    completed: { color: 'success', text: '已完成' },
    failed: { color: 'error', text: '失败' },
    paused: { color: 'warning', text: '已暂停' },
  }
  const config = map[status || ''] || { color: 'default', text: status || '未标注' }
  return <span className="benchmark-status-badge"><Badge status={config.color as any} text={config.text} /></span>
}

function renderRankTag(record: BenchmarkPrediction, topK?: number) {
  const rank = record.best_rank_in_top_k ?? record.best_rank
  if (rank) {
    return <Tag color={rank <= 5 ? 'success' : 'warning'}>主榜 #{rank}</Tag>
  }
  if (record.best_rank_full) {
    return <Tag color="gold">#{record.best_rank_full} / 主榜 Top-{topK || '-'}外</Tag>
  }
  return <Tag color="error">未命中</Tag>
}

function selectedRunLabel(run?: BenchmarkRunSummary) {
  if (!run) return '请选择运行记录'
  return `${run.dataset_id || 'dataset'} / ${run.track || 'track'} / ${renderStatusText(run.status)}`
}

function renderStatusText(status?: string) {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    paused: '已暂停',
  }
  return map[status || ''] || status || '未标注'
}

function JsonBlock({ value }: { value: any }) {
  if (value === undefined || value === null || value === '') {
    return <Text type="secondary">无</Text>
  }
  return <pre className="benchmark-json-block">{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>
}

function predictionHasImage(record: BenchmarkPrediction) {
  return Boolean((record.image_paths || []).length || (record.image_evidence || []).length)
}

function CaseTraceDrawer({
  prediction,
  open,
  onClose,
  topK,
}: {
  prediction: BenchmarkPrediction | null
  open: boolean
  onClose: () => void
  topK?: number
}) {
  const runtime = prediction?.runtime || {}
  const imageEvidence = prediction?.image_evidence || []
  const plannedQueries = prediction?.planned_queries || runtime.planned_queries || runtime.search_snapshot?.planned_queries || []
  const traceEntries = prediction?.trace_entries || runtime.trace_entries || []

  return (
    <Drawer
      className="benchmark-trace-drawer"
      title={prediction ? `Case 链路: ${prediction.case_id}` : 'Case 链路'}
      width={880}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      {prediction ? (
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <Card size="small" className="benchmark-trace-card">
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Space wrap>
                {renderRankTag(prediction, topK)}
                <Tag color={prediction.answerable ? 'blue' : 'default'}>
                  {prediction.answerable ? '有候选' : '无候选'}
                </Tag>
                <Tag>{prediction.track}</Tag>
                <Tag>{runtime.latency_ms || 0} ms</Tag>
              </Space>
              <Descriptions column={1} size="small" className="benchmark-descriptions">
                <Descriptions.Item label="原始问题">{prediction.question_text || '-'}</Descriptions.Item>
                <Descriptions.Item label="实际搜索 Query">{prediction.effective_query || '-'}</Descriptions.Item>
                <Descriptions.Item label="图片路径">
                  {(prediction.image_paths || []).length
                    ? (prediction.image_paths || []).join(' / ')
                    : imageEvidence.length
                      ? '历史运行未保存图片路径，但已保存图片识别结果'
                      : '-'}
                </Descriptions.Item>
              </Descriptions>
            </Space>
          </Card>

          <Card size="small" title="图片识别结果" className="benchmark-trace-card">
            {imageEvidence.length ? (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                {imageEvidence.map((item, index) => (
                  <div className="benchmark-evidence-card" key={item.image_evidence_id || index}>
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                      <Space wrap>
                        <Tag color="cyan">{item.scene || '未标注'}</Tag>
                        <Tag color="blue">confidence {item.confidence ?? '-'}</Tag>
                        {item.needs_user_confirm ? <Tag color="warning">需确认</Tag> : <Tag color="success">无需确认</Tag>}
                      </Space>
                      <Text>{item.summary || '无摘要'}</Text>
                      <Descriptions column={1} size="small" className="benchmark-descriptions">
                        <Descriptions.Item label="车辆信息">
                          <JsonBlock value={item.vehicle} />
                        </Descriptions.Item>
                        <Descriptions.Item label="诊断信息">
                          <JsonBlock value={item.diagnosis} />
                        </Descriptions.Item>
                        <Descriptions.Item label="可见文字">
                          {(item.visible_text || []).length ? (
                            <Space wrap>{(item.visible_text || []).map((text: string) => <Tag key={text}>{text}</Tag>)}</Space>
                          ) : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="建议查询">
                          {(item.suggested_queries || []).length ? (
                            <Space direction="vertical" size={4}>
                              {(item.suggested_queries || []).map((query: string) => <Text code key={query}>{query}</Text>)}
                            </Space>
                          ) : '-'}
                        </Descriptions.Item>
                      </Descriptions>
                    </Space>
                  </div>
                ))}
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该 case 没有图片识别结果" />
            )}
          </Card>

          <Card size="small" title="LLM 查询规划与搜索调用" className="benchmark-trace-card">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Descriptions column={1} size="small" className="benchmark-descriptions">
                <Descriptions.Item label="图片证据摘要">
                  {prediction.image_evidence_summary || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="规划理由">
                  {runtime.query_plan_rationale || runtime.search_snapshot?.query_plan_rationale || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="实际搜索 Query">
                  <Text code>{prediction.effective_query || '-'}</Text>
                </Descriptions.Item>
              </Descriptions>
              {plannedQueries.length ? (
                <div className="benchmark-planned-query-list">
                  {plannedQueries.map((item: any, index: number) => (
                    <div className="benchmark-planned-query" key={`${item.query || index}-${index}`}>
                      <Text code>{item.query || '-'}</Text>
                      <Space wrap>
                        {item.confidence !== undefined ? <Tag>confidence {item.confidence}</Tag> : null}
                        {item.hit_count !== undefined ? <Tag>hits {item.hit_count}</Tag> : null}
                      </Space>
                    </div>
                  ))}
                </div>
              ) : (
                <Text type="secondary">无规划查询记录</Text>
              )}
            </Space>
          </Card>

          <Card size="small" title="结果与命中" className="benchmark-trace-card">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Space wrap>
                <Tag>评分列表 {prediction.returned_result_count ?? 0}</Tag>
                <Tag>完整池 {prediction.full_result_count ?? 0}</Tag>
                <Tag>诊断来源 {runtime.diagnostic_rank_source || '-'}</Tag>
              </Space>
              <Descriptions column={1} size="small" className="benchmark-descriptions">
                <Descriptions.Item label="命中 Gold">
                  {(prediction.matched_gold_names || []).length ? (prediction.matched_gold_names || []).join(' / ') : '-'}
                </Descriptions.Item>
                <Descriptions.Item label="命中资料">
                  {(prediction.matched_result_doc_names || []).length ? (prediction.matched_result_doc_names || []).join(' / ') : '-'}
                </Descriptions.Item>
              </Descriptions>
              <List
                size="small"
                dataSource={(prediction.results_scored || prediction.results || []).slice(0, 8)}
                locale={{ emptyText: '无返回结果' }}
                renderItem={(item: any) => (
                  <List.Item>
                    <Space direction="vertical" size={2} style={{ width: '100%' }}>
                      <Text>{item.rank}. {item.doc_name}</Text>
                      <Text type="secondary">{item.path || '-'}</Text>
                    </Space>
                  </List.Item>
                )}
              />
            </Space>
          </Card>

          <Card size="small" title="原始运行时与 Trace" className="benchmark-trace-card">
            <Tabs
              items={[
                {
                  key: 'runtime',
                  label: 'Runtime',
                  children: <JsonBlock value={runtime} />,
                },
                {
                  key: 'trace',
                  label: 'Trace',
                  children: <JsonBlock value={traceEntries.length ? traceEntries : 'trace 事件已写入全局执行日志'} />,
                },
              ]}
            />
          </Card>
        </Space>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择一个 case" />
      )}
    </Drawer>
  )
}

function DatasetCard({ dataset, selected, onClick }: { dataset: BenchmarkDataset; selected: boolean; onClick: () => void }) {
  return (
    <button className={`benchmark-dataset-row${selected ? ' is-selected' : ''}`} onClick={onClick} type="button">
      <span className="benchmark-dataset-title">{dataset.dataset_id}</span>
      <span className="benchmark-dataset-meta">{dataset.case_count} cases</span>
      <span className="benchmark-dataset-sub">
        可答 {dataset.answerable_count} · 无资料 {dataset.no_answer_count}
      </span>
      <span className="benchmark-dataset-path">{dataset.path}</span>
    </button>
  )
}

export default function Benchmarks() {
  const [overview, setOverview] = useState<any>(null)
  const [datasets, setDatasets] = useState<BenchmarkDataset[]>([])
  const [runs, setRuns] = useState<BenchmarkRunSummary[]>([])
  const [selectedDatasetId, setSelectedDatasetId] = useState<string>()
  const [selectedRunId, setSelectedRunId] = useState<string>()
  const [selectedTrack, setSelectedTrack] = useState<BenchmarkTrack>('production_flow')
  const [topK, setTopK] = useState<number>(20)
  const [detail, setDetail] = useState<any>(null)
  const [traceCase, setTraceCase] = useState<BenchmarkPrediction | null>(null)
  const [loading, setLoading] = useState(false)
  const [starting, setStarting] = useState(false)
  const [pausing, setPausing] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [downloadingReport, setDownloadingReport] = useState(false)
  const [downloadingFailures, setDownloadingFailures] = useState(false)
  const selectedRun = runs.find(item => item.run_id === selectedRunId)

  const loadAll = async (preserveRunId?: string) => {
    setLoading(true)
    try {
      const [overviewRes, datasetsRes, runsRes] = await Promise.all([
        benchmarksService.getOverview(),
        benchmarksService.getDatasets(),
        benchmarksService.getRuns(),
      ])
      setOverview(overviewRes.data)
      setDatasets(datasetsRes.data.items)
      setRuns(runsRes.data.items)
      if (!selectedDatasetId && datasetsRes.data.items[0]) {
        setSelectedDatasetId(datasetsRes.data.items[0].dataset_id)
      }
      const targetRunId = preserveRunId || selectedRunId || runsRes.data.items[0]?.run_id
      if (targetRunId) {
        setSelectedRunId(targetRunId)
        const detailRes = await benchmarksService.getRunDetail(targetRunId)
        setDetail(detailRes.data)
      } else {
        setDetail(null)
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载 benchmark 信息失败')
    } finally {
      setLoading(false)
    }
  }

  // Silent poll: only updates runs and detail, no global loading
  const pollRuns = async () => {
    try {
      const [overviewRes, runsRes] = await Promise.all([
        benchmarksService.getOverview(),
        benchmarksService.getRuns(),
      ])
      setOverview(overviewRes.data)
      setRuns(prev => {
        const next = runsRes.data.items
        // Preserve selection scroll position by keeping existing objects when possible
        const prevMap = new Map(prev.map(r => [r.run_id, r]))
        return next.map((r: BenchmarkRunSummary) => {
          const old = prevMap.get(r.run_id)
          if (old && JSON.stringify(old) === JSON.stringify(r)) return old
          return r
        })
      })
      const targetRunId = selectedRunId || runsRes.data.items[0]?.run_id
      if (targetRunId) {
        const detailRes = await benchmarksService.getRunDetail(targetRunId)
        setDetail(detailRes.data)
      }
    } catch {
      // Silently ignore poll errors to avoid spamming the user
    }
  }

  useEffect(() => {
    loadAll()
  }, [])

  useEffect(() => {
    const hasActive = runs.some(item => item.status === 'running' || item.status === 'queued')
    if (!hasActive) return
    const timer = window.setInterval(() => {
      pollRuns()
    }, 2500)
    return () => window.clearInterval(timer)
  }, [runs, selectedRunId])

  const runColumns: ColumnsType<BenchmarkRunSummary> = [
    {
      title: '运行',
      key: 'run',
      render: (_, record) => (
        <Space direction="vertical" size={3} className="benchmark-run-cell">
          <span className="benchmark-run-id-line">
            <Text strong>{record.run_id}</Text>
          </span>
          <Text type="secondary">{record.dataset_id}</Text>
        </Space>
      ),
    },
    {
      title: 'Track',
      dataIndex: 'track',
      render: value => <Tag color={value === 'raw_retrieval' ? 'geekblue' : 'purple'}>{value}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 92,
      render: value => renderStatus(value),
    },
    {
      title: '进度',
      key: 'progress',
      render: (_, record) => {
        const progress = record.progress
        if (!progress || !progress.total) return '-'
        return (
          <div style={{ minWidth: 160 }}>
            <Progress
              percent={Math.round((progress.completed / Math.max(progress.total, 1)) * 100)}
              size="small"
              status={record.status === 'failed' ? 'exception' : record.status === 'paused' ? 'normal' : undefined}
            />
          </div>
        )
      },
    },
    {
      title: 'Recall@10',
      key: 'recall',
      render: (_, record) => formatPercent(record.summary?.recall_at_10),
    },
    {
      title: '时间',
      key: 'time',
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <Text>{formatTime(record.started_at || record.created_at)}</Text>
          <Text type="secondary">{formatTime(record.finished_at)}</Text>
        </Space>
      ),
    },
  ]

  const predictionColumns: ColumnsType<BenchmarkPrediction> = [
    {
          title: 'Case',
          dataIndex: 'case_id',
          width: 190,
          render: (_, record) => (
            <Space direction="vertical" size={2}>
              <Text code>{record.case_id}</Text>
          {predictionHasImage(record) ? <Tag color="cyan">含图片</Tag> : <Text type="secondary">纯文本</Text>}
        </Space>
      ),
    },
    {
      title: '命中状态',
      key: 'rank_status',
      width: 140,
      render: (_, record) => renderRankTag(record, detail?.config?.top_k),
    },
    {
      title: '实际搜索 Query',
      key: 'effective_query',
      render: (_, record) => (
        <Space direction="vertical" size={3} style={{ maxWidth: 420 }}>
          <Text ellipsis={{ tooltip: record.effective_query || record.question_text }}>
            {record.effective_query || record.question_text || '-'}
          </Text>
          {(record.planned_queries || []).length ? (
            <Text type="secondary">{(record.planned_queries || []).length} 条规划查询</Text>
          ) : null}
        </Space>
      ),
    },
    {
      title: '命中资料',
      key: 'matched_docs',
      width: 260,
      render: (_, record) => (
        (record.matched_result_doc_names || []).length ? (
          <Text ellipsis={{ tooltip: (record.matched_result_doc_names || []).join(' / ') }}>
            {(record.matched_result_doc_names || []).slice(0, 2).join(' / ')}
          </Text>
        ) : <Text type="secondary">无</Text>
      ),
    },
    {
      title: '结果量',
      key: 'result_count',
      width: 120,
      render: (_, record) => {
        const scored = record.returned_result_count ?? record.results_scored?.length ?? record.results?.length ?? 0
        const full = record.full_result_count ?? record.results_full?.length ?? scored
        return <Text>{scored} / {full}</Text>
      },
    },
    {
      title: '耗时',
      key: 'runtime',
      width: 120,
      render: (_, record) => `${record.runtime?.latency_ms || 0} ms`,
    },
    {
      title: '链路',
      key: 'trace',
      width: 120,
      fixed: 'right',
      render: (_, record) => (
        <Button size="small" icon={<EyeOutlined />} onClick={() => setTraceCase(record)}>
          查看链路
        </Button>
      ),
    },
  ]

  const startRun = async () => {
    if (!selectedDatasetId) {
      message.warning('请先选择数据集')
      return
    }
    setStarting(true)
    try {
      const response = await benchmarksService.startRun({
        dataset_id: selectedDatasetId,
        track: selectedTrack,
        top_k: topK,
      })
      message.success('Benchmark 已启动')
      await loadAll(response.data.run_id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '启动 benchmark 失败')
    } finally {
      setStarting(false)
    }
  }

  const pauseRun = async () => {
    if (!selectedRunId) {
      message.warning('请先选择运行记录')
      return
    }
    setPausing(true)
    try {
      await benchmarksService.pauseRun(selectedRunId)
      message.success('正在暂停运行')
      await loadAll(selectedRunId)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '暂停 benchmark 失败')
    } finally {
      setPausing(false)
    }
  }

  const resumeRun = async () => {
    if (!selectedRunId) {
      message.warning('请先选择运行记录')
      return
    }
    setResuming(true)
    try {
      await benchmarksService.resumeRun(selectedRunId)
      message.success('Benchmark 已继续')
      await loadAll(selectedRunId)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '继续 benchmark 失败')
    } finally {
      setResuming(false)
    }
  }

  const downloadReport = async () => {
    if (!selectedRunId) return
    setDownloadingReport(true)
    try {
      let reportDetail = detail
      if (!reportDetail || reportDetail.status?.run_id !== selectedRunId) {
        const response = await benchmarksService.getRunDetail(selectedRunId)
        reportDetail = response.data
        setDetail(reportDetail)
      }
      await benchmarksService.downloadReport(selectedRunId, reportDetail)
      message.success('Excel 报告已开始下载')
    } catch (error: any) {
      message.error(error?.message || '导出 Excel 失败')
    } finally {
      setDownloadingReport(false)
    }
  }

  const downloadFailures = async () => {
    if (!selectedRunId) return
    setDownloadingFailures(true)
    try {
      await benchmarksService.downloadFailures(selectedRunId)
      message.success('失败样例已开始下载')
    } catch (error: any) {
      message.error(error?.message || '下载失败样例失败')
    } finally {
      setDownloadingFailures(false)
    }
  }

  const timelineItems = ((detail?.events as BenchmarkEvent[]) || []).slice(-40).map(item => ({
    color:
      item.event_type === 'run_failed'
        ? '#ff6b6b'
        : item.event_type === 'run_completed'
          ? '#00d4aa'
          : item.event_type === 'case_completed'
            ? '#1677ff'
            : '#8a94a6',
    children: (
      <div className="benchmark-event-item">
        <div className="benchmark-event-head">
          <Text strong>{item.message}</Text>
          <Text type="secondary">{formatTime(item.ts)}</Text>
        </div>
        <Text type="secondary">{item.event_type}</Text>
        {item.payload && Object.keys(item.payload).length > 0 && (
          <pre>{JSON.stringify(item.payload, null, 2)}</pre>
        )}
      </div>
    ),
  }))

  return (
    <div className="benchmark-page">
      <section className="benchmark-hero">
        <div className="benchmark-hero-copy">
          <span className="benchmark-eyebrow">DocSearch Evaluation Console</span>
          <h2><RocketOutlined /> Benchmark</h2>
          <p>真实资料搜索链路评测。关注候选列表命中、排序质量、图片识别和 LLM 查询规划链路。</p>
        </div>
        <div className="benchmark-hero-actions" />
      </section>

      <section className="benchmark-metrics-strip" aria-label="Benchmark 总览">
        <div className="benchmark-metric">
          <span>数据集</span>
          <strong>{overview?.datasets?.count || 0}</strong>
        </div>
        <div className="benchmark-metric">
          <span>总案例数</span>
          <strong>{overview?.datasets?.total_cases || 0}</strong>
        </div>
        <div className="benchmark-metric">
          <span>运行中</span>
          <strong>{overview?.runs?.running_count || 0}</strong>
        </div>
        <div className="benchmark-metric">
          <span>已暂停</span>
          <strong>{overview?.runs?.paused_count || 0}</strong>
        </div>
        <div className="benchmark-metric is-emphasis">
          <span>最近 Recall@10</span>
          <strong>{formatPercent(overview?.latest_metrics?.recall_at_10)}</strong>
        </div>
      </section>

      <section className="benchmark-shell">
        <aside className="benchmark-sidebar">
          <div className="benchmark-sidebar-section">
            <div className="benchmark-section-head">
              <span>运行配置</span>
              <Space>
                <Button size="small" icon={<ReloadOutlined />} onClick={() => loadAll(selectedRunId)} loading={loading}>
                  刷新
                </Button>
                {selectedRun?.status === 'running' || selectedRun?.status === 'queued' ? (
                  <Button size="small" type="primary" icon={<PauseCircleOutlined />} onClick={pauseRun} loading={pausing} danger>
                    暂停
                  </Button>
                ) : selectedRun?.status === 'paused' ? (
                  <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={resumeRun} loading={resuming}>
                    继续
                  </Button>
                ) : (
                  <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={startRun} loading={starting}>
                    执行
                  </Button>
                )}
              </Space>
            </div>
            <div className="benchmark-form-stack">
              <label>
                <span>数据集</span>
                <Select
                  value={selectedDatasetId}
                  onChange={setSelectedDatasetId}
                  options={datasets.map(item => ({ label: item.dataset_id, value: item.dataset_id }))}
                />
              </label>
              <label>
                <span>Track</span>
                <Select
                  value={selectedTrack}
                  onChange={value => setSelectedTrack(normalizeTrackValue(String(value)))}
                  options={[
                    { label: 'production_flow', value: 'production_flow' },
                    { label: 'raw_retrieval', value: 'raw_retrieval' },
                    { label: 'final_list', value: 'final_list' },
                  ]}
                />
              </label>
              <label>
                <span>主榜 Top-K</span>
                <Select
                  value={topK}
                  onChange={value => setTopK(value)}
                  options={[5, 10, 20, 50].map(value => ({ label: String(value), value }))}
                />
              </label>
            </div>
            <p className="benchmark-sidebar-note">
              主榜按主榜 Top-K 计算命中；诊断候选池用于区分“排序没进主榜”和“候选池未召回”。
            </p>
          </div>

          <div className="benchmark-sidebar-section">
            <div className="benchmark-section-head">
              <span>数据集</span>
              <small>{datasets.length} 个</small>
            </div>
            <div className="benchmark-dataset-list">
              {datasets.length > 0 ? (
                datasets.map(item => (
                  <DatasetCard
                    key={item.dataset_id}
                    dataset={item}
                    selected={item.dataset_id === selectedDatasetId}
                    onClick={() => setSelectedDatasetId(item.dataset_id)}
                  />
                ))
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 benchmark 数据集" />
              )}
            </div>
          </div>

          <div className="benchmark-sidebar-section is-muted">
            <div className="benchmark-section-head">
              <span>评测范围</span>
            </div>
            <p>{overview?.scope?.note || '当前 runner 主要评估 list retrieval。'}</p>
            <div className="benchmark-scope-tags">
              <Tag color="blue">{overview?.scope?.primary || 'list_retrieval'}</Tag>
              <Tag color={overview?.scope?.images_used_by_runner ? 'success' : 'warning'}>
                图片: {overview?.scope?.images_used_by_runner ? '参与' : '不参与'}
              </Tag>
              <Tag color={overview?.scope?.clarification_in_main_score ? 'success' : 'default'}>
                澄清计分: {overview?.scope?.clarification_in_main_score ? '是' : '否'}
              </Tag>
            </div>
          </div>
        </aside>

        <main className="benchmark-workspace">
          <section className="benchmark-run-board">
            <div className="benchmark-board-head">
              <div>
                <span className="benchmark-panel-kicker">Runs</span>
                <h3>运行记录</h3>
              </div>
              <div className="benchmark-selected-run-pill">
                <span>当前详情</span>
                <strong>{selectedRun ? selectedRun.run_id : '未选择'}</strong>
              </div>
            </div>
            <Table
              className="benchmark-runs-table"
              rowKey="run_id"
              loading={loading}
              dataSource={runs}
              columns={runColumns}
              pagination={{ pageSize: 5 }}
              scroll={{ x: 920 }}
              rowClassName={record => record.run_id === selectedRunId ? 'benchmark-run-row is-selected' : 'benchmark-run-row'}
              onRow={record => ({
                onClick: async () => {
                  setSelectedRunId(record.run_id)
                  const response = await benchmarksService.getRunDetail(record.run_id)
                  setDetail(response.data)
                },
              })}
            />
          </section>

          <section className="benchmark-detail-board">
            <div className="benchmark-board-head">
              <div>
                <span className="benchmark-panel-kicker">Report</span>
                <h3>运行详情</h3>
                <p className="benchmark-detail-subtitle">{selectedRunLabel(selectedRun)}</p>
              </div>
              {selectedRunId ? (
                <Space>
                  <Button icon={<DownloadOutlined />} loading={downloadingReport} onClick={downloadReport}>
                    导出 Excel
                  </Button>
                  <Button icon={<DownloadOutlined />} loading={downloadingFailures} onClick={downloadFailures}>
                    失败样例
                  </Button>
                </Space>
              ) : null}
            </div>
            {detail ? (
              <Tabs
                className="benchmark-detail-tabs"
                items={[
                  {
                    key: 'summary',
                    label: '报告摘要',
                    children: (
                      <div className="benchmark-report-summary">
                        <div className="benchmark-report-meta">
                          <Descriptions column={2} size="small" className="benchmark-descriptions">
                            <Descriptions.Item label="Run ID">
                              <Text code>{detail.status?.run_id}</Text>
                            </Descriptions.Item>
                            <Descriptions.Item label="状态">
                              {renderStatus(detail.status?.status)}
                            </Descriptions.Item>
                            <Descriptions.Item label="数据集">{detail.config?.dataset_id || '-'}</Descriptions.Item>
                            <Descriptions.Item label="Track">{detail.config?.track || '-'}</Descriptions.Item>
                            <Descriptions.Item label="主榜 Top-K">{detail.config?.top_k || '-'}</Descriptions.Item>
                            <Descriptions.Item label="诊断候选池">{detail.config?.diagnostic_pool_k || '-'}</Descriptions.Item>
                            <Descriptions.Item label="完成时间">{formatTime(detail.status?.finished_at)}</Descriptions.Item>
                          </Descriptions>

                          {detail.status?.progress?.total ? (
                            <Progress
                              percent={Math.round((detail.status.progress.completed / Math.max(detail.status.progress.total, 1)) * 100)}
                              status={detail.status?.status === 'failed' ? 'exception' : undefined}
                            />
                          ) : null}
                        </div>

                        <div className="benchmark-report-metrics">
                          <div>
                            <span>Recall@5</span>
                            <strong>{formatPercent(detail.report?.summary?.recall_at_5)}</strong>
                          </div>
                          <div>
                            <span>Recall@10</span>
                            <strong>{formatPercent(detail.report?.summary?.recall_at_10)}</strong>
                          </div>
                          <div>
                            <span>Recall@50</span>
                            <strong>{formatPercent(detail.report?.summary?.recall_at_50)}</strong>
                          </div>
                          <div>
                            <span>MRR</span>
                            <strong>{detail.report?.summary?.mrr?.toFixed?.(3) || '-'}</strong>
                          </div>
                          <div>
                            <span>Recall@100</span>
                            <strong>{formatPercent(detail.report?.summary?.recall_at_100)}</strong>
                          </div>
                        </div>

                        <div className="benchmark-report-tags">
                          <Tag color="gold">主榜外召回 {detail.report?.summary?.beyond_top_k_count ?? 0}</Tag>
                          <Tag color="orange">主榜外占比 {formatPercent(detail.report?.summary?.beyond_top_k_rate)}</Tag>
                          <Tag color="blue">诊断池中位名次 {detail.report?.summary?.median_gold_rank_full ?? '-'}</Tag>
                          <Tag color="red">诊断池未命中 {detail.report?.summary?.not_found_in_pool_count ?? 0}</Tag>
                          <Tag color="default">No-answer {formatPercent(detail.report?.summary?.no_answer_accuracy)}</Tag>
                        </div>

                        <List
                          size="small"
                          header={<Text strong>按问题类型</Text>}
                          bordered={false}
                          dataSource={Object.entries(detail.report?.by_task_type || {})}
                          renderItem={([taskType, stats]: any) => (
                            <List.Item>
                              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                                <Text>{taskType}</Text>
                                <Space wrap>
                                  <Tag>R@5 {formatPercent(stats.recall_at_5)}</Tag>
                                  <Tag>R@10 {formatPercent(stats.recall_at_10)}</Tag>
                                  <Tag>R@50 {formatPercent(stats.recall_at_50)}</Tag>
                                  <Tag>R@100 {formatPercent(stats.recall_at_100)}</Tag>
                                </Space>
                              </Space>
                            </List.Item>
                          )}
                        />
                      </div>
                    ),
                  },
                  {
                    key: 'cases',
                    label: 'Case 结果',
                    children: (
                      <Table
                        rowKey="case_id"
                        dataSource={detail.predictions || []}
                        columns={predictionColumns}
                        scroll={{ x: 1080 }}
                        pagination={{ pageSize: 6 }}
                      />
                    ),
                  },
                  {
                    key: 'events',
                    label: '执行日志',
                    children: timelineItems.length > 0 ? (
                      <div className="benchmark-log-shell">
                        <Timeline items={timelineItems} />
                      </div>
                    ) : (
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无事件日志" />
                    ),
                  },
                ]}
              />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择一条 benchmark run 查看详情" />
            )}
          </section>
        </main>
      </section>
      <CaseTraceDrawer
        prediction={traceCase}
        open={Boolean(traceCase)}
        onClose={() => setTraceCase(null)}
        topK={detail?.config?.top_k}
      />
    </div>
  )
}
