"""
Opzen AI - DXF Converter Backend (Vercel Serverless Function)
FastAPI app exposed as ASGI handler for Vercel Serverless runtime.
Không bị sleep/cold-start lâu như Render.
"""

import os
import sys
import tempfile
import subprocess
import unicodedata
import re
from typing import Optional, Dict, Any

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    import ezdxf
    EZDXF_AVAILABLE = True
    EZDXF_VERSION = getattr(ezdxf, '__version__', 'unknown')
except Exception:
    EZDXF_AVAILABLE = False
    EZDXF_VERSION = None

def decode_autocad_unicode(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    def repl_u(m):
        try:
            return chr(int(m.group(1), 16))
        except Exception:
            return ''
    decoded = re.sub(r'[\\]+[uU]\+?([0-9A-Fa-f]{4})', repl_u, text)
    return unicodedata.normalize('NFC', decoded)

def sanitize_dxf_for_vietnamese(dxf_content: str) -> str:
    if not dxf_content:
        return dxf_content

    result = re.sub(
        r'(\$ACADVER\r?\n\s*1\r?\n)(?:AC1009|AC1012|AC1014|AC1015|AC1018)\b',
        r'\g<1>AC1024',
        dxf_content
    )
    result = re.sub(
        r'(\$DWGCODEPAGE\r?\n\s*3\r?\n)(?:ANSI_[0-9]+|ASCII|DOS[0-9]+|ISO8859-[0-9]+)\b',
        r'\g<1>UTF-8',
        result,
        flags=re.IGNORECASE
    )
    result = re.sub(
        r'(2\r?\n[sS]tandard\r?\n[\s\S]*?3\r?\n)(?:txt(?:\.shx)?|simplex(?:\.shx)?|romans(?:\.shx)?|monotxt(?:\.shx)?|opensans[^\r\n]*|liberation[^\r\n]*)',
        r'\g<1>Arial.ttf',
        result,
        flags=re.IGNORECASE
    )
    result = re.sub(
        r'(0\r?\nSTYLE\r?\n[\s\S]*?3\r?\n)(?:txt(?:\.shx)?|simplex(?:\.shx)?|romans(?:\.shx)?|monotxt(?:\.shx)?|opensans[^\r\n]*|liberation[^\r\n]*)',
        r'\g<1>Arial.ttf',
        result,
        flags=re.IGNORECASE
    )

    lines = result.splitlines()
    for i in range(len(lines) - 1):
        code = lines[i].strip()
        if code in ('1', '3', '1000'):
            val = lines[i + 1]
            if code == '3' and re.search(r'\.(ttf|shx|fon)$', val, re.IGNORECASE):
                continue
            lines[i + 1] = decode_autocad_unicode(val)

    return '\n'.join(lines)

PYTHON_VIETNAMESE_PREAMBLE = '''# -*- coding: utf-8 -*-
import sys
import os
import unicodedata
import re

def _normalize_vietnamese_unicode(text):
    if not isinstance(text, str):
        return text
    def repl_u(m):
        try:
            return chr(int(m.group(1), 16))
        except Exception:
            return ''
    decoded = re.sub(r'[\\]+[uU]\+?([0-9A-Fa-f]{4})', repl_u, text)
    return unicodedata.normalize('NFC', decoded)

try:
    import ezdxf
    from ezdxf.document import Drawing

    _orig_saveas = Drawing.saveas
    def _safe_vietnamese_saveas(self, *args, **kwargs):
        try:
            self.dxfversion = "AC1024"
            self.encoding = "utf-8"
            if "$ACADVER" in self.header:
                self.header["$ACADVER"] = "AC1024"
            self.header["$DWGCODEPAGE"] = "UTF-8"
            if "Standard" in self.styles:
                self.styles.get("Standard").dxf.font = "Arial.ttf"
            for s in self.styles:
                s.dxf.font = "Arial.ttf"
            if "VIETNAMESE" not in self.styles:
                try:
                    self.styles.new("VIETNAMESE", dxfattribs={"font": "Arial.ttf"})
                except Exception:
                    pass
            try:
                for layout in self.layouts:
                    for entity in layout:
                        dxftype = entity.dxftype()
                        if dxftype in ("TEXT", "ATTRIB", "ATTDEF", "DIMENSION") and entity.dxf.hasattr("text"):
                            entity.dxf.text = _normalize_vietnamese_unicode(entity.dxf.text)
                        elif dxftype == "MTEXT":
                            entity.text = _normalize_vietnamese_unicode(entity.text)
            except Exception:
                pass
        except Exception:
            pass
        kwargs["encoding"] = "utf-8"
        return _orig_saveas(self, *args, **kwargs)

    Drawing.saveas = _safe_vietnamese_saveas

    _orig_new = ezdxf.new
    def _safe_ezdxf_new(dxfversion="R2010", setup=True, **kwargs):
        if isinstance(dxfversion, str) and dxfversion.upper() in ["R12", "AC1009", "R2000", "AC1015", "R2004", "AC1018"]:
            dxfversion = "R2010"
        doc = _orig_new(dxfversion=dxfversion, setup=setup, **kwargs)
        try:
            doc.dxfversion = "AC1024"
            doc.encoding = "utf-8"
            doc.header["$ACADVER"] = "AC1024"
            doc.header["$DWGCODEPAGE"] = "UTF-8"
            for s in doc.styles:
                s.dxf.font = "Arial.ttf"
        except Exception:
            pass
        return doc
    ezdxf.new = _safe_ezdxf_new
except Exception:
    pass
'''

def execute_python_to_dxf(code: str, file_name: str = "mat_bang_autocad.dxf") -> Dict[str, Any]:
    if not code or not isinstance(code, str) or not code.strip():
        return {"success": False, "error": "Mã Python trống hoặc không hợp lệ"}

    with tempfile.TemporaryDirectory(prefix="dxf_vercel_") as tmp_dir:
        script_path = os.path.join(tmp_dir, "generate_dxf.py")
        code_to_run = PYTHON_VIETNAMESE_PREAMBLE + "\n" + code

        if ".saveas(" not in code and "ezdxf" in code:
            fallback_out = file_name or "mat_bang_autocad.dxf"
            code_to_run += f"""

for _var_name in ['doc', 'dwg', 'dxf', 'drawing', 'model']:
    if _var_name in locals() and hasattr(locals()[_var_name], 'saveas'):
        try:
            locals()[_var_name].saveas("{fallback_out}")
            break
        except Exception:
            pass
"""

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code_to_run)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        # Crucial for Vercel Serverless: forward sys.path and Vercel _vendor site-packages
        existing_pythonpath = env.get("PYTHONPATH", "")
        paths_to_add = [
            "/var/task/_vendor",
            "/var/task",
            *sys.path
        ]
        combined_pythonpath = ":".join(filter(None, [p for p in paths_to_add if isinstance(p, str)] + [existing_pythonpath]))
        env["PYTHONPATH"] = combined_pythonpath

        try:
            process = subprocess.run(
                [sys.executable, script_path],
                cwd=tmp_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=25
            )
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Quá thời gian thực thi mã Python (Timeout > 25s)."
            }
        except Exception as run_err:
            return {
                "success": False,
                "error": f"Lỗi thực thi: {str(run_err)}"
            }

        dxf_files = []
        for root, _, files in os.walk(tmp_dir):
            for file in files:
                if file.lower().endswith(".dxf"):
                    dxf_files.append(os.path.join(root, file))

        if dxf_files:
            chosen_path = dxf_files[0]
            if file_name:
                for p in dxf_files:
                    if os.path.basename(p).lower() == file_name.lower():
                        chosen_path = p
                        break

            try:
                with open(chosen_path, "r", encoding="utf-8", errors="replace") as f:
                    raw_content = f.read()

                sanitized = sanitize_dxf_for_vietnamese(raw_content)
                return {
                    "success": True,
                    "dxfContent": sanitized,
                    "fileName": os.path.basename(chosen_path),
                    "lineCount": len(sanitized.splitlines()),
                    "sizeBytes": len(sanitized.encode("utf-8")),
                }
            except Exception as read_err:
                return {
                    "success": False,
                    "error": f"Lỗi đọc DXF: {str(read_err)}"
                }

        error_details = (process.stderr or process.stdout or "Không tìm thấy file .dxf").strip()
        return {
            "success": False,
            "error": error_details or "Script không tạo tệp .dxf"
        }

# FastAPI App for Vercel
app = FastAPI(title="Opzen DXF Converter Vercel API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

class ConvertRequest(BaseModel):
    code: str
    fileName: Optional[str] = "mat_bang_autocad.dxf"

@app.get("/")
@app.get("/api")
@app.get("/api/health")
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "opzen-dxf-vercel-serverless",
        "ezdxf": EZDXF_AVAILABLE,
        "ezdxf_version": EZDXF_VERSION,
        "platform": "Vercel Serverless (Zero cold-start delay)"
    }

@app.post("/convert-dxf")
@app.post("/api/convert-dxf")
async def convert_dxf(req: ConvertRequest):
    result = execute_python_to_dxf(req.code, req.fileName or "mat_bang_autocad.dxf")
    status_code = 200 if result.get("success") else 400
    return JSONResponse(content=result, status_code=status_code)

@app.options("/{path:path}")
async def preflight(path: str):
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )
