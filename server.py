import hmac
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import analyzer
import config
import notify
import risk
import sessions
import sources
import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
STATIC = config.BASE / "static"


@asynccontextmanager
async def lifespan(app):
    storage.init()
    sch = None
    if os.getenv("DISABLE_SCHEDULER") != "1":
        sch = agent.start_scheduler()
    yield
    if sch:
        sch.shutdown(wait=False)


app = FastAPI(title="Mercado Agent", lifespan=lifespan)


def auth(x_token: str = Header(default=""), token: str = Query(default="")):
    if config.API_TOKEN and not hmac.compare_digest(x_token or token, config.API_TOKEN):
        raise HTTPException(401, "token inválido")


class RiskIn(BaseModel):
    balance: float
    risk_pct: float = 1.0
    asset: str = "XAUUSD"
    entry: float
    stop: float
    contract: float | None = None


class JournalIn(BaseModel):
    asset: str
    side: str
    entry: float
    stop: float | None = None
    exit: float | None = None
    risk_usd: float | None = None
    pnl_usd: float | None = None
    notes: str = ""


class AskIn(BaseModel):
    question: str


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/health", dependencies=[Depends(auth)])
def health():
    return {
        "last_run": agent.STATE["last"], "fails": agent.STATE["fails"], "errors": agent.STATE["err"],
        "ntfy_configured": bool(config.NTFY_TOPIC), "anthropic_configured": bool(config.ANTHROPIC_API_KEY),
        "snapshots": {a: s["age_min"] for a, s in agent.STATE["snapshots"].items()},
    }


@app.get("/api/alerts", dependencies=[Depends(auth)])
def alerts(asset: str = "ALL", min_sev: int = 1, limit: int = 60):
    return storage.list_alerts(min(limit, 200), asset, min_sev)


@app.get("/api/market", dependencies=[Depends(auth)])
def market():
    now = time.time()
    return {
        "snapshots": agent.STATE["snapshots"],
        "calendar": [e for e in agent.STATE["calendar"] if now - 3600 < e["ts"] < now + 172800],
        "fear_greed": sources.fetch_fear_greed(),
    }


@app.get("/api/sessions", dependencies=[Depends(auth)])
def get_sessions():
    return sessions.status()


@app.post("/api/risk", dependencies=[Depends(auth)])
def calc_risk(r: RiskIn):
    if r.asset not in risk.CONTRACT:
        raise HTTPException(400, "activo desconocido")
    try:
        return risk.position_size(r.balance, r.risk_pct, r.entry, r.stop, r.asset, r.contract)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/journal", dependencies=[Depends(auth)])
def journal():
    return {"stats": storage.journal_stats(), "trades": storage.journal_list(40)}


@app.post("/api/journal", dependencies=[Depends(auth)])
def journal_add(j: JournalIn):
    if j.side not in ("largo", "corto") or j.asset not in config.ASSETS:
        raise HTTPException(400, "activo o lado inválido")
    return {"id": storage.journal_add(j.model_dump())}


@app.post("/api/ask", dependencies=[Depends(auth)])
def ask(a: AskIn):
    q = a.question.strip()
    if not q or len(q) > 1500:
        raise HTTPException(400, "pregunta vacía o demasiado larga")
    try:
        return {"answer": analyzer.ask(q, agent.context_text())}
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/api/briefing", dependencies=[Depends(auth)])
def briefing_now():
    try:
        return {"answer": analyzer.briefing(agent.context_text())}
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/api/test-push", dependencies=[Depends(auth)])
def test_push():
    ok = notify.push("✅ Mercado Agent", "Notificación de prueba: todo conectado.", priority=3, tags=["white_check_mark"])
    return JSONResponse({"sent": ok}, status_code=200 if ok else 502)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
