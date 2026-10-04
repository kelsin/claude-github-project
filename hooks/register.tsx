import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { BoardView } from '../types'

const EMOJI: Record<string, string> = {
  todo: '🆕',
  plan: '🧠',
  plan_review: '🔍',
  plan_approval: '🙋',
  plan_approved: '✅',
  implement: '🔨',
  pr_review: '👀',
  pr_approval: '🚦',
  pr_approved: '🚀',
  done: '🎉',
}
const POLL_MS = 3000
// The loop refreshes state.json on every poll; older than this means no loop is running.
const FRESH_MS = 15 * 60 * 1000
const MAX_ROWS = 8

// Titles come from GitHub issues anyone may write: drop control characters before drawing them.
const clean = (s: unknown) => String(s ?? '').replace(/[\x00-\x1f\x7f-\x9f]/g, '')

const safeUrl = (u: unknown) => (/^https:\/\/github\.com\/[\x21-\x7e]{1,2000}$/.test(String(u ?? '')) && !String(u).includes('@') ? String(u) : null)

const view = atom({ plugin: 'cgp', key: 'view' } as const, null)

export const register: Register = on => {
  let stopFile = ''
  let seenWaiting: Set<string> | null = null // null until the first board read, so a restart does not toast old questions

  on('session.start', async ($, e, next) => {
    const home = (await $.env.get('CGP_HOME')) || `${await $.env.get('HOME')}/.config/claude-github-project`
    // Derived from the session id (stable across resume), never from an inherited CGP_SESSION that child sessions would share.
    // Exported so this session's cgp commands write their own state file, which only this band reads (same sanitising as scripts/cgp sid()).
    const session = (await $.session.id()).replace(/[^A-Za-z0-9_-]/g, '') || 'default'
    await $.env.set('CGP_SESSION', session)
    const file = `${home}/state-${session}.json`
    stopFile = `${home}/stop-${session}`

    const refresh = async () => {
      let fresh: BoardView | null = null
      try {
        const st = JSON.parse(await $.fs.read(file))
        const age = (await $.clock.now()) - Date.parse(st.updatedAt ?? '')
        const workers = Array.isArray(st.workers) ? st.workers : []
        let stopping = false
        try {
          stopping = (await $.fs.read(stopFile)).trim() === '1'
        } catch {} // no flag file yet
        if (st.board && age < FRESH_MS) {
          fresh = {
            title: clean(st.board.title ?? 'Project board'),
            url: safeUrl(st.board.url),
            planApproval: Number(st.counts?.plan_approval) || 0,
            prApproval: Number(st.counts?.pr_approval) || 0,
            waiting: (Array.isArray(st.waiting) ? st.waiting : []).map((w: any) => ({
              title: clean(w?.title),
              url: safeUrl(w?.url),
              column: String(w?.column ?? ''),
            })),
            blocked: Number(st.blockedCount) || 0,
            workers,
            stopping,
          }
        }
      } catch {
        return // no state yet, or unreadable: keep what is shown until the next poll
      }
      if (fresh) {
        const keys = new Set(fresh.waiting.map(w => w.url ?? w.title))
        const added = fresh.waiting.filter(w => seenWaiting && !seenWaiting.has(w.url ?? w.title))
        seenWaiting = keys
        for (const w of added) {
          await $.ui.toast(`❓ Question for you: ${w.title}`)
        }
      }
      if (JSON.stringify(fresh) !== JSON.stringify(await read($, view))) {
        await update($, view, () => fresh)
      }
    }

    await refresh()
    $.clock.every(POLL_MS, refresh)

    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const v = await read($, view)
    if (e.props.hasSurvey || v === null) {
      return next(e)
    }

    const { Box, Button, Link, Text } = $.ui.resolve(e)
    const shown = v.workers.slice(0, MAX_ROWS)
    const asking = v.waiting.slice(0, MAX_ROWS)
    const toggleStop = async () => {
      let stopping = false
      await update($, view, cur => {
        stopping = cur ? !cur.stopping : false
        return cur ? { ...cur, stopping } : cur
      })
      try {
        await $.fs.write(stopFile, stopping ? '1' : '0')
      } catch {
        await $.ui.toast('cgp: could not write stop flag')
      }
    }

    return (
      <Box flexDirection="column">
        <Box>
          <Text bold>📋 </Text>
          {v.url ? <Link href={v.url} label={v.title} /> : <Text>{v.title}</Text>}
          <Text>
            {'  '}
            {EMOJI.plan_approval} Plan Approval: {v.planApproval}
            {'  '}
            {EMOJI.pr_approval} PR Approval: {v.prApproval}
            {v.waiting.length > 0 ? `  ❓ Waiting on you: ${v.waiting.length}` : ''}
            {v.blocked > 0 ? `  ⛓ Queued behind another story: ${v.blocked}` : ''}
          </Text>
        </Box>
        <Button key="stop" label={v.stopping ? '⏸ Stopping after this cycle (press to cancel)' : '⏸ Stop after this cycle'} onPress={toggleStop} />
        {asking.map((w, i) => (
          <Box key={`ask:${w.url ?? w.title}:${i}`}>
            <Text>❓ </Text>
            {w.url ? <Link href={w.url} label={w.title} /> : <Text>{w.title}</Text>}
            <Text dimColor> waiting on you</Text>
          </Box>
        ))}
        {v.waiting.length > asking.length && (
          <Text dimColor>… +{v.waiting.length - asking.length} more waiting on you</Text>
        )}
        {shown.map((w, i) => (
          <Text key={`${w.item}:${i}`} dimColor>
            {EMOJI[w.column] ?? '•'} {clean(w.title)}
          </Text>
        ))}
        {v.workers.length > shown.length && (
          <Text dimColor>… +{v.workers.length - shown.length} more workers</Text>
        )}
      </Box>
    )
  })
}
