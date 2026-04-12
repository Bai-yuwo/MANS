from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()
@app.get("/")
async def root():
    return FileResponse("frontend/index.html")

@app.get("/api/get_projects")
async def get_projects():
    local_projects = []
    # local_projects = [{"id": "001", "name": "为了省电，天道禁止成仙"}]
    return {"projects": local_projects}

app.mount("/", StaticFiles(directory="."), name="static")
