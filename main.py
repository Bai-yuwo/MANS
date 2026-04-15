import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "frontend.web_app:app",
        host="127.0.0.1",
        port=666,
        reload=True
    )

