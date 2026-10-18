#!/usr/bin/env python3
"""upload-server.py — receive large files onto this Mac.

A minimal FastAPI app that accepts multipart file uploads (no size cap) and
saves them to /Users/admin/uploads/. Purpose: get a >20MB file (e.g. a 500MB
PDF) onto this machine so it can be compressed, since Telegram caps at 20MB.

Run:  python3.11 upload-server.py
Then open http://<this-mac-ip>:8787  (or http://localhost:8787)
"""
import os
import sys
import shutil
import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse

UPLOAD_DIR = "/Users/admin/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="File Upload Server")

INDEX_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Upload</title>
<style>
body{font-family:system-ui;max-width:640px;margin:40px auto;padding:0 20px;background:#111;color:#eee}
h1{font-size:1.4em}
#drop{border:2px dashed #555;border-radius:12px;padding:40px;text-align:center;cursor:pointer}
#drop.drag{background:#1a3a1a;border-color:#4caf50}
#file{display:none}
button{margin-top:16px;padding:10px 24px;font-size:1em;border:none;border-radius:8px;background:#4caf50;color:#fff;cursor:pointer}
#status{margin-top:16px;font-family:monospace;white-space:pre-wrap}
</style></head><body>
<h1>📤 Upload a file</h1>
<div id="drop">Click or drag a file here<br><small>no size limit — saved to /Users/admin/uploads/</small></div>
<input type="file" id="file">
<button onclick="upload()">Upload</button>
<div id="status"></div>
<script>
const drop=document.getElementById('drop'),file=document.getElementById('file'),status=document.getElementById('status');
drop.onclick=()=>file.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('drag')};
drop.ondragleave=()=>drop.classList.remove('drag');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('drag');file.files=e.dataTransfer.files;upload()};
file.onchange=upload;
async function upload(){
  const f=file.files[0]; if(!f)return;
  status.textContent='Uploading '+f.name+' ('+(f.size/1048576).toFixed(1)+' MB)...';
  const fd=new FormData(); fd.append('file',f);
  try{
    const r=await fetch('/upload',{method:'POST',body:fd});
    const j=await r.json();
    status.textContent=r.ok?('✅ '+j.message):('❌ '+j.detail);
  }catch(e){status.textContent='❌ '+e}
}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "no filename")
    # Sanitize the filename to a safe basename.
    name = os.path.basename(file.filename.replace("\\", "/"))
    dest = os.path.join(UPLOAD_DIR, name)
    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
    except Exception as e:
        raise HTTPException(500, f"write failed: {e}")
    return {"message": f"saved {name} ({size/1048576:.1f} MB) to {dest}", "path": dest, "size": size}


if __name__ == "__main__":
    port = int(os.environ.get("UPLOAD_PORT", "8787"))
    host = os.environ.get("UPLOAD_HOST", "0.0.0.0")
    print(f"Upload server on http://{host}:{port}  →  saves to {UPLOAD_DIR}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
