/// <reference types="vite/client" />

// Build-time constants injected by vite.config.ts
declare const __APP_VERSION__: string
declare const __BUILD_TIME__: string

// html2canvas type definition
declare module 'html2canvas' {
  export interface Options {
    allowTaint?: boolean
    backgroundColor?: string | null
    canvas?: HTMLCanvasElement
    foreignObjectRendering?: boolean
    ignoreElements?: (element: Element) => boolean
    imageTimeout?: number
    logging?: boolean
    onclone?: (document: Document) => void
    proxy?: string
    removeContainer?: boolean
    scale?: number
    useCORS?: boolean
    width?: number
    height?: number
    x?: number
    y?: number
    scrollX?: number
    scrollY?: number
    windowWidth?: number
    windowHeight?: number
  }

  export default function html2canvas(element: HTMLElement, options?: Options): Promise<HTMLCanvasElement>
}

interface ImportMetaEnv {
  readonly VITE_API_URL: string
  readonly VITE_GCS_BUCKET: string
  readonly VITE_FIREBASE_API_KEY: string
  readonly VITE_FIREBASE_AUTH_DOMAIN: string
  readonly VITE_FIREBASE_PROJECT_ID: string
  readonly VITE_FIREBASE_STORAGE_BUCKET: string
  readonly VITE_FIREBASE_MESSAGING_SENDER_ID: string
  readonly VITE_FIREBASE_APP_ID: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
