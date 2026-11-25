// Tab strip for dialogs: tabs = [[key, label], ...].
export function Tabs({ tabs, cur, onPick }) {
  return (
    <div role="tablist" className="tabs" style={{ padding: '0 20px' }}>
      {tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={cur === k} className={cur === k ? 'on' : ''} onClick={() => onPick(k)}>{l}</button>)}
    </div>
  )
}
