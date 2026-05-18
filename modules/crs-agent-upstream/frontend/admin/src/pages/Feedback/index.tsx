import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  DatePicker,
  Descriptions,
  Empty,
  Modal,
  Rate,
  Row,
  Select,
  Slider,
  Space,
  Statistic,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import {
  EyeOutlined,
  ReloadOutlined,
  SearchOutlined,
  StarOutlined,
} from '@ant-design/icons'
import dayjs, { Dayjs } from 'dayjs'
import type { ColumnsType } from 'antd/es/table'
import { feedbackService, FeedbackDetail, FeedbackItem, FeedbackListParams } from '../../services/feedback'
import type { RunDetail } from '../../services/logs'
import './index.css'

const { RangePicker } = DatePicker
const { Paragraph, Text } = Typography

const BUSINESS_TYPE_MAP: Record<string, { label: string; color: string }> = {
  GENERAL_CHAT: { label: '维修问答', color: 'blue' },
  DOC_SEARCH: { label: '资料搜索', color: 'green' },
  FAULT_DIAGNOSIS: { label: '故障诊断', color: 'orange' },
  PARAM_QUERY: { label: '参数查询', color: 'purple' },
  AGENT_LOOP: { label: 'Agent Loop', color: 'default' },
}

const TASK_STATUS_MAP: Record<string, { label: string; color: string }> = {
  completed: { label: '已完成', color: 'success' },
  waiting_user: { label: '待补充', color: 'processing' },
  guard_stopped: { label: '已截停', color: 'warning' },
  failed: { label: '处理失败', color: 'error' },
  switched: { label: '已切换问题', color: 'default' },
}

const END_REASON_MAP: Record<string, { label: string; color: string }> = {
  direct_answer: { label: '直接回答', color: 'default' },
  ask_user: { label: 'Ask User', color: 'processing' },
  loop_guard: { label: 'Guard 停止', color: 'warning' },
  runtime_error: { label: '错误退出', color: 'error' },
  user_switched: { label: '用户切换', color: 'default' },
}

const RESPONSE_TYPE_MAP: Record<string, { label: string; color: string }> = {
  message: { label: '文本回复', color: 'default' },
  text: { label: '文本回复', color: 'default' },
  ask_user: { label: '反问卡片', color: 'processing' },
  documents: { label: '搜索结果', color: 'success' },
  fault: { label: '诊断结果', color: 'orange' },
  error: { label: '错误', color: 'error' },
}

const TRIGGER_TYPE_MAP: Record<string, string> = {
  user_message: '用户提问',
  ask_user_resume: '补充信息',
  user_switch: '切换问题',
}

function formatDate(value?: string | null, pattern: string = 'YYYY-MM-DD HH:mm:ss') {
  if (!value) return '-'
  return dayjs(value).format(pattern)
}

function formatElapsed(ms?: number | null) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function renderBusinessTag(value?: string | null) {
  if (!value) return <Tag>未识别</Tag>
  const config = BUSINESS_TYPE_MAP[value]
  return <Tag color={config?.color}>{config?.label || value}</Tag>
}

function renderStatusTag(value?: string | null) {
  if (!value) return <Tag>未记录</Tag>
  const config = TASK_STATUS_MAP[value]
  return <Tag color={config?.color}>{config?.label || value}</Tag>
}

function renderReasonTag(value?: string | null) {
  if (!value) return <Tag>未记录</Tag>
  const config = END_REASON_MAP[value]
  return <Tag color={config?.color}>{config?.label || value}</Tag>
}

function renderResponseTypeTag(value?: string | null) {
  if (!value) return <Tag>未记录</Tag>
  const config = RESPONSE_TYPE_MAP[value]
  return <Tag color={config?.color}>{config?.label || value}</Tag>
}

function renderJson(value: unknown) {
  return (
    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
    </pre>
  )
}

function resolveQuestion(record: FeedbackItem) {
  return record.task_log?.root_question || record.chat_log?.user_message || '-'
}

function resolveResultPreview(record: FeedbackItem) {
  if (record.task_log?.task_status === 'waiting_user' && record.task_log.latest_ask_user_question) {
    return record.task_log.latest_ask_user_question
  }
  return record.task_log?.final_response_preview || record.run_log?.response_preview || record.chat_log?.response_preview || '-'
}

function RuntimeRunCard({ run, highlighted }: { run: RunDetail; highlighted: boolean }) {
  const timelineItems = run.events.map(event => ({
    color:
      event.phase === 'error'
        ? '#ff6b6b'
        : event.phase === 'guard'
          ? '#faad14'
          : event.phase === 'ask_user'
            ? '#1677ff'
            : '#00d4aa',
    children: (
      <div className="event-item">
        <div className="event-item-header">
          <Space size={8} wrap>
            <Text strong>{event.summary || event.event_type}</Text>
            {event.tool_name && <Tag>{event.tool_name}</Tag>}
            {event.phase && <Tag bordered={false}>{event.phase}</Tag>}
            <Text type="secondary">#{event.sequence_no}</Text>
          </Space>
          <Text type="secondary">{formatDate(event.created_at, 'HH:mm:ss')}</Text>
        </div>
        {event.detail && <div className="event-item-detail">{event.detail}</div>}
        {event.payload && Object.keys(event.payload).length > 0 && (
          <div className="event-item-payload">{renderJson(event.payload)}</div>
        )}
      </div>
    ),
  }))

  return (
    <Collapse
      className="run-collapse"
      items={[
        {
          key: run.run_id,
          label: (
            <div className="run-collapse-label">
              <div className="run-collapse-title">
                <Space size={10} wrap>
                  <Text strong>Run {run.sequence_no}</Text>
                  {renderStatusTag(run.run_status)}
                  {renderResponseTypeTag(run.response_type)}
                  <Tag bordered={false}>{TRIGGER_TYPE_MAP[run.trigger_type || ''] || run.trigger_type || '未知触发'}</Tag>
                  {highlighted && <Tag color="gold">本次反馈</Tag>}
                </Space>
                <Text type="secondary">{formatElapsed(run.elapsed_ms)}</Text>
              </div>
              <div className="run-collapse-meta">
                <Space size={[6, 6]} wrap>
                  <Tag>工具 {run.tool_call_count}</Tag>
                  <Tag>外部 {run.external_tool_call_count}</Tag>
                  <Tag>反问 {run.ask_user_count}</Tag>
                  {run.guard_error_code && <Tag color="warning">{run.guard_error_code}</Tag>}
                </Space>
              </div>
            </div>
          ),
          children: (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Descriptions column={2} size="small">
                <Descriptions.Item label="请求ID">
                  <Text copyable>{run.request_id}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="调用方式">
                  {run.transport || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="触发方式">
                  {TRIGGER_TYPE_MAP[run.trigger_type || ''] || run.trigger_type || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="结束原因">
                  {renderReasonTag(run.end_reason)}
                </Descriptions.Item>
                <Descriptions.Item label="开始时间">
                  {formatDate(run.started_at)}
                </Descriptions.Item>
                <Descriptions.Item label="结束时间">
                  {formatDate(run.finished_at)}
                </Descriptions.Item>
              </Descriptions>

              {run.input_message && (
                <Card size="small" bordered={false} className="detail-card">
                  <Text strong>输入内容</Text>
                  <Paragraph style={{ marginBottom: 0 }}>{run.input_message}</Paragraph>
                </Card>
              )}

              {run.ask_user_answer_summary && (
                <Card size="small" bordered={false} className="detail-card">
                  <Text strong>用户补充</Text>
                  <Paragraph style={{ marginBottom: 0 }}>{run.ask_user_answer_summary}</Paragraph>
                </Card>
              )}

              <Card size="small" bordered={false} className="detail-card">
                <Text strong>本次结果</Text>
                <Paragraph style={{ marginBottom: 8 }}>{run.response_preview || run.ask_user_question || '-'}</Paragraph>
                {run.response_payload && Object.keys(run.response_payload).length > 0 && renderJson(run.response_payload)}
              </Card>

              <Card size="small" bordered={false} className="detail-card">
                <Text strong>事件时间线</Text>
                {timelineItems.length > 0 ? (
                  <Timeline items={timelineItems} style={{ marginTop: 12 }} />
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次运行没有记录事件" />
                )}
              </Card>
            </Space>
          ),
        },
      ]}
    />
  )
}

export default function Feedback() {
  const [data, setData] = useState<FeedbackItem[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [businessType, setBusinessType] = useState<string | undefined>()
  const [ratingRange, setRatingRange] = useState<[number, number]>([1, 10])
  const [dateRange, setDateRange] = useState<[Dayjs | null, Dayjs | null]>([null, null])
  const [hasComment, setHasComment] = useState(false)

  const [detailModalOpen, setDetailModalOpen] = useState(false)
  const [currentDetail, setCurrentDetail] = useState<FeedbackDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const fetchData = async () => {
    setLoading(true)
    try {
      const params: FeedbackListParams = {
        page,
        page_size: 20,
        business_type: businessType,
      }

      if (ratingRange[0] > 1) params.rating_min = ratingRange[0]
      if (ratingRange[1] < 10) params.rating_max = ratingRange[1]

      if (dateRange[0] && dateRange[1]) {
        params.start_time = dateRange[0].toISOString()
        params.end_time = dateRange[1].toISOString()
      }

      if (hasComment) params.has_comment = true

      const res = await feedbackService.getList(params)
      setData(res.data.items)
      setTotal(res.data.total)
    } catch (error: any) {
      message.error(error.response?.data?.detail || '获取反馈列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [page, businessType])

  const handleSearch = () => {
    setPage(1)
    fetchData()
  }

  const handleReset = () => {
    setBusinessType(undefined)
    setRatingRange([1, 10])
    setDateRange([null, null])
    setHasComment(false)
    setPage(1)
    setTimeout(fetchData, 0)
  }

  const handleViewDetail = async (id: number) => {
    setDetailModalOpen(true)
    setDetailLoading(true)
    try {
      const res = await feedbackService.getDetail(id)
      setCurrentDetail(res.data)
    } catch (error: any) {
      message.error(error.response?.data?.detail || '获取反馈详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

  const renderRating = (rating: number) => (
    <Rate disabled allowHalf value={rating / 2} style={{ fontSize: 14 }} />
  )

  const columns: ColumnsType<FeedbackItem> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (text: string) => dayjs(text).format('MM-DD HH:mm:ss'),
    },
    {
      title: '用户问题',
      key: 'user_message',
      ellipsis: true,
      width: 260,
      render: (_: unknown, record: FeedbackItem) => (
        <Paragraph ellipsis={{ rows: 2, tooltip: resolveQuestion(record) }} style={{ margin: 0 }}>
          {resolveQuestion(record)}
        </Paragraph>
      ),
    },
    {
      title: '业务类型',
      dataIndex: 'business_type',
      key: 'business_type',
      width: 110,
      render: (type: string) => renderBusinessTag(type),
    },
    {
      title: '任务状态',
      key: 'task_status',
      width: 110,
      render: (_: unknown, record: FeedbackItem) =>
        record.task_log ? renderStatusTag(record.task_log.task_status) : <Tag>旧版记录</Tag>,
    },
    {
      title: '评分',
      dataIndex: 'rating',
      key: 'rating',
      width: 160,
      render: (rating: number) => renderRating(rating),
    },
    {
      title: '结果摘要',
      key: 'result_preview',
      ellipsis: true,
      width: 260,
      render: (_: unknown, record: FeedbackItem) => (
        <div className="feedback-result-cell">
          {record.run_log?.response_type && renderResponseTypeTag(record.run_log.response_type)}
          <Paragraph ellipsis={{ rows: 2, tooltip: resolveResultPreview(record) }} style={{ margin: 0 }}>
            {resolveResultPreview(record)}
          </Paragraph>
        </div>
      ),
    },
    {
      title: '标签',
      dataIndex: 'tags',
      key: 'tags',
      width: 200,
      render: (tags: string[] | null) =>
        tags && tags.length > 0 ? (
          <Space wrap size={[4, 4]}>
            {tags.map((tag, i) => <Tag key={i}>{tag}</Tag>)}
          </Space>
        ) : '-',
    },
    {
      title: '评论',
      dataIndex: 'comment',
      key: 'comment',
      ellipsis: true,
      width: 220,
      render: (comment: string | null) => comment || '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      fixed: 'right',
      render: (_: unknown, record: FeedbackItem) => (
        <Button
          type="link"
          size="small"
          icon={<EyeOutlined />}
          onClick={() => handleViewDetail(record.id)}
        >
          详情
        </Button>
      ),
    },
  ]

  return (
    <div className="feedback-page">
      <Card className="filter-card" bordered={false}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Row gutter={16} align="middle">
            <Col span={5}>
              <Select
                placeholder="业务类型"
                value={businessType}
                onChange={setBusinessType}
                allowClear
                style={{ width: '100%' }}
              >
                {Object.entries(BUSINESS_TYPE_MAP).map(([key, value]) => (
                  <Select.Option key={key} value={key}>
                    {value.label}
                  </Select.Option>
                ))}
              </Select>
            </Col>
            <Col span={7}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ whiteSpace: 'nowrap', color: 'rgba(255,255,255,0.65)' }}>评分:</span>
                <Slider
                  range
                  min={1}
                  max={10}
                  value={ratingRange}
                  onChange={(val) => setRatingRange(val as [number, number])}
                  marks={{ 1: '0.5', 4: '2', 7: '3.5', 10: '5' }}
                  style={{ flex: 1 }}
                />
              </div>
            </Col>
            <Col span={6}>
              <RangePicker
                value={dateRange}
                onChange={(dates) => setDateRange(dates as [Dayjs | null, Dayjs | null])}
                showTime
                format="YYYY-MM-DD HH:mm"
                style={{ width: '100%' }}
                placeholder={['开始时间', '结束时间']}
              />
            </Col>
            <Col span={6}>
              <Space>
                <Checkbox checked={hasComment} onChange={e => setHasComment(e.target.checked)}>
                  仅含评论
                </Checkbox>
                <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
                  搜索
                </Button>
                <Button icon={<ReloadOutlined />} onClick={handleReset}>
                  重置
                </Button>
              </Space>
            </Col>
          </Row>
        </Space>
      </Card>

      <Card bordered={false} style={{ marginTop: 16 }}>
        <Table
          columns={columns}
          dataSource={data}
          loading={loading}
          rowKey="id"
          pagination={{
            current: page,
            total: total,
            pageSize: 20,
            showSizeChanger: false,
            showTotal: (count) => `共 ${count} 条`,
            onChange: (nextPage) => setPage(nextPage),
          }}
          scroll={{ x: 1560 }}
        />
      </Card>

      <Modal
        title={(
          <Space>
            <StarOutlined />
            <span>反馈详情</span>
          </Space>
        )}
        open={detailModalOpen}
        onCancel={() => setDetailModalOpen(false)}
        width={1040}
        footer={[
          <Button key="close" onClick={() => setDetailModalOpen(false)}>
            关闭
          </Button>,
        ]}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>加载中...</div>
        ) : currentDetail ? (
          <Space direction="vertical" size="large" style={{ width: '100%' }}>
            <Card title="反馈信息" size="small" bordered={false} className="detail-card feedback-detail-card">
              <Row gutter={16}>
                <Col span={8}>
                  <Statistic title="评分" valueRender={() => renderRating(currentDetail.rating)} />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="业务类型"
                    valueRender={() => renderBusinessTag(currentDetail.business_type)}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="提交时间"
                    value={dayjs(currentDetail.created_at).format('YYYY-MM-DD HH:mm:ss')}
                  />
                </Col>
              </Row>

              {currentDetail.tags && currentDetail.tags.length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <Text strong>标签：</Text>
                  <Space wrap style={{ marginLeft: 8 }}>
                    {currentDetail.tags.map((tag, index) => (
                      <Tag key={index} color="cyan">{tag}</Tag>
                    ))}
                  </Space>
                </div>
              )}

              {currentDetail.comment && (
                <div style={{ marginTop: 16 }}>
                  <Text strong>评论：</Text>
                  <Paragraph className="feedback-comment-block">
                    {currentDetail.comment}
                  </Paragraph>
                </div>
              )}

              <Descriptions column={2} size="small" style={{ marginTop: 16 }}>
                <Descriptions.Item label="请求ID">
                  <Text copyable>{currentDetail.request_id}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="会话ID">
                  <Text copyable>{currentDetail.session_id}</Text>
                </Descriptions.Item>
                {currentDetail.task_log && (
                  <>
                    <Descriptions.Item label="任务ID">
                      <Text copyable>{currentDetail.task_log.task_id}</Text>
                    </Descriptions.Item>
                    <Descriptions.Item label="任务状态">
                      {renderStatusTag(currentDetail.task_log.task_status)}
                    </Descriptions.Item>
                  </>
                )}
              </Descriptions>
            </Card>

            {currentDetail.task_log ? (
              <>
                <Card title="关联任务概览" size="small" bordered={false} className="detail-card">
                  <Descriptions column={2} size="small">
                    <Descriptions.Item label="业务场景">
                      {renderBusinessTag(currentDetail.task_log.business_type)}
                    </Descriptions.Item>
                    <Descriptions.Item label="最终响应">
                      {renderResponseTypeTag(currentDetail.task_log.final_response_type)}
                    </Descriptions.Item>
                    <Descriptions.Item label="结束原因">
                      {renderReasonTag(currentDetail.task_log.end_reason)}
                    </Descriptions.Item>
                    <Descriptions.Item label="总耗时">
                      {formatElapsed(currentDetail.task_log.total_elapsed_ms)}
                    </Descriptions.Item>
                    <Descriptions.Item label="运行次数">
                      {currentDetail.task_log.run_count}
                    </Descriptions.Item>
                    <Descriptions.Item label="反问次数">
                      {currentDetail.task_log.ask_user_count}
                    </Descriptions.Item>
                    <Descriptions.Item label="工具调用">
                      {currentDetail.task_log.tool_call_count}
                    </Descriptions.Item>
                    <Descriptions.Item label="外部工具">
                      {currentDetail.task_log.external_tool_call_count}
                    </Descriptions.Item>
                    <Descriptions.Item label="开始时间">
                      {formatDate(currentDetail.task_log.started_at)}
                    </Descriptions.Item>
                    <Descriptions.Item label="结束时间">
                      {formatDate(currentDetail.task_log.finished_at)}
                    </Descriptions.Item>
                  </Descriptions>

                  <div className="feedback-task-block">
                    <Text strong>任务问题</Text>
                    <Paragraph className="feedback-content-block">
                      {currentDetail.task_log.root_question}
                    </Paragraph>
                  </div>

                  <div className="feedback-task-block">
                    <Text strong>{currentDetail.task_log.task_status === 'waiting_user' ? '当前反问' : '最终回复摘要'}</Text>
                    <Paragraph className="feedback-content-block">
                      {currentDetail.task_log.task_status === 'waiting_user'
                        ? currentDetail.task_log.latest_ask_user_question || currentDetail.task_log.final_response_preview || '-'
                        : currentDetail.task_log.final_response_preview || '-'}
                    </Paragraph>
                  </div>

                  {currentDetail.task_log.main_tool_names.length > 0 && (
                    <div className="feedback-task-block">
                      <Text strong>主要工具</Text>
                      <div className="feedback-tag-row">
                        {currentDetail.task_log.main_tool_names.map(toolName => (
                          <Tag key={toolName}>{toolName}</Tag>
                        ))}
                      </div>
                    </div>
                  )}

                  {currentDetail.task_log.latest_missing_fields.length > 0 && (
                    <div className="feedback-task-block">
                      <Text strong>最近缺失字段</Text>
                      <div className="feedback-tag-row">
                        {currentDetail.task_log.latest_missing_fields.map(field => (
                          <Tag key={field} color="gold">{field}</Tag>
                        ))}
                      </div>
                    </div>
                  )}
                </Card>

                {currentDetail.run_log && (
                  <Card title="本次反馈对应运行" size="small" bordered={false} className="detail-card">
                    <Descriptions column={2} size="small">
                      <Descriptions.Item label="运行序号">
                        Run {currentDetail.run_log.sequence_no}
                      </Descriptions.Item>
                      <Descriptions.Item label="触发方式">
                        {TRIGGER_TYPE_MAP[currentDetail.run_log.trigger_type || ''] || currentDetail.run_log.trigger_type || '-'}
                      </Descriptions.Item>
                      <Descriptions.Item label="运行状态">
                        {renderStatusTag(currentDetail.run_log.run_status)}
                      </Descriptions.Item>
                      <Descriptions.Item label="结束原因">
                        {renderReasonTag(currentDetail.run_log.end_reason)}
                      </Descriptions.Item>
                      <Descriptions.Item label="响应类型">
                        {renderResponseTypeTag(currentDetail.run_log.response_type)}
                      </Descriptions.Item>
                      <Descriptions.Item label="耗时">
                        {formatElapsed(currentDetail.run_log.elapsed_ms)}
                      </Descriptions.Item>
                    </Descriptions>

                    <div className="feedback-task-block">
                      <Text strong>本次输出摘要</Text>
                      <Paragraph className="feedback-content-block">
                        {currentDetail.run_log.response_preview || currentDetail.run_log.ask_user_question || '-'}
                      </Paragraph>
                    </div>

                    {currentDetail.run_log.missing_fields.length > 0 && (
                      <div className="feedback-task-block">
                        <Text strong>本次缺失字段</Text>
                        <div className="feedback-tag-row">
                          {currentDetail.run_log.missing_fields.map(field => (
                            <Tag key={field} color="gold">{field}</Tag>
                          ))}
                        </div>
                      </div>
                    )}
                  </Card>
                )}

                <Card title="完整任务运行链路" size="small" bordered={false} className="detail-card">
                  {currentDetail.task_log.runs.length > 0 ? (
                    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                      {currentDetail.task_log.runs.map(run => (
                        <RuntimeRunCard
                          key={run.run_id}
                          run={run}
                          highlighted={run.request_id === currentDetail.request_id}
                        />
                      ))}
                    </Space>
                  ) : (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前任务没有运行记录" />
                  )}
                </Card>
              </>
            ) : currentDetail.chat_log ? (
              <Card title="兼容旧版对话日志" size="small" bordered={false} className="detail-card">
                <Descriptions column={2} size="small">
                  <Descriptions.Item label="响应类型">
                    {renderResponseTypeTag(currentDetail.chat_log.response_type)}
                  </Descriptions.Item>
                  <Descriptions.Item label="耗时">
                    {formatElapsed(currentDetail.chat_log.elapsed_ms)}
                  </Descriptions.Item>
                  <Descriptions.Item label="意图类型">
                    {currentDetail.chat_log.intent_type || '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="请求模式">
                    {currentDetail.chat_log.request_mode}
                  </Descriptions.Item>
                </Descriptions>

                <div className="feedback-task-block">
                  <Text strong>用户消息</Text>
                  <Paragraph copyable className="feedback-content-block">
                    {currentDetail.chat_log.user_message}
                  </Paragraph>
                </div>

                {currentDetail.chat_log.response_content && (
                  <div className="feedback-task-block">
                    <Text strong>响应内容</Text>
                    <div className="feedback-json-block">
                      {renderJson(currentDetail.chat_log.response_content)}
                    </div>
                  </div>
                )}
              </Card>
            ) : (
              <Card title="关联日志" size="small" bordered={false} className="detail-card">
                <Text type="secondary">未找到关联的任务日志或旧版对话日志</Text>
              </Card>
            )}
          </Space>
        ) : null}
      </Modal>
    </div>
  )
}
