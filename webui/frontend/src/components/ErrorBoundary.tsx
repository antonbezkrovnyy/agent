import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertCircle } from 'lucide-react'

export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Interface rendering error', error, info)
  }

  render() {
    if (this.state.error) return <div className="card m-6 p-6 text-sm text-rose-200">
      <div className="flex items-center gap-2 font-medium"><AlertCircle size={18} />Ошибка отображения</div>
      <p className="mt-3 font-mono text-xs">{this.state.error.message}</p>
      <button className="btn mt-4" onClick={() => this.setState({ error: null })}>Попробовать снова</button>
    </div>
    return this.props.children
  }
}
