import fs from 'fs'
import path from 'path'

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

export const DEFAULT_ROUTER_CONFIG: RouterConfig = {
  profiles: {
    context: { backend: 'ollama', model: 'qwen2.5:7b-instruct' },
    execute: { backend: 'ollama', model: 'qwen2.5:32b-instruct' },
  },
  routing: {
    'context-gather': 'context',
    summarize: 'context',
    'code-generation': 'execute',
    'code-edit': 'execute',
    'validate-diff': 'context',
  },
}

export function loadRouterConfig(root: string = process.cwd()): RouterConfig {
  const configFile = path.join(root, '.openclaude-router.json')
  if (fs.existsSync(configFile)) {
    try {
      const content = fs.readFileSync(configFile, 'utf-8')
      const parsed = JSON.parse(content)
      return {
        profiles: { ...DEFAULT_ROUTER_CONFIG.profiles, ...parsed.profiles },
        routing: { ...DEFAULT_ROUTER_CONFIG.routing, ...parsed.routing },
      }
    } catch {
      return DEFAULT_ROUTER_CONFIG
    }
  }
  return DEFAULT_ROUTER_CONFIG
}

export interface TaskStepContext {
  toolName?: string
  command?: string
  prompt?: string
}

export function classifyTaskStep(step: TaskStepContext): TaskType {
  const tool = (step.toolName || '').toLowerCase()
  const cmd = (step.command || '').toLowerCase()

  if (
    tool.includes('read') ||
    tool.includes('grep') ||
    tool.includes('glob') ||
    tool.includes('list')
  ) {
    return 'context-gather'
  }

  if (
    tool.includes('edit') ||
    tool.includes('write') ||
    tool.includes('replace')
  ) {
    return 'code-edit'
  }

  if (
    cmd.includes('test') ||
    cmd.includes('lint') ||
    cmd.includes('typecheck') ||
    tool.includes('validate')
  ) {
    return 'validate-diff'
  }

  if (step.prompt && (step.prompt.includes('summary') || step.prompt.includes('map'))) {
    return 'summarize'
  }

  return 'code-generation'
}

export function getProfileForStep(
  step: TaskStepContext,
  config: RouterConfig = DEFAULT_ROUTER_CONFIG,
): { taskType: TaskType; profileName: string; profile: ModelBackendProfile } {
  const taskType = classifyTaskStep(step)
  const profileName = config.routing[taskType] || 'execute'
  const profile = config.profiles[profileName] || config.profiles.execute
  return { taskType, profileName, profile }
}
