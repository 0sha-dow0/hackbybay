# DepCover Frontend

TypeScript/Vite console for the DepCover backend.

```sh
npm install
npm run dev
```

The dev server proxies `/health`, `/repos`, `/incidents`, and `/transplants` to
`http://127.0.0.1:8000`. Leave the API field empty for same-origin/proxied API
requests, or enter a deployed backend URL.
