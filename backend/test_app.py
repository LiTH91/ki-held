from fastapi import FastAPI
import uvicorn
from datetime import datetime

app = FastAPI()

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": str(datetime.now())}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

