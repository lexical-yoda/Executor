import { Services } from '../components/Services'
import { useApp } from '../state'

/** Every service: the launch pad, with search and an issues-only filter. */
export function Hangar() {
  const { snapshot } = useApp()
  const s = snapshot!
  return (
    <div className="deck-stack">
      {!s.runner.ok && (
        <p className="notice">Container states unavailable: {s.runner.error}. Service checks still run.</p>
      )}
      <Services services={s.services} groups={s.groups} />
    </div>
  )
}
