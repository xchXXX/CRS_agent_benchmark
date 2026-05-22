import { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import {
  Plus, CircleCheck, Info, TriangleAlert, CircleX, X, Zap,
  WifiOff, ServerCrash, CircleAlert, RefreshCw, SearchSlash,
  Pencil, CircleCheckBig, ChevronRight, ChevronLeft, Cpu,
  CircleQuestionMark, Search, MessageSquare, Mic, SquareStop,
  FileText
} from 'lucide-react'
import './styles/index.css'
import DocumentViewer from './components/DocumentViewer'
import CircuitBodyHitPanel, { type CircuitBodyBestHit, type CircuitBodySearch } from './components/CircuitBodyHitPanel'
import ReportViewer from './components/ReportViewer'
import { MarkdownRenderer } from './components/MarkdownRenderer'
import ImageUploadButton from './components/ImageUploadButton'
import GeneratingProgress from './components/GeneratingProgress'
import SuggestionChips from './components/SuggestionChips'
import SwitchConfirmDialog from './components/SwitchConfirmDialog'
import ClarifyWizard, { type WizardState, type WizardRound, type TopResult } from './components/ClarifyWizard'
import ParameterQueryCard from './components/ParameterQueryCard'
import FeedbackCard from './components/FeedbackCard'
import AskUserShell from './modules/ask-user-v2/components/AskUserShell'
import { getAskUserV2Form, type AskUserV2Form, type AskUserV2FormState, type AskUserV2Submission } from './modules/ask-user-v2/types'
import { chat as chatApi, chatStream, chatStreamWithImages, chatWithImages, notifyStreamAbort, getImageEvidenceAvailable, getRepairKnowledgeSource, getParameterQuerySource } from './services/api'
import { aliyunSpeechService } from './services/aliyunSpeech'
import { compressImage, getImagePreviewUrl, revokeImagePreviewUrl } from './utils/imageCompressor'
import { requestDeleteOssImages, uploadImage } from './utils/aliOssUtils'
import { getStoredToken } from './utils/tokenValidator'
import { taskManager } from './services/sse'
import { fetchFrontendRuntimeConfig, type FrontendRuntimeConfig } from './utils/debugConsole'
import type {
  ChatRequest,
  ChatResponse,
  ChatClarifyOption,
  FaultContent,
  Notification,
  BusinessType,
  Mode,
  SuggestedQuestion,
  Lifecycle,
  RepairKnowledgeSourceRef,
  RepairKnowledgeSourceDetail,
  ParameterQueryContent,
  ParameterQuerySourceRef,
  ParameterQuerySourceDetail
} from './types'
import type { RepairFollowupState, RepairFollowupFieldGroupState } from './components/RepairFollowupCard'

// Types
interface SearchResult {
  file_id: string
  doc_id: string
  ref_file_id?: number  // 关联文件ID（外部系统）
  parent_id?: number    // 父节点ID（外部系统）
  pic_folder_url?: string  // 外部访问URL（用于生成安全访问链接）
  // 共轨之家外部资料字段
  ggzj_sn?: number
  ggzj_data_type?: number
  ggzj_file_no?: string | null
  ggzj_file_type?: string | null
  body_search?: CircuitBodySearch
  title: string
  path: string
  tags: {
    brand?: string
    series?: string
    model?: string
    ecus?: string[]
    subsystems?: string[]
    emissions?: string[]
    suppliers?: string[]
  }
  score: number
  explain: string[]
}

interface ClarifyInfo {
  need: boolean
  facet?: string
  question?: string
  options?: string[]
}

interface CorrectionItem {
  original: string
  corrected: string
  similarity: number
  entity_type: string
  is_auto: boolean
}

interface CorrectionInfo {
  has_correction: boolean
  original_query: string
  corrected_query: string
  corrections: CorrectionItem[]
}

interface ResultValidity {
  has_valid_results: boolean
  reason?: 'no_results' | 'low_relevance' | 'vague_query'
  message?: string
  suggestion?: string
}

interface SearchResponse {
  results: SearchResult[]
  clarify: ClarifyInfo
  correction?: CorrectionInfo
  stats: {
    took_ms: number
    candidates: number
  }
  validity: ResultValidity
}

interface Message {
  id: string
  type: 'user' | 'system' | 'clarify' | 'results' | 'correction' | 'error' | 'no_results' | 'assistant_text' | 'assistant_fault' | 'clarify_intent' | 'clarify_business' | 'clarify_wizard' | 'repair_followup' | 'ask_user_form' | 'param_request' | 'intent_loading' | 'image_recognition' | 'image_evidence' | 'ecu_selection' | 'batch_diagnosis'
  content: string
  clarify?: ClarifyInfo
  correction?: CorrectionInfo
  results?: SearchResult[]
  stats?: { took_ms: number; candidates: number }
  errorType?: 'network' | 'server' | 'unknown'
  retryAction?: () => void
  validity?: ResultValidity
  timestamp: Date
  // 新增字段
  faultContent?: FaultContent
  clarifyOptions?: ChatClarifyOption[]
  clarifyFacet?: string
  business?: BusinessType
  suggestions?: SuggestedQuestion[]
  // 生命周期相关字段
  lifecycle?: 'ongoing' | 'completed' | 'archived'
  selectedIntent?: string  // 用户选择的意图（用于 clarify_intent 完成后显示）
  completedSummary?: string  // 诊断步骤完成后的摘要文本
  resultSummary?: {
    question: string
    resultType: string
    resultCount?: number
    canCollapse?: boolean
  }
  collapsed?: boolean
  // 图片诊断相关字段
  imagePreview?: string
  imagePreviews?: string[]
  imageFileNames?: string[]
  imageOssObjectKeys?: string[]
  imageOssSessionIds?: Array<string | null>
  imageOssDeleteTokens?: string[]
  // 澄清向导字段
  wizardState?: WizardState
  relatedWizardId?: string
  repairFollowupState?: RepairFollowupState
  askUserV2State?: AskUserV2FormState
  // 故障码 LLM 分析时的状态提示
  streamHint?: string
  // 反馈相关字段
  requestId?: string          // 关联的后端 request_id
  feedbackSubmitted?: boolean // 是否已提交反馈
  repairKnowledgeSources?: RepairKnowledgeSourceRef[]
  paramContent?: ParameterQueryContent
  parameterQuerySources?: ParameterQuerySourceRef[]
}

interface PendingImageAttachment {
  id: string
  file: File
  previewUrl: string
  name: string
  size: number
}

interface UploadedMessageImage {
  url: string
  objectKey: string
  uploadSessionId?: string | null
  deleteToken?: string | null
}

interface SearchState {
  query: string
  filters: Record<string, string>
}

const API_BASE = '/chat/api'

type ExampleChipColor = 'cyan' | 'amber' | 'emerald'
type ExampleQuery = { text: string; color: ExampleChipColor }

// 按功能分类的示例问题池
const EXAMPLE_QUERIES_BY_CATEGORY = {
  // 资料搜索 - 查找电路图、维修手册等文档
  DOC_SEARCH: [
    '东风天锦电路图',
    '三一挖掘机电路图',
    '东风天龙D310_整车电路图',
    '一汽解放J6M载货车_油罐车_整车电路图',
    'EDC17CV44针脚定义',
    '尿素泵电路图',
  ],
  // 故障诊断 - 输入故障码，生成诊断报告
  FAULT_DIAGNOSIS: [
    'P0251 故障诊断',
    'P20EE 怎么处理',
    'EDC17CV44报P01F5怎么办',
    'J1939 通讯故障怎么排查',
  ],
  // 维修问答 - 咨询维修知识、技术原理
  GENERAL_CHAT: [
    '尿素泵工作原理',
    '高压共轨压力低是什么原因',
    'CAN 总线电阻正常是多少',
    'SCR 系统原理与常见故障有哪些',
    '传感器 5V 供电短路怎么查',
  ],
  // 参数查询 - 查 ECU 针脚、电压、定义
  PARAM_QUERY: [
    '电装D34 的 K77 针脚定义是什么',
    '易控F17 的 A-01 针脚定义是什么',
    '恒和33针DCU 的 15 针定义是什么',
    'WISE10A 的 1.19 针脚定义是什么',
  ]
}

const EXAMPLE_QUERY_COUNT = Object.keys(EXAMPLE_QUERIES_BY_CATEGORY).length

// 颜色映射：每种功能对应一种颜色
const CATEGORY_COLORS: Record<string, ExampleChipColor> = {
  DOC_SEARCH: 'cyan',        // 资料搜索 - 蓝色
  FAULT_DIAGNOSIS: 'amber',  // 故障诊断 - 橙色
  GENERAL_CHAT: 'emerald',   // 维修问答 - 绿色
  PARAM_QUERY: 'cyan'        // 参数查询 - 蓝色
}

function getRandomSeed(): number {
  try {
    const arr = new Uint32Array(1)
    crypto.getRandomValues(arr)
    return arr[0] >>> 0
  } catch {
    return (Date.now() >>> 0) ^ Math.floor(Math.random() * 0xffffffff)
  }
}

function mulberry32(seed: number): () => number {
  let t = seed >>> 0
  return () => {
    t += 0x6D2B79F5
    let x = t
    x = Math.imul(x ^ (x >>> 15), x | 1)
    x ^= x + Math.imul(x ^ (x >>> 7), x | 61)
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * 从每个功能分类中各选一个示例问题
 * 保证展示的问题涵盖系统当前的核心能力
 */
function pickExampleQueries(count: number, seed: number): ExampleQuery[] {
  const rng = mulberry32(seed)
  const categories = Object.keys(EXAMPLE_QUERIES_BY_CATEGORY) as Array<keyof typeof EXAMPLE_QUERIES_BY_CATEGORY>
  const results: ExampleQuery[] = []

  // 从每个分类中随机选一个
  for (const category of categories) {
    const pool = EXAMPLE_QUERIES_BY_CATEGORY[category]
    if (pool.length === 0) continue

    // 随机选择索引
    const randomIndex = Math.floor(rng() * pool.length)
    const text = pool[randomIndex]
    const color = CATEGORY_COLORS[category]

    results.push({ text, color })

    // 如果已经够了，停止
    if (results.length >= count) break
  }

  return results
}

function firstNonEmptyValue(...values: unknown[]): unknown {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== '')
}

function normalizeOptionalNumber(value: unknown): number | undefined {
  if (value === undefined || value === null || String(value).trim() === '') {
    return undefined
  }
  const numericValue = Number(value)
  return Number.isFinite(numericValue) ? numericValue : undefined
}

function normalizeOptionalDataType(value: unknown): number | undefined {
  if (value === undefined || value === null || String(value).trim() === '') {
    return undefined
  }
  const textValue = String(value).trim()
  const numericValue = Number(textValue)
  return Number.isFinite(numericValue) ? numericValue : undefined
}

function normalizeDocumentAccessFields(raw: any): {
  pic_folder_url?: string
  ggzj_sn?: number
  ggzj_data_type?: number
  ggzj_file_no?: string | null
  ggzj_file_type?: string | null
} {
  if (!raw || typeof raw !== 'object') {
    return {}
  }

  const picFolderUrl = firstNonEmptyValue(raw.pic_folder_url, raw.picFolderUrl)
  const fileNo = firstNonEmptyValue(raw.ggzj_file_no, raw.ggzjFileNo, raw.fileNo)
  const fileType = firstNonEmptyValue(raw.ggzj_file_type, raw.ggzjFileType, raw.fileType)

  return {
    pic_folder_url: picFolderUrl !== undefined ? String(picFolderUrl) : undefined,
    ggzj_sn: normalizeOptionalNumber(firstNonEmptyValue(raw.ggzj_sn, raw.ggzjSn, raw.sn)),
    ggzj_data_type: normalizeOptionalDataType(firstNonEmptyValue(raw.ggzj_data_type, raw.ggzjDataType, raw.dataType)),
    ggzj_file_no: fileNo !== undefined ? String(fileNo) : null,
    ggzj_file_type: fileType !== undefined ? String(fileType) : null,
  }
}

function buildTopResultFromRaw(raw: any): TopResult | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const fileId = String(raw.file_id || '').trim()
  if (!fileId) return undefined
  const score = Number(raw.score ?? 0)
  const access = normalizeDocumentAccessFields(raw)

  return {
    file_id: fileId,
    title: String(raw.title || raw.filename || ''),
    score: Number.isFinite(score) ? score : 0,
    pic_folder_url: access.pic_folder_url || '',
    brand: raw.brand,
    series: raw.series,
    model: raw.model,
    ggzj_sn: access.ggzj_sn,
    ggzj_data_type: access.ggzj_data_type,
    ggzj_file_no: access.ggzj_file_no,
    ggzj_file_type: access.ggzj_file_type,
    selectionPayload: raw.selectionPayload || raw.selection_payload || {},
  }
}

function buildExistenceInfoFromRaw(raw: any) {
  if (!raw || typeof raw !== 'object') return undefined
  return {
    status: raw.status as 'exact_match' | 'partial_match' | 'no_match',
    message: raw.message,
    suggestions: raw.suggestions
  }
}

function buildWizardPayloadFromAskUser(response: ChatResponse, fallbackQuery: string) {
  const askUser = response.ask_user || response.content
  if (!askUser || typeof askUser !== 'object') {
    return null
  }

  const context = askUser.context || {}
  const topResult = buildTopResultFromRaw(context.top_result)
  const existenceInfo = buildExistenceInfoFromRaw(context.existence_info)

  const newRound: WizardRound = {
    id: Math.random().toString(36).substring(2, 9),
    facet: response.clarify_facet || context.facet || 'ask_user_question',
    question: askUser.question || context.message || '请选择',
    toolCallId: askUser.tool_call_id || response.metadata?.tool_call_id,
    inputType: askUser.input_type,
    allowFreeInput: askUser.allow_free_input,
    inputHint: askUser.input_hint,
    unit: askUser.unit,
    referenceRange: askUser.reference_range,
    context,
    options: (askUser.options || response.clarify_options || []).map((opt: any) => ({
      key: opt.key,
      label: opt.label,
      description: opt.description,
      selectionPayload: opt.selection_payload || {},
    })),
  }

  return {
    newRound,
    resultsCount: context.results_count,
    topResult,
    existenceInfo,
    originalQuery: context.query || fallbackQuery || '',
  }
}

function buildAskUserV2State(response: ChatResponse, fallbackQuery: string): AskUserV2FormState | null {
  const askUser = response.ask_user
  const form = getAskUserV2Form(askUser)
  if (!askUser || !form) {
    return null
  }

  const scene = typeof askUser.context?.scene === 'string' ? askUser.context.scene : ''
  if (scene === 'doc_search' || response.business === 'DOC_SEARCH') {
    return null
  }

  return {
    toolCallId: String(askUser.tool_call_id || response.metadata?.tool_call_id || ''),
    question: String(askUser.question || '请补充必要信息'),
    form,
    status: 'active',
    summaryText: '',
    originalQuery: String(askUser.context?.query || fallbackQuery || ''),
    scene: scene || 'ask_form_v2',
  }
}

function extractChatResponseText(content: ChatResponse['content']): string {
  if (typeof content === 'string') {
    return content
  }
  if (content && typeof content === 'object') {
    const message = (content as any).message
    if (typeof message === 'string') {
      return message
    }
  }
  return ''
}

function isRepairFollowupAskUserContext(context: Record<string, any> | undefined) {
  return context?.scene === 'repair_knowledge_followup' || context?.card_type === 'repair_followup'
}

function normalizeRepairRequiredLevel(value: unknown): RepairFollowupFieldGroupState['requiredLevel'] {
  if (value === 'hard' || value === 'strong' || value === 'soft') {
    return value
  }
  return 'strong'
}

function normalizeRepairSelectionMode(value: unknown): RepairFollowupFieldGroupState['selectionMode'] {
  if (value === 'single' || value === 'multi' || value === 'mixed') {
    return value
  }
  return 'mixed'
}

function inferRepairSelectionMode(
  groupKey: string,
  rawMode: unknown,
  presets: string[]
): RepairFollowupFieldGroupState['selectionMode'] {
  const normalized = normalizeRepairSelectionMode(rawMode)
  if (presets.length === 0) {
    return 'mixed'
  }
  if (normalized !== 'mixed') {
    return normalized
  }

  const hasConcreteFaultCodes = groupKey === 'fault_codes'
    && presets.some((item) => /^[PBUC][0-9A-Z]{4}\b/i.test(item.trim()))
  if (hasConcreteFaultCodes) {
    return 'multi'
  }

  if (['fault_codes', 'working_condition', 'ecu_or_system', 'fault_phenomenon'].includes(groupKey)) {
    return 'single'
  }
  if (['data_evidence', 'repair_history'].includes(groupKey)) {
    return 'multi'
  }

  const presetText = presets.join(' ')
  if (/(暂无|无报码|不清楚|偶发|已知)/.test(presetText)) {
    return 'single'
  }

  return 'mixed'
}

function normalizeRepairPresetList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  return value
    .map((item) => {
      if (typeof item === 'string') return item.trim()
      if (item && typeof item === 'object') {
        return String((item as any).label || (item as any).key || '').trim()
      }
      return ''
    })
    .filter((item) => {
      if (!item || looksLikeRepairFieldPromptText(item) || looksLikeRepairInstructionalText(item)) return false
      const dedupeKey = item.toLowerCase()
      if (seen.has(dedupeKey)) return false
      seen.add(dedupeKey)
      return true
    })
}

function looksLikeRepairInstructionalText(value: string) {
  const text = value.trim()
  if (!text) return false
  return [
    '不要写',
    '不要调用',
    '不要暴露',
    '内部推理',
    '资料不足',
    '当前证据不足',
    '缺乏针对性的维修案例',
    'ask_user_question',
    '唯一允许的方式',
    '必须改为',
  ].some((hint) => text.toLowerCase().includes(hint.toLowerCase()))
}

function looksLikeRepairFieldPromptText(value: string) {
  const text = value.trim()
  if (!text) return false
  if (/[?？]/.test(text)) return true
  if (/^(是否|有无|是不是|能否|请问|请先|请补充|请提供|先确认|确认|什么|多少|哪|怎么|如何|为什么)/.test(text)) return true
  if (/(吗|呢)$/.test(text)) return true

  const hasFieldHint = /(品牌|型号|发动机|故障灯|故障码|报码|温度|现象|工况|ECU|版本|维修历史)/i.test(text)
  if (!hasFieldHint) return false

  const looksLikeBareFieldLabel = /^(?:当前|相关|已掌握的|近期)?(?:车辆)?(?:品牌(?:及发动机型号)?|车系|车型|发动机型号|故障灯\/?报码状态|故障码(?:情况|状态|类别)?|报码(?:情况|状态|方向|类别)?|环境温度(?:及出现条件)?|出现条件|具体难启动表现|故障现象|当前故障现象|工况|ECU(?:\s*或系统信息)?|系统信息|维修历史|关键数据|关键数据流|关键证据|补充信息)(?:信息|情况|状态|表现)?$/i.test(text)
  if (looksLikeBareFieldLabel) return true

  const hasActionHint = /(上传|协助|分析|排查|继续|诊断|查看|给我|直接|先给|请补充|请提供|先确认|确认)/.test(text)
  return hasActionHint
}

function normalizeRepairOptionItems(value: unknown): Array<{ key: string; label: string; description?: string }> {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (typeof item === 'string') {
        const text = item.trim()
        return text && !looksLikeRepairFieldPromptText(text) ? { key: text, label: text } : null
      }
      if (!item || typeof item !== 'object') return null
      const key = String((item as any).key || (item as any).label || '').trim()
      const label = String((item as any).label || (item as any).key || '').trim()
      const description = typeof (item as any).description === 'string'
        ? (item as any).description.trim()
        : undefined
      if (!key || !label || looksLikeRepairFieldPromptText(label)) return null
      return { key, label, description }
    })
    .filter((item): item is { key: string; label: string; description?: string } => Boolean(item))
}

function getRepairPresetOptionLabel(groupKey: string, preset: string) {
  if (groupKey !== 'data_evidence') {
    return preset
  }

  const explicitMap: Record<string, string> = {
    '启动时电瓶电压': '已测启动时电瓶电压',
    '启动转速': '已测启动转速',
    '轨压跟随': '已查看轨压跟随',
    '曲轴/凸轮轴同步状态': '已确认曲轴/凸轮轴同步状态',
    '预热或起动继电器状态': '已检查预热或起动继电器状态',
    'J1939 主干电阻': '已测 J1939 主干电阻',
    'CAN_H/CAN_L 电压': '已测 CAN_H/CAN_L 电压',
    '终端电阻状态': '已确认终端电阻状态',
    '模块在线状态': '已查看模块在线状态',
    '报码截图': '已拍报码截图',
    '故障码截图': '已拍故障码截图',
    '关键数据流': '已记录关键数据流',
    '现场照片': '已拍现场照片',
    '近期维修记录': '有近期维修记录',
    '高低压压力': '已测高低压压力',
    '出风口温度': '已测出风口温度',
    '压缩机工作状态': '已确认压缩机工作状态',
    '冷凝风扇工作状态': '已确认冷凝风扇工作状态',
    '空调相关故障码': '已读取空调相关故障码',
  }
  if (explicitMap[preset]) {
    return explicitMap[preset]
  }
  if (preset.endsWith('截图')) {
    return `已拍${preset}`
  }
  if (/(电压|电阻|压力|温度|转速)/.test(preset)) {
    return `已测${preset}`
  }
  if (/(状态|同步)/.test(preset)) {
    return `已确认${preset}`
  }
  if (/(数据流|跟随|在线)/.test(preset)) {
    return `已查看${preset}`
  }
  return preset
}

function isRepairQuickActionOption(option: { key: string; label: string; description?: string }) {
  const text = `${option.key} ${option.label} ${option.description || ''}`
  return /通用|思路|先给|直接回答|不补充|跳过|稍后|无需补充|上传|协助|分析|继续|诊断|查看/i.test(text)
}

function buildRepairFollowupPayloadFromAskUser(response: ChatResponse, fallbackQuery: string): RepairFollowupState | null {
  const askUser = response.ask_user || response.content
  if (!askUser || typeof askUser !== 'object') {
    return null
  }

  const context = askUser.context || {}
  if (!isRepairFollowupAskUserContext(context)) {
    return null
  }

  const fieldGroups = Array.isArray(context.field_groups) ? context.field_groups : []
  if (fieldGroups.length === 0) {
    return null
  }

  const topLevelOptions = normalizeRepairOptionItems(askUser.options)
  const explicitQuickActions = normalizeRepairOptionItems(context.quick_actions)

  const groups: RepairFollowupFieldGroupState[] = fieldGroups.map((group: any, index: number) => {
    const groupKey = String(group?.key || `repair_group_${index}`)
    const presets = normalizeRepairPresetList(
      group?.presets
      || group?.example_options
      || group?.examples
      || group?.suggestions
      || group?.choices
    )

    return {
      key: groupKey,
      label: looksLikeRepairInstructionalText(String(group?.label || ''))
        ? `补充信息${index + 1}`
        : String(group?.label || `补充信息${index + 1}`),
      requiredLevel: normalizeRepairRequiredLevel(group?.required_level),
      selectionMode: inferRepairSelectionMode(groupKey, group?.selection_mode, presets),
      presets,
      selectedPresets: [],
      textValue: '',
      placeholder: typeof group?.placeholder === 'string' && !looksLikeRepairInstructionalText(group.placeholder)
        ? group.placeholder
        : undefined,
      hint: typeof group?.hint === 'string' && !looksLikeRepairInstructionalText(group.hint)
        ? group.hint
        : undefined,
    }
  })

  const contextSources = Array.isArray(context.source_refs) ? context.source_refs : []
  const sourceRefs: RepairKnowledgeSourceRef[] = contextSources
    .filter((item: any) => item?.id && item?.title)
    .map((item: any) => ({
      id: String(item.id),
      title: String(item.title),
      relation: item.relation === 'related' ? 'related' : 'primary',
      match_score: Number(item.match_score || 0),
    }))

  const quickActions = [
    ...explicitQuickActions,
    ...topLevelOptions.filter(isRepairQuickActionOption),
  ]
    .filter((item, index, array) => array.findIndex((candidate) => candidate.key === item.key) === index)

  return {
    toolCallId: String(askUser.tool_call_id || response.metadata?.tool_call_id || ''),
    question: String(askUser.question || context.message || '请补充必要信息'),
    originalQuery: String(context.query || context.repair_knowledge_query || fallbackQuery || ''),
    status: 'active',
    sourceRefs,
    askReason: typeof context.ask_reason === 'string' && !looksLikeRepairInstructionalText(context.ask_reason)
      ? context.ask_reason
      : undefined,
    groups,
    quickActions,
    summaryText: '',
  }
}

function buildRepairFollowupAskUserV2State(state: RepairFollowupState): AskUserV2FormState {
  const form: AskUserV2Form = {
    form_id: `repair_followup_form_${state.toolCallId}`,
    version: '2.0',
    mode: 'progressive',
    title: '维修问答补充',
    description: '优先点选最接近的情况；没有合适项时再手动补充。',
    ask_reason: state.askReason,
    sections: [
      {
        id: 'core',
        title: '维修问答补充',
        fields: state.groups.map((group) => {
          const hasPresets = group.presets.length > 0
          const isMulti = hasPresets && group.selectionMode === 'multi'
          return {
            key: group.key,
            label: group.label,
            field_type: isMulti ? 'multi_select' : (hasPresets ? 'single_select' : 'text'),
            answer_mode: isMulti ? 'select_and_text' : (hasPresets ? 'select_or_text' : 'text_only'),
            required: group.requiredLevel === 'hard',
            required_level: group.requiredLevel,
            placeholder: group.placeholder,
            hint: group.hint,
            options: group.presets.map((preset) => ({
              key: preset,
              label: getRepairPresetOptionLabel(group.key, preset),
              option_source: 'rule' as const,
              evidence_level: 'confirmed' as const,
            })),
            manual_input: {
              enabled: true,
              always_visible: false,
              placeholder: group.placeholder,
              input_hint: group.hint,
              value_type: group.key === 'fault_codes' ? 'code' : 'text',
            },
          }
        }),
      },
    ],
    actions: state.quickActions.map((action) => ({
      key: action.key,
      label: action.label,
      variant: 'ghost',
      action_type: 'quick_reply',
      payload: { quick_action: action.key },
    })),
    ui_policy: {
      layout: 'stepper',
      auto_submit_single_select: false,
      submit_button_text: '继续分析',
      show_summary_preview: true,
      allow_skip_optional: true,
      dense: true,
    },
    validation_policy: {},
  }

  return {
    toolCallId: state.toolCallId,
    question: state.question,
    form,
    status: state.status,
    summaryText: state.summaryText,
    originalQuery: state.originalQuery,
    scene: 'repair_knowledge_followup',
  }
}

function normalizeSummarySegment(value: string) {
  return value.replace(/\s+/g, ' ').trim()
}

function splitSummaryText(value: string | undefined) {
  if (!value) return []
  return value
    .split(/[；;\n]+/g)
    .map((item) => normalizeSummarySegment(item))
    .filter(Boolean)
}

function mergeSummaryText(...values: Array<string | undefined>) {
  const seen = new Set<string>()
  const merged: string[] = []

  for (const value of values) {
    for (const segment of splitSummaryText(value)) {
      if (seen.has(segment)) continue
      seen.add(segment)
      merged.push(segment)
    }
  }

  return merged.join('；')
}

function normalizeRepairQuery(value: string | undefined) {
  return String(value || '').trim()
}

function isRepairSupplementStatusOpen(status: string | undefined) {
  return status === 'active' || status === 'submitting' || status === 'submitted'
}

function getRepairSupplementQuery(message: Message) {
  if (message.type === 'ask_user_form') {
    return normalizeRepairQuery(message.askUserV2State?.originalQuery)
  }
  if (message.type === 'repair_followup') {
    return normalizeRepairQuery(message.repairFollowupState?.originalQuery)
  }
  return ''
}

function getRepairSupplementSummary(message: Message) {
  if (message.type === 'ask_user_form') {
    return message.askUserV2State?.summaryText || ''
  }
  if (message.type === 'repair_followup') {
    return message.repairFollowupState?.summaryText || ''
  }
  return ''
}

function isRepairSupplementMessage(message: Message, query: string) {
  const expectedQuery = normalizeRepairQuery(query)
  if (!expectedQuery) {
    return false
  }

  if (
    message.type === 'ask_user_form'
    && message.askUserV2State?.scene === 'repair_knowledge_followup'
    && isRepairSupplementStatusOpen(message.askUserV2State?.status)
  ) {
    return getRepairSupplementQuery(message) === expectedQuery
  }

  if (
    message.type === 'repair_followup'
    && isRepairSupplementStatusOpen(message.repairFollowupState?.status)
  ) {
    return getRepairSupplementQuery(message) === expectedQuery
  }

  return false
}

function extractRepairKnowledgeSources(metadata: ChatResponse['metadata'] | undefined): RepairKnowledgeSourceRef[] | undefined {
  const sources = metadata?.repair_knowledge_sources
  if (!Array.isArray(sources) || sources.length === 0) {
    return undefined
  }

  return sources
    .filter((item): item is RepairKnowledgeSourceRef => Boolean(item?.id && item?.title))
    .map((item) => ({
      id: String(item.id),
      title: String(item.title),
      relation: item.relation === 'related' ? 'related' : 'primary',
      match_score: Number(item.match_score || 0),
    }))
}

const CIRCUIT_HIT_SUBLIST_LIMIT = 3
const CIRCUIT_NEARBY_HIT_DISTANCE_PX = 900
const DEFAULT_WEBVIEW_DEBUG_URL = 'https://mft-static.51gonggui.com/pdf-loader/index.html#/?page=2&file=https://mft-static.51gonggui.com/wps/file/img/%E5%85%B1%E8%BD%A8%E5%8E%9F%E5%88%9B_%E4%BA%94%E5%8D%81%E9%93%83_2017_C&E_6WG1_ECU_%E7%94%B5%E8%B7%AF%E5%9B%BE30653'
const WEBVIEW_DEBUG_FAB_POSITION_KEY = 'crs_webview_debug_fab_position'

function getDefaultWebviewDebugFabPosition() {
  if (typeof window === 'undefined') {
    return { x: 0, y: 0 }
  }
  return {
    x: Math.max(12, window.innerWidth - 62),
    y: Math.max(12, window.innerHeight - 154),
  }
}

function clampWebviewDebugFabPosition(position: { x: number; y: number }) {
  if (typeof window === 'undefined') {
    return position
  }
  const margin = 8
  const size = 46
  const maxX = Math.max(margin, window.innerWidth - size - margin)
  const maxY = Math.max(margin, window.innerHeight - size - margin)
  return {
    x: Math.min(Math.max(margin, Math.round(position.x)), maxX),
    y: Math.min(Math.max(margin, Math.round(position.y)), maxY),
  }
}

function loadWebviewDebugFabPosition() {
  if (typeof window === 'undefined') {
    return { x: 0, y: 0 }
  }
  try {
    const saved = window.localStorage.getItem(WEBVIEW_DEBUG_FAB_POSITION_KEY)
    if (saved) {
      const parsed = JSON.parse(saved) as { x?: unknown; y?: unknown }
      const x = Number(parsed.x)
      const y = Number(parsed.y)
      if (Number.isFinite(x) && Number.isFinite(y)) {
        return clampWebviewDebugFabPosition({ x, y })
      }
    }
  } catch {
    // ignore invalid saved position
  }
  return getDefaultWebviewDebugFabPosition()
}

function validCircuitHitBox(value?: number[]): value is [number, number, number, number] {
  return Array.isArray(value) &&
    value.length === 4 &&
    value.every((part) => Number.isFinite(part)) &&
    value[2] > value[0] &&
    value[3] > value[1]
}

function primaryCircuitHitBox(hit?: CircuitBodyBestHit): [number, number, number, number] | null {
  const boxes = hit?.highlight_boxes_px
  if (!Array.isArray(boxes)) {
    return null
  }
  const box = boxes.find(validCircuitHitBox)
  return box || null
}

function circuitHitCenter(hit?: CircuitBodyBestHit): { x: number; y: number; size: number } | null {
  const box = primaryCircuitHitBox(hit)
  if (!box) {
    return null
  }
  return {
    x: (box[0] + box[2]) / 2,
    y: (box[1] + box[3]) / 2,
    size: Math.max(box[2] - box[0], box[3] - box[1], 1),
  }
}

function areNearbyCircuitHits(left: CircuitBodyBestHit, right: CircuitBodyBestHit): boolean {
  if (left.page_index !== right.page_index) {
    return false
  }
  const leftCenter = circuitHitCenter(left)
  const rightCenter = circuitHitCenter(right)
  if (!leftCenter || !rightCenter) {
    return false
  }
  const distance = Math.hypot(leftCenter.x - rightCenter.x, leftCenter.y - rightCenter.y)
  const threshold = Math.max(
    CIRCUIT_NEARBY_HIT_DISTANCE_PX,
    Math.max(leftCenter.size, rightCenter.size) * 3
  )
  return distance <= threshold
}

function compactCircuitHits(hits: CircuitBodyBestHit[]): {
  visibleHits: CircuitBodyBestHit[]
  totalCount: number
  hiddenHitCount: number
  mergedNearbyCount: number
} {
  const groups: Array<{ hit: CircuitBodyBestHit; nearbyCount: number }> = []
  for (const hit of hits) {
    const targetGroup = groups.find((group) => areNearbyCircuitHits(group.hit, hit))
    if (targetGroup) {
      targetGroup.nearbyCount += 1
    } else {
      groups.push({ hit, nearbyCount: 0 })
    }
  }

  const visibleGroups = groups.slice(0, CIRCUIT_HIT_SUBLIST_LIMIT)
  const visibleHits = visibleGroups.map((group) => group.hit)
  return {
    visibleHits,
    totalCount: hits.length,
    hiddenHitCount: Math.max(hits.length - visibleHits.length, 0),
    mergedNearbyCount: groups.reduce((total, group) => total + group.nearbyCount, 0),
  }
}

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [searchState, setSearchState] = useState<SearchState>({ query: '', filters: {} })
  const [isListening, setIsListening] = useState(false)
  const [speechSupported, setSpeechSupported] = useState(false)
  // 文档查看器状态
  const [viewerDoc, setViewerDoc] = useState<{
    title: string
    picFolderUrl: string
    token: string
    urlType?: string
    initialPage?: number
    circuitSearch?: {
      enabled: boolean
      viewerToken?: string
      keyword?: string
      hits?: CircuitBodyBestHit[]
      activeHitId?: string
    }
  } | null>(null)
  // 报告查看器状态
  const [showReportViewer, setShowReportViewer] = useState(false)
  const [currentReportUrl, setCurrentReportUrl] = useState<string | null>(null)
  const [currentReportToken, setCurrentReportToken] = useState<string | null>(null)
  // 新增状态
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [useChatApi, setUseChatApi] = useState(true) // 是否使用新聊天API
  const [ecuInputMode, setEcuInputMode] = useState<string | null>(null) // 当前开启ECU输入的消息ID
  const [ecuInputValue, setEcuInputValue] = useState('') // ECU输入框的值
  // 加载阶段（0=搜索中, 1=智能分析中）
  const [loadingPhase, setLoadingPhase] = useState(0)
  // 模式选择状态
  const [currentMode, setCurrentMode] = useState<Mode>('auto')
  const [modeMenuOpen, setModeMenuOpen] = useState(false)
  // 流式输出状态
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null)
  // 是否正在流式输出 Chat 回复（收到 chunk 后才为 true，用于控制停止按钮显示）
  const [isStreamingChat, setIsStreamingChat] = useState(false)
  // 流式中断控制器
  const abortControllerRef = useRef<AbortController | null>(null)
  // 图片附件状态：选图后先挂在输入框，随文字一起发送。
  const [pendingImageAttachments, setPendingImageAttachments] = useState<PendingImageAttachment[]>([])
  const pendingImageAttachmentsRef = useRef<PendingImageAttachment[]>([])
  const [imagePreviewModal, setImagePreviewModal] = useState<{ src: string; alt: string } | null>(null)
  // 通用图片证据识别功能是否可用。默认展示入口，避免可用性探测失败时按钮直接消失。
  const [imageEvidenceAvailable, setImageEvidenceAvailable] = useState(true)
  const [imageEvidenceMaxFiles, setImageEvidenceMaxFiles] = useState(3)
  // 会话恢复标记
  const [isSessionRestored, setIsSessionRestored] = useState(false)
  // 生命周期管理状态
  const [currentLifecycle, setCurrentLifecycle] = useState<Lifecycle>('idle')
  const [frontendRuntimeConfig, setFrontendRuntimeConfig] = useState<FrontendRuntimeConfig | null>(null)
  const [webviewDebugFabPosition, setWebviewDebugFabPosition] = useState(loadWebviewDebugFabPosition)
  // 搜索结果分页状态：记录每个消息的当前页码
  const [resultPages, setResultPages] = useState<Record<string, number>>({})
  const [expandedCircuitHitByMessage, setExpandedCircuitHitByMessage] = useState<Record<string, string | null>>({})
  const RESULTS_PER_PAGE = 5  // 每页显示5条结果
  const [currentBusiness, setCurrentBusiness] = useState<BusinessType | null>(null)
  const [switchConfirmState, setSwitchConfirmState] = useState<{
    isOpen: boolean
    pendingMessage: string
    contextInfo?: {
      clarifyRound?: number
      query?: string
    }
  }>({
    isOpen: false,
    pendingMessage: ''
  })
  // 新搜索确认弹窗
  const [showNewSearchConfirm, setShowNewSearchConfirm] = useState(false)
  // 示例问题刷新计数器（每次新搜索 +1，触发重新随机取样）
  const [exampleRefreshKey, setExampleRefreshKey] = useState(0)
  // 图片上传冲突确认的 resolve 函数
  const imageUploadResolveRef = useRef<((value: boolean) => void) | null>(null)
  const modeMenuRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const webviewDebugFabDragRef = useRef<{
    pointerId: number
    startX: number
    startY: number
    originX: number
    originY: number
    currentX: number
    currentY: number
    moved: boolean
  } | null>(null)
  // Token 诊断：连续点击 logo 5 次触发
  const logoClickCountRef = useRef(0)
  const logoClickTimerRef = useRef<number | null>(null)
  const [tokenDiagnoseResult, setTokenDiagnoseResult] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchFrontendRuntimeConfig()
      .then((config) => {
        if (!cancelled) {
          setFrontendRuntimeConfig(config)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFrontendRuntimeConfig(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const handleResize = () => {
      setWebviewDebugFabPosition((current) => clampWebviewDebugFabPosition(current))
    }
    window.addEventListener('resize', handleResize)
    window.visualViewport?.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
      window.visualViewport?.removeEventListener('resize', handleResize)
    }
  }, [])
  const [repairKnowledgeModal, setRepairKnowledgeModal] = useState<{
    sources: RepairKnowledgeSourceRef[]
    activeSourceId: string | null
    activeDetail: RepairKnowledgeSourceDetail | null
    loading: boolean
  } | null>(null)
  const [parameterQueryModal, setParameterQueryModal] = useState<{
    sources: ParameterQuerySourceRef[]
    activeSourceId: string | null
    activeDetail: ParameterQuerySourceDetail | null
    loading: boolean
  } | null>(null)

  useEffect(() => {
    if (!imagePreviewModal) {
      return undefined
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setImagePreviewModal(null)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [imagePreviewModal])

  // ==================== Token 诊断（连续点击 logo 5 次触发） ====================
  const handleLogoDiagnose = useCallback(async () => {
    // 2秒内连续点击才计数
    if (logoClickTimerRef.current !== null) {
      window.clearTimeout(logoClickTimerRef.current)
    }
    logoClickCountRef.current += 1
    logoClickTimerRef.current = window.setTimeout(() => {
      logoClickCountRef.current = 0
    }, 2000)

    if (logoClickCountRef.current < 5) return
    logoClickCountRef.current = 0

    // 收集所有来源信息
    const result: Record<string, unknown> = {}

    // 1. 当前 URL
    result['当前URL'] = window.location.href

    // 2. URL 参数（手动解析，空格还原为 +，修复 WebView 对 base64 + 号的错误解码）
    const urlParams: Record<string, string> = {}
    const search = window.location.search
    if (search && search.length > 1) {
      const pairs = search.substring(1).split('&')
      for (const pair of pairs) {
        const eqIdx = pair.indexOf('=')
        if (eqIdx === -1) continue
        const key = decodeURIComponent(pair.substring(0, eqIdx))
        const value = decodeURIComponent(pair.substring(eqIdx + 1)).replace(/ /g, '+')
        urlParams[key] = value
      }
    }
    result['URL参数(原始)'] = Object.keys(urlParams).length > 0 ? urlParams : '(无)'

    // 3. sessionStorage
    const ssTokens: Record<string, string> = {}
    for (const key of ['app_token', 'appToken', 'app-token', 'token']) {
      const v = sessionStorage.getItem(key)
      if (v) ssTokens[key] = v.substring(0, 20) + '...'
    }
    result['sessionStorage'] = Object.keys(ssTokens).length > 0 ? ssTokens : '(无)'

    // 4. localStorage
    const lsTokens: Record<string, string> = {}
    for (const key of ['appToken', 'app-token', 'token']) {
      const v = localStorage.getItem(key)
      if (v) lsTokens[key] = v.substring(0, 20) + '...'
    }
    result['localStorage'] = Object.keys(lsTokens).length > 0 ? lsTokens : '(无)'

    // 5. Cookie
    result['Cookie'] = document.cookie || '(无)'

    // 6. UserAgent
    result['UserAgent'] = navigator.userAgent

    // 7. APICloud JSBridge (window.api) 探测
    const apiInfo: Record<string, unknown> = {}
    if (window.api) {
      apiInfo['存在'] = true
      // 关键参数完整展示（不截断）
      for (const key of ['appParam', 'pageParam', 'wgtParam']) {
        const val = (window.api as Record<string, unknown>)[key]
        if (val !== undefined && val !== null) {
          if (typeof val === 'string') {
            try { apiInfo[`api.${key}`] = JSON.parse(val) } catch { apiInfo[`api.${key}`] = val }
          } else {
            apiInfo[`api.${key}`] = val
          }
        }
      }
      // 其他属性
      for (const key of ['appId', 'appName', 'appVersion', 'deviceId']) {
        const val = (window.api as Record<string, unknown>)[key]
        if (val !== undefined) apiInfo[`api.${key}`] = val
      }
      // 尝试通过 getPrefs 读取可能的 token
      try {
        const getPrefs = (window.api as Record<string, unknown>).getPrefs as
          ((opts: { key: string }) => string | undefined) | undefined
        if (typeof getPrefs === 'function') {
          const prefsTokens: Record<string, string> = {}
          for (const key of ['appToken', 'app-token', 'token', 'app_token', 'userToken']) {
            try {
              const v = getPrefs({ key })
              if (v) prefsTokens[key] = typeof v === 'string' && v.length > 30 ? v.substring(0, 30) + '...' : String(v)
            } catch { /* ignore */ }
          }
          apiInfo['getPrefs'] = Object.keys(prefsTokens).length > 0 ? prefsTokens : '(无)'
        }
      } catch { /* ignore */ }
      // 尝试 getGlobalData
      try {
        const getGlobalData = (window.api as Record<string, unknown>).getGlobalData as
          ((opts: { key: string }) => unknown) | undefined
        if (typeof getGlobalData === 'function') {
          const globalData: Record<string, unknown> = {}
          for (const key of ['appToken', 'token', 'userInfo', 'loginInfo']) {
            try {
              const v = getGlobalData({ key })
              if (v !== undefined && v !== null) {
                const s = typeof v === 'string' ? v : JSON.stringify(v)
                globalData[key] = s.length > 60 ? s.substring(0, 60) + '...' : s
              }
            } catch { /* ignore */ }
          }
          apiInfo['getGlobalData'] = Object.keys(globalData).length > 0 ? globalData : '(无)'
        }
      } catch { /* ignore */ }
    } else {
      apiInfo['存在'] = false
    }
    result['APICloud(window.api)'] = apiInfo

    // 8. 后端请求头诊断
    try {
      const resp = await fetch('/chat/api/legacy/token-diagnose')
      const data = await resp.json()
      result['后端收到的请求头(token相关)'] = data.token_headers && Object.keys(data.token_headers).length > 0
        ? data.token_headers : '(无)'
      result['后端收到的所有请求头'] = data.all_headers
      result['后端收到的Cookie'] = data.cookies && Object.keys(data.cookies).length > 0
        ? data.cookies : '(无)'
    } catch {
      result['后端诊断'] = '请求失败'
    }

    setTokenDiagnoseResult(result)
  }, [])

  // ==================== 加载阶段渐进提示 ====================
  useEffect(() => {
    if (isLoading && !streamingMessageId) {
      const timer = setTimeout(() => setLoadingPhase(1), 1000)
      return () => clearTimeout(timer)
    }
    setLoadingPhase(0)
  }, [isLoading, streamingMessageId])

  // ==================== 图片证据可用性检查 ====================
  useEffect(() => {
    getImageEvidenceAvailable()
      .then(res => {
        setImageEvidenceAvailable(res.available !== false)
        if (res.max_images) {
          setImageEvidenceMaxFiles(res.max_images)
        }
      })
      .catch((error) => {
        console.warn('图片证据可用性检查失败，保留上传入口:', error)
        setImageEvidenceAvailable(true)
      })
  }, [])

  // ==================== 会话状态持久化 ====================
  const SESSION_STORAGE_KEY = 'doc_search_session'

  // 从 localStorage 恢复会话状态
  useEffect(() => {
    try {
      const saved = localStorage.getItem(SESSION_STORAGE_KEY)
      if (saved) {
        const state = JSON.parse(saved)
        // 恢复消息（过滤掉不可恢复的临时状态）
        if (state.messages && Array.isArray(state.messages)) {
          const restoredMessages = state.messages.map((msg: Message) => ({
            ...msg,
            timestamp: new Date(msg.timestamp),
          }))
          setMessages(restoredMessages)
        }
        if (state.sessionId) setSessionId(state.sessionId)
        if (state.searchState) setSearchState(state.searchState)
        if (state.currentMode) setCurrentMode(state.currentMode)
	        console.log('会话状态已恢复')
      }
    } catch (e) {
      console.error('恢复会话状态失败:', e)
    }
    setIsSessionRestored(true)
  }, [])

  // 保存会话状态到 localStorage（防抖）
  useEffect(() => {
    if (!isSessionRestored) return // 等待恢复完成后再开始保存

    const saveState = () => {
      try {
        // 准备要保存的消息（排除临时的 blob URL）
        const messagesToSave = messages.map(msg => ({
          ...msg,
          // 单图临时 blob URL 无法跨页面刷新恢复。
          imagePreview: undefined,
          imagePreviews: msg.imagePreviews?.filter(preview => !preview.startsWith('blob:')),
        }))

        const state = {
          messages: messagesToSave,
          sessionId,
	          searchState,
	          currentMode,
	        }
        localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(state))
      } catch (e) {
        console.error('保存会话状态失败:', e)
      }
    }

    // 使用防抖避免频繁写入
    const timer = setTimeout(saveState, 500)
    return () => clearTimeout(timer)
  }, [messages, sessionId, searchState, currentMode, isSessionRestored])
  // ==================== 会话状态持久化结束 ====================

  useEffect(() => {
    pendingImageAttachmentsRef.current = pendingImageAttachments
  }, [pendingImageAttachments])

  useEffect(() => {
    return () => {
      pendingImageAttachmentsRef.current.forEach(attachment => {
        revokeImagePreviewUrl(attachment.previewUrl)
      })
      pendingImageAttachmentsRef.current = []
    }
  }, [])

  // ==================== 通知自动消失 ====================
  useEffect(() => {
    if (notifications.length === 0) return

    // 为每个通知设置2.5秒后自动消失
    const timers = notifications.map(notification => {
      return setTimeout(() => {
        setNotifications(prev => prev.filter(n => n.id !== notification.id))
      }, 2500)
    })

    return () => {
      timers.forEach(timer => clearTimeout(timer))
    }
  }, [notifications])
  // ==================== 通知自动消失结束 ====================

  // 示例查询 - 简洁展示（每次进入随机 4 条，覆盖四类能力）
  const exampleQueries = useMemo(() => pickExampleQueries(EXAMPLE_QUERY_COUNT, getRandomSeed()), [exampleRefreshKey])

  // 仅允许最新一条助手消息展示推荐，避免点击历史推荐导致上下文穿越
  const latestSuggestionMessageId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i]
      if (
        (msg.type === 'assistant_text' || msg.type === 'assistant_fault') &&
        msg.lifecycle !== 'archived' &&
        msg.suggestions &&
        msg.suggestions.length > 0
      ) {
        return msg.id
      }
    }
    return null
  }, [messages])

  const latestUserMessageIndex = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].type === 'user') {
        return i
      }
    }
    return -1
  }, [messages])

  // 语音输入规范化：将中文数字、拼音转换为标准格式
  const normalizeVoiceInput = (text: string): string => {
    // 中文数字映射
    const chineseNumMap: Record<string, string> = {
      '零': '0', '〇': '0', '洞': '0',
      '一': '1', '幺': '1', '壹': '1',
      '二': '2', '两': '2', '贰': '2',
      '三': '3', '叁': '3',
      '四': '4', '肆': '4',
      '五': '5', '伍': '5',
      '六': '6', '陆': '6',
      '七': '7', '柒': '7', '拐': '7',
      '八': '8', '捌': '8',
      '九': '9', '玖': '9', '勾': '9',
    }

    let result = text

    // 1. 替换中文数字
    for (const [cn, num] of Object.entries(chineseNumMap)) {
      result = result.replace(new RegExp(cn, 'g'), num)
    }

    // 2. 处理字母（去除字母间空格，转大写）
    // 匹配连续的 "字母+空格" 模式，如 "e d c" -> "EDC"
    result = result.replace(/([a-zA-Z])\s+(?=[a-zA-Z])/g, '$1')

    // 3. 去除数字和字母之间的空格，如 "17 c v" -> "17CV"
    result = result.replace(/(\d)\s+([a-zA-Z])/g, '$1$2')
    result = result.replace(/([a-zA-Z])\s+(\d)/g, '$1$2')

    // 4. 将字母转大写（ECU型号通常大写）
    result = result.replace(/[a-z]+/g, (match) => match.toUpperCase())

    // 5. 去除末尾标点符号
    result = result.replace(/[。，、；：？！,.;:?!]+$/, '')

    return result
  }

  // 检查语音识别服务是否可用
  useEffect(() => {
    // 检查浏览器是否支持
    if (!aliyunSpeechService.isSupported()) {
      console.warn('当前环境不支持语音识别')
      setSpeechSupported(false)
      return
    }

    // 检查阿里云语音服务是否可用
    const checkAliyunSpeech = async () => {
      const available = await aliyunSpeechService.checkAvailability()
      if (available) {
        console.log('阿里云语音识别服务可用')
        setSpeechSupported(true)
      } else {
        console.warn('阿里云语音识别服务不可用')
        setSpeechSupported(false)
      }
    }

    checkAliyunSpeech()
  }, [])

  // 开始实时语音识别
  const startRecording = async () => {
    setInputValue('')

    try {
      await aliyunSpeechService.start({
        onStart: () => {
          console.log('语音识别已启动')
          setIsListening(true)
        },
        onPartialResult: (text) => {
          // 中间结果，实时更新输入框
          const normalized = normalizeVoiceInput(text)
          setInputValue(normalized)
        },
        onFinalResult: (text) => {
          // 最终结果
          const normalized = normalizeVoiceInput(text)
          console.log('识别结果:', text, '-> 规范化:', normalized)
          setInputValue(normalized)
        },
        onError: (error) => {
          console.error('语音识别错误:', error)
          setIsListening(false)
        },
        onStop: () => {
          console.log('语音识别已停止')
          setIsListening(false)
        },
      })
    } catch (err) {
      const error = err as Error
      console.error('启动语音识别失败:', error)
      setIsListening(false)
    }
  }

  // 停止语音识别
  const stopRecording = () => {
    aliyunSpeechService.stop()
    setIsListening(false)
  }

  // 按住说话 - 开始录音
  const handleVoiceStart = async (e: React.MouseEvent | React.TouchEvent) => {
    e.preventDefault()
    if (isLoading || isListening) return
    await startRecording()
  }

  // 松开结束 - 停止录音
  const handleVoiceEnd = (e: React.MouseEvent | React.TouchEvent) => {
    e.preventDefault()
    if (isListening) {
      stopRecording()
    }
  }

  // 点击外部关闭模式菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (modeMenuRef.current && !modeMenuRef.current.contains(event.target as Node)) {
        setModeMenuOpen(false)
      }
    }
    if (modeMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [modeMenuOpen])

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const generateId = () => Math.random().toString(36).substring(2, 9)

  const isAbortError = (error: unknown): boolean => {
    return error instanceof DOMException && error.name === 'AbortError'
  }

  const addMessage = (message: Omit<Message, 'id' | 'timestamp'>) => {
    setMessages(prev => [...prev, { ...message, id: generateId(), timestamp: new Date() }])
  }

  const clearTransientLoadingMessages = useCallback((messageId?: string | null) => {
    setMessages(prev => prev.filter(msg => {
      if (messageId) {
        return !(
          msg.id === messageId &&
          (
            msg.type === 'intent_loading' ||
            (msg.type === 'assistant_text' && !msg.content.trim() && !msg.streamHint)
          )
        )
      }
      return msg.type !== 'intent_loading'
    }))
  }, [])

  const openSearchResultDocument = async (result: SearchResult, hit?: CircuitBodyBestHit) => {
    const pageNumber = typeof hit?.page_number === 'number' ? hit.page_number : undefined
    const access = normalizeDocumentAccessFields(result)
    const circuitHits = Array.isArray(result.body_search?.top_hits)
      ? result.body_search.top_hits.filter((item): item is CircuitBodyBestHit => Boolean(item?.hit_id))
      : []
    const fallbackHits = circuitHits.length > 0
      ? circuitHits
      : (result.body_search?.best_hit ? [result.body_search.best_hit] : [])
    const circuitSearch = result.body_search?.status === 'hit' && (result.body_search.viewer_token || hit?.viewer_token)
      ? {
          enabled: true,
          viewerToken: result.body_search.viewer_token || hit?.viewer_token,
          keyword: result.body_search.keyword || hit?.matched_text || hit?.snippet || '',
          hits: fallbackHits,
          activeHitId: hit?.hit_id,
        }
      : undefined

    if (access.ggzj_sn !== undefined) {
      try {
        const { getGgzjFileUrl } = await import('@/services/api')
        const fileUrlResp = await getGgzjFileUrl({
          sn: access.ggzj_sn,
          data_type: access.ggzj_data_type || 2,
          file_no: access.ggzj_file_no || null,
          file_type: access.ggzj_file_type || null,
        })
        if (fileUrlResp.url) {
          const token = generateId()
          setViewerDoc({
            title: result.title,
            picFolderUrl: fileUrlResp.url,
            token,
            urlType: fileUrlResp.url_type,
            initialPage: pageNumber,
            circuitSearch,
          })
        } else {
          const notification: Notification = {
            id: Date.now().toString(),
            type: 'warning',
            title: '无法访问',
            message: fileUrlResp.message || '该文档暂无在线访问链接',
            timestamp: new Date(),
          }
          setNotifications(prev => [...prev, notification])
        }
      } catch {
        const notification: Notification = {
          id: Date.now().toString(),
          type: 'error',
          title: '获取链接失败',
          message: '获取文件链接时出错，请稍后重试',
          timestamp: new Date(),
        }
        setNotifications(prev => [...prev, notification])
      }
      return
    }

    if (access.pic_folder_url) {
      const token = generateId()
      setViewerDoc({
        title: result.title,
        picFolderUrl: access.pic_folder_url,
        token,
        initialPage: pageNumber,
        circuitSearch,
      })
      return
    }

    const notification: Notification = {
      id: Date.now().toString(),
      type: 'warning',
      title: '无法访问',
      message: '该文档暂无在线访问链接',
      timestamp: new Date(),
    }
    setNotifications(prev => [...prev, notification])
  }

  const openWebviewDebugDocument = () => {
    const debugUrl = frontendRuntimeConfig?.webview_debug_url || DEFAULT_WEBVIEW_DEBUG_URL
    const token = generateId()
    setViewerDoc({
      title: 'WebView 图内搜索调试',
      picFolderUrl: debugUrl,
      token,
      urlType: 'raw_pdf',
      initialPage: 2,
      circuitSearch: {
        enabled: true,
        viewerToken: frontendRuntimeConfig?.webview_debug_viewer_token || '',
        keyword: '',
        hits: [],
      },
    })
  }

  const handleWebviewDebugFabPointerDown = (event: React.PointerEvent<HTMLButtonElement>) => {
    webviewDebugFabDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: webviewDebugFabPosition.x,
      originY: webviewDebugFabPosition.y,
      currentX: webviewDebugFabPosition.x,
      currentY: webviewDebugFabPosition.y,
      moved: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handleWebviewDebugFabPointerMove = (event: React.PointerEvent<HTMLButtonElement>) => {
    const drag = webviewDebugFabDragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const deltaX = event.clientX - drag.startX
    const deltaY = event.clientY - drag.startY
    if (Math.abs(deltaX) > 4 || Math.abs(deltaY) > 4) {
      drag.moved = true
    }
    const nextPosition = clampWebviewDebugFabPosition({
      x: drag.originX + deltaX,
      y: drag.originY + deltaY,
    })
    drag.currentX = nextPosition.x
    drag.currentY = nextPosition.y
    setWebviewDebugFabPosition(nextPosition)
  }

  const handleWebviewDebugFabPointerUp = (event: React.PointerEvent<HTMLButtonElement>) => {
    const drag = webviewDebugFabDragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    webviewDebugFabDragRef.current = null
    try {
      event.currentTarget.releasePointerCapture(event.pointerId)
    } catch {
      // ignore release failure
    }
    const finalPosition = clampWebviewDebugFabPosition({ x: drag.currentX, y: drag.currentY })
    setWebviewDebugFabPosition(finalPosition)
    try {
      window.localStorage.setItem(WEBVIEW_DEBUG_FAB_POSITION_KEY, JSON.stringify(finalPosition))
    } catch {
      // ignore storage failure
    }
    if (!drag.moved) {
      openWebviewDebugDocument()
    }
  }

  const handleWebviewDebugFabPointerCancel = (event: React.PointerEvent<HTMLButtonElement>) => {
    const drag = webviewDebugFabDragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    webviewDebugFabDragRef.current = null
  }

  const resolveSearchResultDocumentAccessForPreview = async (
    result: SearchResult
  ): Promise<{ url: string; urlType?: string } | null> => {
    const access = normalizeDocumentAccessFields(result)

    if (access.ggzj_sn !== undefined) {
      try {
        const { getGgzjFileUrl } = await import('@/services/api')
        const fileUrlResp = await getGgzjFileUrl({
          sn: access.ggzj_sn,
          data_type: access.ggzj_data_type || 2,
          file_no: access.ggzj_file_no || null,
          file_type: access.ggzj_file_type || null,
        })
        return fileUrlResp.url
          ? { url: fileUrlResp.url, urlType: fileUrlResp.url_type }
          : null
      } catch {
        return null
      }
    }

    return access.pic_folder_url ? { url: access.pic_folder_url } : null
  }

  const updateRepairFollowupMessage = useCallback(
    (messageId: string, updater: (state: RepairFollowupState) => RepairFollowupState) => {
      setMessages(prev => prev.map((msg) => {
        if (msg.id !== messageId || msg.type !== 'repair_followup' || !msg.repairFollowupState) {
          return msg
        }
        return {
          ...msg,
          repairFollowupState: updater(msg.repairFollowupState),
        }
      }))
    },
    []
  )

  const updateAskUserV2Message = useCallback(
    (messageId: string, updater: (state: AskUserV2FormState) => AskUserV2FormState) => {
      setMessages(prev => prev.map((msg) => {
        if (msg.id !== messageId || msg.type !== 'ask_user_form' || !msg.askUserV2State) {
          return msg
        }
        return {
          ...msg,
          askUserV2State: updater(msg.askUserV2State),
        }
      }))
    },
    []
  )

  const markFeedbackSubmitted = (messageId: string) => {
    setMessages(prev => prev.map(msg =>
      msg.id === messageId ? { ...msg, feedbackSubmitted: true } : msg
    ))
  }

  const loadRepairKnowledgeDetail = async (source: RepairKnowledgeSourceRef) => {
    setRepairKnowledgeModal(prev => prev ? {
      ...prev,
      activeSourceId: source.id,
      loading: true,
    } : prev)

    try {
      const response = await getRepairKnowledgeSource(source.id)
      if (!response.success || !response.data) {
        throw new Error(response.message || '未找到维修经验详情')
      }

      setRepairKnowledgeModal(prev => prev ? {
        ...prev,
        activeSourceId: source.id,
        activeDetail: response.data || null,
        loading: false,
      } : prev)
    } catch (error) {
      setRepairKnowledgeModal(prev => prev ? {
        ...prev,
        loading: false,
      } : prev)
      setNotifications(prev => [...prev, {
        id: generateId(),
        type: 'error',
        title: '来源加载失败',
        message: error instanceof Error ? error.message : '加载维修经验详情失败',
        timestamp: new Date(),
      }])
    }
  }

  const openRepairKnowledgeModal = async (sources: RepairKnowledgeSourceRef[]) => {
    if (!sources.length) return
    const firstSource = sources[0]
    setRepairKnowledgeModal({
      sources,
      activeSourceId: firstSource.id,
      activeDetail: null,
      loading: true,
    })
    await loadRepairKnowledgeDetail(firstSource)
  }

  const loadParameterQueryDetail = async (source: ParameterQuerySourceRef) => {
    setParameterQueryModal(prev => prev ? {
      ...prev,
      activeSourceId: source.id,
      loading: true,
    } : prev)

    try {
      const response = await getParameterQuerySource(source.id)
      if (!response.success || !response.data) {
        throw new Error(response.message || '未找到参数资料详情')
      }

      setParameterQueryModal(prev => prev ? {
        ...prev,
        activeSourceId: source.id,
        activeDetail: response.data || null,
        loading: false,
      } : prev)
    } catch (error) {
      setParameterQueryModal(prev => prev ? {
        ...prev,
        loading: false,
      } : prev)
      setNotifications(prev => [...prev, {
        id: generateId(),
        type: 'error',
        title: '来源加载失败',
        message: error instanceof Error ? error.message : '加载参数资料详情失败',
        timestamp: new Date(),
      }])
    }
  }

  const openParameterQueryModal = async (sources: ParameterQuerySourceRef[]) => {
    if (!sources.length) return
    const firstSource = sources[0]
    setParameterQueryModal({
      sources,
      activeSourceId: firstSource.id,
      activeDetail: null,
      loading: true,
    })
    await loadParameterQueryDetail(firstSource)
  }

  const performSearch = async (
    query: string,
    filters: Record<string, string> = {},
    clarifyFacet?: string,
    clarifyChoice?: string,
    transientMessageId?: string | null
  ) => {
    setIsLoading(true)

    try {
      const appToken = getStoredToken()
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (appToken) {
        headers['x-app-token'] = appToken
      }

      const response = await fetch(`${API_BASE}/search`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          query,
          limit: 20,
          filters: Object.keys(filters).length > 0 ? filters : undefined,
          clarify_facet: clarifyFacet,
          clarify_choice: clarifyChoice,
        }),
      })

      if (!response.ok) throw new Error('Search failed')

      const data: SearchResponse = await response.json()

      // 只在初次搜索时显示纠错提示（澄清选择步骤不显示）
      const isInitialSearch = !clarifyFacet
      if (isInitialSearch && data.correction?.has_correction && data.correction.corrections.length > 0) {
        const correctionTexts = data.correction.corrections
          .map(c => `"${c.original}" → "${c.corrected}"`)
          .join('、')
        addMessage({
          type: 'correction',
          content: correctionTexts,
          correction: data.correction,
        })
      }

      // 检查结果有效性
      if (data.validity && !data.validity.has_valid_results) {
        // 无有效结果，显示友好提示
        addMessage({
          type: 'no_results',
          content: data.validity.message || '未找到相关文档',
          validity: data.validity,
          stats: data.stats,
        })
      } else if (data.clarify.need && data.clarify.options && data.clarify.options.length > 0) {
        addMessage({
          type: 'clarify',
          content: data.clarify.question || '请选择以缩小范围：',
          clarify: data.clarify,
          stats: data.stats,
        })
      } else {
        addMessage({
          type: 'results',
          content: `找到 ${data.stats.candidates} 个相关文档`,
          results: data.results,
          stats: data.stats,
        })
      }
    } catch (error) {
      // 区分错误类型
      let errorType: 'network' | 'server' | 'unknown' = 'unknown'
      let errorMessage = '搜索出错，请稍后重试'

      if (error instanceof TypeError && error.message.includes('fetch')) {
        // 网络错误（无法连接）
        errorType = 'network'
        errorMessage = '网络连接失败，请检查网络后重试'
      } else if (error instanceof Error) {
        if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
          errorType = 'network'
          errorMessage = '无法连接到服务器，请检查网络连接'
        } else if (error.message.includes('500') || error.message.includes('502') || error.message.includes('503')) {
          errorType = 'server'
          errorMessage = '服务暂时不可用，请稍后重试'
        } else if (error.message.includes('timeout') || error.message.includes('Timeout')) {
          errorType = 'network'
          errorMessage = '请求超时，请检查网络后重试'
        }
      }

      clearTransientLoadingMessages(transientMessageId)

      // 创建重试函数
      const retryAction = () => {
        // 移除错误消息
        setMessages(prev => prev.filter(m => m.type !== 'error'))
        // 重新搜索
        performSearch(query, filters, clarifyFacet, clarifyChoice)
      }

      addMessage({
        type: 'error',
        content: errorMessage,
        errorType,
        retryAction,
      })
    } finally {
      setIsLoading(false)
    }
  }

  // 处理聊天API响应
  const handleChatResponse = useCallback((response: ChatResponse) => {
    // 保存 session_id
    if (response.session_id) {
      setSessionId(response.session_id)
    }

    // 处理生命周期信息
    if (response.lifecycle_info) {
      const { current_lifecycle, conflict } = response.lifecycle_info

      // 更新当前生命周期
      setCurrentLifecycle(current_lifecycle)

      // 如果检测到冲突且推荐确认切换，这里不做处理
      // 因为冲突检测已经在前端的 handleSubmit 中处理了
      if (conflict?.detected) {
        console.log('[Lifecycle] Conflict detected by backend:', conflict)
      }
    }

    // 更新业务类型
    if (response.business) {
      setCurrentBusiness(response.business)
    }

    // 根据响应类型添加消息
    switch (response.type) {
      case 'message':
      case 'text': {
        // 处理内容：可能是纯字符串或带有 existence_info 的对象
        const textContent = typeof response.content === 'string'
          ? response.content
          : extractChatResponseText(response.content)
        const textExistenceInfo = typeof response.content === 'object' ? response.content?.existence_info : undefined
        const shouldArchivePrevious = typeof response.content === 'object' && response.content?.should_archive_previous

        // 如果需要归档上一轮对话，将 wizard 路径也标记为 archived，避免跨上下文回退
        if (shouldArchivePrevious) {
          setMessages(prev => prev.map(msg => {
            if (msg.type === 'clarify_wizard' && msg.wizardState && msg.wizardState.status !== 'archived') {
              return {
                ...msg,
                wizardState: { ...msg.wizardState, status: 'archived' as const }
              }
            }
            if (msg.type === 'repair_followup' && msg.repairFollowupState && msg.repairFollowupState.status !== 'archived') {
              return {
                ...msg,
                repairFollowupState: { ...msg.repairFollowupState, status: 'archived' as const }
              }
            }
            if (msg.type === 'ask_user_form' && msg.askUserV2State && msg.askUserV2State.status !== 'archived') {
              return {
                ...msg,
                askUserV2State: { ...msg.askUserV2State, status: 'archived' as const }
              }
            }
            return msg
          }))
        }

        addMessage({
          type: 'assistant_text',
          content: textContent,
          business: response.business || undefined,
          suggestions: response.suggestions,
          requestId: response.request_id,
          repairKnowledgeSources: extractRepairKnowledgeSources(response.metadata),
          // 存在性信息存储在 content 字段的 metadata 中（通过 JSON 嵌入）
          ...(textExistenceInfo ? { wizardState: { existenceInfo: textExistenceInfo } as any } : {})
        })
        // 兜底清理：将未完成的 clarify_intent / clarify_business 标记为 completed
        setMessages(prev => prev.map(msg => {
          if ((msg.type === 'clarify_intent' || msg.type === 'clarify_business') && msg.lifecycle !== 'completed') {
            return {
              ...msg,
              lifecycle: 'completed' as const,
              selectedIntent: msg.selectedIntent || '智能问答'
            }
          }
          if (msg.type === 'repair_followup' && msg.repairFollowupState && ['active', 'submitting'].includes(msg.repairFollowupState.status)) {
            return {
              ...msg,
              repairFollowupState: { ...msg.repairFollowupState, status: 'submitted' as const }
            }
          }
          if (msg.type === 'ask_user_form' && msg.askUserV2State && ['active', 'submitting'].includes(msg.askUserV2State.status)) {
            return {
              ...msg,
              askUserV2State: { ...msg.askUserV2State, status: 'submitted' as const }
            }
          }
          return msg
        }))
        break
      }

      case 'ask_user': {
        const askUserV2State = buildAskUserV2State(response, searchState.query)
        if (askUserV2State) {
          setMessages(prev => {
            if (askUserV2State.scene === 'repair_knowledge_followup') {
              const repairQuery = normalizeRepairQuery(askUserV2State.originalQuery)
              const matchedIndexes = prev.reduce<number[]>((indexes, msg, index) => {
                if (isRepairSupplementMessage(msg, repairQuery)) {
                  indexes.push(index)
                }
                return indexes
              }, [])

              const mergedSummary = mergeSummaryText(
                ...matchedIndexes.map((index) => getRepairSupplementSummary(prev[index])),
                askUserV2State.summaryText
              )
              const nextState = mergedSummary
                ? { ...askUserV2State, summaryText: mergedSummary }
                : askUserV2State

              const preferredAskUserIndex = [...matchedIndexes]
                .reverse()
                .find((index) => prev[index]?.type === 'ask_user_form')
              const preferredIndex = preferredAskUserIndex ?? matchedIndexes[matchedIndexes.length - 1] ?? -1

              if (preferredIndex !== -1) {
                const duplicateIndexes = new Set(matchedIndexes.filter((index) => index !== preferredIndex))
                return prev.flatMap((msg, index) => {
                  if (duplicateIndexes.has(index)) {
                    return []
                  }
                  if (index !== preferredIndex) {
                    return [msg]
                  }
                  return [{
                    id: msg.id,
                    type: 'ask_user_form' as const,
                    content: '',
                    timestamp: msg.timestamp,
                    business: response.business || msg.business,
                    askUserV2State: nextState,
                  }]
                })
              }

              return [...prev, {
                id: generateId(),
                type: 'ask_user_form' as const,
                content: '',
                timestamp: new Date(),
                business: response.business || undefined,
                askUserV2State: nextState,
              }]
            }

            const existingIndex = prev.findIndex(
              msg => msg.type === 'ask_user_form' && msg.askUserV2State?.status === 'active'
            )
            const legacyRepairIndex = askUserV2State.scene === 'repair_knowledge_followup'
              ? prev.findIndex(
                msg => msg.type === 'repair_followup' && msg.repairFollowupState?.status === 'active'
              )
              : -1

            if (existingIndex !== -1) {
              const updated = [...prev]
              updated[existingIndex] = {
                ...updated[existingIndex],
                business: response.business || updated[existingIndex].business,
                askUserV2State,
              }
              if (legacyRepairIndex !== -1 && legacyRepairIndex !== existingIndex) {
                updated.splice(legacyRepairIndex, 1)
              }
              return updated
            }

            if (legacyRepairIndex !== -1) {
              const updated = [...prev]
              updated[legacyRepairIndex] = {
                id: updated[legacyRepairIndex].id,
                type: 'ask_user_form' as const,
                content: '',
                timestamp: updated[legacyRepairIndex].timestamp,
                business: response.business || updated[legacyRepairIndex].business,
                askUserV2State,
              }
              return updated
            }

            return [...prev, {
              id: generateId(),
              type: 'ask_user_form' as const,
              content: '',
              timestamp: new Date(),
              business: response.business || undefined,
              askUserV2State,
            }]
          })
          break
        }

        const repairFollowupState = buildRepairFollowupPayloadFromAskUser(response, searchState.query)
        if (repairFollowupState) {
          setMessages(prev => {
            const existingIndex = prev.findIndex(
              msg => msg.type === 'repair_followup' && msg.repairFollowupState?.status === 'active'
            )

            if (existingIndex !== -1) {
              const updated = [...prev]
              updated[existingIndex] = {
                ...updated[existingIndex],
                business: response.business || updated[existingIndex].business,
                repairFollowupState,
              }
              return updated
            }

            return [...prev, {
              id: generateId(),
              type: 'repair_followup' as const,
              content: '',
              timestamp: new Date(),
              business: response.business || undefined,
              repairFollowupState,
            }]
          })
          break
        }

        const wizardPayload = buildWizardPayloadFromAskUser(response, searchState.query)
        if (!wizardPayload) {
          addMessage({
            type: 'error',
            content: '澄清请求数据不完整，无法继续。',
            errorType: 'server',
          })
          break
        }

        const { newRound, resultsCount, topResult, existenceInfo, originalQuery } = wizardPayload

        setMessages(prev => {
          const existingWizardIndex = prev.findIndex(
            msg => msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active'
          )

          if (existingWizardIndex !== -1) {
            const existingMsg = prev[existingWizardIndex]
            const existingState = existingMsg.wizardState!
            const updatedRounds = [...existingState.rounds, newRound]
            const updated = [...prev]
            updated[existingWizardIndex] = {
              ...existingMsg,
              wizardState: {
                ...existingState,
                rounds: updatedRounds,
                currentRoundIndex: updatedRounds.length - 1,
                resultsCount: resultsCount ?? existingState.resultsCount,
                topResult: topResult ?? existingState.topResult,
                existenceInfo: existenceInfo ?? existingState.existenceInfo,
                originalQuery: originalQuery || existingState.originalQuery,
              }
            }
            return updated
          }

          const wizardState: WizardState = {
            rounds: [newRound],
            currentRoundIndex: 0,
            status: 'active',
            originalQuery,
            resultsCount,
            topResult,
            existenceInfo,
          }

          return [...prev, {
            id: generateId(),
            type: 'clarify_wizard' as const,
            content: '',
            timestamp: new Date(),
            business: response.business || undefined,
            wizardState,
          }]
        })
        break
      }

      case 'documents': {
        // 后端返回格式: { query, total, results: [{file_id, filename, physical_path, ref_file_id, ...}] }
        const docContent = response.content as any
        const results = docContent.results || docContent.documents || []
        const totalHits = Number(docContent.total_hits ?? docContent.total ?? results.length)
        const returnedCount = Number(docContent.returned_count ?? results.length)
        const searchResults: SearchResult[] = results.map((doc: any) => {
          const access = normalizeDocumentAccessFields(doc)
          return {
            file_id: doc.file_id || doc.id,
            doc_id: doc.file_id || doc.id,
            ref_file_id: doc.ref_file_id,
            parent_id: doc.parent_id,
            pic_folder_url: access.pic_folder_url,
            ggzj_sn: access.ggzj_sn,
            ggzj_data_type: access.ggzj_data_type,
            ggzj_file_no: access.ggzj_file_no,
            ggzj_file_type: access.ggzj_file_type,
            body_search: doc.body_search,
            title: doc.filename || doc.title,
            path: doc.physical_path || doc.path,
            tags: {
              brand: doc.brand,
              series: doc.series,
              model: doc.model,
              ...doc.tags
            },
            score: doc.score,
            explain: []
          }
        })
        const resultMessage: Message = {
          id: generateId(),
          type: 'results',
          content: docContent.summary || `找到 ${totalHits} 个相关文档（当前展示 ${returnedCount} 条）`,
          results: searchResults,
          stats: { took_ms: 0, candidates: totalHits },
          suggestions: response.suggestions,
          requestId: response.request_id,
          business: response.business || undefined,
          timestamp: new Date(),
        }

        // 搜索完成时，将活跃的 wizard 标记为 completed，同时清理 clarify_intent
        setMessages(prev => {
          const activeWizard = prev.find(msg => msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active')
          const relatedWizardId = activeWizard?.id
          const updatedMessages = prev.map(msg => {
            if (msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active') {
              return {
                ...msg,
                wizardState: {
                  ...msg.wizardState,
                  status: 'completed' as const,
                  resultsCount: totalHits
                }
              }
            }
            if (msg.type === 'repair_followup' && msg.repairFollowupState && ['active', 'submitting'].includes(msg.repairFollowupState.status)) {
              return {
                ...msg,
                repairFollowupState: { ...msg.repairFollowupState, status: 'submitted' as const }
              }
            }
            if (msg.type === 'ask_user_form' && msg.askUserV2State && ['active', 'submitting'].includes(msg.askUserV2State.status)) {
              return {
                ...msg,
                askUserV2State: { ...msg.askUserV2State, status: 'submitted' as const }
              }
            }
            // 兜底清理：将未完成的 clarify_intent / clarify_business 标记为 completed
            if ((msg.type === 'clarify_intent' || msg.type === 'clarify_business') && msg.lifecycle !== 'completed') {
              return {
                ...msg,
                lifecycle: 'completed' as const,
                selectedIntent: msg.selectedIntent || '资料搜索'  // 默认值
              }
            }
            return msg
          })
          return [
            ...updatedMessages,
            {
              ...resultMessage,
              relatedWizardId,
            }
          ]
        })
        break
      }

      case 'param_request': {
        const paramContent = response.content as ParameterQueryContent
        addMessage({
          type: 'param_request',
          content: paramContent.summary || '',
          business: response.business || undefined,
          requestId: response.request_id,
          paramContent,
          parameterQuerySources: paramContent.source_refs || [],
        })

        setMessages(prev => prev.map(msg => {
          if ((msg.type === 'clarify_intent' || msg.type === 'clarify_business') && msg.lifecycle !== 'completed') {
            return {
              ...msg,
              lifecycle: 'completed' as const,
              selectedIntent: msg.selectedIntent || '参数查询'
            }
          }
          if (msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active') {
            return {
              ...msg,
              wizardState: {
                ...msg.wizardState,
                status: 'completed' as const
              }
            }
          }
          if (msg.type === 'repair_followup' && msg.repairFollowupState && ['active', 'submitting'].includes(msg.repairFollowupState.status)) {
            return {
              ...msg,
              repairFollowupState: { ...msg.repairFollowupState, status: 'submitted' as const }
            }
          }
          if (msg.type === 'ask_user_form' && msg.askUserV2State && ['active', 'submitting'].includes(msg.askUserV2State.status)) {
            return {
              ...msg,
              askUserV2State: { ...msg.askUserV2State, status: 'submitted' as const }
            }
          }
          return msg
        }))
        break
      }

      case 'fault': {
        const faultContent = response.content as FaultContent
        addMessage({
          type: 'assistant_fault',
          content: faultContent.message,
          faultContent: faultContent,
          business: response.business || undefined,
          suggestions: response.suggestions,
          requestId: response.request_id,
        })

        // 兜底清理：将未完成的 clarify_intent / clarify_business 和活跃的 clarify_wizard 标记为 completed
        setMessages(prev => prev.map(msg => {
          // 处理 clarify_intent / clarify_business
          if ((msg.type === 'clarify_intent' || msg.type === 'clarify_business') && msg.lifecycle !== 'completed') {
            return {
              ...msg,
              lifecycle: 'completed' as const,
              selectedIntent: msg.selectedIntent || '故障诊断'
            }
          }
          // 处理 clarify_wizard（ECU 选择）
          if (msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active') {
            // 提取用户选择的 ECU 作为摘要
            const rounds = msg.wizardState.rounds
            const ecuRound = rounds.find(r => r.facet === 'ecu' && r.selected)
            const ecuSummary = ecuRound?.selectedLabel || ecuRound?.selected || faultContent.ecuModel
            return {
              ...msg,
              wizardState: {
                ...msg.wizardState,
                status: 'completed' as const
              },
              completedSummary: ecuSummary ? `ECU: ${ecuSummary}` : undefined
            }
          }
          if (msg.type === 'repair_followup' && msg.repairFollowupState && ['active', 'submitting'].includes(msg.repairFollowupState.status)) {
            return {
              ...msg,
              repairFollowupState: { ...msg.repairFollowupState, status: 'submitted' as const }
            }
          }
          if (msg.type === 'ask_user_form' && msg.askUserV2State && ['active', 'submitting'].includes(msg.askUserV2State.status)) {
            return {
              ...msg,
              askUserV2State: { ...msg.askUserV2State, status: 'submitted' as const }
            }
          }
          return msg
        }))

        // 如果是 generating 状态，启动 SSE 订阅
        if (faultContent.state === 'generating' && faultContent.taskId && faultContent.subscribeUrl) {
          taskManager.subscribe(faultContent.taskId, faultContent.subscribeUrl, {
            onProgress: (progress, message) => {
              console.log(`诊断进度: ${progress}%`, message)
            },
            onComplete: (result) => {
              // 添加通知
              const notification: Notification = {
                id: faultContent.taskId!,
                type: 'success',
                title: '诊断完成',
                message: `故障码 ${faultContent.faultCode} 的诊断报告已生成`,
                action: result.reportUrl ? {
                  label: '查看报告',
                  url: result.reportUrl,
                } : undefined,
                timestamp: new Date(),
              }
              setNotifications(prev => [...prev, notification])

              // 更新对应消息的状态
              setMessages(prev => prev.map(msg => {
                if (msg.faultContent?.taskId === faultContent.taskId) {
                  return {
                    ...msg,
                    faultContent: {
                      ...msg.faultContent!,
                      state: 'ready' as const,
                      reportUrl: result.reportUrl,
                    }
                  }
                }
                return msg
              }))
            },
            onError: (error) => {
              console.error('诊断任务失败:', error)
              setMessages(prev => prev.map(msg => {
                if (msg.faultContent?.taskId === faultContent.taskId) {
                  return {
                    ...msg,
                    faultContent: {
                      ...msg.faultContent!,
                      state: 'failed' as const,
                      error: { code: 'SSE_ERROR', message: error.message }
                    }
                  }
                }
                return msg
              }))
            }
          })
        }
        break
      }

      case 'clarify_intent':
        addMessage({
          type: 'clarify_intent',
          content: response.content?.message || '请选择您想进行的操作',
          clarifyOptions: response.clarify_options,
          clarifyFacet: response.clarify_facet,
          business: response.business || undefined,
        })
        break

      case 'clarify_business': {
        // 构建当前轮次数据
        const newRound: WizardRound = {
          id: generateId(),
          facet: response.clarify_facet || 'unknown',
          question: response.content?.message || '请选择以缩小范围',
          options: (response.clarify_options || []).map(opt => ({
            key: opt.key,
            label: opt.label,
            description: opt.description
          }))
        }

        // 获取结果数量（从后端内容中提取）
        const resultsCount = response.content?.results_count

        // 获取 top1 结果用于快捷入口
        const topResult = buildTopResultFromRaw(response.content?.top_result)

        // 获取存在性信息
        const existenceInfo = buildExistenceInfoFromRaw(response.content?.existence_info)

        // 查找是否已存在 active 状态的 clarify_wizard 消息
        setMessages(prev => {
          const existingWizardIndex = prev.findIndex(
            msg => msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active'
          )

          if (existingWizardIndex !== -1) {
            // 更新已存在的 wizard
            const existingMsg = prev[existingWizardIndex]
            const existingState = existingMsg.wizardState!
            const currentIdx = existingState.currentRoundIndex
            const currentRound = existingState.rounds[currentIdx]

            let updatedRounds: WizardRound[]
            let updatedIndex: number

            if (currentRound && !currentRound.selected) {
              // 当前轮次尚未选择（回退场景）：替换当前轮次
              updatedRounds = [...existingState.rounds]
              updatedRounds[currentIdx] = newRound
              updatedIndex = currentIdx
            } else {
              // 正常流程：追加新轮次
              updatedRounds = [...existingState.rounds, newRound]
              updatedIndex = existingState.rounds.length
            }

            const updatedState: WizardState = {
              ...existingState,
              rounds: updatedRounds,
              currentRoundIndex: updatedIndex,
              resultsCount,
              topResult: topResult ?? existingState.topResult,  // 有新 topResult 则更新
              existenceInfo: existenceInfo ?? existingState.existenceInfo  // 保留或更新存在性信息
            }
            const updated = [...prev]
            updated[existingWizardIndex] = {
              ...existingMsg,
              wizardState: updatedState
            }
            return updated
          } else {
            // 创建新的 wizard 消息
            const wizardState: WizardState = {
              rounds: [newRound],
              currentRoundIndex: 0,
              status: 'active',
              originalQuery: searchState.query || response.content?.query || '',
              resultsCount,
              topResult,  // 添加快捷入口数据
              existenceInfo  // 添加存在性信息
            }
            return [...prev, {
              id: generateId(),
              type: 'clarify_wizard' as const,
              content: '',
              timestamp: new Date(),
              business: response.business || undefined,
              wizardState
            }]
          }
        })
        break
      }

      case 'error':
        addMessage({
          type: 'error',
          content: response.content?.message || '处理请求时出错',
          errorType: 'server',
        })
        break

      default:
        console.warn('未知响应类型:', response.type)
    }

    // 处理系统提示（hints）
    if (response.hints && response.hints.length > 0) {
      response.hints.forEach(hint => {
        if (hint.type === 'new_search_reminder') {
          const notification: Notification = {
            id: generateId(),
            type: 'info',
            title: '提示',
            message: hint.message,
            timestamp: new Date()
          }
          setNotifications(prev => [...prev, notification])
        }
      })
    }
  }, [])

  // 发送聊天消息
  const sendChatMessage = async (
    message: string,
    clarifyChoice?: string,
    clarifyFacet?: string,
    userConfirmedSwitch = false,
    askUserAnswer?: ChatRequest['ask_user_answer'],
    modeOverride?: Mode,
    imageFiles: File[] = []
  ): Promise<boolean> => {
    setIsLoading(true)
    let transientMessageId: string | null = null

    // 新查询时，将所有未提交反馈的消息标记为已提交（隐藏反馈卡片）
    setMessages(prev => prev.map(msg =>
      msg.requestId && !msg.feedbackSubmitted ? { ...msg, feedbackSubmitted: true } : msg
    ))

    try {
      const effectiveMode = modeOverride ?? currentMode
      const clarifyContext = clarifyChoice && !askUserAnswer ? {
        clarify_choice: clarifyChoice,
        clarify_facet: clarifyFacet,
      } : {}
      const requestContext = Object.keys(clarifyContext).length > 0
        ? clarifyContext
        : undefined

	      // 判断是否使用流式输出
      // - general_chat 模式：始终使用流式
      // - auto 模式：也使用流式，后端会根据意图识别结果决定是否fallback
      const useStream = effectiveMode === 'general_chat' || effectiveMode === 'auto'

      if (useStream) {
        // 流式输出模式
        const msgId = generateId()
        transientMessageId = msgId
        setStreamingMessageId(msgId)

        // 创建 AbortController 用于中断流式请求
        const abortController = new AbortController()
        abortControllerRef.current = abortController

        // 确定初始加载类型：
        // 1. 有澄清上下文时：不需要意图识别，使用通用加载（不添加占位消息，使用底部加载指示器）
        // 2. AUTO 模式且无澄清：显示意图识别动画
        // 3. general_chat 模式：显示空聊天气泡
        const showIntentLoading = effectiveMode === 'auto' && !clarifyChoice
        const initialType = showIntentLoading ? 'intent_loading' : 'assistant_text'

        // 如果是澄清响应，不需要添加占位消息，后端会快速返回结果
        if (!clarifyChoice) {
          setMessages(prev => [...prev, {
            id: msgId,
            type: initialType,
            content: '',
            timestamp: new Date(),
          }])
        } else if (clarifyChoice !== 'chat') {
          // 非 chat 的澄清选择可能触发 LLM 智能澄清（3-10s），需要显示加载动画
          setMessages(prev => [...prev, {
            id: msgId,
            type: 'intent_loading' as const,
            content: '',
            timestamp: new Date(),
          }])
        }

        let hasReceivedChunk = false  // 标记是否已收到流式内容

        const request: ChatRequest = {
          message,
          session_id: sessionId || undefined,
          context: requestContext,
          ask_user_answer: askUserAnswer,
          mode: effectiveMode,
            lifecycle_check: {
              current_lifecycle: currentLifecycle,
              current_business: currentBusiness ?? undefined,
              has_ongoing: hasOngoingConversation(),
              user_confirmed_switch: userConfirmedSwitch
            }
        }

        const streamFn = imageFiles.length > 0
          ? chatStreamWithImages(request, imageFiles, {
            onStart: (newSessionId) => {
              if (newSessionId) setSessionId(newSessionId)
            },
            onHint: (hintMessage) => {
              // 收到 hint 时：如果仍在 intent_loading 阶段，保持动画仅更新文案
              setMessages(prev => prev.map(msg => {
                if (msg.id !== msgId) return msg
                if (msg.type === 'intent_loading') {
                  return { ...msg, streamHint: hintMessage }
                }
                return { ...msg, type: 'assistant_text' as const, content: '', streamHint: hintMessage }
              }))
            },
            onChunk: (chunk) => {
              // 首次收到 chunk 时，将 intent_loading 转换为 assistant_text
              if (!hasReceivedChunk) {
                hasReceivedChunk = true
                setIsStreamingChat(true)
                setMessages(prev => prev.map(msg =>
                  msg.id === msgId
                    ? { ...msg, type: 'assistant_text', content: chunk, streamHint: undefined }
                    : msg
                ))
              } else {
                // 后续 chunk 直接追加内容
                setMessages(prev => prev.map(msg =>
                  msg.id === msgId
                    ? { ...msg, content: msg.content + chunk, streamHint: undefined }
                    : msg
                ))
              }
            },
            onDone: (fullContent, response) => {
              const resolvedFullContent = fullContent || (
                response && (response.type === 'message' || response.type === 'text')
                  ? extractChatResponseText(response.content)
                  : ''
              )

              if (response && response.type !== 'message' && response.type !== 'text') {
                setMessages(prev => prev.filter(msg => msg.id !== msgId))
                handleChatResponse(response)
                setStreamingMessageId(null)
                setIsStreamingChat(false)
                abortControllerRef.current = null
                return
              }

              // 确保最终内容正确，类型为 assistant_text
              setMessages(prev => prev.map(msg => {
                if (msg.id !== msgId) return msg
                const lifecycle = response?.lifecycle_info?.current_lifecycle
                return {
                  ...msg,
                  type: 'assistant_text',
                  content: resolvedFullContent,
                  streamHint: undefined,
                  business: response?.business || msg.business,
                  suggestions: response?.suggestions || msg.suggestions,
                  lifecycle: lifecycle === 'ongoing' || lifecycle === 'completed' ? lifecycle : msg.lifecycle,
                  requestId: response?.request_id || msg.requestId,
                  repairKnowledgeSources: extractRepairKnowledgeSources(response?.metadata) || msg.repairKnowledgeSources,
                }
              }))

              // done 事件里可能携带完整响应（用于流式补齐 business/suggestions/lifecycle）
              if (response?.business) {
                setCurrentBusiness(response.business)
              }
              if (response?.lifecycle_info?.current_lifecycle) {
                setCurrentLifecycle(response.lifecycle_info.current_lifecycle)
              }
              if (response?.hints && response.hints.length > 0) {
                response.hints.forEach(hint => {
                  if (hint.type === 'new_search_reminder') {
                    const notification: Notification = {
                      id: generateId(),
                      type: 'info',
                      title: '提示',
                      message: hint.message,
                      timestamp: new Date()
                    }
                    setNotifications(prev => [...prev, notification])
                  }
                })
              }

              setStreamingMessageId(null)
              setIsStreamingChat(false)
              abortControllerRef.current = null
            },
            onFallback: (response) => {
              // 回退到非流式响应，移除加载状态并处理完整响应
              setMessages(prev => prev.filter(msg => msg.id !== msgId))
              handleChatResponse(response)
              setStreamingMessageId(null)
              setIsStreamingChat(false)
              abortControllerRef.current = null
            },
            onError: (error) => {
              // 如果有占位消息则更新，否则添加新消息
              setMessages(prev => {
                const hasPlaceholder = prev.some(msg => msg.id === msgId)
                if (hasPlaceholder) {
                  return prev.map(msg =>
                    msg.id === msgId
                      ? { ...msg, type: 'assistant_text', content: `抱歉，处理请求时出错：${error}` }
                      : msg
                  )
                } else {
                  return [...prev, {
                    id: msgId,
                    type: 'assistant_text' as const,
                    content: `抱歉，处理请求时出错：${error}`,
                    timestamp: new Date()
                  }]
                }
              })
              setStreamingMessageId(null)
              setIsStreamingChat(false)
              abortControllerRef.current = null
            },
          }, abortController.signal)
          : chatStream(
            request,
          {
            onStart: (newSessionId) => {
              if (newSessionId) setSessionId(newSessionId)
            },
            onHint: (hintMessage) => {
              // 收到 hint 时：如果仍在 intent_loading 阶段，保持动画仅更新文案
              setMessages(prev => prev.map(msg => {
                if (msg.id !== msgId) return msg
                if (msg.type === 'intent_loading') {
                  return { ...msg, streamHint: hintMessage }
                }
                return { ...msg, type: 'assistant_text' as const, content: '', streamHint: hintMessage }
              }))
            },
            onChunk: (chunk) => {
              // 首次收到 chunk 时，将 intent_loading 转换为 assistant_text
              if (!hasReceivedChunk) {
                hasReceivedChunk = true
                setIsStreamingChat(true)
                setMessages(prev => prev.map(msg =>
                  msg.id === msgId
                    ? { ...msg, type: 'assistant_text', content: chunk, streamHint: undefined }
                    : msg
                ))
              } else {
                // 后续 chunk 直接追加内容
                setMessages(prev => prev.map(msg =>
                  msg.id === msgId
                    ? { ...msg, content: msg.content + chunk, streamHint: undefined }
                    : msg
                ))
              }
            },
            onDone: (fullContent, response) => {
              const resolvedFullContent = fullContent || (
                response && (response.type === 'message' || response.type === 'text')
                  ? extractChatResponseText(response.content)
                  : ''
              )

              if (response && response.type !== 'message' && response.type !== 'text') {
                setMessages(prev => prev.filter(msg => msg.id !== msgId))
                handleChatResponse(response)
                setStreamingMessageId(null)
                setIsStreamingChat(false)
                abortControllerRef.current = null
                return
              }

              // 确保最终内容正确，类型为 assistant_text
              setMessages(prev => prev.map(msg => {
                if (msg.id !== msgId) return msg
                const lifecycle = response?.lifecycle_info?.current_lifecycle
                return {
                  ...msg,
                  type: 'assistant_text',
                  content: resolvedFullContent,
                  streamHint: undefined,
                  business: response?.business || msg.business,
                  suggestions: response?.suggestions || msg.suggestions,
                  lifecycle: lifecycle === 'ongoing' || lifecycle === 'completed' ? lifecycle : msg.lifecycle,
                  requestId: response?.request_id || msg.requestId,
                  repairKnowledgeSources: extractRepairKnowledgeSources(response?.metadata) || msg.repairKnowledgeSources,
                }
              }))

              // done 事件里可能携带完整响应（用于流式补齐 business/suggestions/lifecycle）
              if (response?.business) {
                setCurrentBusiness(response.business)
              }
              if (response?.lifecycle_info?.current_lifecycle) {
                setCurrentLifecycle(response.lifecycle_info.current_lifecycle)
              }
              if (response?.hints && response.hints.length > 0) {
                response.hints.forEach(hint => {
                  if (hint.type === 'new_search_reminder') {
                    const notification: Notification = {
                      id: generateId(),
                      type: 'info',
                      title: '提示',
                      message: hint.message,
                      timestamp: new Date()
                    }
                    setNotifications(prev => [...prev, notification])
                  }
                })
              }

              setStreamingMessageId(null)
              setIsStreamingChat(false)
              abortControllerRef.current = null
            },
            onFallback: (response) => {
              // 回退到非流式响应，移除加载状态并处理完整响应
              setMessages(prev => prev.filter(msg => msg.id !== msgId))
              handleChatResponse(response)
              setStreamingMessageId(null)
              setIsStreamingChat(false)
              abortControllerRef.current = null
            },
            onError: (error) => {
              // 如果有占位消息则更新，否则添加新消息
              setMessages(prev => {
                const hasPlaceholder = prev.some(msg => msg.id === msgId)
                if (hasPlaceholder) {
                  return prev.map(msg =>
                    msg.id === msgId
                      ? { ...msg, type: 'assistant_text', content: `抱歉，处理请求时出错：${error}` }
                      : msg
                  )
                } else {
                  return [...prev, {
                    id: msgId,
                    type: 'assistant_text' as const,
                    content: `抱歉，处理请求时出错：${error}`,
                    timestamp: new Date()
                  }]
                }
              })
              setStreamingMessageId(null)
              setIsStreamingChat(false)
              abortControllerRef.current = null
            },
          },
          abortController.signal
        )

        await streamFn
      } else {
        // 非流式模式，使用原有逻辑
        const request: ChatRequest = {
          message,
          session_id: sessionId || undefined,
          context: requestContext,
          ask_user_answer: askUserAnswer,
          mode: effectiveMode,
          lifecycle_check: {
            current_lifecycle: currentLifecycle,
            current_business: currentBusiness ?? undefined,
            has_ongoing: hasOngoingConversation(),
              user_confirmed_switch: userConfirmedSwitch
            }
        }
        const response = imageFiles.length > 0
          ? await chatWithImages(request, imageFiles)
          : await chatApi(request)

        handleChatResponse(response)
      }
      return true
    } catch (error) {
      console.error('聊天请求失败:', error)

      if (isAbortError(error)) {
        return false
      }

      // 降级到旧搜索API
      if (useChatApi && imageFiles.length === 0 && !clarifyChoice && !askUserAnswer) {
        console.log('聊天API失败，降级到搜索API')
        setUseChatApi(false)
        await performSearch(message, {}, undefined, undefined, transientMessageId)
        return false
      }

      let errorType: 'network' | 'server' | 'unknown' = 'unknown'
      let errorMessage = '处理请求时出错，请稍后重试'

      if (error instanceof TypeError && error.message.includes('fetch')) {
        errorType = 'network'
        errorMessage = '网络连接失败，请检查网络后重试'
      }

      clearTransientLoadingMessages(transientMessageId)

      addMessage({
        type: 'error',
        content: errorMessage,
        errorType,
        retryAction: () => {
          setMessages(prev => prev.filter(m => m.type !== 'error'))
          sendChatMessage(message, clarifyChoice, clarifyFacet, false, askUserAnswer, modeOverride, imageFiles)
        },
      })
      return false
    } finally {
      setIsLoading(false)
      setStreamingMessageId(null)
      setIsStreamingChat(false)
      abortControllerRef.current = null
    }
  }

  const handleRepairFollowupAskUserV2Submit = useCallback(async (messageId: string, submission: AskUserV2Submission) => {
    const repairMessage = messages.find((msg) => msg.id === messageId && msg.type === 'repair_followup')
    const state = repairMessage?.repairFollowupState
    if (!state || !state.toolCallId || isLoading) return false

    const previousSummaryText = state.summaryText || ''
    const mergedSummaryText = mergeSummaryText(previousSummaryText, submission.summaryText) || submission.summaryText

    updateRepairFollowupMessage(messageId, (current) => ({
      ...current,
      status: 'submitting',
      summaryText: mergedSummaryText,
    }))

    const answerPayload: Record<string, any> = {
      schema_version: '2.0',
      scene: 'repair_knowledge_followup',
      form_id: submission.formId,
      action: submission.action,
      fields: submission.fields,
      summary_text: mergedSummaryText,
    }
    if (submission.action !== 'submit') {
      answerPayload.quick_action = String(submission.actionPayload?.quick_action || submission.action || '').trim() || submission.action
    }

    const success = await sendChatMessage(
      '',
      undefined,
      undefined,
      false,
      {
        tool_call_id: state.toolCallId,
        answer: answerPayload,
        metadata: (
          submission.selectionPayload || submission.actionPayload
            ? {
              ...(submission.selectionPayload ? { selection_payload: submission.selectionPayload } : {}),
              ...(submission.actionPayload ? { action_payload: submission.actionPayload } : {}),
            }
            : undefined
        ),
      }
    )

    updateRepairFollowupMessage(messageId, (current) => ({
      ...current,
      status: success ? 'submitted' : 'active',
      summaryText: success ? mergedSummaryText : previousSummaryText,
    }))
    return success
  }, [isLoading, messages, sendChatMessage, updateRepairFollowupMessage])

  const handleAskUserV2Submit = useCallback(async (messageId: string, submission: AskUserV2Submission) => {
    const askUserMessage = messages.find((msg) => msg.id === messageId && msg.type === 'ask_user_form')
    const state = askUserMessage?.askUserV2State
    if (!state || !state.toolCallId || isLoading) return false

    const previousSummaryText = state.summaryText || ''
    const mergedSummaryText = mergeSummaryText(previousSummaryText, submission.summaryText) || submission.summaryText

    updateAskUserV2Message(messageId, (current) => ({
      ...current,
      status: 'submitting',
      summaryText: mergedSummaryText,
    }))

    const answerPayload: Record<string, any> = {
      schema_version: '2.0',
      scene: state.scene || 'ask_form_v2',
      form_id: submission.formId,
      action: submission.action,
      fields: submission.fields,
      summary_text: mergedSummaryText,
    }
    if (submission.action !== 'submit') {
      answerPayload.quick_action = String(submission.actionPayload?.quick_action || submission.action || '').trim() || submission.action
    }

    const success = await sendChatMessage(
      '',
      undefined,
      undefined,
      false,
      {
        tool_call_id: state.toolCallId,
        answer: answerPayload,
        metadata: (
          submission.selectionPayload || submission.actionPayload
            ? {
              ...(submission.selectionPayload ? { selection_payload: submission.selectionPayload } : {}),
              ...(submission.actionPayload ? { action_payload: submission.actionPayload } : {}),
            }
            : undefined
        ),
      }
    )

    updateAskUserV2Message(messageId, (current) => ({
      ...current,
      status: success ? 'submitted' : 'active',
      summaryText: success ? mergedSummaryText : previousSummaryText,
    }))
    return success
  }, [isLoading, messages, sendChatMessage, updateAskUserV2Message])

  // 停止生成：中断流式输出，保留已接收的部分内容
  const stopGenerating = useCallback(() => {
    if (!abortControllerRef.current || !streamingMessageId) return

    // 1. 中断 fetch 连接（后端 LLM 请求也会随之取消）
    abortControllerRef.current.abort()
    abortControllerRef.current = null

    // 2. 获取当前已接收的部分内容
    const partialContent = messages.find(m => m.id === streamingMessageId)?.content || ''

    // 3. 确保消息类型为 assistant_text（可能还停留在 intent_loading）
    if (partialContent) {
      setMessages(prev => prev.map(msg =>
        msg.id === streamingMessageId
          ? { ...msg, type: 'assistant_text' }
          : msg
      ))
    } else {
      // 没有收到任何内容，移除占位消息
      setMessages(prev => prev.filter(msg => msg.id !== streamingMessageId))
    }

    // 4. 清理状态
    setStreamingMessageId(null)
    setIsStreamingChat(false)
    setIsLoading(false)

    // 5. 通知后端保存部分内容到对话历史（异步，不阻塞 UI）
    if (sessionId && partialContent.trim()) {
      notifyStreamAbort(sessionId, partialContent)
    }
  }, [streamingMessageId, messages, sessionId])

  // 处理聊天澄清选择
  const handleChatClarifyChoice = async (choice: string, facet?: string) => {
    if (isLoading) return

    // 查找活跃的 wizard 消息
    const activeWizard = messages.find(
      msg => msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active'
    )

    if (activeWizard && activeWizard.wizardState) {
      // 更新 wizard 的当前轮次的 selected 字段
      const { wizardState } = activeWizard
      const currentRound = wizardState.rounds[wizardState.currentRoundIndex]
      const selectedOption = currentRound?.options.find(opt => opt.key === choice)
      if (currentRound) {
        // 找到选项的 label 用于显示
        const updatedRound: WizardRound = {
          ...currentRound,
          selected: choice,
          selectedLabel: selectedOption?.label || choice
        }
        const updatedRounds = [...wizardState.rounds]
        updatedRounds[wizardState.currentRoundIndex] = updatedRound

        setMessages(prev => prev.map(msg => {
          if (msg.id === activeWizard.id) {
            return {
              ...msg,
              wizardState: {
                ...wizardState,
                rounds: updatedRounds
              }
            }
          }
          return msg
        }))
      }

      if (currentRound?.toolCallId) {
        await sendChatMessage(
          '',
          undefined,
          undefined,
          false,
          {
            tool_call_id: currentRound.toolCallId,
            answer: selectedOption?.label || choice,
            metadata: selectedOption?.selectionPayload
              ? { selection_payload: selectedOption.selectionPayload }
              : undefined,
          }
        )
        return
      }
    } else {
      // 检测 clarify_intent / clarify_business 类型，用户选择后标记为 completed，不添加用户消息气泡
      const clarifyMsg = messages.find(
        msg =>
          (msg.type === 'clarify_intent' || msg.type === 'clarify_business')
          && msg.lifecycle !== 'completed'
          && msg.lifecycle !== 'archived'
      )
      if (clarifyMsg) {
        const selectedOption = clarifyMsg.clarifyOptions?.find(opt => opt.key === choice)
        const selectedLabel = selectedOption?.label || choice

        setMessages(prev => prev.map(msg => {
          if (msg.id === clarifyMsg.id) {
            return {
              ...msg,
              lifecycle: 'completed' as const,
              selectedIntent: selectedLabel
            }
          }
          return msg
        }))
      } else {
        // 非 clarify_intent 的其他澄清，添加用户消息
        addMessage({ type: 'user', content: choice })
      }
    }

    await sendChatMessage(searchState.query || choice, choice, facet)
  }

  // 处理快捷入口预览（只打开文档查看器，不改变任何状态）
  const handleQuickAccess = async (topResult: TopResult) => {
    if (isLoading) return
    const access = normalizeDocumentAccessFields(topResult)

    // 共轨之家外部资料：按需获取文件链接
    if (access.ggzj_sn !== undefined) {
      try {
        const { getGgzjFileUrl } = await import('@/services/api')
        const fileUrlResp = await getGgzjFileUrl({
          sn: access.ggzj_sn,
          data_type: access.ggzj_data_type || 2,
          file_no: access.ggzj_file_no || null,
          file_type: access.ggzj_file_type || null,
        })
        if (fileUrlResp.url) {
          const token = generateId()
          setViewerDoc({
            title: topResult.title,
            picFolderUrl: fileUrlResp.url,
            token,
            urlType: fileUrlResp.url_type
          })
        }
      } catch {
        // 静默失败
      }
    } else if (access.pic_folder_url) {
      const token = generateId()
      setViewerDoc({
        title: topResult.title,
        picFolderUrl: access.pic_folder_url,
        token
      })
    }
    // 注意：不标记 wizard 为 completed，用户关闭查看器后可以继续澄清
  }

  // 处理快捷入口确认（通知后端结束澄清，返回该文档作为最终结果）
  const handleQuickConfirm = async (_wizardId: string, topResult: TopResult) => {
    if (isLoading) return

    const wizardMsg = messages.find(msg => msg.id === _wizardId)
    const currentRound = wizardMsg?.wizardState?.rounds[wizardMsg.wizardState.currentRoundIndex]
    if (currentRound?.toolCallId && topResult.selectionPayload) {
      await sendChatMessage(
        '',
        undefined,
        undefined,
        false,
        {
          tool_call_id: currentRound.toolCallId,
          answer: topResult.title,
          metadata: { selection_payload: topResult.selectionPayload },
        }
      )
      return
    }

    // 发送确认请求给后端
    await sendChatMessage(
      searchState.query || '',
      `__QUICK_CONFIRM_${topResult.file_id}__`,
      'quick_access'
    )
    // 后端会返回 DOCUMENTS 响应，前端 handleChatResponse 会自动将 wizard 标记为 completed
  }

  // 处理 wizard 回退
  const handleWizardBack = async (wizardId: string, targetRoundIndex: number) => {
    if (isLoading) return

    // 找到 wizard 消息
    const wizardMsg = messages.find(msg => msg.id === wizardId)
    if (!wizardMsg || !wizardMsg.wizardState) return

    const { wizardState } = wizardMsg
    const wizardIndex = messages.findIndex(msg => msg.id === wizardId)
    if (wizardIndex < 0 || (wizardState.status !== 'active' && wizardState.status !== 'completed')) return
    if (targetRoundIndex < 0 || targetRoundIndex >= wizardState.rounds.length) return

    // 如果 wizard 之后已经出现新的用户消息，说明用户已开启新一轮上下文，旧路径禁止回退
    const hasNewerUserTurn = messages.slice(wizardIndex + 1).some(msg => msg.type === 'user')
    if (hasNewerUserTurn) return

    const removedResultIds = wizardIndex >= 0
      ? messages
        .slice(wizardIndex + 1)
        .filter(msg => msg.type === 'results' && (!msg.relatedWizardId || msg.relatedWizardId === wizardId))
        .map(msg => msg.id)
      : []

    // 回退到目标轮次：清除目标轮次及之后的所有选择
    const updatedRounds = wizardState.rounds.slice(0, targetRoundIndex + 1).map((round, idx) => {
      if (idx === targetRoundIndex) {
        // 目标轮次：清除选择
        return { ...round, selected: undefined, selectedLabel: undefined }
      }
      return round
    })
    const targetRoundContext = updatedRounds[targetRoundIndex]?.context || {}
    const restoredTopResult = buildTopResultFromRaw(targetRoundContext.top_result)
    const restoredExistenceInfo = buildExistenceInfoFromRaw(targetRoundContext.existence_info)

    setMessages(prev => prev.flatMap((msg, index) => {
      if (
        wizardIndex >= 0 &&
        index > wizardIndex &&
        msg.type === 'results' &&
        (!msg.relatedWizardId || msg.relatedWizardId === wizardId)
      ) {
        return []
      }
      if (msg.id === wizardId) {
        return [{
          ...msg,
          wizardState: {
            ...wizardState,
            rounds: updatedRounds,
            currentRoundIndex: targetRoundIndex,
            status: 'active' as const,
            topResult: restoredTopResult,
            existenceInfo: restoredExistenceInfo,
          }
        }]
      }
      return [msg]
    }))

    if (removedResultIds.length > 0) {
      setResultPages(prev => {
        const next = { ...prev }
        removedResultIds.forEach(id => {
          delete next[id]
        })
        return next
      })
    }
  }

  // 处理推荐问题点击
  const handleSuggestionClick = async (suggestion: SuggestedQuestion, sourceMessageId?: string) => {
    if (isLoading || !suggestion.query) return

    // 推荐问题会开启新一轮，先归档旧路径和旧按钮，避免历史上下文可回退
    cleanupOngoingState()

    // 点击后立即隐藏该条消息下的推荐，避免回点历史推荐造成上下文混乱
    if (sourceMessageId) {
      setMessages(prev => prev.map(msg =>
        msg.id === sourceMessageId
          ? { ...msg, suggestions: [] }
          : msg
      ))
    }

    // 添加用户消息（显示推荐文本）
    addMessage({ type: 'user', content: suggestion.text })

    // 根据 action_type 设置模式
    let targetMode: Mode = currentMode
    if (suggestion.action_type !== 'auto' && suggestion.action_type !== 'none') {
      targetMode = suggestion.action_type as Mode
    }

    // 临时切换模式并发送消息
    if (targetMode !== currentMode) {
      setCurrentMode(targetMode)
    }

    // 发送实际查询
    await sendChatMessage(suggestion.query, undefined, undefined, false, undefined, targetMode)

    // 如果需要恢复模式，在这里恢复
    // 注意：这里我们不恢复模式，让用户在新的上下文中继续
  }

  // ==================== 图片附件处理函数 ====================

  const clearPendingImageAttachments = useCallback(() => {
    setPendingImageAttachments(prev => {
      prev.forEach(attachment => {
        revokeImagePreviewUrl(attachment.previewUrl)
      })
      return []
    })
  }, [])

  const removePendingImageAttachment = useCallback((attachmentId: string) => {
    setPendingImageAttachments(prev => {
      const target = prev.find(item => item.id === attachmentId)
      if (target) {
        revokeImagePreviewUrl(target.previewUrl)
      }
      return prev.filter(item => item.id !== attachmentId)
    })
  }, [])

  const handleImageSelect = async (files: File[]) => {
    const remainingSlots = Math.max(0, imageEvidenceMaxFiles - pendingImageAttachments.length)
    if (remainingSlots <= 0) return

    const selectedFiles = files.slice(0, remainingSlots)
    if (selectedFiles.length === 0) return

    try {
      const compressedImages = await Promise.all(selectedFiles.map(file => compressImage(file)))
      const attachments = compressedImages.map((compressedFile, index) => {
        const originalFile = selectedFiles[index]
        const filename = originalFile.name || `image_${Date.now()}_${index + 1}.jpg`
        const file = compressedFile instanceof File
          ? compressedFile
          : new File([compressedFile], filename, {
              type: compressedFile.type || originalFile.type || 'image/jpeg',
              lastModified: originalFile.lastModified || Date.now(),
            })
        return {
          id: generateId(),
          file,
          previewUrl: getImagePreviewUrl(file),
          name: originalFile.name || filename,
          size: file.size,
        }
      })

      setPendingImageAttachments(prev => [...prev, ...attachments].slice(0, imageEvidenceMaxFiles))
    } catch (error) {
      console.error('处理待发送图片失败:', error)
      addMessage({
        type: 'error',
        content: '处理图片失败，请重新选择',
        errorType: 'server'
      })
    }
  }

  const uploadMessageImagesToOss = async (
    attachments: PendingImageAttachment[]
  ): Promise<UploadedMessageImage[]> => {
    if (attachments.length === 0) return []

    const uploaded: UploadedMessageImage[] = []
    for (const attachment of attachments) {
      const [error, result] = await uploadImage(attachment.file, sessionId)
      if (error || !result) {
        throw error || new Error('图片上传 OSS 失败')
      }
      uploaded.push(result)
    }
    return uploaded
  }

  // 查看诊断报告（使用 ReportViewer 组件）
  const handleViewReport = (reportUrl: string) => {
    // 添加 showBack=false 参数，隐藏报告页面自带的返回按钮
    const separator = reportUrl.includes('?') ? '&' : '?'
    const urlWithParam = `${reportUrl}${separator}showBack=false`
    console.log('[诊断报告] 打开报告:', urlWithParam)
    const token = generateId()
    setCurrentReportUrl(urlWithParam)
    setCurrentReportToken(token)
    setShowReportViewer(true)
  }

  // 关闭报告查看器
  const handleCloseReportViewer = (token?: string) => {
    if (token && currentReportToken && token !== currentReportToken) return
    setShowReportViewer(false)
    setCurrentReportUrl(null)
    setCurrentReportToken(null)
  }

  // ==================== 图片附件处理函数结束 ====================

  // 关闭通知
  const dismissNotification = (notificationId: string) => {
    setNotifications(prev => prev.filter(n => n.id !== notificationId))
  }

    // 检查是否有进行中的对话（用于切换确认）
    // 注意：这里只检测需要用户明确确认才能中断的场景（多轮澄清、ECU选择等）
    // 普通的建议按钮（no_match、推荐问题）会在 submitMessage 中自动清理
  const hasOngoingConversation = useCallback(() => {
    // 检查是否有需要交互的进行中消息
    const ongoingTypes = ['clarify_intent', 'clarify_business', 'clarify', 'ecu_selection']
    const hasOngoingMessage = messages.some(msg =>
      ongoingTypes.includes(msg.type) &&
      msg.lifecycle !== 'completed' &&
      msg.lifecycle !== 'archived'
    )
    // 检查是否有活跃的 wizard
    const hasActiveWizard = messages.some(msg =>
      msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active'
    )
    const hasActiveRepairFollowup = messages.some(msg =>
      msg.type === 'repair_followup' &&
      msg.repairFollowupState &&
      ['active', 'submitting'].includes(msg.repairFollowupState.status)
    )
    const hasActiveAskUserV2 = messages.some(msg =>
      msg.type === 'ask_user_form' &&
      msg.askUserV2State &&
      ['active', 'submitting'].includes(msg.askUserV2State.status)
    )
    return hasOngoingMessage || hasActiveWizard || hasActiveRepairFollowup || hasActiveAskUserV2
  }, [messages])

  // 获取当前进行中对话的上下文信息
  const getOngoingContextInfo = useCallback(() => {
    const activeRepairFollowup = messages.find(msg =>
      msg.type === 'repair_followup' &&
      msg.repairFollowupState &&
      ['active', 'submitting'].includes(msg.repairFollowupState.status)
    )
    if (activeRepairFollowup?.repairFollowupState) {
      return {
        clarifyRound: 1,
        query: activeRepairFollowup.repairFollowupState.originalQuery
      }
    }

    const activeAskUserV2 = messages.find(msg =>
      msg.type === 'ask_user_form' &&
      msg.askUserV2State &&
      ['active', 'submitting'].includes(msg.askUserV2State.status)
    )
    if (activeAskUserV2?.askUserV2State) {
      return {
        clarifyRound: 1,
        query: activeAskUserV2.askUserV2State.originalQuery || searchState.query
      }
    }

    // 先检查 wizard
    const activeWizard = messages.find(msg =>
      msg.type === 'clarify_wizard' && msg.wizardState?.status === 'active'
    )
    if (activeWizard && activeWizard.wizardState) {
      return {
        clarifyRound: activeWizard.wizardState.currentRoundIndex + 1,
        query: activeWizard.wizardState.originalQuery
      }
    }

    const clarifyMessages = messages.filter(msg =>
      (msg.type === 'clarify_intent' || msg.type === 'clarify_business') &&
      msg.lifecycle !== 'completed'
    )
    const clarifyRound = clarifyMessages.length
    const latestUserMessage = [...messages].reverse().find(msg => msg.type === 'user')

    return {
      clarifyRound,
      query: latestUserMessage?.content || searchState.query
    }
  }, [messages, searchState.query])

  // 图片上传前的冲突检测（返回 Promise，用户确认返回 true，取消返回 false）
  const checkImageUploadConflict = useCallback((): Promise<boolean> => {
    // 无进行中的对话，直接允许
    if (!hasOngoingConversation()) {
      return Promise.resolve(true)
    }

    // 有进行中的对话，弹出确认框
    return new Promise((resolve) => {
      imageUploadResolveRef.current = resolve
      const contextInfo = getOngoingContextInfo()
      setSwitchConfirmState({
        isOpen: true,
        pendingMessage: '__IMAGE_UPLOAD__',  // 特殊标记，表示是图片上传场景
        contextInfo
      })
    })
  }, [hasOngoingConversation, getOngoingContextInfo])

  // 清理进行中的状态
  const cleanupOngoingState = useCallback(() => {
    setMessages(prev => prev.map(msg => {
      // 将进行中的交互消息标记为 archived
      if (['clarify_intent', 'clarify_business', 'clarify', 'ecu_selection'].includes(msg.type) &&
          msg.lifecycle !== 'completed') {
        return { ...msg, lifecycle: 'archived' as const }
      }
      // 新一轮开始后，将 wizard（含已完成路径）归档，避免历史路径跨上下文回退
      if (msg.type === 'clarify_wizard' && msg.wizardState && msg.wizardState.status !== 'archived') {
        return {
          ...msg,
          wizardState: { ...msg.wizardState, status: 'archived' as const }
        }
      }
      if (msg.type === 'repair_followup' && msg.repairFollowupState && msg.repairFollowupState.status !== 'archived') {
        return {
          ...msg,
          repairFollowupState: { ...msg.repairFollowupState, status: 'archived' as const }
        }
      }
      if (msg.type === 'ask_user_form' && msg.askUserV2State && msg.askUserV2State.status !== 'archived') {
        return {
          ...msg,
          askUserV2State: { ...msg.askUserV2State, status: 'archived' as const }
        }
      }
      // 将带有交互按钮的 assistant_text/assistant_fault 消息标记为 archived
      // 包括 no_match 建议按钮和推荐问题
      if (['assistant_text', 'assistant_fault'].includes(msg.type) &&
          ((msg as any).wizardState?.existenceInfo?.suggestions || msg.suggestions?.length)) {
        return { ...msg, lifecycle: 'archived' as const }
      }
      return msg
    }))
    setCurrentLifecycle('idle')
    setCurrentBusiness(null)
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const hasPendingImages = pendingImageAttachments.length > 0
    if ((!inputValue.trim() && !hasPendingImages) || isLoading) return

    const query = inputValue.trim()

    // 检查是否有进行中的对话需要确认
    if (hasOngoingConversation()) {
      // 弹出确认对话框
      setSwitchConfirmState({
        isOpen: true,
        pendingMessage: query,
        contextInfo: getOngoingContextInfo()
      })
      return
    }

    // 直接发送
    await submitMessage(query)
  }

  // 实际的消息提交逻辑
  const submitMessage = async (query: string, userConfirmedSwitch = false) => {
    const attachmentsToSend = [...pendingImageAttachments]
    setInputValue('')
    setPendingImageAttachments([])
    let uploadedImages: UploadedMessageImage[] = []

    if (attachmentsToSend.length > 0) {
      setIsLoading(true)
    }
    try {
      uploadedImages = await uploadMessageImagesToOss(attachmentsToSend)
    } catch (error) {
      console.error('上传图片到 OSS 失败:', error)
      setIsLoading(false)
      addMessage({
        type: 'error',
        content: error instanceof Error ? error.message : '图片上传失败，请稍后重试',
        errorType: 'server',
      })
      setPendingImageAttachments(prev => {
        const restored = [...attachmentsToSend, ...prev]
        const deduped: PendingImageAttachment[] = []
        for (const attachment of restored) {
          if (deduped.some(item => item.id === attachment.id)) continue
          deduped.push(attachment)
        }
        return deduped.slice(0, imageEvidenceMaxFiles)
      })
      return
    }

    // 清理旧的交互按钮（无论是否有进行中对话都执行，避免上下文混乱）
    cleanupOngoingState()

    // Reset search state for new query
    setSearchState({ query, filters: {} })

    addMessage({
      type: 'user',
      content: query || (attachmentsToSend.length > 1 ? `发送了 ${attachmentsToSend.length} 张图片` : '发送了图片'),
      imagePreviews: uploadedImages.map(item => item.url),
      imageFileNames: attachmentsToSend.map(item => item.name),
      imageOssObjectKeys: uploadedImages.map(item => item.objectKey),
      imageOssSessionIds: uploadedImages.map(item => item.uploadSessionId || null),
      imageOssDeleteTokens: uploadedImages.map(item => item.deleteToken || ''),
    })

    // 手动输入的新问题始终走自动识别，避免继承历史隐藏模式。
    if (currentMode !== 'auto') {
      setCurrentMode('auto')
    }

    // 使用新的聊天API或降级到搜索API
    if (useChatApi) {
      const success = await sendChatMessage(
        query,
        undefined,
        undefined,
        userConfirmedSwitch,
        undefined,
        'auto',
        attachmentsToSend.map(item => item.file)
      )
      if (!success) {
        setPendingImageAttachments(prev => {
          const restored = [...attachmentsToSend, ...prev]
          const deduped: PendingImageAttachment[] = []
          for (const attachment of restored) {
            if (deduped.some(item => item.id === attachment.id)) continue
            deduped.push(attachment)
          }
          return deduped.slice(0, imageEvidenceMaxFiles)
        })
      }
    } else {
      await performSearch(query)
    }
  }

  // 确认切换处理
  const handleConfirmSwitch = async () => {
    const { pendingMessage } = switchConfirmState
    setSwitchConfirmState({ isOpen: false, pendingMessage: '' })

    // 清理进行中的状态
    cleanupOngoingState()

    // 检查是否是图片上传场景
    if (pendingMessage === '__IMAGE_UPLOAD__') {
      // 图片上传场景：resolve Promise，让文件选择器打开
      if (imageUploadResolveRef.current) {
        imageUploadResolveRef.current(true)
        imageUploadResolveRef.current = null
      }
    } else if (pendingMessage) {
      // 文字消息场景：提交消息，标记用户已确认切换
      await submitMessage(pendingMessage, true)
    }
  }

  // 取消切换处理
  const handleCancelSwitch = () => {
    // 图片上传场景：resolve Promise 为 false
    if (switchConfirmState.pendingMessage === '__IMAGE_UPLOAD__' && imageUploadResolveRef.current) {
      imageUploadResolveRef.current(false)
      imageUploadResolveRef.current = null
    }
    setSwitchConfirmState({ isOpen: false, pendingMessage: '' })
    // 保留输入内容，用户可以修改
  }

  const handleClarifyChoice = async (facet: string, choice: string) => {
    if (isLoading) return

    addMessage({ type: 'user', content: choice })

    // Update filters
    const newFilters = { ...searchState.filters }
    if (choice !== '其他' && choice !== '不确定') {
      newFilters[facet] = choice
    }
    setSearchState(prev => ({ ...prev, filters: newFilters }))

    await performSearch(searchState.query, newFilters, facet, choice)
  }

  const handleNewSearch = () => {
    setShowNewSearchConfirm(true)
  }

  const collectOssImagesForDeletion = useCallback(() => {
    const collected: Array<{ key: string; deleteToken?: string | null; sessionId?: string | null }> = []
    for (const message of messages) {
      const keys = message.imageOssObjectKeys || []
      const sessionIds = message.imageOssSessionIds || []
      const tokens = message.imageOssDeleteTokens || []
      keys.forEach((key, index) => {
        collected.push({ key, deleteToken: tokens[index], sessionId: sessionIds[index] ?? null })
      })
    }
    const seen = new Set<string>()
    return collected.filter(item => {
      if (!item.key || !item.deleteToken || seen.has(item.key)) return false
      seen.add(item.key)
      return true
    })
  }, [messages])

  const confirmNewSearch = useCallback(() => {
    const imagesToDelete = collectOssImagesForDeletion()
    if (imagesToDelete.length > 0) {
      const groups = new Map<string, Array<{ key: string; deleteToken?: string | null }>>()
      imagesToDelete.forEach(item => {
        const groupKey = item.sessionId || ''
        const group = groups.get(groupKey) || []
        group.push({ key: item.key, deleteToken: item.deleteToken })
        groups.set(groupKey, group)
      })
      for (const [uploadSessionId, objects] of groups) {
        void requestDeleteOssImages({
          sessionId: uploadSessionId || null,
          objects,
          reason: 'new_search',
        }).catch(error => {
          console.warn('提交 OSS 图片异步删除任务失败:', error)
        })
      }
    }

    setShowNewSearchConfirm(false)
    setExampleRefreshKey(k => k + 1) // 刷新主页示例问题
    setMessages([])
    setSearchState({ query: '', filters: {} })
    setSessionId(null) // 重置会话
    setUseChatApi(true) // 重置为使用聊天API
    setCurrentMode('auto') // 重置为自动模式
    taskManager.unsubscribeAll() // 取消所有SSE订阅
    clearPendingImageAttachments()
    // 清除持久化的会话状态
    localStorage.removeItem(SESSION_STORAGE_KEY)
    inputRef.current?.focus()
  }, [clearPendingImageAttachments, collectOssImagesForDeletion])

  const renderTags = (tags: SearchResult['tags']) => {
    const tagItems: { label: string; value: string; color: string }[] = []

    if (tags.ecus?.length) tagItems.push({ label: 'ECU', value: tags.ecus[0], color: 'emerald' })
    if (tags.emissions?.length) tagItems.push({ label: '排放', value: tags.emissions[0], color: 'purple' })

    return tagItems
  }

  // 业务类型名称映射
  const getBusinessName = (business: BusinessType | undefined): string => {
    const nameMap: Record<string, string> = {
      'DOC_SEARCH': '资料搜索',
      'PARAM_QUERY': '参数查询',
      'FAULT_DIAGNOSIS': '故障诊断',
      'GENERAL_CHAT': '维修问答',
      'INTENT_CLARIFYING': '意图识别',
      'IDLE': '空闲',
      'AGENT_LOOP': '维修问答'
    }
    return business ? nameMap[business] || business : ''
  }

  // 检测业务状态变化
  const getBusinessChange = (index: number): string | null => {
    if (index === 0) return null

    const currentMsg = messages[index]
    // 只检查非用户消息
    if (currentMsg.type === 'user') return null
    if (!currentMsg.business) return null

    // 向前查找上一条非用户消息
    for (let i = index - 1; i >= 0; i--) {
      const prevMsg = messages[i]
      if (prevMsg.type !== 'user' && prevMsg.business) {
        if (prevMsg.business !== currentMsg.business) {
          return getBusinessName(currentMsg.business)
        }
        return null
      }
    }

    // 首条业务消息
    return getBusinessName(currentMsg.business)
  }

  return (
    <div className="app-container">
      {/* Background Effects */}
      <div className="bg-grid" />
      <div className="bg-glow bg-glow-1" />
      <div className="bg-glow bg-glow-2" />

      {/* Header - 只在有消息时显示 */}
      {messages.length > 0 && (
        <header className="app-header">
          <div className="header-inner">
            <div className="header-content">
              <div className="logo-section">
                <div className="logo-icon" onClick={handleLogoDiagnose}>
                  <img src={`${import.meta.env.BASE_URL}logo_black.svg`} alt="Logo" />
                </div>
                <div className="logo-text">
                  <h1>CRS 智能汽修助手</h1>
                </div>
              </div>
              <div className="header-actions">
                <button className="new-search-btn" onClick={handleNewSearch}>
                  <Plus size={16} />
                  新搜索
                </button>
              </div>
            </div>
          </div>
        </header>
      )}

      {/* 通知横幅 */}
      {notifications.length > 0 && (
        <div className="notification-container">
          {notifications.map((notification) => (
            <div key={notification.id} className={`notification-banner notification-${notification.type}`}>
              <div className="notification-icon">
                {notification.type === 'success' && <CircleCheck size={18} />}
                {notification.type === 'info' && <Info size={18} />}
                {notification.type === 'warning' && <TriangleAlert size={18} />}
                {notification.type === 'error' && <CircleX size={18} />}
              </div>
              <div className="notification-content">
                <span className="notification-title">{notification.title}</span>
                <span className="notification-message">{notification.message}</span>
              </div>
              {notification.action && (
                <a
                  href={notification.action.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="notification-action"
                  onClick={(e) => {
                    if (notification.action?.onClick) {
                      e.preventDefault()
                      notification.action.onClick()
                    }
                  }}
                >
                  {notification.action.label}
                </a>
              )}
              <button
                className="notification-close"
                onClick={() => dismissNotification(notification.id)}
              >
                <X size={16} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Messages Area */}
      <main className="messages-container">
        <div className="messages-inner">
        {messages.length === 0 ? (
          <div className="welcome-screen">
            <div className="welcome-logo" onClick={handleLogoDiagnose}>
              <img src={`${import.meta.env.BASE_URL}logo_black.svg`} alt="CRS Logo" />
            </div>
            <h2>CRS 智能汽修助手</h2>
            <p>输入故障码、搜索维修资料、咨询技术问题</p>
            <div className="example-queries">
              <span className="example-label">试试这些</span>
              <div className="example-chips">
                {exampleQueries.map((item) => (
                  <button
                    key={item.text}
                    className={`example-chip chip-${item.color}`}
                    onClick={() => {
                      setInputValue(item.text)
                      inputRef.current?.focus()
                    }}
                  >
                    {item.text}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="messages-list">
            {messages.map((message, index) => {
              const businessChange = getBusinessChange(index)
              const hasNewerUserTurn = latestUserMessageIndex > index

              // clarify_intent / clarify_business 完成后完全不渲染（含 badge）
              if ((message.type === 'clarify_intent' || message.type === 'clarify_business') && message.lifecycle === 'completed') {
                return null
              }

              // 交互消息被归档后隐藏（用户开始新一轮对话，避免旧按钮/旧路径造成上下文混乱）
              if (['clarify_intent', 'clarify_business', 'clarify', 'ecu_selection'].includes(message.type) &&
                  message.lifecycle === 'archived') {
                return null
              }

              if (
                message.type === 'clarify_wizard' &&
                (message.wizardState?.status === 'archived' || hasNewerUserTurn)
              ) {
                return null
              }

              if (
                message.type === 'repair_followup' &&
                (message.repairFollowupState?.status === 'archived' || hasNewerUserTurn)
              ) {
                return null
              }

              if (
                message.type === 'ask_user_form' &&
                (message.askUserV2State?.status === 'archived' || hasNewerUserTurn)
              ) {
                return null
              }

              return (
                <div
                  key={message.id}
                  className={`message message-${message.type}`}
                  style={{ animationDelay: `${index * 0.05}s` }}
                >
                  {/* 状态切换标注 */}
                  {businessChange && (
                    <div className="business-change-badge">
                      <span className="badge-icon">
                        <Zap size={12} />
                      </span>
                      <span className="badge-text">切换至：{businessChange}</span>
                    </div>
                  )}

                  {message.type === 'user' && (
                  <div className="message-bubble user-bubble">
                    {message.imagePreviews && message.imagePreviews.length > 0 && (
                      <div className="user-attachment-grid">
                        {message.imagePreviews.map((preview, previewIndex) => (
                          <button
                            type="button"
                            key={`${message.id}-preview-${previewIndex}`}
                            className="user-attachment-item"
                            onClick={() =>
                              setImagePreviewModal({
                                src: preview,
                                alt: message.imageFileNames?.[previewIndex] || `用户上传图片 ${previewIndex + 1}`,
                              })
                            }
                            title="预览图片"
                          >
                            <img
                              src={preview}
                              alt={message.imageFileNames?.[previewIndex] || `用户上传图片 ${previewIndex + 1}`}
                              className="user-attachment-image"
                            />
                          </button>
                        ))}
                      </div>
                    )}
                    <span className="message-text">{message.content}</span>
                  </div>
                )}

                {message.type === 'system' && (
                  <div className="message-bubble system-bubble">
                    <span className="message-text">{message.content}</span>
                  </div>
                )}

                {message.type === 'error' && (
                  <div className={`error-card error-${message.errorType || 'unknown'}`}>
                    <div className="error-icon">
                      {message.errorType === 'network' ? (
                        <WifiOff size={20} />
                      ) : message.errorType === 'server' ? (
                        <ServerCrash size={20} />
                      ) : (
                        <CircleAlert size={20} />
                      )}
                    </div>
                    <div className="error-content">
                      <span className="error-title">
                        {message.errorType === 'network' ? '网络错误' :
                         message.errorType === 'server' ? '服务不可用' : '出错了'}
                      </span>
                      <span className="error-message">{message.content}</span>
                    </div>
                    {message.retryAction && (
                      <button
                        className="error-retry-btn"
                        onClick={message.retryAction}
                        disabled={isLoading}
                      >
                        <RefreshCw size={14} />
                        重试
                      </button>
                    )}
                  </div>
                )}

                {message.type === 'no_results' && message.validity && (
                  <div className="no-results-card">
                    <div className="no-results-icon">
                      <SearchSlash size={28} strokeWidth={1.5} />
                    </div>
                    <div className="no-results-content">
                      <span className="no-results-title">{message.content}</span>
                      {message.validity.suggestion && (
                        <span className="no-results-suggestion">{message.validity.suggestion}</span>
                      )}
                    </div>
                    {message.stats && (
                      <div className="no-results-stats">
                        <span>耗时 {message.stats.took_ms}ms</span>
                      </div>
                    )}
                  </div>
                )}

                {message.type === 'correction' && message.correction && (
                  <div className="correction-card">
                    <div className="correction-icon">
                      <Pencil size={16} />
                    </div>
                    <div className="correction-content">
                      <span className="correction-label">智能纠错：</span>
                      {message.correction.corrections.map((c, idx) => (
                        <span key={idx} className="correction-item">
                          <span className="correction-original">{c.original}</span>
                          <span className="correction-arrow">→</span>
                          <span className="correction-corrected">{c.corrected}</span>
                          {idx < message.correction!.corrections.length - 1 && <span className="correction-separator">、</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {message.type === 'clarify' && message.clarify && (
                  <div className="clarify-card">
                    <div className="clarify-header">
                      <div className="clarify-icon">
                        <Info size={18} />
                      </div>
                      <span className="clarify-question">{message.content}</span>
                    </div>
                    <div className="clarify-options">
                      {message.clarify.options?.map((option, idx) => (
                        <button
                          key={option}
                          className="clarify-option"
                          onClick={() => handleClarifyChoice(message.clarify!.facet!, option)}
                          disabled={isLoading}
                          style={{ animationDelay: `${idx * 0.08}s` }}
                        >
                          <span className="option-index">{idx + 1}</span>
                          <span className="option-text">{option}</span>
                          <ChevronRight size={16} className="option-arrow" />
                        </button>
                      ))}
                    </div>
                    {message.stats && (
                      <div className="clarify-stats">
                        <span>当前匹配 {message.stats.candidates} 个文档</span>
                        <span className="stats-separator">•</span>
                        <span>{message.stats.took_ms}ms</span>
                      </div>
                    )}
                  </div>
                )}

                {message.type === 'results' && message.results && (() => {
                  const orderedResults = message.results
                  const currentPage = resultPages[message.id] || 0
                  const totalPages = Math.max(1, Math.ceil(orderedResults.length / RESULTS_PER_PAGE))
                  const startIdx = currentPage * RESULTS_PER_PAGE
                  const endIdx = startIdx + RESULTS_PER_PAGE
                  const currentResults = orderedResults.slice(startIdx, endIdx)
                  const hasPagination = totalPages > 1

                  return (
                  <div className="results-card">
                    <div className="results-header">
                      <div className="results-icon">
                        <CircleCheckBig size={20} />
                      </div>
                      <div className="results-summary">
                        <span className="results-title">{message.content}</span>
                        {hasPagination ? (
                          <span className="results-hint">当前找到资料较多，您可点击下一页查看更多</span>
                        ) : (
                          message.stats && (
                            <span className="results-time">耗时 {message.stats.took_ms}ms</span>
                          )
                        )}
                      </div>
                    </div>
                    <div className="results-list">
                      {currentResults.map((result, idx) => {
                        const resultRank = startIdx + idx + 1
                        const tagItems = renderTags(result.tags)
                        const topHits = Array.isArray(result.body_search?.top_hits)
                          ? result.body_search.top_hits.filter((hit): hit is CircuitBodyBestHit => Boolean(hit?.hit_id))
                          : []
                        const circuitHits = topHits.length > 0
                          ? topHits
                          : (result.body_search?.status === 'hit' && result.body_search.best_hit ? [result.body_search.best_hit] : [])
                        const hasCircuitHits = result.body_search?.status === 'hit' && circuitHits.length > 0
                        const compactedCircuitHits = compactCircuitHits(circuitHits)
                        const visibleCircuitHits = compactedCircuitHits.visibleHits
                        const hiddenCircuitHitCount = compactedCircuitHits.hiddenHitCount + (result.body_search?.more_hits_count || 0)
                        const expandedHitKey = expandedCircuitHitByMessage[message.id]
                        const isCircuitHitExpanded = Boolean(
                          hasCircuitHits && visibleCircuitHits.some((hit) => (
                            `${message.id}:${result.file_id}:${hit.candidate_id || hit.hit_id || hit.page_index}` === expandedHitKey
                          ))
                        )

                        return (
                          <div
                            key={result.file_id}
                            className={`result-item${hasCircuitHits ? ' result-item-body-hit' : ' result-item-clickable'}${isCircuitHitExpanded ? ' result-item-body-hit-expanded' : ''}`}
                            style={{ animationDelay: `${idx * 0.05}s` }}
                            onClick={() => {
                              if (!hasCircuitHits) {
                                openSearchResultDocument(result)
                              }
                            }}
                          >
                            <div className="result-rank">{resultRank}</div>
                            <div className="result-content">
                              <div className="result-title-row">
                                {hasCircuitHits && (
                                  <span className="result-inline-rank">{resultRank}</span>
                                )}
                                <h4 className="result-title">{result.title}</h4>
                                {hasCircuitHits && resultRank === 1 && (
                                  <span className="result-primary-badge">最可能</span>
                                )}
                              </div>
                              {tagItems.length > 0 && (
                                <div className="result-tags">
                                  {tagItems.map((tag) => (
                                    <span key={tag.label} className={`result-tag tag-${tag.color}`}>
                                      <span className="tag-label">{tag.label}</span>
                                      <span className="tag-value">{tag.value}</span>
                                    </span>
                                  ))}
                                </div>
                              )}
                              {hasCircuitHits && (
                                <div className="circuit-hit-sublist" onClick={(event) => event.stopPropagation()}>
                                  <div className="circuit-hit-sublist-header">
                                    <span>图内命中位置</span>
                                    <span>
                                      {compactedCircuitHits.totalCount > visibleCircuitHits.length
                                        ? `展示 ${visibleCircuitHits.length}/${compactedCircuitHits.totalCount}`
                                        : `${visibleCircuitHits.length} 处`}
                                    </span>
                                  </div>
                                  {compactedCircuitHits.mergedNearbyCount > 0 && (
                                    <div className="circuit-hit-sublist-note">
                                      已收起 {compactedCircuitHits.mergedNearbyCount} 个相近位置
                                    </div>
                                  )}
                                  {visibleCircuitHits.map((hit, hitIndex) => {
                                    const circuitHitKey = `${message.id}:${result.file_id}:${hit.candidate_id || hit.hit_id || hit.page_index}`
                                    const isHitExpanded = expandedHitKey === circuitHitKey
                                    return (
                                      <CircuitBodyHitPanel
                                        key={circuitHitKey}
                                        bodySearch={result.body_search}
                                        hit={hit}
                                        expanded={isHitExpanded}
                                        rank={hitIndex + 1}
                                        isPrimary={hitIndex === 0}
                                        resolveDocumentAccess={async () => {
                                          return resolveSearchResultDocumentAccessForPreview(result)
                                        }}
                                        onToggle={() => {
                                          setExpandedCircuitHitByMessage(prev => ({
                                            ...prev,
                                            [message.id]: isHitExpanded ? null : circuitHitKey,
                                          }))
                                        }}
                                        onOpenDocument={() => openSearchResultDocument(result, hit)}
                                      />
                                    )
                                  })}
                                  {hiddenCircuitHitCount > 0 ? (
                                    <button
                                      type="button"
                                      className="circuit-hit-more"
                                      onClick={(event) => {
                                        event.preventDefault()
                                        event.stopPropagation()
                                        openSearchResultDocument(result, visibleCircuitHits[0] || circuitHits[0])
                                      }}
                                    >
                                      还有 {hiddenCircuitHitCount} 处命中，进入文档内查看
                                    </button>
                                  ) : null}
                                </div>
                              )}
                            </div>
                            <div className="result-view-btn">
                              <span>{hasCircuitHits ? '位置' : '查看'}</span>
                              <ChevronRight size={16} strokeWidth={2.5} />
                            </div>
                          </div>
                        )
                      })}
                    </div>
                    {/* 分页控件 */}
                    {totalPages > 1 && (
                      <div className="results-pagination">
                        <button
                          className="pagination-btn"
                          disabled={currentPage === 0}
                          onClick={() => setResultPages(prev => ({ ...prev, [message.id]: currentPage - 1 }))}
                        >
                          <ChevronLeft size={16} strokeWidth={2.5} />
                          上一页
                        </button>
                        <span className="pagination-info">
                          {currentPage + 1} / {totalPages}
                          <span className="pagination-total">（共 {message.results.length} 条）</span>
                        </span>
                        <button
                          className="pagination-btn"
                          disabled={currentPage >= totalPages - 1}
                          onClick={() => setResultPages(prev => ({ ...prev, [message.id]: currentPage + 1 }))}
                        >
                          下一页
                          <ChevronRight size={16} strokeWidth={2.5} />
                        </button>
                      </div>
                    )}
                  </div>
                  )
                })()}

                {/* 搜索结果反馈卡片 */}
                {message.type === 'results' && message.requestId && !message.feedbackSubmitted && (
                  <FeedbackCard
                    requestId={message.requestId}
                    sessionId={sessionId}
                    businessType="DOC_SEARCH"
                    onSubmitted={() => markFeedbackSubmitted(message.id)}
                  />
                )}

                {/* 意图识别加载状态 */}
                {message.type === 'intent_loading' && (
                  <div className="intent-loading-card">
                    {/* 神经网络动画 */}
                    <div className="intent-neural-container">
                      {/* 中央光晕 */}
                      <div className="intent-neural-core" />

                      {/* 连接线 SVG */}
                      <svg className="intent-neural-lines" viewBox="0 0 48 48">
                        {/* 静态连接线 */}
                        <path className="intent-neural-line" d="M24 4 L24 24" />
                        <path className="intent-neural-line" d="M8 18 L24 24" />
                        <path className="intent-neural-line" d="M40 18 L24 24" />
                        <path className="intent-neural-line" d="M8 30 L24 24" />
                        <path className="intent-neural-line" d="M40 30 L24 24" />
                        <path className="intent-neural-line" d="M24 44 L24 24" />

                        {/* 脉冲动画线 */}
                        <path className="intent-neural-pulse" d="M24 4 L24 24" />
                        <path className="intent-neural-pulse" d="M8 18 L24 24" />
                        <path className="intent-neural-pulse" d="M40 30 L24 24" />
                        <path className="intent-neural-pulse" d="M24 44 L24 24" />
                      </svg>

                      {/* 神经节点 */}
                      <span className="intent-neural-node" />
                      <span className="intent-neural-node" />
                      <span className="intent-neural-node" />
                      <span className="intent-neural-node" />
                      <span className="intent-neural-node" />
                      <span className="intent-neural-node" />
                      <span className="intent-neural-node" />
                    </div>

                    {/* 文本内容 */}
                    <div className="intent-loading-content">
                      <div className="intent-loading-title">
                        <span>CRS Agent</span>
                        <span className="intent-loading-badge">AUTO</span>
                      </div>
                      <div className="intent-loading-text">
                        {message.streamHint || '正在理解您的意图'}
                        <span className="intent-typing-dots">
                          <span />
                          <span />
                          <span />
                        </span>
                      </div>
                    </div>
                  </div>
                )}

                {/* 助手文本回复 */}
                {message.type === 'assistant_text' && (
                  <div className={`assistant-bubble ${streamingMessageId === message.id ? 'assistant-streaming' : ''}`}>
                    <div className="assistant-avatar assistant-logo" style={{ backgroundImage: `url(${import.meta.env.BASE_URL}logo_black.svg)` }} />
                    <div className="assistant-content">
                      {/* 故障码 LLM 分析状态提示 */}
                      {message.streamHint && (
                        <div className="stream-hint-badge">
                          <span className="stream-hint-icon">⚠</span>
                          <span className="stream-hint-text">{message.streamHint}</span>
                        </div>
                      )}
                      <MarkdownRenderer
                        content={message.content}
                        className="assistant-markdown"
                        isStreaming={streamingMessageId === message.id}
                      />
                      {message.repairKnowledgeSources && message.repairKnowledgeSources.length > 0 && (
                        <div className="assistant-source-row">
                          <button
                            type="button"
                            className="assistant-source-trigger"
                            onClick={() => void openRepairKnowledgeModal(message.repairKnowledgeSources || [])}
                          >
                            参考维修经验
                            <span className="assistant-source-count">{message.repairKnowledgeSources.length}</span>
                          </button>
                        </div>
                      )}
                      {/* 无匹配提示卡片 - no_match 时显示建议（归档后隐藏按钮） */}
                      {(message as any).wizardState?.existenceInfo?.status === 'no_match' && message.lifecycle !== 'archived' && (
                        <div className="no-match-card">
                          <div className="no-match-icon">
                            <SearchSlash size={18} />
                          </div>
                          <div className="no-match-content">
                            {Object.entries((message as any).wizardState.existenceInfo.suggestions || {}).map(([facet, values]: [string, any]) => (
                              <div key={facet} className="no-match-suggestions">
                                <span className="suggestion-label">
                                  {facet === 'brand' ? '可选品牌' : facet === 'series' ? '可选系列' : '可选型号'}：
                                </span>
                                {values.slice(0, 5).map((v: string) => (
                                  <button
                                    key={v}
                                    className="suggestion-chip"
                                    onClick={async () => {
                                      cleanupOngoingState()
                                      addMessage({ type: 'user', content: v })
                                      await sendChatMessage(v)
                                    }}
                                    disabled={isLoading}
                                  >
                                    {v}
                                  </button>
                                ))}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {/* 推荐问题（归档后隐藏） */}
                      {message.suggestions && message.suggestions.length > 0 && streamingMessageId !== message.id && message.lifecycle !== 'archived' && message.id === latestSuggestionMessageId && (
                        <SuggestionChips
                          suggestions={message.suggestions}
                          onSelect={(suggestion) => handleSuggestionClick(suggestion, message.id)}
                          disabled={isLoading}
                        />
                      )}
                    </div>
                  </div>
                )}

                {/* 通用对话反馈卡片 */}
                {message.type === 'assistant_text' && message.requestId && !message.feedbackSubmitted && streamingMessageId !== message.id && (
                  <FeedbackCard
                    requestId={message.requestId}
                    sessionId={sessionId}
                    businessType={message.business || 'GENERAL_CHAT'}
                    onSubmitted={() => markFeedbackSubmitted(message.id)}
                  />
                )}

                {message.type === 'param_request' && message.paramContent && (
                  <ParameterQueryCard
                    content={message.paramContent}
                    onOpenSources={message.parameterQuerySources && message.parameterQuerySources.length > 0
                      ? () => void openParameterQueryModal(message.parameterQuerySources || [])
                      : undefined}
                  />
                )}

                {message.type === 'param_request' && message.requestId && !message.feedbackSubmitted && (
                  <FeedbackCard
                    requestId={message.requestId}
                    sessionId={sessionId}
                    businessType="PARAM_QUERY"
                    onSubmitted={() => markFeedbackSubmitted(message.id)}
                  />
                )}

                {/* 故障诊断卡片 */}
                {message.type === 'assistant_fault' && message.faultContent && (
                  <div className={`fault-card fault-${message.faultContent.state}`}>
                    <div className="fault-header">
                      <div className="fault-icon">
                        <TriangleAlert size={20} />
                      </div>
                      <div className="fault-info">
                        <span className="fault-code">{message.faultContent.faultCode}</span>
                        <span className="fault-ecu">{message.faultContent.ecuModel}</span>
                      </div>
                      <div className={`fault-status status-${message.faultContent.state}`}>
                        {message.faultContent.state === 'ready' && '已就绪'}
                        {message.faultContent.state === 'generating' && (
                          <>
                            <span className="status-spinner" />
                            生成中
                          </>
                        )}
                        {message.faultContent.state === 'failed' && '失败'}
                      </div>
                    </div>
                    <div className="fault-body">
                      {message.faultContent.state === 'generating' ? (
                        <GeneratingProgress
                          faultCode={message.faultContent.faultCode}
                          ecuModel={message.faultContent.ecuModel}
                          isComplete={false}
                        />
                      ) : (
                        <p className="fault-message">{message.content}</p>
                      )}
                      {message.faultContent.state === 'ready' && message.faultContent.reportUrl && (
                        <button
                          className="fault-report-btn"
                          onClick={() => handleViewReport(message.faultContent!.reportUrl!)}
                        >
                          <FileText size={16} />
                          查看诊断报告
                        </button>
                      )}
                      {message.faultContent.state === 'failed' && message.faultContent.error && (
                        <p className="fault-error">{message.faultContent.error.message}</p>
                      )}
                      {/* 推荐问题（归档后隐藏） */}
                      {message.suggestions && message.suggestions.length > 0 && message.faultContent.state === 'ready' && message.lifecycle !== 'archived' && message.id === latestSuggestionMessageId && (
                        <SuggestionChips
                          suggestions={message.suggestions}
                          onSelect={(suggestion) => handleSuggestionClick(suggestion, message.id)}
                          disabled={isLoading}
                        />
                      )}
                    </div>
                  </div>
                )}

                {/* 故障诊断反馈卡片 */}
                {message.type === 'assistant_fault' && message.requestId && !message.feedbackSubmitted && message.faultContent?.state === 'ready' && (
                  <FeedbackCard
                    requestId={message.requestId}
                    sessionId={sessionId}
                    businessType="FAULT_DIAGNOSIS"
                    onSubmitted={() => markFeedbackSubmitted(message.id)}
                  />
                )}

                {/* 意图澄清卡片（completed 状态在外层已过滤，此处只渲染交互态） */}
                {message.type === 'clarify_intent' && message.clarifyOptions && (
                  <div className="intent-clarify-card">
                    <div className="intent-header">
                      <div className="intent-icon">
                        <CircleQuestionMark size={20} />
                      </div>
                      <span className="intent-question">{message.content}</span>
                    </div>
                    <div className="intent-options">
                      {message.clarifyOptions.map((option, idx) => (
                        <button
                          key={option.key}
                          className="intent-option"
                          onClick={() => handleChatClarifyChoice(option.key, message.clarifyFacet)}
                          disabled={isLoading}
                          style={{ animationDelay: `${idx * 0.1}s` }}
                        >
                          <span className="intent-option-icon">
                            {option.key === 'doc_search' && <Search size={18} />}
                            {option.key === 'param_query' && <Cpu size={18} />}
                            {option.key === 'fault_diagnosis' && <TriangleAlert size={18} />}
                            {option.key === 'general_chat' && <MessageSquare size={18} />}
                          </span>
                          <span className="intent-option-content">
                            <span className="intent-option-label">{option.label}</span>
                            {option.description && (
                              <span className="intent-option-desc">{option.description}</span>
                            )}
                          </span>
                          <ChevronRight size={16} className="intent-option-arrow" />
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* 业务澄清卡片 - 旧版兼容 */}
                {message.type === 'clarify_business' && message.clarifyOptions && (
                  <div className="clarify-card">
                    <div className="clarify-header">
                      <div className="clarify-icon">
                        <Info size={18} />
                      </div>
                      <span className="clarify-question">{message.content}</span>
                    </div>
                    <div className="clarify-options">
                      {message.clarifyOptions.map((option, idx) => (
                        <button
                          key={option.key}
                          className="clarify-option"
                          onClick={() => handleChatClarifyChoice(option.key, message.clarifyFacet)}
                          disabled={isLoading}
                          style={{ animationDelay: `${idx * 0.08}s` }}
                        >
                          <span className="option-index">{idx + 1}</span>
                          <span className="option-text">{option.label}</span>
                          <ChevronRight size={16} className="option-arrow" />
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* 折叠式澄清向导（新版） */}
                {message.type === 'clarify_wizard' && message.wizardState && (
                  <ClarifyWizard
                    state={message.wizardState}
                    onSelect={(choice, facet) => handleChatClarifyChoice(choice, facet)}
                    onBack={(roundIndex) => handleWizardBack(message.id, roundIndex)}
                    onQuickAccess={(topResult) => handleQuickAccess(topResult)}
                    onQuickConfirm={(topResult) => handleQuickConfirm(message.id, topResult)}
                    isLoading={isLoading}
                    ecuInputMode={ecuInputMode === message.id}
                    onEcuInputModeChange={(active) => {
                      setEcuInputMode(active ? message.id : null)
                      if (!active) setEcuInputValue('')
                    }}
                    ecuInputValue={ecuInputValue}
                    onEcuInputValueChange={setEcuInputValue}
                  />
                )}

                {message.type === 'repair_followup' && message.repairFollowupState && (
                  <AskUserShell
                    state={buildRepairFollowupAskUserV2State(message.repairFollowupState)}
                    isLoading={isLoading}
                    onSubmit={(submission) => handleRepairFollowupAskUserV2Submit(message.id, submission)}
                  />
                )}

                {message.type === 'ask_user_form' && message.askUserV2State && (
                  <AskUserShell
                    state={message.askUserV2State}
                    isLoading={isLoading}
                    onSubmit={(submission) => handleAskUserV2Submit(message.id, submission)}
                  />
                )}
              </div>
              )
            })}

            {isLoading && !streamingMessageId && (
              <div className={`message message-loading ${loadingPhase === 1 ? 'message-loading--analyzing' : ''}`}>
                <div className="loading-indicator">
                  <div className="loading-dots">
                    <span />
                    <span />
                    <span />
                  </div>
                  <span className="loading-text">
                    {loadingPhase === 0 ? '正在搜索...' : '正在智能分析文档差异...'}
                  </span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        )}
        </div>
      </main>

      {frontendRuntimeConfig?.webview_debug_enabled && !viewerDoc && !showReportViewer && (
        <button
          type="button"
          className="webview-debug-fab"
          style={{
            left: `${webviewDebugFabPosition.x}px`,
            top: `${webviewDebugFabPosition.y}px`,
          }}
          onPointerDown={handleWebviewDebugFabPointerDown}
          onPointerMove={handleWebviewDebugFabPointerMove}
          onPointerUp={handleWebviewDebugFabPointerUp}
          onPointerCancel={handleWebviewDebugFabPointerCancel}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              openWebviewDebugDocument()
            }
          }}
          aria-label="打开 WebView 图内搜索调试"
          title="WebView 图内搜索调试"
        >
          <FileText size={20} />
        </button>
      )}

      {/* Input Area */}
      <footer className="input-container">
        <div className="input-inner">
          <form onSubmit={handleSubmit} className="input-form">
            {pendingImageAttachments.length > 0 && (
              <div className="pending-image-strip">
                {pendingImageAttachments.map((attachment) => (
                  <div key={attachment.id} className="pending-image-chip">
                    <img src={attachment.previewUrl} alt={attachment.name} className="pending-image-thumb" />
                    <button
                      type="button"
                      className="pending-image-remove"
                      onClick={() => removePendingImageAttachment(attachment.id)}
                      disabled={isLoading}
                      title="移除图片"
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="input-wrapper">
	              {/* 图片上传按钮 - 通用图片证据识别 */}
	              <ImageUploadButton
	                onImageSelect={handleImageSelect}
	                onBeforeSelect={checkImageUploadConflict}
	                disabled={isLoading || !imageEvidenceAvailable || pendingImageAttachments.length >= imageEvidenceMaxFiles}
	                disabledReason={!imageEvidenceAvailable ? '图片证据识别服务暂未启用' : undefined}
	                maxFiles={Math.max(1, imageEvidenceMaxFiles - pendingImageAttachments.length)}
	              />

              <input
                ref={inputRef}
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder={isListening ? "正在聆听..." : "输入您的问题或搜索关键词..."}
                className="search-input"
                disabled={isLoading || isListening}
              />
              {/* 语音输入按钮 - 按住说话 */}
              {speechSupported && (
                <button
                  type="button"
                  className={`voice-btn ${isListening ? 'listening' : ''}`}
                  onMouseDown={handleVoiceStart}
                  onMouseUp={handleVoiceEnd}
                  onMouseLeave={handleVoiceEnd}
                  onTouchStart={handleVoiceStart}
                  onTouchEnd={handleVoiceEnd}
                  disabled={isLoading}
                  title="按住说话"
                >
                  <Mic size={18} />
                  {isListening && <span className="voice-pulse" />}
                </button>
              )}
              {isStreamingChat ? (
                <button
                  type="button"
                  className="submit-btn stop-btn"
                  onClick={stopGenerating}
                  title="停止生成"
                >
                  <SquareStop size={18} />
                </button>
              ) : (
                <button
                  type="submit"
                  className="submit-btn"
                  disabled={(!inputValue.trim() && pendingImageAttachments.length === 0) || isLoading}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
                  </svg>
                </button>
              )}
            </div>
          </form>
        </div>
      </footer>

      {/* 文档查看器 */}
      {viewerDoc && (
        <DocumentViewer
          key={viewerDoc.token}
          title={viewerDoc.title}
          picFolderUrl={viewerDoc.picFolderUrl}
          urlType={viewerDoc.urlType}
          initialPage={viewerDoc.initialPage}
          circuitSearch={viewerDoc.circuitSearch}
          closeToken={viewerDoc.token}
          onClose={(token) => {
            if (!viewerDoc || (token && token !== viewerDoc.token)) return
            setViewerDoc(null)
          }}
        />
      )}

      {/* 报告查看器 */}
      {showReportViewer && currentReportUrl && currentReportToken && (
        <ReportViewer
          key={currentReportToken}
          reportUrl={currentReportUrl}
          closeToken={currentReportToken}
          onClose={handleCloseReportViewer}
        />
      )}

      {/* 上下文切换确认对话框 */}
      <SwitchConfirmDialog
        isOpen={switchConfirmState.isOpen}
        currentBusiness={currentBusiness || 'IDLE'}
        contextInfo={switchConfirmState.contextInfo}
        onConfirm={handleConfirmSwitch}
        onCancel={handleCancelSwitch}
      />

      {/* 新搜索确认弹窗 */}
      {showNewSearchConfirm && (
        <>
          <div
            className="switch-dialog-overlay"
            onClick={() => setShowNewSearchConfirm(false)}
          />
          <div className="switch-dialog">
            <div className="switch-dialog-icon">
              <Plus size={24} />
            </div>
            <h3 className="switch-dialog-title">开始新对话</h3>
            <p className="switch-dialog-message">
              当前对话记录将被清除，确定要开始新对话吗？
            </p>
            <div className="switch-dialog-actions">
              <button
                className="switch-dialog-btn switch-dialog-btn-cancel"
                onClick={() => setShowNewSearchConfirm(false)}
              >
                取消
              </button>
              <button
                className="switch-dialog-btn switch-dialog-btn-confirm"
                onClick={confirmNewSearch}
              >
                确定
              </button>
            </div>
          </div>
        </>
      )}

      {repairKnowledgeModal && (
        <>
          <div
            className="switch-dialog-overlay"
            onClick={() => setRepairKnowledgeModal(null)}
          />
          <div className="switch-dialog repair-knowledge-dialog">
            <div className="repair-knowledge-dialog-header">
              <h3 className="repair-knowledge-dialog-title">参考维修经验</h3>
              <button
                type="button"
                className="notification-close"
                onClick={() => setRepairKnowledgeModal(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="repair-knowledge-source-list">
              {repairKnowledgeModal.sources.map((source) => (
                <button
                  key={source.id}
                  type="button"
                  className={`repair-knowledge-source-item ${repairKnowledgeModal.activeSourceId === source.id ? 'active' : ''}`}
                  onClick={() => void loadRepairKnowledgeDetail(source)}
                  disabled={repairKnowledgeModal.loading && repairKnowledgeModal.activeSourceId === source.id}
                >
                  <span className="repair-knowledge-source-item-title">{source.title}</span>
                  <span className="repair-knowledge-source-item-meta">
                    {source.relation === 'primary' ? '主要命中' : '相关经验'}
                  </span>
                </button>
              ))}
            </div>
            <div className="repair-knowledge-source-content">
              {repairKnowledgeModal.loading && !repairKnowledgeModal.activeDetail ? (
                <div className="loading-indicator">
                  <div className="loading-dots">
                    <span />
                    <span />
                    <span />
                  </div>
                  <span className="loading-text">正在加载维修经验...</span>
                </div>
              ) : repairKnowledgeModal.activeDetail ? (
                <>
                  <div className="repair-knowledge-source-content-header">
                    <div>
                      <div className="repair-knowledge-source-content-title">{repairKnowledgeModal.activeDetail.title}</div>
                      {repairKnowledgeModal.activeDetail.topic && (
                        <div className="repair-knowledge-source-content-topic">{repairKnowledgeModal.activeDetail.topic}</div>
                      )}
                    </div>
                  </div>
                  <MarkdownRenderer
                    content={repairKnowledgeModal.activeDetail.content}
                    className="assistant-markdown repair-knowledge-markdown"
                  />
                </>
              ) : (
                <div className="repair-knowledge-empty">未找到对应的维修经验内容。</div>
              )}
            </div>
          </div>
        </>
      )}

      {parameterQueryModal && (
        <>
          <div
            className="switch-dialog-overlay"
            onClick={() => setParameterQueryModal(null)}
          />
          <div className="switch-dialog repair-knowledge-dialog">
            <div className="repair-knowledge-dialog-header">
              <h3 className="repair-knowledge-dialog-title">参考参数资料</h3>
              <button
                type="button"
                className="notification-close"
                onClick={() => setParameterQueryModal(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="repair-knowledge-source-list">
              {parameterQueryModal.sources.map((source) => (
                <button
                  key={source.id}
                  type="button"
                  className={`repair-knowledge-source-item ${parameterQueryModal.activeSourceId === source.id ? 'active' : ''}`}
                  onClick={() => void loadParameterQueryDetail(source)}
                  disabled={parameterQueryModal.loading && parameterQueryModal.activeSourceId === source.id}
                >
                  <span className="repair-knowledge-source-item-title">{source.title}</span>
                  <span className="repair-knowledge-source-item-meta">
                    {source.relation === 'primary' ? '主要命中' : '相关资料'}
                  </span>
                </button>
              ))}
            </div>
            <div className="repair-knowledge-source-content">
              {parameterQueryModal.loading && !parameterQueryModal.activeDetail ? (
                <div className="loading-indicator">
                  <div className="loading-dots">
                    <span />
                    <span />
                    <span />
                  </div>
                  <span className="loading-text">正在加载参数资料...</span>
                </div>
              ) : parameterQueryModal.activeDetail ? (
                <>
                  <div className="repair-knowledge-source-content-header">
                    <div>
                      <div className="repair-knowledge-source-content-title">{parameterQueryModal.activeDetail.title}</div>
                      {(parameterQueryModal.activeDetail.ecu_name || parameterQueryModal.activeDetail.system_voltage) && (
                        <div className="repair-knowledge-source-content-topic">
                          {parameterQueryModal.activeDetail.ecu_name || ''}
                          {parameterQueryModal.activeDetail.system_voltage ? ` · ${parameterQueryModal.activeDetail.system_voltage}V` : ''}
                        </div>
                      )}
                    </div>
                  </div>
                  <MarkdownRenderer
                    content={parameterQueryModal.activeDetail.content}
                    className="assistant-markdown repair-knowledge-markdown"
                  />
                </>
              ) : (
                <div className="repair-knowledge-empty">未找到对应的参数资料内容。</div>
              )}
            </div>
          </div>
        </>
      )}

      {/* Token 诊断弹窗 */}
      {tokenDiagnoseResult && (
        <>
          <div className="switch-dialog-overlay" onClick={() => setTokenDiagnoseResult(null)} />
          <div className="switch-dialog" style={{ maxWidth: '90vw', maxHeight: '80vh', overflow: 'auto' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: '15px' }}>Token 来源诊断</h3>
            <div style={{ fontSize: '12px', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', textAlign: 'left' }}>
              {Object.entries(tokenDiagnoseResult).map(([key, value]) => (
                <div key={key} style={{ marginBottom: '10px' }}>
                  <div style={{ fontWeight: 'bold', color: '#1a73e8', marginBottom: '2px' }}>{key}</div>
                  <div style={{ paddingLeft: '8px', color: typeof value === 'string' && value.startsWith('(') ? '#999' : '#333' }}>
                    {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ textAlign: 'right', marginTop: '12px' }}>
              <button className="switch-dialog-btn switch-dialog-btn-confirm" onClick={() => setTokenDiagnoseResult(null)}>
                关闭
              </button>
            </div>
          </div>
        </>
      )}

      {imagePreviewModal && (
        <div
          className="image-preview-overlay"
          onClick={() => setImagePreviewModal(null)}
        >
          <div
            className="image-preview-dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              className="image-preview-close"
              onClick={() => setImagePreviewModal(null)}
              title="关闭预览"
            >
              <X size={18} />
            </button>
            <img
              src={imagePreviewModal.src}
              alt={imagePreviewModal.alt}
              className="image-preview-full"
            />
            <div className="image-preview-caption">{imagePreviewModal.alt}</div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
