// Render-crash containment (post-Schedule-crash fix): a component throwing on bad data must
// surface as a legible error panel, never a dead blank console. Keyed remounting (resetKey)
// clears the boundary when the user navigates to different content.

import { Component, type ReactNode } from "react";

interface Props { children: ReactNode; resetKey?: string }
interface State { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="boundary-fault bezel">
          <span className="section-label">RENDER FAULT — CONTAINED</span>
          <p className="surface-error">{this.state.error.message}</p>
          <p className="empty">
            The rest of the console is unaffected. Usually bad/unexpected viz data — fix the
            data file (or report the shape) and reselect.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
