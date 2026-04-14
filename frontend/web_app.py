from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

app = FastAPI()
@app.get("/")
async def root():
    return FileResponse("frontend/index.html")

@app.get("/api/get_projects")
async def get_projects():
    workspace_path = "workspace"
    works_jsons = list(Path(workspace_path).glob("*.json"))
    local_projects = [{"id": "1", "name": "龙与少年"}]
    return {"projects": local_projects}

app.mount("/", StaticFiles(directory="."), name="static")
