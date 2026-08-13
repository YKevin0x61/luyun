/**
 * Overlapping printer connects (page warmup + print job) share one in-flight
 * attempt so we do not disconnect/reconnect twice.
 */
export function createConnectGate() {
  let inFlight = null

  return {
    /**
     * @param {() => (boolean|Promise<boolean>)} connect
     * @returns {Promise<boolean>}
     */
    run(connect) {
      if (inFlight) return inFlight
      try {
        inFlight = Promise.resolve(connect()).finally(() => {
          inFlight = null
        })
      } catch (error) {
        return Promise.reject(error)
      }
      return inFlight
    }
  }
}

/**
 * Warmup is a no-op unless this screen can actually print.
 * @param {{ platformSupported?: boolean, printEnabled?: boolean, deviceAddress?: string }} opts
 */
export function canWarmupPrinter({ platformSupported, printEnabled, deviceAddress } = {}) {
  return Boolean(platformSupported && printEnabled && deviceAddress)
}
