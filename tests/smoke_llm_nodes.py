"""Independent node tests with local LLM calls replaced by deterministic JSON."""
from __future__ import annotations

from nodes import chart_selector as charts
from nodes import insight_extractor as insights
from nodes import planner as planning
from nodes import query_generator as queries


def main() -> None:
    planning.invoke_llm = lambda *_: '{"steps":[{"id":1,"question":"Layoffs by industry","purpose":"rank industries"}]}'
    plan = planning.planner({"question": "Highest layoffs?"})
    assert plan["plan"][0]["id"] == 1

    queries.invoke_llm = lambda *_: '{"sql":"SELECT industry, SUM(employees_laid_off) AS layoffs FROM layoffs GROUP BY industry","explanation":"Totals by industry"}'
    generated = queries.query_generator({"question": "Highest layoffs?", "schema": "layoffs(industry TEXT, employees_laid_off INTEGER)", **plan})
    assert generated["queries"][0]["sql"].startswith("SELECT")

    result_state = {"question": "Highest layoffs?", "results": [{"step_id": 1, "columns": ["industry", "layoffs"], "rows": [{"industry": "Hardware", "layoffs": 100}], "error": None}], "errors": []}
    insights.invoke_llm = lambda *_: '{"findings":[{"finding":"Hardware leads","metric":"100 layoffs","supporting_data":[{"industry":"Hardware","layoffs":100}]}],"summary":"Hardware is highest."}'
    extracted = insights.insight_extractor(result_state)
    assert extracted["insights"][0]["findings"]

    class FakeToolLlm:
        def bind_tools(self, _tools):
            return self

    class ToolCallResponse:
        tool_calls = [{"id": "test-call", "name": "make_bar_chart", "args": {"x_column": "industry", "y_column": "layoffs", "title": "Layoffs by industry"}}]

    charts.reasoning_llm = FakeToolLlm()
    charts.invoke_message = lambda *_: ToolCallResponse()
    selected = charts.chart_selector({**result_state, **extracted})
    assert selected["chart_specs"][0]["chart_type"] == "bar"
    print("planner, query_generator, insight_extractor, and chart_selector: PASS (mocked LLM)")


if __name__ == "__main__":
    main()
