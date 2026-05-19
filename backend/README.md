# arclap-station (backend)

Python 3.11 / FastAPI / SQLite on-device control plane for the Arclap Station.

## Quickstart (dev)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # on Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
ruff check .
mypy --strict arclap_station
pytest -v
arclap-station serve --host 127.0.0.1 --port 8080
```

`python-gphoto2` is gated behind the `gphoto` extra. On dev machines without
libgphoto2 the runtime falls back to the mock adapter automatically.
