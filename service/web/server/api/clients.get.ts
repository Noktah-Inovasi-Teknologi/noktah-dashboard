export interface ClientSummary {
  id: string
  name: string
  is_active: boolean
}

export default defineEventHandler(event => hubApi<ClientSummary[]>(event, '/v1/clients'))
