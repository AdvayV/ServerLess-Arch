"""Web interfaces and JSON APIs for the virtual-time simulations."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from corrections import compare as compare_corrections
from reassessment import compare as compare_reassessments
from simulator import compare


app = FastAPI(title="Serverless Simulations", description="Virtual-time caching and scheduling comparisons")
ROOT = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class SimulationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunOptions(SimulationOptions):
    scenario: StrictStr = "shift"
    budget_mib: StrictInt = 512


class CorrectionOptions(SimulationOptions):
    scenario: StrictStr = "single"


class ReassessmentOptions(SimulationOptions):
    scenario: StrictStr = "amendments"
    worker_count: StrictInt = Field(default=2, ge=1, le=32)
    cache_capacity: StrictInt = Field(default=12, ge=0, le=4096)


@app.get("/")
def home():
    return FileResponse(ROOT / "INDEX.html")


@app.get("/corrections")
def corrections_home():
    return FileResponse(ROOT / "CORRECTIONS.html")


@app.get("/reassessments")
def reassessments_home():
    return FileResponse(ROOT / "REASSESSMENTS.html")


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


@app.post("/api/reassessments")
def run_reassessments(options: ReassessmentOptions):
    try:
        return compare_reassessments(options.scenario, options.worker_count, options.cache_capacity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
