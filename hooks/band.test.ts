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
  ['fresh', state({}), ['My Board', 'Plan Review: 2', 'PR Review: 1', '🔨 Story A', '🧠 Story B'], ['HIDDEN']],
  ['stale and idle', state({ workers: [], updatedAt: '2026-10-03T10:00:00Z' }), ['HIDDEN'], ['My Board']],
  ['stale even with workers (interrupted loop)', state({ updatedAt: '2026-10-03T10:00:00Z' }), ['HIDDEN'], ['Story A']],
  ['bad json', '{"board":', ['HIDDEN'], ['My Board']],
  ['unlinkable url', state({ board: { title: 'My Board', url: 'https://github.com/users/kélsin/p' } }), ['My Board', 'Story A'], ['HIDDEN']],
  ['a worker phase replaces the column emoji and names what it is doing', state({ workers: [{ item: 'a', column: 'plan', title: 'Story A', phase: 'reviewing', detail: '3 reviewers' }] }), ['🔍 Story A · reviewing (3 reviewers)'], ['🧠']],
  ['a phase without detail', state({ workers: [{ item: 'a', column: 'implement', title: 'Story A', phase: 'ci' }] }), ['⏳ Story A · waiting on CI'], ['()']],
  ['an unknown phase is ignored', state({ workers: [{ item: 'a', column: 'implement', title: 'Story A', phase: 'pwned', detail: 'x' }] }), ['🔨 Story A'], ['pwned', '(x)']],
  ['phase detail is stripped of control characters', state({ workers: [{ item: 'a', column: 'plan', title: 'S', phase: 'planning', detail: 'a\x1b[2Jb' }] }), ['planning (a[2Jb)'], ['\x1b']],
  ['blocked count', state({ blockedCount: 2 }), ['Queued behind another story: 2'], ['HIDDEN']],
  ['control characters in titles are stripped', state({ workers: [{ item: 'a', column: 'plan', title: 'Evil\x1b]52;c;x\x07 Title' }] }), ['Evil]52;c;x Title'], ['\x1b']],
  ['waiting count', state({ waiting: [{ title: 't', url: null, column: 'plan' }] }), ['Waiting on you: 1'], ['HIDDEN']],
  ['waiting story is listed by title', state({ waiting: [{ title: 'Add login', url: 'https://github.com/o/r/issues/4', column: 'plan' }] }), ['❓ Add login waiting on you'], ['HIDDEN']],
  ['waiting titles are stripped of control characters', state({ waiting: [{ title: 'Bad\x1b[2J Q', url: null, column: 'plan' }] }), ['Bad[2J Q'], ['\x1b']],
]

// Mocks the world beneath the plugin; `files` is read live so tests can change it between draws.
const world = (on: any, files: Record<string, string>, id = 'test-session', env: Record<string, string> = {}, failWrites = false) => {
  mock.env(on, { HOME: '/h', CGP_SESSION: 'inherited-from-parent', ...env })
  const clock = mock.clock(on, { now })
  const exported: string[] = []
  const writes: [string, string][] = []
  on('session.id', async () => ({ value: id }) as any)
  on('env.set', async (_a: any, e: any) => {
    if (e.name === 'CGP_SESSION') exported.push(e.value)
    return { value: undefined } as any
  })
  on('session.start', (_a: any, e: any) => ({ cwd: e.cwd }))
  on('fs.read', async (_a: any, e: any) => {
    const hit = Object.keys(files).find(k => String(e.path).endsWith(k))
    if (hit === undefined) throw new Error('ENOENT')
    return { value: files[hit] } as any
  })
  on('fs.write', async (_a: any, e: any) => {
    if (failWrites) throw new Error('EACCES')
    writes.push([e.path, e.text])
    files[e.path.slice(e.path.lastIndexOf('/') + 1)] = e.text
    return { value: undefined } as any
  })
  return { clock, exported, writes }
}
const mount = ($: any, surface: 'terminal' | 'desktop') =>
  $.ui.mount({ plugin: 'cgp', surface, component: 'AbovePrompt', props: { hasSurvey: false, isWorking: true } as any })

for (const surface of ['terminal', 'desktop'] as const) {
  for (const [name, body, has, hasNot] of cases) {
    test(`${surface}: ${name}`, async ($, on) => {
      world(on, { 'state-test-session.json': body })
      on('ui.render', () => ({ type: 'Text', props: {}, children: ['HIDDEN'] }) as any)
      await $.session.start({ cwd: '/', surface, isInteractive: true })
      const ui = await mount($, surface)
      const drawn = text(await ui.drawn())
      for (const t of has) expect(drawn).toContain(t)
      for (const t of hasNot) expect(drawn).not.toContain(t)
    })
  }

  test(`${surface}: stop button toggles the stop flag`, async ($, on) => {
    const { writes } = world(on, { 'state-test-session.json': state({}) })
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    const ui = await mount($, surface)
    expect(text(await ui.drawn())).toContain('Stop after this cycle')
    await ui.press({ key: 'stop' } as any)
    expect(writes).toEqual([['/h/.config/claude-github-project/stop-test-session', '1']])
    expect(text(await ui.drawn())).toContain('Stopping after this cycle')
    await ui.press({ key: 'stop' } as any)
    expect(writes[1][1]).toBe('0')
    expect(text(await ui.drawn())).toContain('Stop after this cycle')
  })

  test(`${surface}: stop flag written externally appears after a poll`, async ($, on) => {
    const files: Record<string, string> = { 'state-test-session.json': state({}) }
    const { clock } = world(on, files)
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    const ui = await mount($, surface)
    expect(text(await ui.drawn())).toContain('Stop after this cycle')
    files['stop-test-session'] = '1'
    await clock.advance(3000)
    expect(text(await ui.drawn())).toContain('Stopping after this cycle')
  })

  test(`${surface}: a flag value other than 1 is not stopping`, async ($, on) => {
    world(on, { 'state-test-session.json': state({}), 'stop-test-session': 'yes' })
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    const ui = await mount($, surface)
    const drawn = text(await ui.drawn())
    expect(drawn).toContain('Stop after this cycle')
    expect(drawn).not.toContain('Stopping')
  })

  test(`${surface}: band hides once the loop is released`, async ($, on) => {
    const files: Record<string, string> = { 'state-test-session.json': state({}) }
    const { clock } = world(on, files)
    on('ui.render', () => ({ type: 'Text', props: {}, children: ['HIDDEN'] }) as any)
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    const ui = await mount($, surface)
    expect(text(await ui.drawn())).toContain('My Board')
    files['state-test-session.json'] = state({ workers: [], updatedAt: null })
    await clock.advance(3000)
    const drawn = text(await ui.drawn())
    expect(drawn).toContain('HIDDEN')
    expect(drawn).not.toContain('My Board')
  })

  test(`${surface}: band goes stale when the loop stops refreshing`, async ($, on) => {
    const { clock } = world(on, { 'state-test-session.json': state({}) })
    on('ui.render', () => ({ type: 'Text', props: {}, children: ['HIDDEN'] }) as any)
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    const ui = await mount($, surface)
    expect(text(await ui.drawn())).toContain('Story A')
    await clock.advance(16 * 60 * 1000)
    expect(text(await ui.drawn())).toContain('HIDDEN')
  })

  test(`${surface}: overflow past the row cap is summarised`, async ($, on) => {
    const workers = Array.from({ length: 11 }, (_, i) => ({ item: `i${i}`, column: 'plan', title: `Story ${i}` }))
    world(on, { 'state-test-session.json': state({ workers }) })
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    const ui = await mount($, surface)
    const drawn = text(await ui.drawn())
    expect(drawn).toContain('Story 7')
    expect(drawn).not.toContain('Story 8')
    expect(drawn).toContain('… +3 more workers')
  })

  test(`${surface}: session id comes from the session, survives resume, ignores inherited CGP_SESSION`, async ($, on) => {
    const { exported } = world(on, { 'state-abc-123.json': state({}) }, 'abc-123')
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    await $.session.start({ cwd: '/', surface, isInteractive: true }) // resume: same id again
    expect(exported).toEqual(['abc-123', 'abc-123'])
    const ui = await mount($, surface)
    expect(text(await ui.drawn())).toContain('My Board')
  })

  test(`${surface}: CGP_HOME relocates the state files`, async ($, on) => {
    const { writes } = world(on, { 'state-test-session.json': state({}) }, 'test-session', { CGP_HOME: '/custom' })
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    const ui = await mount($, surface)
    await ui.press({ key: 'stop' } as any)
    expect(writes).toEqual([['/custom/stop-test-session', '1']])
  })

  test(`${surface}: a failed flag write toasts`, async ($, on) => {
    world(on, { 'state-test-session.json': state({}) }, 'test-session', {}, true)
    const toasts: string[] = []
    on('ui.toast', async (_a: any, e: any) => {
      toasts.push(e.text)
      return { value: undefined } as any
    })
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    const ui = await mount($, surface)
    await ui.press({ key: 'stop' } as any)
    expect(toasts).toEqual(['cgp: could not write stop flag'])
  })

  test(`${surface}: a newly waiting story toasts once, not at startup`, async ($, on) => {
    const files: Record<string, string> = {
      'state-test-session.json': state({ waiting: [{ title: 'Old', url: 'https://github.com/o/r/issues/1', column: 'plan' }] }),
    }
    const { clock } = world(on, files)
    const toasts: string[] = []
    on('ui.toast', async (_a: any, e: any) => {
      toasts.push(e.text)
      return { value: undefined } as any
    })
    await $.session.start({ cwd: '/', surface, isInteractive: true })
    expect(toasts).toEqual([])
    files['state-test-session.json'] = state({
      waiting: [
        { title: 'Old', url: 'https://github.com/o/r/issues/1', column: 'plan' },
        { title: 'New', url: 'https://github.com/o/r/issues/2', column: 'implement' },
      ],
    })
    await clock.advance(3000)
    await clock.advance(3000)
    expect(toasts).toEqual(['❓ Question for you: New'])
  })
}
