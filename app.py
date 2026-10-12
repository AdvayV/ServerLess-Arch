"""Web interfaces and JSON APIs for both virtual-time simulations."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from corrections import compare as compare_corrections
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
