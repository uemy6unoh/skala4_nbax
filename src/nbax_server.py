# nbax_server.py
# 로컬 서버 모드: 실행하면 홈페이지가 뜨고, 웹에서 요리 타입을 선택하면
# 그 시점에 에이전트가 실제로 돌아 레시피/장보기를 생성합니다.
# 외부 배포가 아니라 내 맥에서만 도는 파이썬 표준 라이브러리 서버입니다 (추가 설치 없음).
#
# 실행:
#   python src/nbax_server.py
#   python src/nbax_server.py --fresh   # 저장 결과를 비우고 Agent 1부터 재실행
#   -> http://localhost:8777 자동 오픈 (종료: Ctrl+C)
#
# 비용 절약:
#   - 재고 리포트 A는 1회만 계산 (CSV가 바뀌면 다시 계산)
#   - 생성한 레시피/장보기는 nbax_data.js에 저장 -> 다음 클릭·다음 실행에서 재사용
#     (nbax_run.py로 미리 만들어둔 결과도 그대로 인식)

import argparse
import json
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import nbax_agents as agents

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
WEB_DIR = PROJECT_ROOT / "web"
DATA_JS = WEB_DIR / "nbax_data.js"
PORT = 8777

STATIC_FILES = {
    "/": (WEB_DIR / "nbax_index.html", "text/html; charset=utf-8"),
    "/nbax_index.html": (WEB_DIR / "nbax_index.html", "text/html; charset=utf-8"),
    "/nbax_style.css": (WEB_DIR / "nbax_style.css", "text/css; charset=utf-8"),
    "/nbax_app.js": (WEB_DIR / "nbax_app.js", "text/javascript; charset=utf-8"),
    "/nbax_data.js": (DATA_JS, "text/javascript; charset=utf-8"),
    "/nbax_logo.png": (WEB_DIR / "nbax_logo.png", "image/png"),
    "/nbax_fridge.csv": (agents.CSV_PATH, "text/csv; charset=utf-8"),
}

# ------------------------------------------------------------
# 결과 캐시 (메모리 + nbax_data.js 영속화)
# ------------------------------------------------------------
_lock = threading.Lock()  # 에이전트 실행 직렬화
_state = {"fridge": None, "recipes": {}, "shopping": {}}


def _current_csv() -> str:
    return agents.CSV_PATH.read_text(encoding="utf-8")


def _load_saved():
    """이전 실행(nbax_run.py 포함)의 nbax_data.js를 캐시로 불러온다.
    CSV 내용이 바뀌었으면 무효 처리."""
    if not DATA_JS.exists():
        return
    try:
        text = DATA_JS.read_text(encoding="utf-8")
        d = json.loads(text[text.index("{"): text.rindex("}") + 1].replace("<\\/", "</"))
    except (ValueError, json.JSONDecodeError):
        return
    if (
        d.get("csv") != _current_csv()
        or d.get("pipeline_version") != agents.PIPELINE_VERSION
    ):
        print("[cache] CSV 또는 파이프라인이 변경되어 이전 결과를 무시합니다.")
        return
    _state["fridge"] = d.get("fridge_report")
    _state["recipes"] = d.get("recipes", {})
    _state["shopping"] = d.get("shopping", {})
    print(f"[cache] 이전 결과 로드: 요리 {list(_state['recipes'])}, "
          f"장보기 {list(_state['shopping'])}")


def _save():
    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": agents.model_name,
        "pipeline_version": agents.PIPELINE_VERSION,
        "csv": _current_csv(),
        "fridge_report": _state["fridge"],
        "recipes": _state["recipes"],
        "shopping": _state["shopping"],
    }
    payload = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    DATA_JS.write_text(
        f"// nbax_server.py가 생성한 결과 데이터\nwindow.NBAX_DATA = {payload};\n",
        encoding="utf-8",
    )


def _reset_cache():
    """메모리와 nbax_data.js의 생성 결과를 비워 다음 요청부터 다시 실행한다."""
    _state["fridge"] = None
    _state["recipes"] = {}
    _state["shopping"] = {}
    _save()
    print("[cache] 저장 결과를 비웠습니다. Agent 1부터 새로 실행합니다.")


# ---- 에이전트 실행 (전부 _lock 안에서 호출됨: 동시 실행 방지) ----
def _fridge() -> str:
    if _state["fridge"] is None:
        print("\n[agent] 냉장고 관리사 실행 → 냉장고 브리핑")
        _state["fridge"] = agents.run_fridge_report()
        _save()
    return _state["fridge"]


def _recipe(cuisine: str) -> tuple[str, bool]:
    cached = cuisine in _state["recipes"]
    if not cached:
        fridge_report = _fridge()
        print(f"\n[agent] {cuisine} 요리사 실행 → 레시피 (교차검증 포함)")
        out = agents.run_recipe(cuisine, fridge_report)
        # '적합한 레시피 없음'은 캐시하지 않음 -> 다시 클릭하면 재시도 가능
        if out.strip().startswith(agents.NO_RECIPE):
            return out, False
        _state["recipes"][cuisine] = out
        _save()
    return _state["recipes"][cuisine], cached


def _shopping(mode: str, cuisine: str | None) -> tuple[str, bool]:
    key = "direct" if mode == "direct" else f"recipe:{cuisine}"
    cached = key in _state["shopping"]
    if not cached:
        fridge_report = _fridge()
        if mode == "direct":
            print("\n[agent] 장보기 관리사 실행 → 바로 장보기 (현 재고 보충)")
            _state["shopping"][key] = agents.run_shopping(
                "direct", fridge_report
            )
        else:
            b, _ = _recipe(cuisine)
            if b.strip().startswith(agents.NO_RECIPE):
                raise ValueError(f"{cuisine} 레시피가 없어 요리 후 장보기를 만들 수 없습니다.")
            print(f"\n[agent] 장보기 관리사 실행 → 요리 후 장보기 (소진 재료 보충·{cuisine})")
            _state["shopping"][key] = agents.run_shopping(
                "recipe", fridge_report, b
            )
        _save()
    return _state["shopping"][key], cached


# ------------------------------------------------------------
# HTTP 핸들러
# ------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        request_path = urlsplit(self.path).path
        entry = STATIC_FILES.get(request_path)
        if entry:
            path, ctype = entry
            if path.exists():
                self._send(200, path.read_bytes(), ctype)
            else:
                self._send(404, b"Not Found", "text/plain")
        else:
            self._send(404, b"Not Found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        try:
            with _lock:
                if self.path == "/api/fridge":
                    a = _fridge()
                    self._json({"fridge_report": a, "csv": _current_csv(),
                                "model": agents.model_name})
                elif self.path == "/api/recipe":
                    cuisine = payload.get("cuisine", "")
                    b, cached = _recipe(cuisine)
                    self._json({"cuisine": cuisine, "recipe": b, "cached": cached})
                elif self.path == "/api/shopping":
                    mode = payload.get("mode", "")
                    cuisine = payload.get("cuisine")
                    if mode not in ("direct", "recipe"):
                        raise ValueError('mode는 "direct" 또는 "recipe"여야 합니다.')
                    if mode == "recipe" and cuisine not in agents.CUISINES:
                        raise ValueError(f"cuisine은 {agents.CUISINES} 중 하나여야 합니다.")
                    s, cached = _shopping(mode, cuisine)
                    self._json({"mode": mode, "cuisine": cuisine,
                                "shopping": s, "cached": cached})
                else:
                    self._json({"error": "unknown endpoint"}, 404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # 에이전트 실행 실패 등
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def log_message(self, fmt, *args):
        print(f"[web] {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="nbax 냉장고 에이전트 웹서버")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="저장된 A/B/장보기 결과를 비우고 Agent 1부터 새로 실행",
    )
    args = parser.parse_args()

    if args.fresh or not DATA_JS.exists():
        _reset_cache()
    else:
        _load_saved()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}/?v=6"
    print(f"\n냉장고를 부탁해 AX 서버 시작: {url}  (종료: Ctrl+C)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
        server.shutdown()


if __name__ == "__main__":
    main()
