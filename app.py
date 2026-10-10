"""Small web interface for the serverless retention simulator."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from simulator import compare


app = FastAPI(title="College Portal: Warm or Cold?", description="An explainable serverless memory demo")
ROOT = Path(__file__).resolve().parent


class RunOptions(BaseModel):
    scenario: str = "shift"
    budget_mib: int = 512


@app.get("/")
def home():
    return FileResponse(ROOT / "INDEX.html")


@app.post("/api/run")
def run(options: RunOptions):
    try:
        return compare(options.scenario, options.budget_mib)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
