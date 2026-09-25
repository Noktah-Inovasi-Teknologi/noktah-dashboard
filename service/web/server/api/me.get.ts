// Who is signed in. Cloudflare Access adds this header to every request it lets
// through to the Worker; locally (bun run dev) there is no Access, so it's null.
// Anything that authorizes a data change must verify the Access JWT
// (Cf-Access-Jwt-Assertion), not trust this header alone; that comes with the API.
import { FIXTURE_EMAIL, fixtureState } from '../fixtures'

export default defineEventHandler((event) => {
  if (fixtureState(event)) return { email: FIXTURE_EMAIL }
  return { email: getHeader(event, 'cf-access-authenticated-user-email') ?? null }
})
