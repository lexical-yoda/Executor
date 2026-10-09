import type { Snapshot } from './api'
import { poolTone, tempTone } from './components/Storage'
import type { Deck, DrawerRef } from './route'

export type AlertLevel = 'bad' | 'warn'

export interface Alert {
  key: string
  level: AlertLevel
  deck: Deck
  title: string
  detail?: string | null
  ref: DrawerRef | null
}

// Machines that come and go all day (the access devices) never raise an alert.
const QUIET_ICONS = new Set(['laptop', 'phone'])

/** Everything on the page that needs attention, worst first. */
export function collectAlerts(s: Snapshot | null): Alert[] {
  if (!s) return []
  const out: Alert[] = []
  const add = (a: Alert) => out.push(a)

  if (!s.runner.ok) {
    add({
      key: 'runner',
      level: 'bad',
      deck: 'armory',
      title: 'Runner unreachable',
      detail: `Container states and actions are unavailable: ${s.runner.error}`,
      ref: null,
    })
  }
  for (const svc of s.services) {
    if (svc.status === 'down' || svc.status === 'degraded') {
      add({
        key: `service:${svc.id}`,
        level: svc.status === 'down' ? 'bad' : 'warn',
        deck: 'engineering',
        title: `${svc.name} ${svc.status === 'down' ? 'is down' : 'is degraded'}`,
        detail: svc.error,
        ref: { kind: 'service', id: svc.id },
      })
    }
  }
  for (const m of s.machines) {
    if (m.status === 'down' && !QUIET_ICONS.has(m.icon)) {
      add({
        key: `machine:${m.id}`,
        level: 'bad',
        deck: 'engineering',
        title: `${m.name} is offline`,
        detail: m.error,
        ref: { kind: 'machine', id: m.id },
      })
    }
  }
  if (s.beszel.configured && !s.beszel.ok) {
    add({
      key: 'beszel',
      level: 'warn',
      deck: 'engineering',
      title: 'Machine stats unavailable',
      detail: s.beszel.error,
      ref: null,
    })
  }
  for (const c of s.edge?.certificates ?? []) {
    if (c.error) {
      add({
        key: `cert:${c.host}`,
        level: 'warn',
        deck: 'engineering',
        title: `Certificate check failed for ${c.host}`,
        detail: c.error,
        ref: { kind: 'certificate', id: c.host },
      })
    } else if (c.days_left !== null && c.days_left < 14) {
      add({
        key: `cert:${c.host}`,
        level: c.days_left < 5 ? 'bad' : 'warn',
        deck: 'engineering',
        title: `Certificate for ${c.host} expires in ${Math.floor(c.days_left)} days`,
        ref: { kind: 'certificate', id: c.host },
      })
    }
  }
  const bw = s.edge?.bandwidth
  if (bw?.projected_pct != null && bw.projected_pct > 100) {
    add({
      key: 'bandwidth',
      level: 'warn',
      deck: 'engineering',
      title: `VPS bandwidth heading for ${Math.round(bw.projected_pct)}% of the allowance`,
      ref: { kind: 'bandwidth', id: '' },
    })
  }
  const b = s.backups
  if (b) {
    if (b.duplicati.configured && !b.duplicati.ok) {
      add({
        key: 'duplicati',
        level: 'warn',
        deck: 'archives',
        title: 'Duplicati unavailable',
        detail: b.duplicati.error,
        ref: null,
      })
    }
    for (const job of b.duplicati.jobs) {
      if (job.status === 'failed' || job.status === 'stale' || job.status === 'warning') {
        add({
          key: `backup:${job.id}`,
          level: job.status === 'failed' ? 'bad' : 'warn',
          deck: 'archives',
          title: `Backup '${job.name}' ${job.status === 'failed' ? 'failed' : job.status === 'stale' ? 'is overdue' : 'had warnings'}`,
          detail: job.last_error,
          ref: { kind: 'backup', id: job.id },
        })
      }
    }
    for (const f of b.files) {
      if (f.status === 'failed' || f.status === 'stale' || f.status === 'missing') {
        add({
          key: `files:${f.name}`,
          level: 'bad',
          deck: 'archives',
          title: `'${f.name}' ${f.status === 'failed' ? 'failed' : f.status === 'stale' ? 'is overdue' : 'is missing files'}`,
          detail: f.error,
          ref: { kind: 'files', id: f.name },
        })
      }
    }
    for (const st of b.storage) {
      if (st.error) {
        add({
          key: `storage:${st.name}`,
          level: 'warn',
          deck: 'archives',
          title: `AWS figures unavailable for ${st.name}`,
          detail: st.error,
          ref: { kind: 'storage', id: st.name },
        })
      }
    }
  }
  const m = s.media
  if (m) {
    if (m.requests.configured && !m.requests.ok) {
      add({
        key: 'jellyseerr',
        level: 'warn',
        deck: 'holonet',
        title: 'Requests unavailable',
        detail: m.requests.error,
        ref: null,
      })
    }
    for (const [source, err] of Object.entries(m.downloads.errors)) {
      add({ key: `queue:${source}`, level: 'warn', deck: 'holonet', title: `${source} queue unavailable`, detail: err, ref: null })
    }
    const t = m.downloads.torrents
    if (t.configured && !t.ok) {
      add({ key: 'qbit', level: 'warn', deck: 'holonet', title: 'qBittorrent unavailable', detail: t.error, ref: null })
    } else if (t.errored) {
      add({
        key: 'qbit-errored',
        level: 'warn',
        deck: 'holonet',
        title: `${t.errored} ${t.errored === 1 ? 'torrent has' : 'torrents have'} errors`,
        ref: null,
      })
    }
  }
  const nas = s.truenas
  if (nas && (!nas.configured || nas.error)) {
    add({
      key: 'truenas',
      level: 'warn',
      deck: 'engineering',
      title: 'Storage health unavailable',
      detail: nas.configured ? nas.error : 'TRUENAS_API_KEY is not set',
      ref: { kind: 'nas', id: '' },
    })
  }
  for (const pool of nas?.pools ?? []) {
    const tone = poolTone(pool)
    if (tone !== 'up') {
      const problem =
        pool.status !== 'ONLINE' || !pool.healthy
          ? `is ${pool.status.toLowerCase()}`
          : pool.scan?.errors
            ? `scrub found ${pool.scan.errors} errors`
            : 'reports disk errors'
      add({
        key: `pool:${pool.name}`,
        // Red when the pool is not online or a scrub found errors; amber for disk error counts.
        level: pool.status !== 'ONLINE' || !pool.healthy || pool.scan?.errors ? 'bad' : 'warn',
        deck: 'engineering',
        title: `Pool ${pool.name} ${problem}`,
        detail: pool.detail,
        ref: { kind: 'pool', id: pool.name },
      })
    }
  }
  for (const disk of nas?.disks ?? []) {
    const tone = tempTone(disk, disk.temp)
    if (tone === 'degraded' || tone === 'down') {
      add({
        key: `disk:${disk.name}`,
        level: tone === 'down' ? 'bad' : 'warn',
        deck: 'engineering',
        title: `Disk ${disk.name} is running hot (${Math.round(disk.temp!)} °C)`,
        detail: disk.model,
        ref: { kind: 'disk', id: disk.name },
      })
    }
  }
  for (const a of nas?.alerts ?? []) {
    if (a.level === 'INFO' || a.level === 'NOTICE') continue
    add({
      key: `nas:${a.id}`,
      level: a.level === 'WARNING' ? 'warn' : 'bad',
      deck: 'engineering',
      title: `TrueNAS: ${a.klass}`,
      detail: a.text,
      ref: { kind: 'nas', id: '' },
    })
  }
  const tr = s.edge?.traffic
  if (tr?.configured && tr.error) {
    add({
      key: 'traffic',
      level: 'warn',
      deck: 'engineering',
      title: 'Edge traffic figures unavailable',
      detail: tr.error,
      ref: null,
    })
  }
  for (const site of tr?.sites ?? []) {
    const r = site.recent
    if (r && r.requests >= 20 && r.s5 / r.requests > 0.05) {
      add({
        key: `site:${site.site}`,
        level: 'bad',
        deck: 'engineering',
        title: `${site.site} is failing: ${Math.round((r.s5 / r.requests) * 100)}% server errors`,
        detail: `${r.s5} of ${r.requests} requests in the last 15 minutes`,
        ref: { kind: 'site', id: site.site },
      })
    }
  }
  const dns = s.pihole
  if (dns && (!dns.configured || dns.error)) {
    add({
      key: 'pihole',
      level: 'warn',
      deck: 'engineering',
      title: 'Pi-hole figures unavailable',
      detail: dns.configured ? dns.error : 'PIHOLE_PASSWORD is not set',
      ref: { kind: 'dns', id: '' },
    })
  } else if (dns?.ok && dns.blocking && dns.blocking !== 'enabled') {
    add({
      key: 'pihole-blocking',
      level: 'warn',
      deck: 'engineering',
      title: 'Pi-hole is not blocking ads',
      detail: dns.blocking_timer ? `Back on by itself in ${Math.round(dns.blocking_timer / 60)} min` : null,
      ref: { kind: 'dns', id: '' },
    })
  }
  const p = s.photos
  if (p && (!p.configured || p.error)) {
    add({
      key: 'immich',
      level: 'warn',
      deck: 'archives',
      title: 'Photo library figures unavailable',
      detail: p.configured ? p.error : 'IMMICH_API_KEY is not set',
      ref: { kind: 'photos', id: '' },
    })
  }
  if (s.jellyfin && !s.jellyfin.ok) {
    add({
      key: 'jellyfin',
      level: 'warn',
      deck: 'holonet',
      title: 'Jellyfin sessions unavailable',
      detail: s.jellyfin.error,
      ref: null,
    })
  }
  return out.sort((a, b) => (a.level === b.level ? 0 : a.level === 'bad' ? -1 : 1))
}

/** The bridge's one-line status, in the ship's voice. */
export function statusLine(alerts: Alert[], connected: boolean): { tone: 'up' | 'degraded' | 'down' | 'unknown'; text: string } {
  if (!connected) return { tone: 'unknown', text: 'Signal lost' }
  const bad = alerts.filter((a) => a.level === 'bad')
  if (bad.length) {
    return { tone: 'down', text: bad.length === 1 ? `Red alert · ${bad[0].title}` : `Red alert · ${bad.length} systems failing` }
  }
  if (alerts.length) {
    return {
      tone: 'degraded',
      text: alerts.length === 1 ? `Shields holding · ${alerts[0].title}` : `Shields holding · ${alerts.length} warnings`,
    }
  }
  return { tone: 'up', text: 'All systems nominal' }
}
