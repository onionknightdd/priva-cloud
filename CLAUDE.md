# Priva — Claude Agent SDK Project Rules

## Frontend SPA — build, hotload & redeploy

The user/admin SPAs (`web/user`, `web/admin`) are built on the host and served by the
**control-panel** pod from `/app/web/{user,admin}/dist` via FastAPI `StaticFiles` (read from
disk per request). So a frontend change does **NOT** need a Docker image rebuild — hotload the
freshly-built `dist/` into the running pod and it serves live, no restart.

**The loop for every frontend change:**

1. **Update the code.**
2. **Build and verify — no errors.**
   ```bash
   cd web && npm run build:user        # and/or: npm run build:admin
   ```
   Build must finish clean. The pre-existing "chunks larger than 1100 kB" warning is fine; a real
   error is not. Optionally grep `web/<app>/dist/assets` to confirm new strings/keys landed.
3. **Hotload the new files directly into the running pod** (StaticFiles serves the new hashed
   bundle + `index.html` immediately — no restart, no image rebuild):
   ```bash
   POD=$(kubectl get pods -n priva-cloud -l app=control-panel \
     --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp \
     -o jsonpath='{.items[-1].metadata.name}')
   # user SPA → /app/web/user/dist   (admin SPA → swap web/admin and /app/web/admin/dist)
   tar -C web/user/dist -cf - . \
     | kubectl exec -i -n priva-cloud "$POD" -- tar -C /app/web/user/dist --warning=no-unknown-keyword -xf -
   ```
   - `--warning=no-unknown-keyword` silences the harmless `LIBARCHIVE.xattr.com.apple.provenance`
     spam macOS `tar` emits (one line per file). Pod has GNU tar; rootfs is writable.
   - Smoke-check it's live (from inside the pod, no port-forward):
     ```bash
     kubectl exec -n priva-cloud "$POD" -- python -c 'import urllib.request,re; \
       h=urllib.request.urlopen("http://localhost:8080/").read().decode(); \
       print("serving", re.search(r"/assets/index-[A-Za-z0-9_-]+\.js",h).group(0))'
     ```
4. **Ask the user to verify** in the browser. **Only rebuild the image + redeploy when the user
   explicitly asks** — hotloaded files live only in the running pod and are lost on any
   restart/reschedule; the image rebuild is what makes the change persist.

**Full redeploy (only when the user asks):** rebuild the image so `dist/` is baked in.
```bash
docker build -f deploy/docker/control-panel.Dockerfile -t priva/control-panel:dev .   # COPY . /app bakes web/{user,admin}/dist
minikube image rm priva/control-panel:dev      # same :dev tag won't replace under IfNotPresent unless removed first
minikube image load priva/control-panel:dev
kubectl rollout restart deploy/control-panel -n priva-cloud
```
Docker / minikube / SSH steps need the sandbox disabled (`dangerouslyDisableSandbox: true`).
Backend (`services/**`) changes are NOT hotloadable — they always require the image rebuild above.

---

## WebUI design

> Full design spec: `web/design-spec.md`. This file is the executable summary.
> Read this before working on any component.
> always use askuserqestion to confirm the desgin/layout/style with ASCII Art/ASCII Box Drawing from user before working on any component
---

### Tech Stack

| Purpose | Technology |
|---------|------------|
| Framework | React 18 + Vite |
| Styling | Tailwind (locked config) + CSS variables |
| State management | Zustand |
| Icons | lucide-react |
| Markdown rendering | react-markdown + remark-gfm + rehype-highlight |
| Code highlight theme | highlight.js `github-dark` |

---

### Design Language

**Style:** GitHub Dark Default palette · system UI stack (SF Pro Text / system-ui / CJK system fonts) / JetBrains Mono (code) · Vercel×Linear industrial minimalism
**Metaphor:** A living, hardcover technical manual. Serious, precise, in control.  
**Never:** AI-slop aesthetics — no purple gradients, no glassmorphism, no rounded-full buttons.

---

### Color System

Only use these CSS variables. Never hardcode hex values. Never use Tailwind color classes.

```css
/* Background layers */
--bg-base:       #0d1117;
--bg-surface:    #161b22;
--bg-elevated:   #21262d;
--bg-overlay:    #0d1117e6;

/* Borders */
--border-subtle: #21262d;
--border:        #30363d;
--border-strong: #484f58;

/* Text */
--text-primary:   #e6edf3;
--text-secondary: #8b949e;
--text-dim:       #484f58;
--text-inverse:   #0d1117;

/* Semantic colors */
--blue:   #58a6ff;   /* primary actions, links, active */
--green:  #3fb950;   /* success, running, online */
--yellow: #d29922;   /* warning, pending, slow */
--red:    #f85149;   /* error, failed, critical */
--purple: #bc8cff;   /* agent thinking, AI-related */
--cyan:   #79c0ff;   /* tool calls, data, params */
--orange: #ffa657;   /* important notice, below red */

/* Status left-border colors */
--status-running: var(--purple);
--status-success: var(--green);
--status-error:   var(--red);
--status-pending: var(--yellow);
--status-idle:    var(--border);
```

---

### Typography

**UI font:** `var(--font-ui)` uses the operating system UI stack (`SF Pro Text`, `-apple-system`, `Segoe UI`, `system-ui`, `PingFang SC`, `Microsoft YaHei`, then local `Noto Sans`). **Code font:** `var(--font-code)` uses local JetBrains Mono with Source Han Mono SC for CJK. **Terminal font:** `var(--font-terminal)` uses the local Nerd Font build. Do not bundle SF Pro, PingFang, Segoe UI, or Microsoft YaHei.

| Weight | Usage |
|--------|-------|
| 700 | Strong emphasis and code weight where explicitly required |
| 600 | Panel headers, group labels |
| 400 | Body text, descriptions, inputs |
| 300 | Legacy light metadata only; prefer 400 with dim color for new UI |

Font sizes: `xs=11px` `sm=12px` `base=13px` `md=14px` `lg=16px` `xl=20px`

Status chips / labels: ALL CAPS + `letter-spacing: 0.06em`

---

### Tailwind Usage Rules

Tailwind handles **layout only**. Colors, borders, shadows, and radius come from CSS variables.

**ALLOWED Tailwind classes:**
- Layout: `flex` `grid` `items-*` `justify-*` `gap-*` `col-span-*`
- Spacing: `p-*` `px-*` `py-*` `m-*` `mx-*` `my-*`
- Sizing: `w-*` `h-*` `min-w-0` `max-w-*` `flex-1` `flex-shrink-0`
- Text: `text-xs` `text-sm` `text-base` `font-light` `font-normal` `font-semibold` `font-bold` `truncate` `whitespace-nowrap` `break-words` `uppercase`
- Overflow: `overflow-hidden` `overflow-x-auto` `overflow-y-auto`
- Position: `relative` `absolute` `fixed` `sticky` `inset-0` `top-*` `left-*` `right-*` `bottom-*`
- Display: `hidden` `block` `inline-flex` `inline-block`
- Transition: `transition` `duration-150`
- Border radius: `rounded-none` `rounded-sm` `rounded` (= 2px / 4px max)

**FORBIDDEN Tailwind classes:**
- All color classes: `bg-*` `text-*` `border-*` `ring-*` → use CSS variables instead
- `shadow-*` (except `shadow-none`)
- `rounded-lg` `rounded-xl` `rounded-2xl` `rounded-full`
- `outline-*`

**Correct pattern:**
```jsx
// Tailwind for layout, CSS variables for color
<div className="flex items-center gap-2 px-3 py-2 overflow-hidden"
     style={{ color: 'var(--text-primary)', background: 'var(--bg-surface)',
              borderBottom: '1px solid var(--border)' }}>

// NEVER do this
<div className="bg-gray-900 text-blue-400 rounded-xl shadow-lg border-gray-700">
```

---

### Component Rules (Non-negotiable)

#### Layout
- **No horizontal scroll ever.** Every container: `box-sizing: border-box; min-width: 0`
- All text containers: `word-break: break-word; overflow-wrap: break-word`
- No `box-shadow` anywhere. Use background color difference for depth.
- Max `border-radius: 4px`. Approved exception: radio indicators and small circular controls (e.g. ≤18px remove/select buttons) may use `border-radius: 50%`.

#### Status Indicators
Always use a **2px left border** for status, never dots or colored backgrounds.
```css
border-left: 2px solid var(--status-running); /* purple */
border-left: 2px solid var(--status-success); /* green */
border-left: 2px solid var(--status-error);   /* red */
border-left: 2px solid var(--status-pending); /* yellow */
```

#### Icons (lucide-react)
- `strokeWidth={1.5}` on every icon, no exceptions.
- Size: navigation=16px, action buttons=14px, status=12px
- Color: always `currentColor`, never hardcoded.
```jsx
import { Check, Copy, Loader, ChevronRight } from 'lucide-react'
<Check size={14} strokeWidth={1.5} />
```

#### Loading States
- **Skeleton screen** for initial data load. Shape must match real content layout.
- No spinners. No opacity pulse. Shimmer only.
```css
@keyframes skeleton-shimmer {
  0%   { background-position: -400px 0; }
  100% { background-position:  400px 0; }
}
.skeleton {
  border-radius: 2px;
  background: linear-gradient(90deg,
    var(--bg-elevated) 25%,
    var(--bg-surface)  50%,
    var(--bg-elevated) 75%
  );
  background-size: 800px 100%;
  animation: skeleton-shimmer 1.4s ease infinite;
}
```
State flow: `skeleton` → `loaded`. No intermediate states.

#### Copy Interaction
- Trigger: hover over code block / JSON / log content
- Show: `Copy` icon top-right (lucide, 14px, strokeWidth 1.5), hidden by default
- On click: copy → icon changes to `Check` (green) → revert after 800ms
- No toast. The icon IS the feedback.
```jsx
function CopyButton({ content }) {
  const [copied, setCopied] = useState(false)
  return (
    <button className="copy-btn" onClick={() => {
      navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 800)
    }} style={{ color: copied ? 'var(--green)' : 'var(--text-dim)',
                transition: 'color 150ms ease' }}>
      {copied ? <Check size={14} strokeWidth={1.5} /> : <Copy size={14} strokeWidth={1.5} />}
    </button>
  )
}
/* CSS: .copyable:hover .copy-btn { opacity: 1 } */
/* CSS: .copy-btn { opacity: 0; transition: opacity 150ms ease } */
```

#### Animations
```
Hover states:        150ms ease
Panels / modals:     200ms cubic-bezier(0.16, 1, 0.3, 1)
Sidebar / canvas:    220ms cubic-bezier(0.16, 1, 0.3, 1)
```
No continuous animations except: skeleton shimmer, running icon spin, minimized canvas pulse, and the System Map's live byte-path edges, which animate a constant particle flow while the path is healthy, freeze and show an ✕ when the path is unreachable, and are fully disabled under `prefers-reduced-motion`.

#### Modals & Drawers
- Overlay: `var(--bg-overlay)` with backdrop-filter blur
- Confirm dialogs: center scale-in, 200ms spring easing
- Detail drawers: slide in from right (480px wide)
- Danger actions: require typing confirmation text before button activates

#### Dropdowns / Selects
- **NEVER use a native `<select>`.** Its user-agent styling (system arrow, white menu, OS fonts) breaks the design system.
- Use the shared **`Dropdown`** component (`web/shared/components/shared/Dropdown.jsx`) — the canonical select style, derived from the agent UI model selector. `CategoryDropdown` (checkmark style) is legacy; do not use it for new controls.
- **Trigger:** `var(--bg-surface)` + 1px `var(--border)`, 4px radius, chevron-down; hover → `var(--border-strong)` + `var(--text-primary)` (150ms).
- **Menu:** `var(--bg-elevated)`, 1px border, 4px radius, springs in over 200ms `cubic-bezier(0.16, 1, 0.3, 1)` (opacity + 4px translate).
- **Active option:** 2px left border `var(--cyan)` + `var(--bg-surface)` bg + `var(--text-primary)`. Never a checkmark-only or filled-background-only state. Hover (inactive) → `var(--bg-surface)`.
- Props: `options:[{value,label,icon?,disabled?}]` · `value` · `onChange(value)` · `icon` · `align` `'left'|'right'` · `placement` `'bottom'|'top'` · `size` `'sm'|'md'` · `searchable` · `mono`.
```jsx
import Dropdown from '@shared/components/shared/Dropdown'
<Dropdown size="sm" align="right" value={windowSec} onChange={setWindowSec}
  options={[{ value: 60, label: 'last 1m' }, { value: 300, label: 'last 5m' }]} />
```

---

### Sidebar

- Fixed left, always visible. Content area scrolls independently.
- Default: 240px | Drag range: 180px–480px | Collapsed: 48px (icons only)
- Resize handle: 4px wide, transparent → `var(--blue)` on hover/drag
- Collapse: button at bottom or double-click handle
- Width persisted to `localStorage['sidebar-width']`
- Active item: 2px left border `var(--blue)` + `var(--bg-elevated)` bg
- Collapsed active item: border only + tooltip on hover
```css
.sidebar { width: var(--sidebar-width, 240px); transition: width 220ms cubic-bezier(0.16,1,0.3,1); }
.main-content { margin-left: var(--sidebar-width, 240px); transition: margin-left 220ms cubic-bezier(0.16,1,0.3,1); }
```

---

### Canvas Panel (Task Progress)

- Auto-shows when agent tasks start, hides when idle
- Layout: message flow (left, `flex: 1`) + canvas (right, `flex-shrink: 0`)
- Default: 380px | Drag range: 280px–60vw | Minimized: 40px
- Resize handle: left edge, same pattern as sidebar
- Width persisted to `localStorage['canvas-width']`
- Task tree: 16px indent per depth, status left-border on active node
- Active node: `var(--bg-elevated)` bg + `var(--purple)` 2px left border
- Minimized: show `done/total` vertically, pulse when running

---

### Dangerous Operations

Must show confirmation dialog before executing:

| Action | Confirmation type |
|--------|------------------|
| Stop a running task | Dialog + red confirm button |
| Delete history / logs | Dialog + type resource name to confirm |
| Re-trigger a task | Dialog + show current state |

---

### State Management (Zustand)

Canvas and message flow linked via shared `activeTaskId`. Never use DOM or prop drilling.

```js
// src/stores/taskStore.js
import { create } from 'zustand'

const useTaskStore = create((set) => ({
  activeTaskId: null,
  setActiveTaskId: (id) => set({ activeTaskId: id }),
  tasks: {},
  updateTask: (id, data) => set((s) => ({ tasks: { ...s.tasks, [id]: { ...s.tasks[id], ...data } } })),
}))

export default useTaskStore
```

---

### Markdown Rendering

```jsx
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github-dark.css'

// Required global CSS override:
// .hljs { background: var(--bg-elevated) !important; padding: 0 !important; }

<div className="markdown-body overflow-hidden" style={{ wordBreak: 'break-word' }}>
  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}
    components={markdownComponents}>
    {content}
  </ReactMarkdown>
</div>
```

All markdown elements overridden via `components`. No browser UA defaults, no white backgrounds.
Full component map in `design-spec.md §七`.

---

### Pre-submission Checklist

- [ ] Shrink browser window — zero horizontal scrollbar
- [ ] All colors from CSS variables — no hardcoded hex
- [ ] No `box-shadow` used anywhere
- [ ] Font is `var(--font-ui)` for UI, `var(--font-code)` for code, and `var(--font-terminal)` for terminal surfaces
- [ ] All Lucide icons have `strokeWidth={1.5}`
- [ ] Hover states have 150ms transition
- [ ] Loading uses skeleton shimmer, not spinner
- [ ] Skeleton shape matches real content layout
- [ ] Copy button: appears on hover, Check icon on click, reverts after 800ms
- [ ] Status shown via 2px left border, not dots
- [ ] No native `<select>` — dropdowns use the shared `Dropdown` component
- [ ] Dangerous actions have confirmation dialog
