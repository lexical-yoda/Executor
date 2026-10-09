import { useEffect } from 'react'
import { api } from './api'
import { Actions } from './components/Actions'
import { Header } from './components/Header'
import { Machines } from './components/Machines'
import { Services } from './components/Services'
import { useNow, usePoll } from './hooks'

export default function App() {
  const { data: snapshot, error, updatedAt } = usePoll(api.status, 5000)
  const now = useNow(1000)
  const title = snapshot?.site.title

  useEffect(() => {
    if (title) document.title = title
  }, [title])

  return (
    <div className="app">
      <div className="starfield" aria-hidden="true" />
      <Header snapshot={snapshot} error={error} updatedAt={updatedAt} now={now} />
      <main className="layout">
        {snapshot ? (
          <>
            <Machines machines={snapshot.machines} now={now} />
            <Actions runnerOk={snapshot.runner.ok} now={now} />
            {!snapshot.runner.ok && (
              <p className="notice">
                Container states unavailable: {snapshot.runner.error}. Service checks still run.
              </p>
            )}
            <Services services={snapshot.services} groups={snapshot.groups} />
          </>
        ) : (
          <div className="loading">
            <span className="loader" />
            <p className="muted">{error ? `Cannot load status: ${error}` : 'Establishing link…'}</p>
          </div>
        )}
      </main>
      <footer className="footer muted small">Executor · WireGuard only</footer>
    </div>
  )
}
