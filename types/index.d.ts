export type Worker = { item: string; column: string; title: string; phase?: string; detail?: string }
export type Waiting = { title: string; url: string | null; column: string }
export type BoardView = {
  title: string
  url: string | null
  planApproval: number
  prApproval: number
  waiting: Waiting[]
  blocked: number
  workers: Worker[]
  stopping: boolean
}

declare module 'claude-code' {
  interface PluginState {
    cgp: { view: BoardView | null }
  }
}
