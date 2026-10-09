import { Backups } from '../components/Backups'
import { PhotoLibrarySection } from '../components/Photos'
import { Empty } from '../components/ui'
import { useApp } from '../state'

export function Archives() {
  const { snapshot } = useApp()
  const b = snapshot!.backups
  const photos = snapshot!.photos
  return (
    <div className="deck-stack">
      {photos && <PhotoLibrarySection photos={photos} />}
      {b ? <Backups backups={b} /> : !photos && <Empty>No backups are configured.</Empty>}
    </div>
  )
}
