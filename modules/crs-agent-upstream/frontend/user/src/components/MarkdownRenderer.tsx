/**
 * Markdown 渲染组件
 *
 * 用于渲染助手回复中的 Markdown 格式文本
 * 支持流式输出时的实时渲染和光标显示
 */

import { useRef, useLayoutEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

interface MarkdownRendererProps {
  content: string
  className?: string
  isStreaming?: boolean  // 是否正在流式输出
}

function normalizeMarkdownContent(rawContent: string): string {
  if (!rawContent) return ''

  let normalized = rawContent
    .replace(/\r\n?/g, '\n')
    .replace(/\u2028|\u2029/g, '\n')
    .replace(/＃/g, '#')

  if (!normalized.includes('\n') && normalized.includes('\\n')) {
    normalized = normalized.replace(/\\n/g, '\n')
  }

  normalized = normalized
    .replace(/\\t/g, '  ')
    .replace(/(^|\n)[ \t]*\\(#{1,6}\s+)/g, '$1$2')
    .replace(/^[ \t]{1,3}(#{1,6}\s+)/gm, '$1')
    .replace(/([：:。；;!！?？])\s*(#{1,6}\s+)/g, '$1\n\n$2')
    .replace(/([^\n])([ \t]+#{1,6}\s+)/g, '$1\n\n$2')
    .replace(/([^\n])\n(#{1,6}\s+)/g, '$1\n\n$2')

  return normalized
}

export function MarkdownRenderer({ content, className, isStreaming = false }: MarkdownRendererProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const normalizedContent = normalizeMarkdownContent(content)

  // 流式输出时，将光标插入到最后一个文本节点后面
  useLayoutEffect(() => {
    if (!isStreaming || !containerRef.current) return

    try {
      // 移除之前的光标
      const existingCursor = containerRef.current.querySelector('.streaming-cursor')
      if (existingCursor) {
        existingCursor.remove()
      }

      // 创建光标元素
      const cursor = document.createElement('span')
      cursor.className = 'streaming-cursor'

      // 找到最后一个文本节点并在其后插入光标
      const walker = document.createTreeWalker(
        containerRef.current,
        NodeFilter.SHOW_TEXT,
        null
      )

      let lastTextNode: Text | null = null
      let currentNode: Node | null = walker.nextNode()
      while (currentNode) {
        if (currentNode.textContent && currentNode.textContent.trim()) {
          lastTextNode = currentNode as Text
        }
        currentNode = walker.nextNode()
      }

      if (lastTextNode && lastTextNode.parentNode) {
        // 在最后一个文本节点后插入光标
        const parent = lastTextNode.parentNode
        if (lastTextNode.nextSibling) {
          parent.insertBefore(cursor, lastTextNode.nextSibling)
        } else {
          parent.appendChild(cursor)
        }
      } else {
        // 如果没有文本节点，直接添加到容器
        containerRef.current.appendChild(cursor)
      }
    } catch (e) {
      console.error('Error inserting cursor:', e)
    }
  }, [normalizedContent, isStreaming])

  // 处理空内容
  if (!normalizedContent) {
    return (
      <div className={`markdown-content ${isStreaming ? 'markdown-streaming' : ''} ${className || ''}`}>
        {isStreaming && <span className="streaming-cursor" />}
      </div>
    )
  }

  const components: Components = {
    // 代码块渲染
    code({ className: codeClassName, children, ...props }) {
      const isInline = !codeClassName
      return isInline ? (
        <code className="inline-code" {...props}>
          {children}
        </code>
      ) : (
        <code className={codeClassName} {...props}>
          {children}
        </code>
      )
    },
    // 链接在新标签打开
    a({ href, children }) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      )
    },
    // 段落
    p({ children }) {
      return <p className="md-paragraph">{children}</p>
    },
    // 列表
    ul({ children }) {
      return <ul className="md-list">{children}</ul>
    },
    ol({ children }) {
      return <ol className="md-list md-list-ordered">{children}</ol>
    },
    // 引用
    blockquote({ children }) {
      return <blockquote className="md-blockquote">{children}</blockquote>
    },
    // 表格
    table({ children }) {
      return (
        <div className="md-table-wrap">
          <table className="md-table">{children}</table>
        </div>
      )
    },
    // 标题
    h1({ children }) {
      return <h1 className="md-heading md-h1">{children}</h1>
    },
    h2({ children }) {
      return <h2 className="md-heading md-h2">{children}</h2>
    },
    h3({ children }) {
      return <h3 className="md-heading md-h3">{children}</h3>
    },
    // 分隔线
    hr() {
      return <hr className="md-hr" />
    }
  }

  return (
    <div
      ref={containerRef}
      className={`markdown-content ${isStreaming ? 'markdown-streaming' : ''} ${className || ''}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
      >
        {normalizedContent}
      </ReactMarkdown>
    </div>
  )
}

export default MarkdownRenderer
