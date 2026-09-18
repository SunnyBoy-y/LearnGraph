"""Closed generation contracts. Private scoring never appears in page DTOs."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Blueprint(Closed):
    title: str = Field(min_length=1, max_length=200)
    objectives: list[str] = Field(min_length=1, max_length=6)
    sections: list[str] = Field(min_length=2, max_length=8)
    activity_brief: str = Field(max_length=4000)
    image_prompt: str = Field(max_length=2000)
    estimated_minutes: int = Field(ge=1, le=120)


class LessonSection(Closed):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=20, max_length=10000)
    takeaway: str = Field(min_length=1, max_length=1000)


class Lesson(Closed):
    sections: list[LessonSection] = Field(min_length=2, max_length=8)
    svg: str = Field(min_length=20, max_length=50000)
    caption: str = Field(min_length=1, max_length=500)
    html: str = Field(max_length=100000, description="Optional self-contained HTML demonstration; no network or assessment answers")
    @model_validator(mode="after")
    def unique_ids(self):
        if len({s.id for s in self.sections}) != len(self.sections):
            raise ValueError("duplicate section ids")
        return self


class Variable(Closed):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    label: str = Field(min_length=1, max_length=120)
    initial: int = Field(ge=-10000, le=10000)
    minimum: int = Field(ge=-10000, le=10000)
    maximum: int = Field(ge=-10000, le=10000)
    unit: str = Field(default="", max_length=30)
    states: dict[str, str] = Field(default_factory=dict, description="Optional names for integer states, e.g. 0=raw, 1=seared; never contain answers or pass conditions")


class Condition(Closed):
    variable: str
    operator: Literal["eq", "gte", "lte"]
    value: int = Field(ge=-10000, le=10000)


class Effect(Closed):
    variable: str
    operation: Literal["set", "add"]
    value: int = Field(ge=-10000, le=10000)


class Action(Closed):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    label: str = Field(min_length=1, max_length=120)
    requires: list[Condition] = Field(max_length=12)
    effects: list[Effect] = Field(min_length=1, max_length=12)
    feedback: str = Field(max_length=600)


class Activity(Closed):
    title: str = Field(max_length=200)
    instructions: str = Field(max_length=3000)
    variables: list[Variable] = Field(min_length=1, max_length=12)
    actions: list[Action] = Field(min_length=2, max_length=20)
    success: list[Condition] = Field(min_length=1, max_length=12)
    solution: list[str] = Field(min_length=2, max_length=60)
    @model_validator(mode="after")
    def references(self):
        ids = {v.id for v in self.variables}
        if len(ids) != len(self.variables) or len({a.id for a in self.actions}) != len(self.actions):
            raise ValueError("duplicate activity ids")
        if any(v.minimum > v.initial or v.initial > v.maximum for v in self.variables):
            raise ValueError("invalid variable bounds")
        for variable in self.variables:
            if len(variable.states) > 32 or any(not k.lstrip('-').isdigit() or not variable.minimum <= int(k) <= variable.maximum or len(v) > 120 for k,v in variable.states.items()):
                raise ValueError("invalid variable state labels")
        refs = [c.variable for a in self.actions for c in a.requires] + [e.variable for a in self.actions for e in a.effects] + [c.variable for c in self.success]
        if any(ref not in ids for ref in refs) or any(s not in {a.id for a in self.actions} for s in self.solution):
            raise ValueError("unknown activity reference")
        return self


class ActivityScene(Closed):
    html: str = Field(min_length=50, max_length=100000)


class Question(Closed):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    kind: Literal["single_choice", "multiple_choice", "true_false", "fill_blank", "short_answer", "essay"]
    prompt: str = Field(min_length=3, max_length=4000)
    options: list[str] = Field(max_length=8)
    points: int = Field(ge=1, le=100)
    answers: list[str] = Field(min_length=1, max_length=12, description="Option indexes as strings (0-based), acceptable fill strings, or model answer")
    rubric: str = Field(min_length=1, max_length=3000)
    explanation: str = Field(min_length=1, max_length=3000)
    critical: bool


class Exam(Closed):
    title: str = Field(max_length=200)
    pass_score: int = Field(default=80, ge=50, le=100)
    practical_points: int = Field(ge=0, le=50)
    questions: list[Question] = Field(min_length=3, max_length=16)
    @model_validator(mode="after")
    def rules(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("duplicate question ids")
        if sum(q.points for q in self.questions) + self.practical_points != 100:
            raise ValueError("total points must equal 100")
        for q in self.questions:
            if len(set(q.answers)) != len(q.answers):
                raise ValueError("duplicate answers")
            if q.kind in {"single_choice", "multiple_choice", "true_false"}:
                if len(q.options) < 2 or any(a not in {str(i) for i in range(len(q.options))} for a in q.answers):
                    raise ValueError("invalid choice answers")
                if q.kind != "multiple_choice" and len(q.answers) != 1:
                    raise ValueError("single choice must have one answer")
                if q.kind == "true_false" and len(q.options) != 2:
                    raise ValueError("true/false requires exactly two options")
            elif q.options:
                raise ValueError("written questions cannot have choice options")
        return self


class SubjectiveGrade(Closed):
    ratio: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    feedback: str = Field(min_length=1, max_length=2000)


class PolicyPatch(Closed):
    enabled: bool
    mode: Literal["path", "all"] = "path"
    image_enabled: bool = False
    expected_revision: int = Field(ge=0)


class BuildRequest(Closed):
    trigger: Literal["manual", "on_demand"] = "on_demand"


class ProgressPatch(Closed):
    expected_revision: int = Field(ge=0)
    section_id: str | None = Field(default=None, min_length=1, max_length=40)
    action_id: str | None = Field(default=None, min_length=1, max_length=40)
    reset_activity: bool = False
    @model_validator(mode="after")
    def one_operation(self):
        if sum((bool(self.section_id), bool(self.action_id), self.reset_activity)) != 1:
            raise ValueError("exactly one progress operation is required")
        return self


class AttemptStart(Closed):
    request_key: str = Field(min_length=8, max_length=80)


class AnswerPatch(Closed):
    expected_revision: int = Field(ge=0)
    answers: dict[str, str | list[str]]
    @model_validator(mode="after")
    def bounded(self):
        import json
        if len(self.answers) > 16 or len(json.dumps(self.answers)) > 100000:
            raise ValueError("answer draft is too large")
        return self
