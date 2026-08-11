export interface Tag {
  kind: 'def' | 'ref'
  name: string
  line: number
  signature: string
  subKind?: string
}

export interface FileTags {
  path: string
  tags: Tag[]
}

export interface ContextBlock {
  path: string
  content: string
}

export interface TaskFocus {
  focusFiles: string[]
  focusSymbols: string[]
}
