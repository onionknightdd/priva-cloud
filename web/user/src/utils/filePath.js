function normalizePath(path) {
  if (!path) return ''
  if (path === '/') return '/'
  return path.replace(/\/+$/, '')
}
function joinPath(base, child) {
  if (!base || base === '/') return `/${child}`
  return `${normalizePath(base)}/${child}`
}

/**
 * Resolve an agent-reported relative path against the active session cwd.
 *
 * The file-preview backend anchors relative paths to the account workspace,
 * while FileBrowser anchors them to the session cwd. Resolve before probing so
 * existence checks and the file eventually opened in Canvas address the same
 * filesystem entry.
 */
export function resolveFilePathAgainstCwd(filePath, cwd) {
  if (!filePath) return filePath
  if (filePath[0] === '/' || filePath[0] === '~') return filePath
  if (!cwd || cwd === '~') return filePath
  return joinPath(cwd, filePath.replace(/^\.\/+/, ''))
}
