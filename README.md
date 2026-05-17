# LRU Cache / Buffer Pool (DBMS Project)

Models a **buffer pool** with **LRU / FIFO / Optimal** replacement, **MongoDB** as simulated disk + persistence, and a web UI.

## Is this OK for a DB connectivity project?

**Yes.** The app demonstrates:

- Connecting to a database server (**MongoDB** via **PyMongo**)
- CRUD on collections (`disk_pages`, `workloads`, `simulation_runs`)
- A clear **data path**: Browser → FastAPI → MongoDB
- Optional **DB-backed** mode where buffer misses read documents and evictions write them back

For a strict **relational / SQL** course requirement, SQLite + SQLAlchemy is also a valid choice; this repo uses **MongoDB** for document-store connectivity.

## Concepts

| Idea | In this code |
|------|----------------|
| Page | Integer `page_id` in `disk_pages` collection |
| Frame | Slot in the in-memory pool |
| Replacement | **LRU**, **FIFO (FCFS)**, or **Optimal** |
| Disk | MongoDB collection `disk_pages` |
| History | `simulation_runs` documents (with embedded `steps`) |

## Database connectivity (MongoDB)

| Layer | File | Role |
|-------|------|------|
| Connection | `db/mongo_connection.py` | `MONGODB_URI`, `MONGODB_DB`, indexes, ID counters |
| Disk I/O | `disk_manager.py` | `find_one` / `update_one` on `disk_pages` |
| Buffer + disk | `db_backed_pool.py` | Miss → read doc; evict dirty → write doc |
| Persistence | `repository.py` | Workloads, runs, step traces |

Default: `mongodb://127.0.0.1:27017`, database `buffer_lab`.

## Prerequisites

1. **MongoDB** running locally ([MongoDB Community](https://www.mongodb.com/try/download/community))  
   Or use **MongoDB Atlas** and set `MONGODB_URI` to your cluster connection string.

## Run

```bash
cd lru-cache-dbms
pip install -r requirements.txt
python server.py
```

Open http://127.0.0.1:8765/

### Environment

```bash
set MONGODB_URI=mongodb://127.0.0.1:27017
set MONGODB_DB=buffer_lab
```

## API (selected)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/db/status` | Mongo connection + collection counts |
| GET | `/api/disk/pages` | List disk pages |
| POST | `/api/disk/seed` | Insert missing pages |
| GET | `/api/runs` | Simulation history |
| DELETE | `/api/runs` | Delete all saved runs (clear history) |
| POST | `/api/simulate` | Run (`policy`: `lru` \| `fifo` \| `optimal`) |
| POST | `/api/simulate/compare` | Compare all three policies |

## Files

- `buffer_pool.py`, `page_replacement.py` — in-memory algorithms
- `db/mongo_connection.py` — MongoDB client
- `disk_manager.py`, `repository.py` — data access
- `server.py` — API + static UI
- `simulate.py` — CLI (memory-only trace)
