export type Worker = { item: string; column: string; title: string }
export type Waiting = { title: string; url: string | null; column: string }
export type BoardView = {
  title: string
  url: string | null
  planApproval: number
  prApproval: number
  waiting: Waiting[]
  workers: Worker[]
}

declare module 'claude-code' {
  interface PluginState {
    cgp: { view: BoardView | null }
  }
}
