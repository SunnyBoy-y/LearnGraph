/**
 * Where a successful sign-in should land.
 *
 * `from` is captured by the route guard *before* signing out, so after an
 * account switch it normally names the **previous** account's workspace.
 * Reusing it blindly sent the new account to `/w/<old workspace>/...`, the
 * workspace switch then failed with 403, and the route guard ended up on an
 * empty page. Only reuse the path when it belongs to the workspace this
 * sign-in actually resolved to; a same-account re-login after an expired
 * session still returns to where the user was.
 */
export function postLoginPath(from: string | null | undefined, workspaceId: string): string {
  if (typeof from === 'string' && from.startsWith('/') && !from.startsWith('/auth/')) {
    if (/^\/w\/([^/?#]+)/.exec(from)?.[1] === workspaceId) return from
  }
  return `/w/${workspaceId}`
}
