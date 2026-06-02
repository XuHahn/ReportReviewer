import { Component, type ReactNode } from 'react'
import { logger } from '../logger'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    logger.error('Component crashed', { componentStack: info.componentStack }, error)
  }

  render() {
    if (this.state.hasError) {
      const msg = this.state.error?.message || '未知错误'
      const stack = this.state.error?.stack
      return (
        <div style={{ padding: 40, textAlign: 'center' }}>
          <h2 style={{ color: 'var(--color-error)', marginBottom: 12 }}>出了点问题</h2>
          <p style={{ color: 'var(--color-gray-500)', marginBottom: 16 }}>{msg}</p>
          <button
            className="filter-btn"
            onClick={() => this.setState({ hasError: false, error: null })}
          >
            重试
          </button>
          {import.meta.env.DEV && stack && (
            <details style={{ marginTop: 20, textAlign: 'left' }}>
              <summary style={{ cursor: 'pointer', color: 'var(--color-gray-500)' }}>错误详情</summary>
              <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', marginTop: 8, color: 'var(--color-gray-600)' }}>
                {stack}
              </pre>
            </details>
          )}
        </div>
      )
    }

    return this.props.children
  }
}
