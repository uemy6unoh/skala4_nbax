"""Agent 2: 선택한 요리 타입의 레시피 B를 생성한다.

공통 LLM, 재고 로더, CrewAI context 복원 함수는 nbax_agents에서 가져오며
기존 프롬프트와 실행 방식은 그대로 유지한다.
"""

from crewai import Agent, Task

import nbax_agents as shared


def create_recipe(
    cuisine: str,
    fridge_report: str,
    feedback: str | None = None,
) -> str:
    """Agent 1의 A와 선택적 재시도 피드백으로 레시피 B를 생성한다."""
    fridge_context = shared._completed_context_task(
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
        llm=shared.llm,
        allow_delegation=False,
        verbose=True,
    )
    task = Task(
        description=(
            (f"[재시도] {feedback}\n\n" if feedback else "") +
            "context의 A와 아래 원본 재고를 바탕으로 레시피를 작성하세요. "
            "A의 추천 타입보다 사용자가 선택한 타입을 우선합니다.\n\n"
            f"[원본 재고 · D-day 계산됨]\n{shared.load_fridge_text()}\n\n"
            f"1. {cuisine} 대표 계열({shared.CUISINE_IDENTITY[cuisine]})을 참고해 "
            "메뉴명만 봐도 타입 정체성이 느껴지는 요리 또는 퓨전을 고릅니다. "
            "'재료명+볶음/조림'처럼 일반적인 이름만 붙이지 마세요.\n"
            "2. D-0~D-5 재료를 최소 1개, 어울리면 2개 이상 사용합니다. "
            "모든 임박 재료를 억지로 넣을 필요는 없습니다.\n"
            "3. 유통기한 지난 재료는 금지합니다. 재고 재료만 쓰되 기본 조미료는 허용합니다.\n"
            "4. 형식: '# 요리 이름' → '## 재료'(임박 재료엔 '(임박)') → "
            "'## 조리법' 5~7줄 번호 목록(재료 양·불 세기·시간 포함).\n"
            f"마땅한 {cuisine} 요리가 없으면 첫 줄에 '{shared.NO_RECIPE}'만 쓰고 "
            "이유와 어울리는 타입을 제안하세요."
        ),
        expected_output=(
            f"{cuisine} 레시피(이름·재료·5~7줄 조리법) 또는 "
            f"'{shared.NO_RECIPE}' 사유 (한국어 마크다운)"
        ),
        agent=agent,
        context=[fridge_context],
    )
    return shared._run_single(agent, task)
