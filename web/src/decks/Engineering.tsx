import { DnsCard } from '../components/Dns'
import { Edge } from '../components/Edge'
import { Machines } from '../components/Machines'
import { Services } from '../components/Services'
import { Storage } from '../components/Storage'
import { useApp } from '../state'

export function Engineering() {
  const { snapshot } = useApp()
  const s = snapshot!
  return (
    <div className="deck-stack">
      <Machines machines={s.machines} />
      {s.truenas && <Storage health={s.truenas} />}
      {s.pihole && <DnsCard dns={s.pihole} />}
      {s.edge && <Edge edge={s.edge} />}
      {!s.runner.ok && (
        <p className="notice">Container states unavailable: {s.runner.error}. Service checks still run.</p>
      )}
      <Services services={s.services} groups={s.groups} />
    </div>
  )
}
