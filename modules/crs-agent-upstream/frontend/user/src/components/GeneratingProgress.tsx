/**
 * 诊断报告生成进度组件
 *
 * 显示模拟的生成进度日志，增强用户等待体验
 * 支持随机时间间隔和动态子状态文案
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { Check } from 'lucide-react'

// 步骤配置
interface ProgressStep {
  id: string
  label: string
  subLabels?: string[]
  minTime: number
  maxTime: number
  subInterval?: number
}

// 组件 Props
interface GeneratingProgressProps {
  faultCode: string
  ecuModel: string
  isComplete?: boolean
  onSimulationEnd?: () => void
}

// 步骤配置
const PROGRESS_STEPS: ProgressStep[] = [
  {
    id: 'parse',
    label: '解析故障码',
    minTime: 1000,
    maxTime: 2400
  },
  {
    id: 'match',
    label: '匹配ECU型号',
    minTime: 1600,
    maxTime: 3600
  },
  {
    id: 'search',
    label: '检索知识库',
    subLabels: ['已匹配 {n} 条记录'],
    minTime: 4000,
    maxTime: 7000,
    subInterval: 1600
  },
  {
    id: 'diagnose',
    label: '大模型诊断中',
    subLabels: ['分析故障原因', '生成维修建议', '评估风险等级'],
    minTime: 8000,
    maxTime: 14000,
    subInterval: 2500
  },
  {
    id: 'output',
    label: '报告整理输出中',
    minTime: Infinity,
    maxTime: Infinity
  }
]

// 生成随机时间
function randomTime(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

// 生成随机数量
function randomCount(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

export default function GeneratingProgress({
  faultCode,
  ecuModel,
  isComplete = false,
  onSimulationEnd
}: GeneratingProgressProps) {
  // 当前步骤索引
  const [currentStep, setCurrentStep] = useState(0)
  // 当前子状态索引
  const [subIndex, setSubIndex] = useState(0)
  // 知识库匹配数量（随机）
  const [matchCount, setMatchCount] = useState(0)
  // 是否模拟结束（到达最后一步）
  const [simulationEnded, setSimulationEnded] = useState(false)

  // 定时器引用
  const stepTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const subTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 清理定时器
  const clearTimers = useCallback(() => {
    if (stepTimerRef.current) {
      clearTimeout(stepTimerRef.current)
      stepTimerRef.current = null
    }
    if (subTimerRef.current) {
      clearInterval(subTimerRef.current)
      subTimerRef.current = null
    }
  }, [])

  // 处理步骤进度
  useEffect(() => {
    // 如果已完成，直接跳到结束
    if (isComplete) {
      setCurrentStep(PROGRESS_STEPS.length)
      clearTimers()
      return
    }

    // 如果已经到达最后一步或超出，不再处理
    if (currentStep >= PROGRESS_STEPS.length - 1) {
      if (!simulationEnded) {
        setSimulationEnded(true)
        onSimulationEnd?.()
      }
      return
    }

    const step = PROGRESS_STEPS[currentStep]
    const duration = randomTime(step.minTime, step.maxTime)

    // 设置步骤定时器
    stepTimerRef.current = setTimeout(() => {
      setCurrentStep(prev => prev + 1)
      setSubIndex(0)
    }, duration)

    // 如果有子状态，设置子状态定时器
    if (step.subLabels && step.subInterval) {
      // 初始化随机值
      if (step.id === 'search') {
        setMatchCount(randomCount(2, 8))
      }

      subTimerRef.current = setInterval(() => {
        setSubIndex(prev => {
          const maxSub = step.subLabels!.length - 1
          if (prev < maxSub) {
            // 更新随机值
            if (step.id === 'search') {
              setMatchCount(c => Math.min(c + randomCount(1, 3), 12))
            }
            return prev + 1
          }
          return prev
        })
      }, step.subInterval)
    }

    return clearTimers
  }, [currentStep, isComplete, simulationEnded, onSimulationEnd, clearTimers])

  // 渲染步骤状态图标
  const renderStepIcon = (stepIndex: number) => {
    if (isComplete || stepIndex < currentStep) {
      // 已完成
      return (
        <span className="step-icon step-done">
          <Check size={16} strokeWidth={3} />
        </span>
      )
    } else if (stepIndex === currentStep) {
      // 进行中
      return <span className="step-icon step-active"><span className="step-spinner" /></span>
    } else {
      // 待执行
      return <span className="step-icon step-pending" />
    }
  }

  // 渲染步骤文本
  const renderStepText = (step: ProgressStep, stepIndex: number) => {
    const isActive = stepIndex === currentStep && !isComplete
    const isDone = isComplete || stepIndex < currentStep

    let label = step.label

    // 动态子状态文案
    if (isActive && step.subLabels && step.subLabels[subIndex]) {
      let subLabel = step.subLabels[subIndex]
      // 替换 {n} 占位符
      if (subLabel.includes('{n}')) {
        subLabel = subLabel.replace('{n}', String(matchCount))
      }
      label = `${step.label}... ${subLabel}`
    } else if (isActive) {
      label = `${step.label}...`
    } else if (isDone && step.id === 'search') {
      label = `${step.label} (共 ${matchCount || randomCount(3, 8)} 条)`
    }

    return (
      <span className={`step-text ${isDone ? 'done' : ''} ${isActive ? 'active' : ''}`}>
        {label}
      </span>
    )
  }

  return (
    <div className="generating-progress">
      <div className="progress-header">
        <div className="progress-indicator">
          <span className="pulse-dot" />
        </div>
        <div className="progress-title">
          <span className="title-code">{faultCode}</span>
          <span className="title-ecu">{ecuModel}</span>
        </div>
      </div>

      <div className="progress-steps">
        {PROGRESS_STEPS.map((step, index) => (
          <div
            key={step.id}
            className={`progress-step ${
              isComplete || index < currentStep ? 'completed' : ''
            } ${
              index === currentStep && !isComplete ? 'current' : ''
            } ${
              index > currentStep && !isComplete ? 'pending' : ''
            }`}
          >
            {renderStepIcon(index)}
            {renderStepText(step, index)}
          </div>
        ))}
      </div>

      <div className="progress-footer">
        <span className="footer-hint">
          {isComplete ? '报告生成完成' : '正在生成诊断报告，请稍候...'}
        </span>
      </div>
    </div>
  )
}
