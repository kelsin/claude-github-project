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

const view = atom({ plugin: 'cgp', key: 'view' } as const, null)

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const home = await $.env.get('HOME')
    // One id per session: exported so this session's cgp commands write their own state file, which only this band reads.
    let session = await $.env.get('CGP_SESSION')
    if (!session) {
      session = `${(await $.clock.now()).toString(36)}${Math.random().toString(36).slice(2, 8)}`
      await $.env.set('CGP_SESSION', session)
    }
    const file = `${home}/.config/claude-github-project/state-${session}.json`

    const refresh = async () => {
      let fresh: BoardView | null = null
      try {
        const st = JSON.parse(await $.fs.read(file))
        const age = (await $.clock.now()) - Date.parse(st.updatedAt ?? '')
        const workers = Array.isArray(st.workers) ? st.workers : []
        // workers can run for a long time between board polls: keep showing while any is active
        if (st.board && (workers.length > 0 || age < FRESH_MS)) {
          fresh = {
            title: clean(st.board.title ?? 'Project board'),
            url: /^https:\/\/github\.com\/[\x21-\x7e]{1,2000}$/.test(st.board.url ?? '') && !st.board.url.includes('@') ? st.board.url : null,
            planApproval: Number(st.counts?.plan_approval) || 0,
            prApproval: Number(st.counts?.pr_approval) || 0,
            waiting: Array.isArray(st.waiting) ? st.waiting : [],
            blocked: Number(st.blockedCount) || 0,
            workers,
          }
        }
      } catch {
        return // no state yet, or unreadable: keep what is shown until the next poll
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

    const { Box, Link, Text } = $.ui.resolve(e)
    const shown = v.workers.slice(0, MAX_ROWS)

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
