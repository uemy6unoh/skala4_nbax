# nbax_agents.py
# 냉장고 관리 CrewAI 에이전트 (practice_2.CrewAI_Agent_System.ipynb 기반)
# - Agent 1: 냉장고 관리사 -> 브리핑 A (임박 재료 + 요리 타입 추천)
# - Agent 2: 요리사(한식/중식/양식/일식) -> 레시피 후보 B 생성
# - Agent 3: 레시피 교차검증자 -> 레시피가 이름·타입에 걸맞은지 판정 (탈락 시 Agent 2가 1회 재시도)
# - Agent 4: 장보기 관리사 -> 보충 장보기 (요리 후 소진 재료 또는 현 재고 기준, ReAct)
#
# 컨텍스트 흐름: Agent 1의 A -> Agent 2의 B -> Agent 3이 A+B 검증
#                -> Agent 4에 A(직접 장보기) 또는 A+B(요리 후 장보기) 전달
# 비용 절약: A는 1회만 계산해 재사용, D-day는 파이썬 전처리로 주입 (LLM 날짜 계산 금지)
# 실행: nbax_server.py (라이브) / nbax_run.py (배치)

import os
import re
import csv
import warnings
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

warnings.filterwarnings("ignore")

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
CSV_PATH = PROJECT_ROOT / "data" / "nbax_fridge.csv"


# ------------------------------------------------------------
# .env 로드 (practice_2와 동일한 방식: 현재/상위 폴더에서 탐색)
# ------------------------------------------------------------
def find_env_file(start: Path) -> Path | None:
    for folder in [start, *start.parents]:
        candidate = folder / ".env"
        if candidate.exists():
            return candidate
    return None


env_path = find_env_file(PROJECT_ROOT)
if env_path is None:
    raise FileNotFoundError(
        ".env 파일을 찾을 수 없습니다. 프로젝트 루트에 .env 파일을 만들고 "
        "OPENAI_API_KEY=... 형식으로 API 키를 저장하세요."
    )

load_dotenv(dotenv_path=env_path, override=False)

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError(".env 파일에 OPENAI_API_KEY가 설정되어 있지 않습니다.")

from crewai import Agent, Crew, LLM, Process, Task, TaskOutput  # noqa: E402

# practice_2와 동일: gpt-4o-mini 사용
model_name = os.getenv("OPENAI_MODEL_NAME", "openai/gpt-4o-mini")
if not model_name.startswith("openai/"):
    model_name = f"openai/{model_name}"

llm = LLM(
    model=model_name,
    api_key=api_key,
    temperature=0.3,
)

CUISINES = ["한식", "중식", "양식", "일식"]
NO_RECIPE = "적합한 레시피 없음"  # 요리사가 검증 통과 후보가 없을 때 첫 줄에 쓰는 고정 문구
PIPELINE_VERSION = "agent-context-v7"

CUISINE_IDENTITY = {
    "한식": "찌개, 전골, 제육, 불고기, 전, 비빔밥",
    "중식": "마파두부, 어향, 깐풍, 고추잡채, 볶음밥, 짬뽕",
    "양식": "파스타, 리조토, 그라탱, 스튜, 오믈렛",
    "일식": "야키소바, 야키우동, 돈부리, 나베, 데리야키, 오코노미야키",
}

# ------------------------------------------------------------
# CSV -> 텍스트 (에이전트 입력용)
# ------------------------------------------------------------
def load_fridge_text() -> str:
    """CSV를 텍스트로 변환. LLM이 날짜 계산을 직접 하지 않도록
    D-day(유통기한까지 남은 일수)를 파이썬에서 미리 계산해 컬럼으로 추가한다."""
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    exp_idx = header.index("유통기한")
    today = date.today()

    def dday(r: list[str]) -> str:
        diff = (date.fromisoformat(r[exp_idx].strip()) - today).days
        if diff < 0:
            return f"D+{-diff} (유통기한 지남)"
        return f"D-{diff}" if diff > 0 else "D-0 (오늘까지)"

    # 유통기한이 가까운 순으로 정렬해 전달 -> 에이전트 출력 순서도 자연히 정렬됨
    body.sort(key=lambda r: r[exp_idx].strip())
    lines = [" | ".join(header + ["D-day"])]
    lines += [" | ".join(r + [dday(r)]) for r in body]
    return "\n".join(lines)


def imminent_ingredients() -> list[str]:
    """오늘 기준 D-0~D-5 재료명을 반환한다."""
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    today = date.today()
    return [
        row["재료명"].strip()
        for row in rows
        if 0 <= (date.fromisoformat(row["유통기한"].strip()) - today).days <= 5
    ]


def _run_single(agent: Agent, task: Task) -> str:
    """에이전트 1명 + Task 1개짜리 크루를 실행하고 결과 텍스트를 반환"""
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)
    result = crew.kickoff()
    return result.raw


def _completed_context_task(label: str, raw: str, agent_role: str) -> Task:
    """이전 요청에서 완료된 결과를 CrewAI context용 Task로 복원한다.

    웹 클릭 사이에는 하나의 Crew를 계속 실행해 둘 수 없으므로 저장된 A/B를
    완료된 TaskOutput으로 연결해 practice_2의 context=[앞 Task] 흐름을 유지한다.
    """
    task = Task(
        description=f"{label}을 생성한 완료 Task",
        expected_output=label,
    )
    task.output = TaskOutput(
        description=task.description,
        expected_output=task.expected_output,
        raw=f"[{label}]\n{raw}",
        agent=agent_role,
    )
    return task


# ------------------------------------------------------------
# Agent 1: 냉장고 관리사 -> 홈 브리핑 A (임박 재료 + 요리 타입 추천)
# ------------------------------------------------------------
def run_fridge_report() -> str:
    agent = Agent(
        role="냉장고 관리사",
        goal="냉장고 CSV를 분석해 유통기한 임박 재료를 경고하고, 이를 가장 잘 소진할 요리 타입을 고른다.",
        backstory=(
            "당신은 가정의 냉장고 재고를 관리하는 전문가입니다. "
            "유통기한이 임박한 재료를 정확히 골라내 경고하고, 그 재료들을 가장 잘 "
            "소진할 수 있는 요리 타입(한식/중식/양식/일식)을 하나 고릅니다. "
            "데이터에 없는 재료를 만들어내지 않습니다. "
            "모든 결과물은 한국어로 작성합니다."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )
    task = Task(
        description=(
            f"오늘 날짜는 {date.today().isoformat()}입니다. 아래는 냉장고 재고입니다.\n\n"
            f"{load_fridge_text()}\n\n"
            "홈 화면용 냉장고 브리핑을 마크다운으로 작성하세요.\n"
            "1. '## ⏰ 마감 임박 재료': D-day가 D-0~D-5인 재료를 '- 재료명 수량+단위 (D-일수)' "
            "형식으로 임박한 순서대로 나열합니다. 없으면 '없음'.\n"
            "2. '## 🚫 폐기 대상': '유통기한 지남' 재료를 나열하고 사용 금지를 한 줄 경고합니다. "
            "없으면 이 섹션은 생략합니다.\n"
            "3. 맨 마지막 줄에만 '추천타입: 한식'처럼 '추천타입: <한식/중식/양식/일식 중 하나>'를 "
            "출력합니다. 추천 사유나 요리 이름, 추천 관련 섹션·문장은 절대 넣지 마세요.\n"
            "D-day는 이미 계산되어 있으니 그대로 쓰고, 전체 재고 목록은 나열하지 마세요."
        ),
        expected_output=(
            "마감 임박 재료와 (있다면) 폐기 대상만 담고, 맨 끝 줄이 '추천타입: X'인 "
            "한국어 마크다운 브리핑 (추천 관련 섹션·문장 없음)"
        ),
        agent=agent,
    )
    return _run_single(agent, task)


# ------------------------------------------------------------
# Agent 2: 요리사 -> 레시피 B
# Agent 3: 레시피 교차검증자 -> 레시피 전체(이름·재료·조리법)를 검증
#   생성자는 자기 결과에 관대하므로 생성과 검증을 분리한다.
#   흐름: 요리사 생성 -> 교차검증 -> 탈락 시 사유 전달 후 1회 재시도 -> 그래도 탈락이면 NO_RECIPE
# ------------------------------------------------------------
def run_recipe(cuisine: str, fridge_report: str) -> str:
    """Agent 1의 결과 A를 컨텍스트로 받아 레시피 B를 생성한다."""
    if cuisine not in CUISINES:
        raise ValueError(f"cuisine은 {CUISINES} 중 하나여야 합니다: {cuisine}")
    if not fridge_report or not fridge_report.strip():
        raise ValueError("Agent 1의 냉장고 브리핑(A)이 필요합니다.")
    feedback = None
    for _ in range(2):  # 최초 시도 + 탈락 시 1회 재시도
        recipe = _cook(cuisine, fridge_report, feedback)
        if recipe.strip().startswith(NO_RECIPE):
            return recipe
        print(f"[Agent 3] {cuisine} 레시피 교차검증 실행")
        ok, reason = _verify_recipe(cuisine, fridge_report, recipe)
        if ok:
            return recipe
        print(f"[Agent 3] 교차검증 탈락:\n{reason}")
        feedback = (
            f"직전 레시피 탈락 사유: {reason}\n"
            f"{cuisine}의 맛·양념·조리 특징과 임박 재료 활용을 보강하세요. "
            "기존 요리를 고치거나 다른 요리를 제안해도 됩니다."
        )
    return (f"{NO_RECIPE}\n\n교차검증을 통과한 {cuisine} 레시피를 만들지 못했습니다. "
            "홈의 추천 타입을 참고해 주세요.")


def _cook(cuisine: str, fridge_report: str, feedback: str | None = None) -> str:
    # 선택한 타입에 따라 요리사 에이전트의 role이 바뀜
    fridge_context = _completed_context_task(
        "앞 단계 결과 A", fridge_report, "냉장고 관리사"
    )
    agent = Agent(
        role=f"{cuisine} 요리사",
        goal=f"임박 재료를 자연스럽게 활용한 {cuisine} 레시피 1개를 제안한다.",
        backstory=(
            f"당신은 20년 경력의 {cuisine} 전문 요리사입니다. "
            "가정식과 퓨전에도 열려 있지만 선택한 요리 타입의 특징은 분명히 살립니다. "
            "냉장고 재고 안에서 임박 재료를 억지스럽지 않게 활용합니다. "
            "모든 결과물은 한국어로 작성합니다."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )
    task = Task(
        description=(
            (f"[재시도] {feedback}\n\n" if feedback else "") +
            "context의 A와 아래 원본 재고를 바탕으로 레시피를 작성하세요. "
            "A의 추천 타입보다 사용자가 선택한 타입을 우선합니다.\n\n"
            f"[원본 재고 · D-day 계산됨]\n{load_fridge_text()}\n\n"
            f"1. {cuisine} 대표 계열({CUISINE_IDENTITY[cuisine]})을 참고해 "
            "메뉴명만 봐도 타입 정체성이 느껴지는 요리 또는 퓨전을 고릅니다. "
            "'재료명+볶음/조림'처럼 일반적인 이름만 붙이지 마세요.\n"
            "2. D-0~D-5 재료를 최소 1개, 어울리면 2개 이상 사용합니다. "
            "모든 임박 재료를 억지로 넣을 필요는 없습니다.\n"
            "3. 유통기한 지난 재료는 금지합니다. 재고 재료만 쓰되 기본 조미료는 허용합니다.\n"
            "4. 형식: '# 요리 이름' → '## 재료'(임박 재료엔 '(임박)') → "
            "'## 조리법' 5~7줄 번호 목록(재료 양·불 세기·시간 포함).\n"
            f"마땅한 {cuisine} 요리가 없으면 첫 줄에 '{NO_RECIPE}'만 쓰고 "
            "이유와 어울리는 타입을 제안하세요."
        ),
        expected_output=(
            f"{cuisine} 레시피(이름·재료·5~7줄 조리법) 또는 '{NO_RECIPE}' 사유 (한국어 마크다운)"
        ),
        agent=agent,
        context=[fridge_context],
    )
    return _run_single(agent, task)


def _verify_recipe(
    cuisine: str,
    fridge_report: str,
    recipe_md: str,
) -> tuple[bool, str]:
    imminent = imminent_ingredients()
    if imminent and not any(name in recipe_md for name in imminent):
        return False, "D-0~D-5 임박 재료를 최소 1개 사용해야 합니다."

    fridge_context = _completed_context_task(
        "앞 단계 결과 A", fridge_report, "냉장고 관리사"
    )
    recipe_context = _completed_context_task(
        "앞 단계 결과 B", recipe_md, f"{cuisine} 요리사"
    )
    agent = Agent(
        role="레시피 교차검증자",
        goal="레시피의 타입 특징, 임박 재료 활용, 조리 가능성을 검증한다.",
        backstory=(
            "당신은 실용적인 요리 연구가입니다. 선택 타입의 특징이 보이면 "
            "가정식·퓨전·재료 대체를 허용하고, 명백히 다른 타입이나 성립하지 않는 요리만 탈락시킵니다. "
            "모든 결과물은 한국어로 작성합니다."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )
    task = Task(
        description=(
            f"context의 A와 B를 바탕으로 사용자가 선택한 {cuisine} 레시피를 검증하세요.\n\n"
            f"[원본 재고 · D-day 계산됨]\n{load_fridge_text()}\n\n"
            f"[{cuisine} 대표 계열]\n{CUISINE_IDENTITY[cuisine]}\n\n"
            "판정 기준:\n"
            "- 통과: 대표 요리 계열과 조리 특징이 분명함\n"
            "- 보완허용: 퓨전이지만 메뉴명·맛/양념·조리법 중 2가지 이상에서 타입 특징이 보임\n"
            "- 탈락: 단순 '재료명+볶음/조림' 수준이거나 타입 특징이 없음, "
            "임박 재료 미사용, 폐기 재료 사용, 조리 불가능\n\n"
            "다음 두 줄만 출력하세요.\n"
            "판정: 통과/보완허용/탈락 중 하나\n"
            "사유: 한 문장"
        ),
        expected_output="'판정: X'와 '사유: 한 문장' 두 줄",
        agent=agent,
        context=[fridge_context, recipe_context],
    )
    out = _run_single(agent, task)
    decision = re.search(r"판정\s*[:：]\s*(통과|보완\s*허용|보완허용|탈락)", out)
    if decision and decision.group(1).replace(" ", "") in ("통과", "보완허용"):
        return True, ""
    return False, out.strip() or "검증 형식을 확인할 수 없습니다."


# ------------------------------------------------------------
# Agent 4: 장보기 관리사 -> 재고 보충 구매 추천 (ReAct 스타일)
#   장보기의 개념: "요리를 만들기 위한" 장보기가 아니라,
#   재료를 소진한 뒤(또는 현 재고 기준) 냉장고를 "다시 채우는" 보충 장보기
# ------------------------------------------------------------
def run_shopping(
    mode: str,
    fridge_report: str,
    recipe: str | None = None,
) -> str:
    """Agent 1의 A와 선택적으로 Agent 2의 B를 컨텍스트로 받아 Agent 4가 장보기를 생성한다.

    mode="direct": A를 참고한 현 재고 보충
    mode="recipe": A+B를 참고한 요리 후 보충
    """
    if mode not in ("direct", "recipe"):
        raise ValueError('mode는 "direct" 또는 "recipe"여야 합니다.')
    if not fridge_report or not fridge_report.strip():
        raise ValueError("Agent 1의 냉장고 브리핑(A)이 필요합니다.")
    if mode == "recipe" and not recipe:
        raise ValueError('mode="recipe"에는 recipe 텍스트(B)가 필요합니다.')

    agent = Agent(
        role="장보기 관리사",
        goal="소진·부족해진 재료를 파악해 냉장고를 다시 채울 보충 구매 목록을 추천한다.",
        backstory=(
            "당신은 가정의 장보기를 담당하는 구매 전문가입니다. "
            "새 요리를 위한 쇼핑이 아니라, 소진되었거나 곧 없어질 재료를 다시 채워 "
            "냉장고를 평소 상태로 유지하는 '보충 장보기'가 당신의 일입니다. "
            "이미 충분한 재료는 추천하지 않습니다. "
            "모든 결과물은 한국어로 작성합니다."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    context_tasks = [
        _completed_context_task(
            "앞 단계 결과 A", fridge_report, "냉장고 관리사"
        )
    ]
    context_text = (
        "CrewAI context로 제공된 앞 단계 결과 A를 장보기 판단의 출발점으로 사용하세요.\n\n"
        "정확한 전체 수량 계산을 위한 원본 재고입니다. "
        "A와 원본이 충돌하면 날짜가 계산된 원본 재고를 따르세요.\n\n"
        f"[원본 재고 · D-day 계산됨]\n{load_fridge_text()}\n\n"
    )
    if mode == "direct":
        guide = (
            "현 재고 기준의 보충 장보기입니다. 폐기 대상의 대체품, 곧 없어질 임박 재료, "
            "수량이 적은 기본 식재료를 보충 대상으로 봅니다."
        )
    else:
        context_tasks.append(
            _completed_context_task(
                "앞 단계 결과 B", recipe, "요리사"
            )
        )
        context_text += (
            "CrewAI context로 제공된 앞 단계 결과 B의 사용 재료와 사용량을 "
            "요리 후 재고 계산에 반영하세요.\n\n"
        )
        guide = (
            "이미 이 레시피대로 요리해 재료를 소진한 상태입니다. 재고에서 레시피 사용량을 뺀 "
            "'요리 후 냉장고'를 기준으로, 소진·부족해진 재료와 폐기 대상 대체품을 보충 대상으로 봅니다."
        )

    task = Task(
        description=(
            context_text +
            "냉장고를 다시 채울 보충 장보기 목록을 작성하세요. " + guide + "\n"
            "다음 ReAct 절차로 사고하되, 최종 Answer만 출력하세요.\n"
            "Thought: 무엇이 소진·부족해졌는지 판단한다.\n"
            "Action: 다시 채워야 할 재료만 고른다.\n"
            "Answer: 아래 형식으로 출력한다.\n"
            "1. '## 🧊 냉장고 상태': 2~3줄 요약.\n"
            "2. '## 🛒 보충 구매 목록': 각 항목을 '- [재료명](https://www.coupang.com/np/search?q=재료명) "
            "(현재: 남은수량+단위)' 형식으로 작성합니다.\n"
            "레시피 경로는 실제 사용량을 뺀 현재 잔량을 쓰고, 소진됐으면 '현재: 0단위', "
            "유통기한이 지났으면 '현재: 수량+단위 · 폐기 대상'으로 표시하세요.\n"
            "구매 이유나 '방금 만든 요리에 사용' 같은 반복 문장은 쓰지 마세요. "
            "URL의 재료명은 공백 없이 작성합니다."
        ),
        expected_output=(
            "냉장고 상태 요약과 현재 잔량·쿠팡 링크만 담은 보충 구매 목록"
        ),
        agent=agent,
        context=context_tasks,
    )
    return _run_single(agent, task)
