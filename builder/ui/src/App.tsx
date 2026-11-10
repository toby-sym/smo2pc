import { useEffect, useRef, useState } from 'react'

type Field = 'prodKeys' | 'titleKeys' | 'base' | 'update'
type PickedFiles = Record<Field, string>
type NativeMessage =
  | { type: 'picked'; field: Field; path: string }
  | { type: 'outputPicked'; path: string; files: PickedFiles }
  | { type: 'log'; text: string }
  | { type: 'buildFinished'; success: boolean; exePath?: string; text: string }
  | { type: 'error'; text: string }

const initialFiles: PickedFiles = { prodKeys: '', titleKeys: '', base: '', update: '' }

function send(message: unknown) {
  window.chrome?.webview?.postMessage(message)
}

function App() {
  const [files, setFiles] = useState(initialFiles)
  const [building, setBuilding] = useState(false)
  const [built, setBuilt] = useState(false)
  const [logs, setLogs] = useState<string[]>([])
  const logBox = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const receive = (event: MessageEvent<NativeMessage>) => {
      const message = event.data
      if (message.type === 'picked') {
        setFiles(current => ({ ...current, [message.field]: message.path }))
      } else if (message.type === 'outputPicked') {
        setLogs([])
        setBuilt(false)
        setBuilding(true)
        send({ action: 'build', files: message.files, outputPath: message.path })
      } else if (message.type === 'log') {
        setLogs(current => [...current.slice(-999), message.text])
      } else if (message.type === 'buildFinished') {
        setBuilding(false)
        setBuilt(message.success)
        setLogs(current => [...current.slice(-999), message.text])
      } else if (message.type === 'error') {
        setBuilding(false)
        setLogs(current => [...current.slice(-999), `ERROR: ${message.text}`])
      }
    }
    window.chrome?.webview?.addEventListener('message', receive)
    return () => window.chrome?.webview?.removeEventListener('message', receive)
  }, [])

  useEffect(() => {
    if (logBox.current) logBox.current.scrollTop = logBox.current.scrollHeight
  }, [logs])

  const choose = (field: Field) => send({ action: 'pick', field })
  const startBuild = () => {
    if (!files.prodKeys || !files.base || !files.update) return
    send({ action: 'chooseOutput', files })
  }

  const fileCard = (field: Field, title: string, detail: string, required = true) => (
    <section className="file-card" key={field}>
      <div className="file-copy">
        <div className="file-title">{title}<span>{required ? 'Required' : 'Optional'}</span></div>
        <p>{detail}</p>
        <div className={`file-path ${files[field] ? 'selected' : ''}`} title={files[field]}>
          {files[field] || 'No file selected'}
        </div>
      </div>
      <button className="secondary" onClick={() => choose(field)} disabled={building}>
        {files[field] ? 'Change file' : 'Choose file'}
      </button>
    </section>
  )

  const canBuild = Boolean(files.prodKeys && files.base && files.update) && !building

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand-mark">S</div>
        <div><strong>SMO2PC</strong><small>LOCAL BUILDER</small></div>
        <div className="local-badge"><i /> Files stay on this PC</div>
      </header>

      <div className="content">
        <div className="eyebrow">WINDOWS · VERSION 1.4.1</div>
        <h1>Build your game</h1>
        <p className="intro">Choose your own game dump and console keys. The builder prepares the files and compiles the Windows app on this PC.</p>

        <div className="steps"><div className="active"><b>1</b><span>Your files</span></div><div><b>2</b><span>Build</span></div><div><b>3</b><span>Ready to play</span></div></div>

        <div className="section-heading"><h2>Game files</h2><p>Base game and the 1.4.1 update</p></div>
        <div className="file-list">
          {fileCard('base', 'Base game', 'Select your base game .NSP or .XCI')}
          {fileCard('update', '1.4.1 update', 'Select the matching 1.4.1 update .NSP or .XCI')}
        </div>

        <div className="section-heading keys-heading"><h2>Console keys</h2><p>Used locally to decrypt your packages</p></div>
        <div className="file-list">
          {fileCard('prodKeys', 'prod.keys', 'The key file dumped from your own console')}
          {fileCard('titleKeys', 'title.keys', 'Only needed when the package has no ticket', false)}
        </div>

        <div className="privacy-note"><span>🔒</span><p>Keys and game packages are read directly from their selected paths. They are not uploaded or copied into the source repository.</p></div>

        <div className="build-row">
          <div><strong>{building ? 'Build running' : built ? 'Build complete' : 'Ready when you are'}</strong><small>{built ? 'The executable and required runtime files were saved to your chosen folder.' : 'Choose where to save the executable when you start the build.'}</small></div>
          <button className="primary" disabled={!canBuild} onClick={startBuild}>{building ? <><span className="spinner" /> Building…</> : built ? 'Build again' : 'Choose output and build'}</button>
        </div>

        {(logs.length > 0 || building) && <section className="log-panel"><div className="log-title"><span>BUILD OUTPUT</span><span className={building ? 'live' : ''}>{building ? 'RUNNING' : 'FINISHED'}</span></div><div className="log-box" ref={logBox}>{logs.map((line, index) => <div key={index}>{line}</div>)}{building && <div className="cursor">▍</div>}</div></section>}

        <footer>Requires the project’s Windows build tools and .NET 8+. Your own legal game dump is required.</footer>
      </div>
    </main>
  )
}

export default App
