import { Backups } from '../components/Backups'
import { Empty } from '../components/ui'
import { useApp } from '../state'

export function Archives() {
  const { snapshot } = useApp()
  const b = snapshot!.backups
  return <div className="deck-stack">{b ? <Backups backups={b} /> : <Empty>No backups are configured.</Empty>}</div>
}
