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

const view = atom({ plugin: 'cgp', key: 'view' } as const, null)

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const home = await $.env.get('HOME')
    const file = `${home}/.config/claude-github-project/state.json`

    const refresh = async () => {
      let fresh: BoardView | null = null
      try {
        const st = JSON.parse(await $.fs.read(file))
        const age = (await $.clock.now()) - Date.parse(st.updatedAt ?? '')
        if (st.board && age < FRESH_MS) {
          fresh = {
            title: st.board.title,
            url: st.board.url,
            planApproval: st.counts?.plan_approval ?? 0,
            prApproval: st.counts?.pr_approval ?? 0,
            waiting: st.waiting ?? [],
            workers: st.workers ?? [],
          }
        }
      } catch {
        // no state yet, or mid-write: keep the band hidden until the next poll
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
          <Link href={v.url} label={v.title} />
          <Text>
            {'  '}
            {EMOJI.plan_approval} Plan Approval: {v.planApproval}
            {'  '}
            {EMOJI.pr_approval} PR Approval: {v.prApproval}
            {v.waiting.length > 0 ? `  ❓ Waiting on you: ${v.waiting.length}` : ''}
          </Text>
        </Box>
        {shown.map(w => (
          <Text key={w.item} dimColor>
            {EMOJI[w.column] ?? '•'} {w.title}
          </Text>
        ))}
        {v.workers.length > shown.length && (
          <Text dimColor>… +{v.workers.length - shown.length} more workers</Text>
        )}
      </Box>
    )
  })
}
