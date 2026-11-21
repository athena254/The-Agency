/*
  Toast store — minimal, module-level (no provider plumbing). UI renders via
  <ToastRegion /> in components/ui.tsx.
*/

import { create } from 'zustand'

export interface ToastMessage {
  id: number
  text: string
  tone: 'success' | 'danger' | 'info'
}

interface ToastState {
  toasts: ToastMessage[]
  push: (text: string, tone?: ToastMessage['tone']) => void
  dismiss: (id: number) => void
}

let toastSeq = 0

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (text, tone = 'info') =>
    set((state) => {
      toastSeq += 1
      const id = toastSeq
      // A modest cap keeps a burst of notices from stacking forever.
      const toasts = [...state.toasts, { id, text, tone }].slice(-4)
      window.setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 6000)
      return { toasts }
    }),
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}))

/** Push a toast from anywhere: `const toast = useToast(); toast('Saved')`. */
export function useToast() {
  return useToastStore((state) => state.push)
}
