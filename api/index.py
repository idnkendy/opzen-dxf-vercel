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

ACCENT_MAP = {
    'à': 'a', 'á': 'a', 'ả': 'a', 'ã': 'a', 'ạ': 'a',
    'ă': 'a', 'ằ': 'a', 'ắ': 'a', 'ẳ': 'a', 'ẵ': 'a', 'ặ': 'a',
    'â': 'a', 'ầ': 'a', 'ấ': 'a', 'ẩ': 'a', 'ẫ': 'a', 'ậ': 'a',
    'è': 'e', 'é': 'e', 'ẻ': 'e', 'ẽ': 'e', 'ẹ': 'e',
    'ê': 'e', 'ề': 'e', 'ế': 'e', 'ể': 'e', 'ễ': 'e', 'ệ': 'e',
    'ì': 'i', 'í': 'i', 'ỉ': 'i', 'ĩ': 'i', 'ị': 'i',
    'ò': 'o', 'ó': 'o', 'ỏ': 'o', 'õ': 'o', 'ọ': 'o',
    'ô': 'o', 'ồ': 'o', 'ố': 'o', 'ổ': 'o', 'ỗ': 'o', 'ộ': 'o',
    'ơ': 'o', 'ờ': 'o', 'ớ': 'o', 'ở': 'o', 'ỡ': 'o', 'ợ': 'o',
    'ù': 'u', 'ú': 'u', 'ủ': 'u', 'ũ': 'u', 'ụ': 'u',
    'ư': 'u', 'ừ': 'u', 'ứ': 'u', 'ử': 'u', 'ữ': 'u', 'ự': 'u',
    'ỳ': 'y', 'ý': 'y', 'ỷ': 'y', 'ỹ': 'y', 'ỵ': 'y',
    'À': 'A', 'Á': 'A', 'Ả': 'A', 'Ã': 'A', 'Ạ': 'A',
    'Ă': 'A', 'Ằ': 'A', 'Ắ': 'A', 'Ẳ': 'A', 'Ẵ': 'A', 'Ặ': 'A',
    'Â': 'A', 'Ầ': 'A', 'Ấ': 'A', 'Ẩ': 'A', 'Ẫ': 'A', 'Ậ': 'A',
    'È': 'E', 'É': 'E', 'Ẻ': 'E', 'Ẽ': 'E', 'Ẹ': 'E',
    'Ê': 'E', 'Ề': 'E', 'Ế': 'E', 'Ể': 'E', 'Ễ': 'E', 'Ệ': 'E',
    'Ì': 'I', 'Í': 'I', 'Ỉ': 'I', 'Ĩ': 'I', 'Ị': 'I',
    'Ò': 'O', 'Ó': 'O', 'Ỏ': 'O', 'Õ': 'O', 'Ọ': 'O',
    'Ô': 'O', 'Ồ': 'O', 'Ố': 'O', 'Ổ': 'O', 'Ỗ': 'O', 'Ộ': 'O',
    'Ơ': 'O', 'Ờ': 'O', 'Ớ': 'O', 'Ở': 'O', 'Ỡ': 'O', 'Ợ': 'O',
    'Ù': 'U', 'Ú': 'U', 'Ủ': 'U', 'Ũ': 'U', 'Ụ': 'U',
    'Ư': 'U', 'Ừ': 'U', 'Ứ': 'U', 'Ử': 'U', 'Ữ': 'U', 'Ự': 'U',
    'Ỳ': 'Y', 'Ý': 'Y', 'Ỷ': 'Y', 'Ỹ': 'Y', 'Ỵ': 'Y',
    'đ': 'd', 'Đ': 'D'
}

def remove_vietnamese_accents(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    text = text.replace('²', '2').replace('³', '3')
    pattern = re.compile("|".join(re.escape(k) for k in ACCENT_MAP.keys()))
    res = pattern.sub(lambda m: ACCENT_MAP[m.group(0)], text)
    nfkd = unicodedata.normalize('NFKD', res)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))

def sanitize_dxf_for_vietnamese(dxf_content: str) -> str:
    if not dxf_content:
        return dxf_content

    result = re.sub(
        r'(2\r?\n[sS]tandard\r?\n[\s\S]*?3\r?\n)(?:txt(?:\.shx)?|simplex(?:\.shx)?)',
        r'\g<1>Arial.ttf',
        dxf_content
    )
    result = re.sub(
        r'(0\r?\nSTYLE\r?\n[\s\S]*?3\r?\n)(?:txt(?:\.shx)?|simplex(?:\.shx)?)',
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
            lines[i + 1] = remove_vietnamese_accents(val)

    return '\n'.join(lines)

PYTHON_VIETNAMESE_PREAMBLE = '''# -*- coding: utf-8 -*-
import sys
import os
import unicodedata
import re

# Đảm bảo tiến trình con luôn tìm thấy ezdxf
for _p in ['/var/task/_vendor', '/var/task']:
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

def _remove_vietnamese_accents(text):
    if not isinstance(text, str):
        return text
    text = text.replace('²', '2').replace('³', '3')
    text = text.replace('đ', 'd').replace('Đ', 'D')
    nfkd = unicodedata.normalize('NFKD', text)
    res = []
    for c in nfkd:
        if not unicodedata.combining(c):
            if c in ('đ', '₫'):
                res.append('d')
            elif c == 'Đ':
                res.append('D')
            else:
                res.append(c)
    cleaned = ''.join(res)
    def repl_u(m):
        try:
            ch = chr(int(m.group(1), 16))
            nfkd_ch = unicodedata.normalize('NFKD', ch)
            c_clean = ''.join([x for x in nfkd_ch if not unicodedata.combining(x)])
            return 'd' if c_clean == 'đ' else ('D' if c_clean == 'Đ' else c_clean)
        except Exception:
            return ''
    cleaned = re.sub(r'[\\]+[uU]\+([0-9A-Fa-f]{4})', repl_u, cleaned)
    return cleaned

try:
    import ezdxf
    from ezdxf.document import Drawing

    _orig_saveas = Drawing.saveas
    def _safe_vietnamese_saveas(self, *args, **kwargs):
        try:
            if "Standard" in self.styles:
                self.styles.get("Standard").dxf.font = "Arial.ttf"
            for s in self.styles:
                f = str(s.dxf.get("font", "")).lower()
                if not f or f.startswith("txt") or f.startswith("simplex"):
                    s.dxf.font = "Arial.ttf"
            if "VIETNAMESE" not in self.styles:
                try:
                    self.styles.new("VIETNAMESE", dxfattribs={"font": "Arial.ttf"})
                except Exception:
                    pass
            try:
                for entity in self.modelspace():
                    dxftype = entity.dxftype()
                    if dxftype == "TEXT":
                        entity.dxf.text = _remove_vietnamese_accents(entity.dxf.text)
                    elif dxftype == "MTEXT":
                        entity.text = _remove_vietnamese_accents(entity.text)
            except Exception:
                pass
        except Exception:
            pass
        return _orig_saveas(self, *args, **kwargs)

    Drawing.saveas = _safe_vietnamese_saveas

    _orig_new = ezdxf.new
    def _safe_ezdxf_new(dxfversion="R2010", setup=True, **kwargs):
        if isinstance(dxfversion, str) and dxfversion.upper() in ["R12", "AC1009"]:
            dxfversion = "R2010"
        return _orig_new(dxfversion=dxfversion, setup=setup, **kwargs)
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
        # Bắt buộc truyền các thư mục vendor của Vercel vào PYTHONPATH cho tiến trình con
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
