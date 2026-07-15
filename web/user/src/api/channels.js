// Feishu bot config is the USER's own credentials, served by the control-panel
// (not the sandbox): the user is the sole writer of app_id / app_secret / the
// user_enabled toggle. The secret is write-only — responses carry only
// app_secret_set, never the value.
import { getJSON, putJSON } from '@shared/api/client'

export const getFeishuConfig = () => getJSON('/auth/me/feishu-config')
export const updateFeishuConfig = (data) => putJSON('/auth/me/feishu-config', data)
