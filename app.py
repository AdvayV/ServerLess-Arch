"""Small web interface for the serverless retention simulator."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from corrections import compare as compare_corrections
from simulator import compare


app = FastAPI(title="College Portal: Warm or Cold?", description="An explainable serverless memory demo")
ROOT = Path(__file__).resolve().parent


class RunOptions(BaseModel):
    scenario: str = "shift"
    budget_mib: int = 512


class CorrectionOptions(BaseModel):
    scenario: str = "single"


@app.get("/")
def home():
    return FileResponse(ROOT / "INDEX.html")


@app.get("/corrections")
def corrections_home():
    return FileResponse(ROOT / "CORRECTIONS.html")


@app.post("/api/run")
def run(options: RunOptions):
    try:
        return compare(options.scenario, options.budget_mib)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/corrections")
def run_corrections(options: CorrectionOptions):
    try:
        return compare_corrections(options.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
