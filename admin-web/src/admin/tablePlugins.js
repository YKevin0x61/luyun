/**
 * Per-table Admin DataTable plugins (toolbar extras + lifecycle hooks).
 */

import DishStationsTableExtras from '../components/admin/DishStationsTableExtras.vue'
import { dishStationsPluginBase } from './plugins/dishStations.js'

const PLUGINS = [
  {
    ...dishStationsPluginBase,
    Extras: DishStationsTableExtras,
  },
]

const BY_TABLE = Object.fromEntries(PLUGINS.map((p) => [p.table, p]))

/** @param {string | null | undefined} tableName */
export function resolveTablePlugin(tableName) {
  if (!tableName) return null
  return BY_TABLE[tableName] || null
}

export function listTablePlugins() {
  return PLUGINS.slice()
}
