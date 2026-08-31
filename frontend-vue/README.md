# BoundlessKG — frontend-vue

Vue 3 + Vite SPA that talks to the BoundlessKG FastAPI backend
(defined in `../src/`).

## Development

```bash
cd frontend-vue
npm install
npm run dev
# open http://localhost:5175
# Vite proxies /api/* to http://localhost:8000 (the FastAPI server)
```

## Production build

```bash
npm run build
# output → dist/  (consumed by the Dockerfile)
```

The `dist/` output is mounted into the production container at
`/app/frontend-vue/dist/` and served as static files by FastAPI.

## Layout

```
src/
├── api.ts            # fetch wrapper + typed response shapes
├── router.ts         # vue-router routes
├── styles.css        # design tokens + utility classes
├── App.vue           # sidebar + <RouterView>
├── views/
│   ├── DomainList.vue
│   ├── DomainView.vue
│   ├── NotesView.vue
│   ├── PlansView.vue
│   ├── ResourcesView.vue
│   ├── TimelineView.vue
│   └── PipelineView.vue
└── components/
    ├── ChatPanel.vue
    └── GraphCanvas.vue
```

The frontend never touches the filesystem or LLM directly — every
state-changing action goes through `/api/*`. This keeps the browser
sandbox clean and matches the backend's "domain objects in, JSON out"
contract.