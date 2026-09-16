const ROLE_MANIFESTS = [
  {
    role: 'hygiene',
    matches: (pathname) => pathname === '/hygiene' || pathname.startsWith('/hygiene/'),
    manifest: '/pwa/manifests/hygiene.webmanifest',
    appleTouchIcon: '/pwa/icons/hygiene-192.png',
    themeColor: '#16a34a',
  },
  {
    role: 'recipe',
    matches: (pathname) => pathname === '/recipe' || pathname.startsWith('/recipe/'),
    manifest: '/pwa/manifests/recipe.webmanifest',
    appleTouchIcon: '/pwa/icons/recipe-192.png',
    themeColor: '#d97706',
  },
  {
    role: 'admin',
    matches: () => true,
    manifest: '/pwa/manifests/admin.webmanifest',
    appleTouchIcon: '/pwa/icons/admin-192.png',
    themeColor: '#0a0d16',
  },
]

export function selectPwaManifest(pathname) {
  const path = String(pathname || '/')
  return ROLE_MANIFESTS.find((entry) => entry.matches(path)) || ROLE_MANIFESTS.at(-1)
}

export function applyPwaManifest(pathname, documentRef = globalThis.document) {
  const selected = selectPwaManifest(pathname)
  if (!documentRef || typeof documentRef.getElementById !== 'function') return selected

  const manifestLink = documentRef.getElementById('app-manifest')
  if (manifestLink) manifestLink.setAttribute('href', selected.manifest)

  const appleIcon = documentRef.getElementById('app-apple-touch-icon')
  if (appleIcon) appleIcon.setAttribute('href', selected.appleTouchIcon)

  const themeMeta = documentRef.querySelector('meta[name="theme-color"]')
  if (themeMeta) themeMeta.setAttribute('content', selected.themeColor)

  return selected
}

export { ROLE_MANIFESTS }
