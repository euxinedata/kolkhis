import { useMemo } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { AllCommunityModule, themeQuartz, colorSchemeDark } from 'ag-grid-community'
import type { ColDef, SortChangedEvent } from 'ag-grid-community'

interface ResultsGridProps {
  columns: string[]
  rows: Record<string, unknown>[]
  onSortChanged?: (sort: { column: string; direction: 'asc' | 'desc' } | null) => void
}

const theme = themeQuartz.withPart(colorSchemeDark).withParams({
  backgroundColor: '#0a0a0a',
  headerBackgroundColor: '#111',
  rowBorder: { color: '#1a1a1a' },
  borderColor: '#1a1a1a',
  fontSize: 12,
})

export function ResultsGrid({ columns, rows, onSortChanged }: ResultsGridProps) {
  const colDefs: ColDef[] = useMemo(() =>
    columns.map(col => ({
      field: col,
      headerName: col,
      sortable: true,
      resizable: true,
      filter: true,
      valueFormatter: (params: { value: unknown }) => {
        if (params.value === null || params.value === undefined) return ''
        return String(params.value)
      },
    })),
    [columns]
  )

  function handleSortChanged(event: SortChangedEvent) {
    if (!onSortChanged) return
    const sortModel = event.api.getColumnState().filter(c => c.sort)
    if (sortModel.length > 0) {
      onSortChanged({
        column: sortModel[0].colId,
        direction: sortModel[0].sort as 'asc' | 'desc',
      })
    } else {
      onSortChanged(null)
    }
  }

  return (
    <AgGridReact
      modules={[AllCommunityModule]}
      theme={theme}
      rowData={rows}
      columnDefs={colDefs}
      rowHeight={25}
      headerHeight={28}
      onSortChanged={handleSortChanged}
      suppressMovableColumns={false}
      animateRows={false}
    />
  )
}
