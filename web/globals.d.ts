// Ambient declarations for the web app.

declare namespace NodeJS {
  interface ProcessEnv {
    /** Base URL of the backend REST API (TRD §11). Empty = same-origin. */
    readonly NEXT_PUBLIC_API_URL?: string;
  }
}
