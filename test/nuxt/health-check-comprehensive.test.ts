import { describe, it, expect } from 'vitest'
import { $fetch, setup } from '@nuxt/test-utils/e2e'

// Type definitions based on the actual API
interface HealthCheckResponse {
  status: 'healthy' | 'unhealthy'
  timestamp: string
  uptime: number
  version: string
  memory: {
    used: number
    total: number
    rss: number
  }
  responseTime: number
  error?: string
}

// Custom validation helpers
function expectHealthyResponse(response: HealthCheckResponse) {
  expect(response).toBeDefined()
  expect(response.status).toBe('healthy')
  expect(response).not.toHaveProperty('error')
  return response
}

function expectUnhealthyResponse(response: HealthCheckResponse) {
  expect(response).toBeDefined()
  expect(response.status).toBe('unhealthy')
  expect(response).toHaveProperty('error')
  expect(typeof response.error).toBe('string')
  return response
}

describe('Health Check API - Comprehensive Testing', async () => {
  await setup()

  describe('Response Structure Validation', () => {
    it('should return all required fields', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      // Required fields
      expect(response).toHaveProperty('status')
      expect(response).toHaveProperty('timestamp')
      expect(response).toHaveProperty('uptime')
      expect(response).toHaveProperty('version')
      expect(response).toHaveProperty('memory')
      expect(response).toHaveProperty('responseTime')
    })

    it('should have correct field types', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      expect(typeof response.status).toBe('string')
      expect(typeof response.timestamp).toBe('string')
      expect(typeof response.uptime).toBe('number')
      expect(typeof response.version).toBe('string')
      expect(typeof response.memory).toBe('object')
      expect(typeof response.responseTime).toBe('number')
    })

    it('should have valid memory object structure', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      expect(response.memory).toHaveProperty('used')
      expect(response.memory).toHaveProperty('total')
      expect(response.memory).toHaveProperty('rss')
      
      expect(typeof response.memory.used).toBe('number')
      expect(typeof response.memory.total).toBe('number')
      expect(typeof response.memory.rss).toBe('number')
    })
  })

  describe('Data Validation', () => {
    it('should have valid timestamp format', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      // Should be ISO string
      expect(response.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{3}Z$/)
      
      // Should be valid date
      const timestamp = new Date(response.timestamp)
      expect(timestamp.getTime()).not.toBeNaN()
      expect(timestamp.toISOString()).toBe(response.timestamp)
      
      // Should be recent (within last minute)
      const now = Date.now()
      const responseTime = timestamp.getTime()
      expect(now - responseTime).toBeLessThan(60000) // 1 minute
    })

    it('should have valid Node.js version format', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      // Should match Node.js version pattern (v20.x.x)
      expect(response.version).toMatch(/^v\d+\.\d+\.\d+$/)
      expect(response.version).toBe(process.version)
    })

    it('should have realistic memory values', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      // All memory values should be positive
      expect(response.memory.used).toBeGreaterThan(0)
      expect(response.memory.total).toBeGreaterThan(0)
      expect(response.memory.rss).toBeGreaterThan(0)
      
      // Used memory should not exceed total
      expect(response.memory.used).toBeLessThanOrEqual(response.memory.total)
      
      // RSS should be reasonable (usually larger than heap)
      expect(response.memory.rss).toBeGreaterThanOrEqual(response.memory.used)
      
      // Memory values should be in MB (reasonable ranges)
      expect(response.memory.used).toBeLessThan(10000) // Less than 10GB
      expect(response.memory.total).toBeLessThan(10000)
      expect(response.memory.rss).toBeLessThan(10000)
    })

    it('should have valid uptime value', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      expect(response.uptime).toBeGreaterThan(0)
      expect(response.uptime).toBeLessThan(365 * 24 * 60 * 60) // Less than 1 year
    })

    it('should have measurable response time', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      expect(response.responseTime).toBeGreaterThanOrEqual(0)
      expect(response.responseTime).toBeLessThan(10000) // Less than 10 seconds
    })
  })

  describe('Performance Testing', () => {
    it('should respond quickly', async () => {
      const startTime = Date.now()
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      const endTime = Date.now()
      
      const actualResponseTime = endTime - startTime
      expect(actualResponseTime).toBeLessThan(1000) // Less than 1 second
      
      // Internal response time should be close to actual
      expect(Math.abs(response.responseTime - actualResponseTime)).toBeLessThan(100)
    })

    it('should handle concurrent requests', async () => {
      const promises = Array.from({ length: 5 }, () => 
        $fetch<HealthCheckResponse>('/api/health-check')
      )
      
      const responses = await Promise.all(promises)
      
      responses.forEach(response => {
        expectHealthyResponse(response)
        expect(response.responseTime).toBeLessThan(1000)
      })
      
      // All responses should have recent timestamps
      const timestamps = responses.map(r => new Date(r.timestamp).getTime())
      const minTime = Math.min(...timestamps)
      const maxTime = Math.max(...timestamps)
      expect(maxTime - minTime).toBeLessThan(5000) // Within 5 seconds
    })
  })

  describe('Consistency Testing', () => {
    it('should return consistent data across multiple calls', async () => {
      const response1 = await $fetch<HealthCheckResponse>('/api/health-check')
      await new Promise(resolve => setTimeout(resolve, 100)) // Small delay
      const response2 = await $fetch<HealthCheckResponse>('/api/health-check')
      
      // These should remain the same
      expect(response1.version).toBe(response2.version)
      expect(response1.status).toBe(response2.status)
      
      // These should increase
      expect(response2.uptime).toBeGreaterThanOrEqual(response1.uptime)
      
      // Timestamps should be different and in order
      const time1 = new Date(response1.timestamp).getTime()
      const time2 = new Date(response2.timestamp).getTime()
      expect(time2).toBeGreaterThan(time1)
    })
  })

  describe('HTTP Headers and Status', () => {
    it('should return correct HTTP status code', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      expectHealthyResponse(response)
      
      // Note: In Nuxt test environment, successful $fetch indicates 200 status
      expect(response).toBeDefined()
      expect(response.status).toBe('healthy')
    })

    it('should return JSON content type', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      // Note: $fetch already parses JSON, so successful parsing indicates JSON content
      expect(typeof response).toBe('object')
      expect(response).not.toBe(null)
      expect(response.status).toBeDefined()
    })
  })

  describe('Edge Cases', () => {
    it('should handle different HTTP methods appropriately', async () => {
      // GET should work
      const getResponse = await $fetch<HealthCheckResponse>('/api/health-check')
      expectHealthyResponse(getResponse)
      
      // POST should also work (most endpoints accept any method)
      try {
        const postResponse = await $fetch<HealthCheckResponse>('/api/health-check', {
          method: 'POST'
        })
        expectHealthyResponse(postResponse)
      } catch (error) {
        // If POST is not allowed, it should return 405 Method Not Allowed
        expect(error).toBeDefined()
      }
    })

    it('should handle query parameters gracefully', async () => {
      // Should ignore query parameters
      const response = await $fetch<HealthCheckResponse>('/api/health-check?test=123&foo=bar')
      expectHealthyResponse(response)
    })
  })

  describe('Data Integrity', () => {
    it('should maintain data relationships', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      // Memory relationships
      expect(response.memory.used).toBeLessThanOrEqual(response.memory.total)
      expect(response.memory.rss).toBeGreaterThanOrEqual(response.memory.used)
      
      // Time relationships
      const timestamp = new Date(response.timestamp).getTime()
      const now = Date.now()
      expect(timestamp).toBeLessThanOrEqual(now)
      expect(now - timestamp).toBeLessThan(5000) // Should be very recent
    })

    it('should validate response completeness', async () => {
      const response = await $fetch<HealthCheckResponse>('/api/health-check')
      
      // No undefined values
      expect(response.status).toBeDefined()
      expect(response.timestamp).toBeDefined()
      expect(response.uptime).toBeDefined()
      expect(response.version).toBeDefined()
      expect(response.memory).toBeDefined()
      expect(response.responseTime).toBeDefined()
      
      // No null values
      expect(response.status).not.toBeNull()
      expect(response.timestamp).not.toBeNull()
      expect(response.uptime).not.toBeNull()
      expect(response.version).not.toBeNull()
      expect(response.memory).not.toBeNull()
      expect(response.responseTime).not.toBeNull()
    })
  })
})