/*
  Route-level error boundary. A render crash in one page must not blank the
  whole console — the shell stays, the page shows an honest error panel.
*/

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Page title for the fallback panel. */
  page: string
}

interface State {
  error: Error | null
}

export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The browser console keeps the stack; we do not report anywhere else.
    console.error(`[mission-control] render error on ${this.props.page}:`, error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mc-notice mc-notice-danger" role="alert">
          <div className="min-w-0 flex-1">
            <div className="text-xs font-medium">{this.props.page} failed to render</div>
            <div className="mt-0.5 text-xs opacity-90">
              {this.state.error.message || 'Unexpected client error.'}
            </div>
          </div>
          <button
            type="button"
            className="mc-btn ml-2 flex-none"
            onClick={() => this.setState({ error: null })}
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
