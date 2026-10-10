import { DnsCard } from '../components/Dns'
import { Edge } from '../components/Edge'
import { JumpBar } from '../components/JumpBar'
import { Machines } from '../components/Machines'
import { Storage } from '../components/Storage'
import { useApp } from '../state'

export function Engineering() {
  const { snapshot } = useApp()
  const s = snapshot!
  const sections = [
    { id: 'machines', label: 'Machines' },
    ...(s.truenas ? [{ id: 'storage', label: 'Storage' }] : []),
    ...(s.pihole ? [{ id: 'dns', label: 'DNS' }] : []),
    ...(s.edge ? [{ id: 'edge', label: 'Edge' }] : []),
  ]
  return (
    <div className="deck-stack">
      {sections.length > 2 && <JumpBar sections={sections} />}
      <Machines machines={s.machines} />
      {s.truenas && <Storage health={s.truenas} />}
      {s.pihole && <DnsCard dns={s.pihole} />}
      {s.edge && <Edge edge={s.edge} />}
    </div>
  )
}
