/** Type-ahead helpers for cross-station recipe name search on the list page. */

export const SEARCH_GROUP_ITEM_CAP = 8

export function capGroupedSearchHits(groups, cap = SEARCH_GROUP_ITEM_CAP) {
  return (groups || []).map((group) => ({
    ...group,
    items: Array.isArray(group.items) ? group.items.slice(0, cap) : [],
  }))
}

export function flattenCappedSearchHits(groups, cap = SEARCH_GROUP_ITEM_CAP) {
  const flat = []
  for (const group of capGroupedSearchHits(groups, cap)) {
    for (const item of group.items) {
      flat.push({
        recipe_id: item.recipe_id,
        recipe_name: item.recipe_name,
        section: item.section,
        station_slug: group.station_slug,
        station_title: group.station_title,
      })
    }
  }
  return flat
}

export function nextTypeaheadKeyboardState(state, key) {
  const open = Boolean(state && state.open)
  const itemCount = Number(state && state.itemCount) || 0
  const activeIndex = Number.isInteger(state && state.activeIndex) ? state.activeIndex : -1

  if (key === 'Escape') {
    return { open: false, activeIndex: -1, selectedIndex: null }
  }
  if (key === 'ArrowDown') {
    if (itemCount === 0) return { open: true, activeIndex: -1, selectedIndex: null }
    const nextIndex = !open || activeIndex < 0 ? 0 : (activeIndex + 1) % itemCount
    return { open: true, activeIndex: nextIndex, selectedIndex: null }
  }
  if (key === 'ArrowUp') {
    if (itemCount === 0) return { open: true, activeIndex: -1, selectedIndex: null }
    const nextIndex = !open || activeIndex < 0
      ? itemCount - 1
      : (activeIndex - 1 + itemCount) % itemCount
    return { open: true, activeIndex: nextIndex, selectedIndex: null }
  }
  if (key === 'Enter') {
    if (!open || activeIndex < 0 || activeIndex >= itemCount) {
      return { open, activeIndex, selectedIndex: null }
    }
    return { open: false, activeIndex, selectedIndex: activeIndex }
  }
  return { open, activeIndex, selectedIndex: null }
}

export function recipeFocusLocation(stationSlug, recipeId) {
  return {
    path: '/recipe/detail',
    query: { slug: stationSlug, focus: String(recipeId) },
  }
}

export function recipeManageEditLocation(stationSlug, recipeId) {
  return {
    path: '/recipe/manage',
    query: { slug: stationSlug, edit: String(recipeId) },
  }
}
