/** User-facing recipe copy. Keep SOP / 条目 / Markdown out of the chrome. */

export const RECIPE_BRAND_MARK = '配'
export const RECIPE_BRAND_TITLE = '配方'
export const RECIPE_BRAND_TAGLINE = '按岗位查看'
export const RECIPE_NAV_HOME_LABEL = '管理后台'
export const RECIPE_NAV_STATIONS_LABEL = '岗位列表'
export const RECIPE_NAV_MANAGE_LABEL = '配方管理'
export const RECIPE_DASHBOARD_BLURB = '按岗位查看配方'

export function recipeDocumentTitle(pageName) {
  const name = pageName == null ? '' : String(pageName).trim()
  return name ? `${name} · ${RECIPE_BRAND_TITLE}` : RECIPE_BRAND_TITLE
}

export function recipeCountLabel(count) {
  const n = Number(count)
  const value = Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
  return `${value} 条配方`
}
