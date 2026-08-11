import fs from 'fs'
import path from 'path'
import type { RouterConfig, TaskType } from '../domain/entities.js'

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

export class LoadRouterConfigUseCase {
  execute(root: string = process.cwd()): RouterConfig {
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
}
