import type { Monaco } from '@monaco-editor/react'

export const THEME_NAME = 'kolkhis-dark'

export function defineKolkhisTheme(monaco: Monaco) {
  monaco.editor.defineTheme(THEME_NAME, {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#0a0a0a',
      'editorGutter.background': '#0a0a0a',
      'editorLineNumber.foreground': '#444444',
      'editorLineNumber.activeForeground': '#888888',
    },
  })
}
