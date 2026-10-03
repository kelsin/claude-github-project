import { test, expect, mock } from 'claude-code/testing'

const now = Date.parse('2026-10-03T12:00:00Z')
const state = (over: object) =>
  JSON.stringify({
    board: { title: 'My Board', url: 'https://github.com/users/kelsin/projects/3' },
    counts: { plan_approval: 2, pr_approval: 1 },
    waiting: [],
    workers: [
      { item: 'a', column: 'implement', title: 'Story A' },
      { item: 'b', column: 'plan', title: 'Story B' },
    ],
    updatedAt: '2026-10-03T11:59:00Z',
    ...over,
  })

const text = (n: any): string =>
  typeof n === 'string' ? n : [n?.props?.label, ...(n?.children ?? []).map(text)].filter(Boolean).join('')

const cases: [string, string, string[], string[]][] = [
  // name, file body, text that must appear, text that must not
  ['fresh', state({}), ['My Board', 'Plan Approval: 2', 'PR Approval: 1', '🔨 Story A', '🧠 Story B'], ['HIDDEN']],
  ['stale and idle', state({ workers: [], updatedAt: '2026-10-03T10:00:00Z' }), ['HIDDEN'], ['My Board']],
  ['stale but workers active', state({ updatedAt: '2026-10-03T10:00:00Z' }), ['Story A'], ['HIDDEN']],
  ['bad json', '{"board":', ['HIDDEN'], ['My Board']],
  ['unlinkable url', state({ board: { title: 'My Board', url: 'https://github.com/users/kélsin/p' } }), ['My Board', 'Story A'], ['HIDDEN']],
  ['blocked count', state({ blockedCount: 2 }), ['Queued behind another story: 2'], ['HIDDEN']],
  ['control characters in titles are stripped', state({ workers: [{ item: 'a', column: 'plan', title: 'Evil\x1b]52;c;x\x07 Title' }] }), ['Evil]52;c;x Title'], ['\x1b']],
  ['waiting count', state({ waiting: [{ title: 't', url: null, column: 'plan' }] }), ['Waiting on you: 1'], ['HIDDEN']],
]

for (const surface of ['terminal', 'desktop'] as const) {
  for (const [name, body, has, hasNot] of cases) {
    test(`${surface}: ${name}`, async ($, on) => {
      mock.env(on, { HOME: '/h' })
      mock.clock(on, { now })
      on('session.start', (_a: any, e: any) => ({ cwd: e.cwd }))
      on('fs.read', async () => ({ value: body }) as any)
      on('ui.render', () => ({ type: 'Text', props: {}, children: ['HIDDEN'] }) as any)
      await $.session.start({ cwd: '/', surface, isInteractive: true })
      const ui = await $.ui.mount({ plugin: 'cgp', surface, component: 'AbovePrompt', props: { hasSurvey: false, isWorking: true } as any })
      const drawn = text(await ui.drawn())
      for (const t of has) expect(drawn).toContain(t)
      for (const t of hasNot) expect(drawn).not.toContain(t)
    })
  }
}
