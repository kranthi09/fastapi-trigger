from fastapi import FastAPI
import subprocess

app = FastAPI()

@app.post("/run-validation")
def run_script():
    subprocess.Popen(["python3", "autoencoders_validation.py"])
    return {"status": "started"}
