// Clickable step tiles of a guided flow. state: cur | done | warn | red | run | '' (todo).
export function Steps({ steps, label = 'Steps', onPick }) {
  return (
    <ol aria-label={label} className="gsteps" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>
      {steps.map((s, i) => (
        <li key={i}>
          <button className={`gstep ${s.state || ''}`} aria-current={s.state === 'cur' ? 'step' : undefined} onClick={() => onPick(i + 1)}>
            <span className="gstep-h"><span className="gstep-n">{s.state === 'done' ? '✓' : i + 1}</span><strong>{s.title}</strong></span>
            <span className="gstep-v">{s.value}</span>
            {s.sub && <span className="gstep-s">{s.sub}</span>}
          </button>
        </li>
      ))}
    </ol>
  )
}
