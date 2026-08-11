import { describe, expect, test } from 'bun:test'
import {
  classifyTaskStep,
  getProfileForStep,
  DEFAULT_ROUTER_CONFIG,
  type RouterConfig,
} from './index.js'

describe('Task Router', () => {
  test('classifies context gathering tools correctly', () => {
    expect(classifyTaskStep({ toolName: 'FileRead' })).toBe('context-gather')
    expect(classifyTaskStep({ toolName: 'Grep' })).toBe('context-gather')
    expect(classifyTaskStep({ toolName: 'Glob' })).toBe('context-gather')
    expect(classifyTaskStep({ toolName: 'ListDir' })).toBe('context-gather')
  })

  test('classifies code edit tools correctly', () => {
    expect(classifyTaskStep({ toolName: 'FileEdit' })).toBe('code-edit')
    expect(classifyTaskStep({ toolName: 'FileWrite' })).toBe('code-edit')
    expect(classifyTaskStep({ toolName: 'Replace' })).toBe('code-edit')
  })

  test('classifies test and validation commands correctly', () => {
    expect(classifyTaskStep({ toolName: 'RunCommand', command: 'npm test' })).toBe('validate-diff')
    expect(classifyTaskStep({ toolName: 'Bash', command: 'bun run typecheck' })).toBe('validate-diff')
  })

  test('routes context-gather steps to context model profile', () => {
    const { taskType, profileName, profile } = getProfileForStep({ toolName: 'Grep' })
    expect(taskType).toBe('context-gather')
    expect(profileName).toBe('context')
    expect(profile.model).toBe('qwen2.5:7b-instruct')
  })

  test('routes code-edit and code-generation steps to execute model profile', () => {
    const { taskType, profileName, profile } = getProfileForStep({ toolName: 'FileEdit' })
    expect(taskType).toBe('code-edit')
    expect(profileName).toBe('execute')
    expect(profile.model).toBe('qwen2.5:32b-instruct')
  })

  test('supports custom router config override', () => {
    const customConfig: RouterConfig = {
      profiles: {
        small: { backend: 'ollama', model: 'llama3.2:3b' },
        big: { backend: 'ollama', model: 'qwen2.5-coder:32b' },
      },
      routing: {
        'context-gather': 'small',
        summarize: 'small',
        'code-generation': 'big',
        'code-edit': 'big',
        'validate-diff': 'small',
      },
    }

    const res = getProfileForStep({ toolName: 'FileRead' }, customConfig)
    expect(res.profileName).toBe('small')
    expect(res.profile.model).toBe('llama3.2:3b')
  })
})
