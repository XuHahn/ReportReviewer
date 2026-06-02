let permissionRequested = false

export async function requestNotificationPermission(): Promise<boolean> {
  if (!('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission === 'denied') return false
  if (!permissionRequested) {
    permissionRequested = true
    const result = await Notification.requestPermission()
    return result === 'granted'
  }
  return false
}

export function notify(title: string, body: string) {
  if (!('Notification' in window)) return
  if (Notification.permission === 'granted') {
    new Notification(title, { body, icon: '/favicon.ico' })
  }
}
