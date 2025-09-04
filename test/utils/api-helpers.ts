import { expect } from 'vitest'

// Type definitions for common API responses
export interface StandardApiResponse<T = any> {
  data?: T
  error?: string
  status: number
  message?: string
  timestamp?: string
}

export interface PaginatedResponse<T> extends StandardApiResponse<T[]> {
  pagination: {
    page: number
    limit: number
    total: number
    totalPages: number
  }
}

// Validation helpers
export function expectValidResponse<T>(response: StandardApiResponse<T>) {
  expect(response).toBeDefined()
  expect(response).toHaveProperty('status')
  expect(typeof response.status).toBe('number')
  expect(response.status).toBeGreaterThanOrEqual(200)
  expect(response.status).toBeLessThan(300)
  return response
}

export function expectErrorResponse(response: StandardApiResponse, expectedStatus?: number) {
  expect(response).toBeDefined()
  expect(response).toHaveProperty('status')
  expect(response.status).toBeGreaterThanOrEqual(400)
  
  if (expectedStatus) {
    expect(response.status).toBe(expectedStatus)
  }
  
  expect(response).toHaveProperty('error')
  expect(typeof response.error).toBe('string')
  return response
}

export function expectPaginatedResponse<T>(response: PaginatedResponse<T>) {
  expectValidResponse(response)
  
  expect(response).toHaveProperty('data')
  expect(Array.isArray(response.data)).toBe(true)
  
  expect(response).toHaveProperty('pagination')
  expect(response.pagination).toHaveProperty('page')
  expect(response.pagination).toHaveProperty('limit')
  expect(response.pagination).toHaveProperty('total')
  expect(response.pagination).toHaveProperty('totalPages')
  
  expect(typeof response.pagination.page).toBe('number')
  expect(typeof response.pagination.limit).toBe('number')
  expect(typeof response.pagination.total).toBe('number')
  expect(typeof response.pagination.totalPages).toBe('number')
  
  return response
}

// Schema validation using JSON Schema-like approach
export function validateSchema<T>(data: T, schema: Record<string, any>) {
  for (const [key, validator] of Object.entries(schema)) {
    if (validator.required && !(key in (data as any))) {
      throw new Error(`Missing required field: ${key}`)
    }
    
    if (key in (data as any)) {
      const value = (data as any)[key]
      
      if (validator.type && typeof value !== validator.type) {
        throw new Error(`Field ${key} should be ${validator.type}, got ${typeof value}`)
      }
      
      if (validator.pattern && typeof value === 'string' && !validator.pattern.test(value)) {
        throw new Error(`Field ${key} doesn't match pattern`)
      }
      
      if (validator.min !== undefined && typeof value === 'number' && value < validator.min) {
        throw new Error(`Field ${key} should be at least ${validator.min}`)
      }
      
      if (validator.max !== undefined && typeof value === 'number' && value > validator.max) {
        throw new Error(`Field ${key} should be at most ${validator.max}`)
      }
    }
  }
}

// Common validation schemas
export const userSchema = {
  id: { type: 'string', required: true },
  email: { 
    type: 'string', 
    required: true, 
    pattern: /^[^\s@]+@[^\s@]+\.[^\s@]+$/ 
  },
  name: { type: 'string', required: true },
  createdAt: { type: 'string', required: true },
  updatedAt: { type: 'string', required: true }
}

export const postSchema = {
  id: { type: 'string', required: true },
  title: { type: 'string', required: true },
  content: { type: 'string', required: true },
  authorId: { type: 'string', required: true },
  published: { type: 'boolean', required: true },
  createdAt: { type: 'string', required: true },
  updatedAt: { type: 'string', required: true }
}

// Test data factories
export function createMockUser(overrides: Partial<any> = {}) {
  return {
    id: 'user-123',
    email: 'test@example.com',
    name: 'Test User',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides
  }
}

export function createMockPost(overrides: Partial<any> = {}) {
  return {
    id: 'post-123',
    title: 'Test Post',
    content: 'This is test content',
    authorId: 'user-123',
    published: true,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides
  }
}