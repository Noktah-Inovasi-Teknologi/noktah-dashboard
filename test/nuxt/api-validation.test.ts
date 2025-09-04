import { describe, it, expect } from 'vitest'
import { $fetch, setup } from '@nuxt/test-utils/e2e'

// Schema validation helper
interface ApiResponse<T = any> {
  data?: T
  error?: string
  status: number
  message?: string
}

// Custom matchers for API responses
function expectValidApiResponse<T>(response: ApiResponse<T>) {
  expect(response).toBeDefined()
  expect(response).toHaveProperty('status')
  expect(typeof response.status).toBe('string')  // Health check returns status as string
  return response
}

describe('API Response Validation', async () => {
  await setup()

  describe('Response Structure Validation', () => {
    it('should validate successful API response structure', async () => {
      const response = await $fetch<ApiResponse>('/api/health-check')
      
      const validatedResponse = expectValidApiResponse(response)
      expect(validatedResponse.status).toBe('healthy')
      expect(validatedResponse).not.toHaveProperty('error')
    })

    it('should handle error responses correctly', async () => {
      try {
        await $fetch('/api/nonexistent-endpoint')
      } catch (error: any) {
        expect(error.response).toBeDefined()
        expect(error.response.status).toBe(404)
      }
    })
  })

  describe('Data Type Validation', () => {
    it('should validate response data types', async () => {
      const response = await $fetch('/api/health-check')
      
      // Type assertions
      expect(typeof response.status).toBe('string')
      expect(typeof response.timestamp).toBe('string')
      expect(typeof response.uptime).toBe('number')
      expect(typeof response.version).toBe('string')
      expect(typeof response.memory).toBe('object')
      expect(typeof response.responseTime).toBe('number')
    })

    it('should validate nested object structures', async () => {
      const response = await $fetch('/api/health-check')
      
      // Nested object validation
      expect(response.memory).toMatchObject({
        used: expect.any(Number),
        total: expect.any(Number),
        rss: expect.any(Number)
      })
    })
  })

  describe('Custom Validation Rules', () => {
    it('should validate custom business rules', async () => {
      const response = await $fetch('/api/health-check')
      
      // Custom validations
      expect(response.uptime).toBeGreaterThan(0)
      expect(response.responseTime).toBeGreaterThanOrEqual(0)
      expect(response.memory.used).toBeLessThanOrEqual(response.memory.total)
      
      // Date validation
      const timestamp = new Date(response.timestamp)
      expect(timestamp.getTime()).not.toBeNaN()
      expect(timestamp).toBeInstanceOf(Date)
    })

    it('should validate array responses', async () => {
      // Example for array responses
      // const response = await $fetch<ApiResponse<User[]>>('/api/users')
      // 
      // expect(Array.isArray(response.data)).toBe(true)
      // response.data?.forEach(user => {
      //   expect(user).toHaveProperty('id')
      //   expect(user).toHaveProperty('email')
      //   expect(typeof user.id).toBe('string')
      //   expect(user.email).toMatch(/^[^\s@]+@[^\s@]+\.[^\s@]+$/)
      // })
    })
  })

  describe('Error Handling', () => {
    it('should handle network errors gracefully', async () => {
      try {
        // Simulate network failure
        await $fetch('/api/timeout-endpoint', {
          timeout: 1000
        })
      } catch (error: any) {
        expect(error).toBeDefined()
        expect(error.message || error.statusMessage).toBeDefined()
      }
    })

    it('should validate error response format', async () => {
      try {
        await $fetch('/api/invalid-request', {
          method: 'POST',
          body: { invalid: 'data' }
        })
      } catch (error: any) {
        if (error.response) {
          expect(error.response.status).toBeGreaterThanOrEqual(400)
          expect(error.response.status).toBeLessThan(600)
        }
      }
    })
  })
})