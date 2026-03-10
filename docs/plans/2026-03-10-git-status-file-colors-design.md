# Git Status File Colors Design

## Goal

Color-code filenames in the project editor file tree by git status, similar to PyCharm/VS Code.

## Architecture

The backend already returns git status via `GET /api/workspace/status` as `{filepath: status}`. The frontend already fetches this into `vcsStatus` state but doesn't use it. Wire the state to the react-complex-tree `renderItemTitle` callback to apply existing CSS classes.

## Data Flow

1. User clicks refresh button in file tree
2. Frontend calls `GET /api/workspace/status` (already implemented in `loadStatus()`)
3. Response stored in `vcsStatus` state (already implemented)
4. `renderItemTitle` looks up item path in `vcsStatus`, applies CSS class to the `<span>`

## Status Colors

Already defined in `ProjectEditor.css`:

| Status | CSS Class | Color | Meaning |
|--------|-----------|-------|---------|
| `new` | `.vcs-new` | `#629755` (green) | Untracked/added file |
| `modified` | `.vcs-modified` | `#6897bb` (blue) | Modified file |
| `deleted` | `.vcs-deleted` | `#9e6054` (brown, strikethrough) | Deleted file |

## Changes

### `frontend/src/pages/ProjectEditor.tsx`

1. Expose `vcsStatus` from useState (currently discarded: `const [, setVcsStatus]`)
2. In `renderItemTitle`, compute the item's repo-relative path and look it up in `vcsStatus`
3. Apply `vcs-{status}` CSS class to the title `<span>` if a match is found
4. Call `loadStatus()` in the refresh button handler alongside file tree reload

## Refresh Strategy

Manual only — user clicks the existing refresh button. No polling, no auto-refresh after saves. Can be automated later.
