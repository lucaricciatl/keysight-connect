# web — React front-end for keysight connect

Vite + React + TypeScript UI for the B2901B SMU bench. It talks to the FastAPI
backend in `../server/app.py` over `/api` and `/ws`.

```bash
npm install
npm run dev      # hot reload on :5173, proxying /api and /ws to :8000
npm run build    # writes dist/, which the backend serves
npm run lint
```

See the [repository README](../README.md) for the full setup, the 2-wire /
4-wire sensing options, and the mock instrument used for hardware-free runs.
