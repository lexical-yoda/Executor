import { Component, type ReactNode } from 'react'

/** Keeps one broken panel from blanking the whole page. */
export class Guard extends Component<{ children: ReactNode; label?: string }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="notice guard-notice">
          {this.props.label ?? 'This part of the page'} hit a problem and stopped: {this.state.error.message}.{' '}
          <button type="button" className="link-btn" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
