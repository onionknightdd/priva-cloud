import { sandboxPost } from '@shared/api/client'

export const resolveImageRoute = (model) => sandboxPost('/agent/image-route', {
  model: model || null,
})
