export type TaskType =
  | 'context-gather'
  | 'summarize'
  | 'code-generation'
  | 'code-edit'
  | 'validate-diff'

export interface ModelBackendProfile {
  backend: string
  model: string
}

export interface RouterConfig {
  profiles: Record<string, ModelBackendProfile>
  routing: Record<TaskType, string>
}

export interface TaskStepContext {
  toolName?: string
  command?: string
  prompt?: string
}
