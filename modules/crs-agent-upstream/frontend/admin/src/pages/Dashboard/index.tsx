import { useEffect, useState } from 'react'
import { Tag } from 'antd'
import {
  TagsOutlined,
  AppstoreOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  FileSearchOutlined,
  StarOutlined,
  MessageOutlined,
  ThunderboltOutlined,
  RocketOutlined
} from '@ant-design/icons'
import dayjs from 'dayjs'
import api from '../../services/api'
import { dashboardService, DashboardSummary } from '../../services/dashboard'
import './index.css'

const BUSINESS_LABELS: Record<string, string> = {
  GENERAL_CHAT: '通用对话',
  DOC_SEARCH: '资料搜索',
  FAULT_DIAGNOSIS: '故障诊断',
  PARAM_QUERY: '参数查询',
  AGENT_LOOP: 'Agent Loop',
}

const TASK_STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  waiting_user: '待补充',
  guard_stopped: '已截停',
  failed: '处理失败',
  switched: '已切换问题',
}

export default function Dashboard() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [health, setHealth] = useState<any>(null)

  useEffect(() => {
    Promise.all([dashboardService.getSummary(), api.get('/health')]).then(([summaryRes, healthRes]) => {
      setSummary(summaryRes.data)
      setHealth(healthRes.data)
    })
  }, [])

  const getStatusTone = (status: string | undefined) => {
    if (status === 'healthy' || status === 'ok') return 'healthy'
    if (status === 'degraded') return 'degraded'
    if (!status) return 'unknown'
    return 'down'
  }

  const formatDate = (value: string | null | undefined) => {
    if (!value) return '暂无'
    return dayjs(value).format('MM-DD HH:mm')
  }

  const formatRating = (value: number | null | undefined) => {
    if (typeof value !== 'number') return '-'
    return value.toFixed(1)
  }

  const formatRecall = (value: number | null | undefined) => {
    if (typeof value !== 'number') return '暂无'
    return `${(value * 100).toFixed(1)}%`
  }

  const healthStatus = health?.status || '-'
  const healthTone = getStatusTone(health?.status)
  const topBusinesses = summary?.logs.top_businesses || []
  const statusDistribution = summary?.logs.status_distribution || []

  const primaryMetrics = [
    {
      label: '启用维度',
      value: summary?.dimensions.facet_count ?? 0,
      caption: '可用于澄清的维度定义',
      icon: <TagsOutlined />
    },
    {
      label: '维度值总数',
      value: summary?.dimensions.value_count ?? 0,
      caption: '当前维度配置覆盖面',
      icon: <AppstoreOutlined />
    },
    {
      label: '日志总量',
      value: summary?.logs.total_count ?? 0,
      caption: '系统累计处理记录',
      icon: <FileSearchOutlined />
    },
    {
      label: '近 7 天日志',
      value: summary?.logs.last_7d_count ?? 0,
      caption: '近期请求活跃度',
      icon: <MessageOutlined />
    },
    {
      label: '近 30 天平均评分',
      value: formatRating(summary?.feedback.avg_rating_30d),
      caption: '用户反馈质量信号',
      icon: <StarOutlined />
    },
    {
      label: 'Benchmark 数据集',
      value: summary?.benchmarks.dataset_count ?? 0,
      caption: '离线评测资产',
      icon: <RocketOutlined />
    }
  ]

  return (
    <div className="dashboard-page">
      <section className="overview-hero">
        <div className="hero-copy">
          <span className="eyebrow">后台首页</span>
          <h1><DashboardOutlined /> 系统概览</h1>
          <p>统一查看维度配置、检索日志、反馈质量、Benchmark 与服务状态。</p>
        </div>

        <div className={`health-console health-${healthTone}`}>
          <span className="console-label">服务状态</span>
          <strong>{healthStatus}</strong>
          <span className="console-caption">接口健康检查</span>
          <CloudServerOutlined />
        </div>
      </section>

      <section className="metric-rail" aria-label="核心指标">
        {primaryMetrics.map(metric => (
          <div className="metric-node" key={metric.label}>
            <span className="metric-icon">{metric.icon}</span>
            <span className="metric-label">{metric.label}</span>
            <strong>{metric.value}</strong>
            <small>{metric.caption}</small>
          </div>
        ))}
      </section>

      <div className="overview-grid">
        <main className="overview-main">
          <section className="dimension-section surface-panel">
            <div>
              <span className="section-kicker">运行基础</span>
              <h2>维度与缓存</h2>
              <p>资料搜索澄清仍依赖本地维度配置，缓存状态决定当前配置是否已经进入运行态。</p>
            </div>
            <div className="cache-readout">
              <Tag color={summary?.dimensions.cache_loaded ? 'success' : 'error'}>
                {summary?.dimensions.cache_loaded ? '已加载' : '未加载'}
              </Tag>
              <strong>{summary?.dimensions.cache_loaded ? '内存缓存可用' : '未完成加载'}</strong>
              <span>缓存状态</span>
            </div>
            <div className="dimension-stats">
              <div>
                <span>维度定义</span>
                <strong>{summary?.dimensions.facet_count ?? 0}</strong>
              </div>
              <div>
                <span>维度值</span>
                <strong>{summary?.dimensions.value_count ?? 0}</strong>
              </div>
            </div>
          </section>

          <section className="log-section surface-panel">
            <div className="section-heading">
              <div>
                <span className="section-kicker">请求观察</span>
                <h2>日志概览</h2>
              </div>
              <FileSearchOutlined />
            </div>

            <div className="log-summary">
              <div>
                <span>近 7 天平均耗时</span>
                <strong>{summary?.logs.avg_elapsed_ms_7d ? `${summary.logs.avg_elapsed_ms_7d} ms` : '暂无'}</strong>
              </div>
              <div>
                <span>最近一条日志</span>
                <strong>{formatDate(summary?.logs.latest_created_at)}</strong>
              </div>
            </div>

            <div className="signal-groups">
              <div className="signal-group">
                <span className="group-label">高频业务</span>
                <div className="chips">
                  {topBusinesses.length > 0 ? (
                    topBusinesses.map(item => (
                      <Tag key={`${item.business_type}-${item.count}`}>
                        {(item.business_type && BUSINESS_LABELS[item.business_type]) || item.business_type || '未记录'} {item.count}
                      </Tag>
                    ))
                  ) : (
                    <span className="empty-text">暂无数据</span>
                  )}
                </div>
              </div>

              <div className="signal-group">
                <span className="group-label">任务状态</span>
                <div className="chips">
                  {statusDistribution.length > 0 ? (
                    statusDistribution.map(item => (
                      <Tag key={`${item.task_status}-${item.count}`} color="processing">
                        {(item.task_status && TASK_STATUS_LABELS[item.task_status]) || item.task_status || '未记录'} {item.count}
                      </Tag>
                    ))
                  ) : (
                    <span className="empty-text">暂无数据</span>
                  )}
                </div>
              </div>
            </div>
          </section>
        </main>

        <aside className="overview-side">
          <section className="feedback-section surface-panel">
            <span className="section-kicker">用户反馈</span>
            <div className="feedback-score">
              <StarOutlined />
              <strong>{formatRating(summary?.feedback.avg_rating_30d)}</strong>
              <span>近 30 天平均评分</span>
            </div>
            <div className="compact-list">
              <div>
                <span>反馈总量</span>
                <strong>{summary?.feedback.total_count ?? 0}</strong>
              </div>
              <div>
                <span>近 30 天新增</span>
                <strong>{summary?.feedback.last_30d_count ?? 0}</strong>
              </div>
              <div>
                <span>近 30 天带评论</span>
                <strong>{summary?.feedback.with_comment_30d ?? 0}</strong>
              </div>
              <div>
                <span>最近一条反馈</span>
                <strong>{formatDate(summary?.feedback.latest_created_at)}</strong>
              </div>
            </div>
          </section>

          <section className="benchmark-section surface-panel">
            <div className="section-heading">
              <div>
                <span className="section-kicker">评测结果</span>
                <h2>Benchmark 概览</h2>
              </div>
              <RocketOutlined />
            </div>
            <div className="benchmark-focus">
              <span>最近一次 Recall@10</span>
              <strong>{formatRecall(summary?.benchmarks.latest_recall_at_10)}</strong>
            </div>
            <div className="compact-list">
              <div>
                <span>数据集数量</span>
                <strong>{summary?.benchmarks.dataset_count ?? 0}</strong>
              </div>
              <div>
                <span>总案例数</span>
                <strong>{summary?.benchmarks.total_cases ?? 0}</strong>
              </div>
              <div>
                <span>运行中任务</span>
                <strong>{summary?.benchmarks.running_count ?? 0}</strong>
              </div>
              <div>
                <span>最近 Track</span>
                <strong>{summary?.benchmarks.latest_track || '暂无'}</strong>
              </div>
              <div>
                <span>最近运行时间</span>
                <strong>{formatDate(summary?.benchmarks.latest_run_at)}</strong>
              </div>
            </div>
          </section>

          <section className="hint-section surface-panel">
            <span className="section-kicker">运行说明</span>
            <h2>运行提示</h2>
            <div className="hint-list">
              <div className="hint-row">
                <ThunderboltOutlined />
                <span>用户侧资料搜索当前走外部 GGZJ 查询，首页不再统计本地 docs 库。</span>
              </div>
              <div className="hint-row">
                <CloudServerOutlined />
                <span>鉴权固定开启。联调时必须传有效 App Token，否则资料搜索不会执行。</span>
              </div>
              <div className="hint-row">
                <TagsOutlined />
                <span>资料搜索澄清仍依赖本地维度配置，修改维度后记得刷新缓存。</span>
              </div>
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}
