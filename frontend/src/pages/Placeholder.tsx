export function Placeholder({ view }: { view: string }) {
  return (
    <div style={{ padding: '2em', color: '#8888bb' }}>
      <h2 style={{ color: '#646cff', textTransform: 'capitalize' }}>{view}</h2>
      <p>Coming soon</p>
    </div>
  )
}
