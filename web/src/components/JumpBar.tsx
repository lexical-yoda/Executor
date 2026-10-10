import { useEffect, useState } from 'react'

/** A sticky row of links to a long deck's sections, the one in view lit. */
export function JumpBar({ sections }: { sections: { id: string; label: string }[] }) {
  const [current, setCurrent] = useState(sections[0]?.id ?? '')
  const ids = sections.map((s) => s.id).join(',')

  useEffect(() => {
    const order = ids.split(',')
    const visible = new Set<string>()
    // A section counts as current while it crosses the band just below the bars.
    const seen = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) visible.add(e.target.id)
          else visible.delete(e.target.id)
        }
        const first = order.find((id) => visible.has(id))
        if (first) setCurrent(first)
      },
      { rootMargin: '-160px 0px -55% 0px' },
    )
    for (const id of order) {
      const el = document.getElementById(id)
      if (el) seen.observe(el)
    }
    return () => seen.disconnect()
  }, [ids])

  return (
    <nav className="jump-bar" aria-label="Sections">
      {sections.map((s) => (
        <button
          key={s.id}
          type="button"
          className={`jump-link${current === s.id ? ' jump-current' : ''}`}
          aria-current={current === s.id ? 'location' : undefined}
          onClick={() => {
            setCurrent(s.id)
            document.getElementById(s.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }}
        >
          {s.label}
        </button>
      ))}
    </nav>
  )
}
