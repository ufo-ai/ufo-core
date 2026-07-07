# Environment

## Dev Server

Use `start_server` to start the dev server. It handles port cleanup, background startup, and health checking automatically:

```
start_server(command="npm run dev", project_path="/workspace/<project-name>", port=5000)
```

It kills any existing process on the port, starts the server in background, and waits until it responds. Use the same call to restart after backend code changes. Frontend changes hot-reload automatically via Vite HMR.

## Deployment Constraints

- The site is served at `http://localhost:<port>` inside the sandbox, so `localStorage`, `sessionStorage`, cookies, the Fullscreen/Pointer Lock APIs, and `alert()`/`confirm()`/`prompt()` all work normally
- The template uses hash-based routing (`useHashLocation` from wouter); path-based routing also works on the direct serve
- Vite is configured with `base: "./"` for relative asset paths — do not change this to absolute paths
- `fetch()`, the HTML `download` attribute, and binary assets (`<img>`, `<video>`, `<audio>`) all work same-origin. A backend (`shared/19-backend.md`) is only needed for server-side logic or a forced `Content-Disposition: attachment`
- For images, prefer authoring originals (inline SVG, CSS gradients) or dropping assets the user provided (or real images you fetched into the workspace) into `client/public/` over hotlinking external URLs (Wikipedia, Imgur, etc.) — external hosts may block hotlinking or lack CORS headers
- Screenshot via Playwright (`js_repl`) or the browser tools — they render JavaScript through a real browser, so they work for React SPAs as well as static HTML

## Packages

After copying the template, run `npm install` before starting the dev server. The following packages are included in `package.json`:

### UI Components (shadcn/ui + Radix)

- `@radix-ui/react-accordion`
- `@radix-ui/react-alert-dialog`
- `@radix-ui/react-aspect-ratio`
- `@radix-ui/react-avatar`
- `@radix-ui/react-checkbox`
- `@radix-ui/react-collapsible`
- `@radix-ui/react-context-menu`
- `@radix-ui/react-dialog`
- `@radix-ui/react-dropdown-menu`
- `@radix-ui/react-hover-card`
- `@radix-ui/react-label`
- `@radix-ui/react-menubar`
- `@radix-ui/react-navigation-menu`
- `@radix-ui/react-popover`
- `@radix-ui/react-progress`
- `@radix-ui/react-radio-group`
- `@radix-ui/react-scroll-area`
- `@radix-ui/react-select`
- `@radix-ui/react-separator`
- `@radix-ui/react-slider`
- `@radix-ui/react-slot`
- `@radix-ui/react-switch`
- `@radix-ui/react-tabs`
- `@radix-ui/react-toast`
- `@radix-ui/react-toggle`
- `@radix-ui/react-toggle-group`
- `@radix-ui/react-tooltip`
- `class-variance-authority` — variant system for shadcn components
- `clsx` + `tailwind-merge` — class merging via `cn()` in `lib/utils.ts`
- `cmdk` — command palette component
- `embla-carousel-react` — carousel
- `input-otp` — OTP input
- `react-resizable-panels` — resizable panel layouts
- `vaul` — drawer component

### Data & Forms

- `@tanstack/react-query` — data fetching (default queryFn pre-configured in `lib/queryClient.ts`)
- `react-hook-form` + `@hookform/resolvers` — form handling with Zod validation
- `drizzle-orm` + `drizzle-zod` + `drizzle-kit` — ORM, schema validation, migrations
- `zod` + `zod-validation-error` — schema validation

### Styling

- `tailwindcss` + `autoprefixer` + `postcss` — CSS framework
- `tailwindcss-animate` + `tw-animate-css` — animation utilities
- `@tailwindcss/typography` — prose plugin for rich text

### Icons & Visualization

- `lucide-react` — icons for actions and visual cues
- `react-icons` — company logos (`react-icons/si`)
- `recharts` — charts and data visualization

### Animation & Interaction

- `framer-motion` — complex animations
- `next-themes` — theme switching (light/dark)

### Backend

- `express` — HTTP server
- `better-sqlite3` — SQLite database (persistent on-disk storage)
- `express-session` + `memorystore` — session management
- `passport` + `passport-local` — authentication
- `ws` — WebSocket support

### Utilities

- `date-fns` — date formatting and manipulation
- `react-day-picker` — calendar date picker
- `wouter` — routing (with `useHashLocation` for iframe compatibility)

### Build Tools

- `vite` + `@vitejs/plugin-react` — dev server and bundler
- `typescript` — type checking
- `tsx` — TypeScript execution for server
- `esbuild` — production server bundling
