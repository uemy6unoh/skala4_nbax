# nbax_run.py
# 실행하면 에이전트가 로컬에서 순서대로 돌고, 결과가 nbax_data.js 로 저장된 뒤
# 홈페이지(web/nbax_index.html)가 브라우저로 자동으로 열립니다. 서버 없음 - 순수 로컬 실행.
#
# 사용법 (crewai가 설치된 venv 사용):
#   python src/nbax_run.py                       # 기본: 한식 + 장보기 2경로
#   python src/nbax_run.py --cuisine 중식 양식     # 원하는 요리만
#   python src/nbax_run.py --all                  # 한식/중식/양식/일식 전부
#   python src/nbax_run.py --retailer kurly       # 컬리 장보기 링크 생성
#   python src/nbax_run.py --no-shopping          # 장보기 생략 (비용 절약)
#
# 파이프라인 (A는 1회만 계산해 재사용 -> 토큰 비용 절약):
#   Agent1 냉장고 관리사 -> A
#   Agent2 요리사(선택된 타입마다) : A -> B
#   Agent3 레시피 교차검증자 : A+B -> 통과/보완허용/탈락
#   Agent4 장보기 관리사 : A -> 바로 장보기 / A+B -> 레시피 장보기

import argparse
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
WEB_DIR = PROJECT_ROOT / "web"
INDEX = WEB_DIR / "nbax_index.html"
DATA_JS = WEB_DIR / "nbax_data.js"


def main():
    parser = argparse.ArgumentParser(description="nbax 냉장고 에이전트 데모")
    parser.add_argument("--cuisine", nargs="+", default=["한식"],
                        choices=["한식", "중식", "양식", "일식"],
                        help="레시피를 생성할 요리 타입 (복수 선택 가능)")
    parser.add_argument("--all", action="store_true", help="4가지 요리 전부 생성")
    parser.add_argument("--retailer", nargs="+", default=["coupang"],
                        choices=["coupang", "kurly"],
                        help="장보기 플랫폼 (복수 선택 가능)")
    parser.add_argument("--no-shopping", action="store_true", help="장보기 단계 생략")
    parser.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 생략")
    args = parser.parse_args()

    import nbax_agents as agents  # .env 검증이 import 시점에 수행됨

    cuisines = agents.CUISINES if args.all else list(dict.fromkeys(args.cuisine))
    retailers = list(dict.fromkeys(args.retailer))

    shopping_steps = len(retailers) * (1 + len(cuisines))
    total = 1 + len(cuisines) + (0 if args.no_shopping else shopping_steps)
    step = 0

    def log(msg):
        nonlocal step
        step += 1
        print(f"\n{'='*60}\n[{step}/{total}] {msg}\n{'='*60}")

    # ---- Agent 1: 재고 리포트 A (1회만) ----
    log("Agent 1 · 냉장고 관리사 → 냉장고 브리핑")
    fridge_report = agents.run_fridge_report()

    # ---- Agent 2: 요리사 -> 레시피 B ----
    recipes = {}
    for c in cuisines:
        log(f"Agent 2 · {c} 요리사 → Agent 3 · 레시피 교차검증")
        recipes[c] = agents.run_recipe(c, fridge_report)

    # ---- Agent 4: 장보기 관리사 ----
    shopping = {}
    if not args.no_shopping:
        for retailer in retailers:
            label = agents.RETAILERS[retailer]["label"]
            log(f"Agent 4 · 장보기 관리사 → 바로 장보기 ({label} · 현 재고 보충)")
            shopping[f"direct:{retailer}"] = agents.run_shopping(
                "direct", fridge_report, retailer=retailer
            )
            for c in cuisines:
                if recipes[c].strip().startswith(agents.NO_RECIPE):
                    print(f"({c}: 적합한 레시피가 없어 요리 후 장보기 생략)")
                    continue
                log(f"Agent 4 · 장보기 관리사 → 요리 후 장보기 ({label} · 소진 재료 보충·{c})")
                shopping[f"recipe:{c}:{retailer}"] = agents.run_shopping(
                    "recipe", fridge_report, recipes[c], retailer=retailer
                )

    # ---- 결과 데이터 저장 (기존 nbax_data.js가 있으면 요리별 결과는 병합) ----
    old = _load_old_data(agents.PIPELINE_VERSION)
    if old:
        recipes = {**old.get("recipes", {}), **recipes}
        shopping = {**old.get("shopping", {}), **shopping}
    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": agents.model_name,
        "pipeline_version": agents.PIPELINE_VERSION,
        "csv": agents.CSV_PATH.read_text(encoding="utf-8"),
        "fridge_report": fridge_report,
        "recipes": recipes,
        "shopping": shopping,
    }
    payload = json.dumps(data, ensure_ascii=False, indent=2).replace("</", "<\\/")
    DATA_JS.write_text(f"// nbax_run.py가 생성한 결과 데이터\nwindow.NBAX_DATA = {payload};\n",
                       encoding="utf-8")

    print(f"\n완료! 데이터: {DATA_JS}\n홈페이지: {INDEX}")
    if not args.no_open:
        webbrowser.open(INDEX.as_uri())


def _load_old_data(pipeline_version: str) -> dict | None:
    """이전 실행의 nbax_data.js에서 데이터를 읽어 병합용으로 반환 (없으면 None)"""
    if not DATA_JS.exists():
        return None
    try:
        text = DATA_JS.read_text(encoding="utf-8")
        start = text.index("{")
        end = text.rindex("}")
        data = json.loads(text[start:end + 1].replace("<\\/", "</"))
        if data.get("pipeline_version") != pipeline_version:
            return None
        return data
    except (ValueError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    sys.exit(main())
