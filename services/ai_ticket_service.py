import importlib
import os
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field


EXTRACTION_FIELDS = [
    "subject",
    "description",
    "resolution_goal",
    "department",
    "submitted_by",
    "assigned_owner",
    "start_date",
    "status",
    "impact_score",
    "impact_rationale",
    "reach_score",
    "reach_rationale",
    "feasibility_score",
    "feasibility_rationale",
    "reuse_score",
    "reuse_rationale",
    "risk_score",
    "risk_rationale",
]

SCORE_FIELDS = {
    "impact_score",
    "reach_score",
    "feasibility_score",
    "reuse_score",
    "risk_score",
}


class TicketExtractionSchema(BaseModel):
    subject: str = Field(default="")
    description: str = Field(default="")
    resolution_goal: str = Field(default="")
    department: str = Field(default="")
    submitted_by: str = Field(default="")
    assigned_owner: str = Field(default="")
    start_date: str = Field(default="")
    status: str = Field(default="")
    impact_score: str = Field(default="")
    impact_rationale: str = Field(default="")
    reach_score: str = Field(default="")
    reach_rationale: str = Field(default="")
    feasibility_score: str = Field(default="")
    feasibility_rationale: str = Field(default="")
    reuse_score: str = Field(default="")
    reuse_rationale: str = Field(default="")
    risk_score: str = Field(default="")
    risk_rationale: str = Field(default="")


class AITicketExtractionService:
    def __init__(self):
        self.provider = (os.getenv("AI_TICKET_PROVIDER", "openai") or "openai").strip().lower()

    def is_configured(self) -> bool:
        return self._build_chat_model() is not None

    def extract(self, source_text: str) -> Dict[str, Any]:
        cleaned_text = (source_text or "").strip()
        if not cleaned_text:
            return {
                "data": self._blank_payload(),
                "extracted_fields": [],
                "missing_fields": EXTRACTION_FIELDS[:],
                "message": "Some fields could not be determined. Please complete manually.",
            }

        chat_model = self._build_chat_model()
        if chat_model is None:
            raise RuntimeError(
                "Work in Progress (WIP): AI extraction has not yet been configured. Configure AI_TICKET_PROVIDER and the required provider credentials to enable this feature."
            )

        from langchain_core.messages import HumanMessage, SystemMessage

        structured_model = chat_model.with_structured_output(TicketExtractionSchema)
        result = structured_model.invoke(
            [
                SystemMessage(
                    content=(
                        "Extract all available secure ticket information. "
                        "Infer reasonable scoring suggestions when sufficient context is available. "
                        "Generate supporting rationale for scoring recommendations. "
                        "Return valid JSON only. "
                        "Do not fabricate information when insufficient context exists. "
                        "Leave unknown fields blank. "
                        "For score fields, return only 0,1,2,3,4,5 as strings when confidence is sufficient. "
                        "For start_date use YYYY-MM-DD when present."
                    )
                ),
                HumanMessage(content=cleaned_text),
            ]
        )

        if hasattr(result, "model_dump"):
            raw_payload = result.model_dump()
        elif isinstance(result, dict):
            raw_payload = result
        else:
            raw_payload = {}

        payload = self._normalize_payload(raw_payload)
        extracted_fields, missing_fields = self._classify_fields(payload)
        message = ""
        if missing_fields:
            message = "Some fields could not be determined. Please complete manually."

        return {
            "data": payload,
            "extracted_fields": extracted_fields,
            "missing_fields": missing_fields,
            "message": message,
        }

    def _blank_payload(self) -> Dict[str, str]:
        return {field_name: "" for field_name in EXTRACTION_FIELDS}

    def _normalize_payload(self, raw_payload: Dict[str, Any]) -> Dict[str, str]:
        normalized = self._blank_payload()
        for field_name in EXTRACTION_FIELDS:
            value = raw_payload.get(field_name, "")
            value_as_text = str(value).strip() if value is not None else ""
            if field_name in SCORE_FIELDS:
                if value_as_text in {"0", "1", "2", "3", "4", "5"}:
                    normalized[field_name] = value_as_text
                else:
                    normalized[field_name] = ""
            else:
                normalized[field_name] = value_as_text
        return normalized

    def _classify_fields(self, payload: Dict[str, str]) -> Tuple[List[str], List[str]]:
        extracted_fields = [field for field in EXTRACTION_FIELDS if payload.get(field, "").strip()]
        missing_fields = [field for field in EXTRACTION_FIELDS if not payload.get(field, "").strip()]
        return extracted_fields, missing_fields

    def _build_chat_model(self):
        llm_factory_path = (os.getenv("AI_TICKET_LLM_FACTORY", "") or "").strip()
        if llm_factory_path:
            return self._build_model_from_factory(llm_factory_path)

        if self.provider == "openai":
            return self._build_openai_model()
        if self.provider == "azure_openai":
            return self._build_azure_openai_model()
        return None

    def _build_model_from_factory(self, import_path: str):
        try:
            module_path, factory_name = import_path.split(":", 1)
            module = importlib.import_module(module_path)
            factory = getattr(module, factory_name)
            return factory()
        except Exception as exc:  # pragma: no cover - defensive fallback for pluggable environments
            raise RuntimeError(f"Failed to load custom LLM factory '{import_path}': {exc}") from exc

    def _build_openai_model(self):
        api_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
        if not api_key:
            return None

        try:
            from langchain_openai import ChatOpenAI
        except Exception:
            return None

        return ChatOpenAI(
            api_key=api_key,
            model=os.getenv("AI_TICKET_OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0,
        )

    def _build_azure_openai_model(self):
        api_key = (os.getenv("AZURE_OPENAI_API_KEY", "") or "").strip()
        endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT", "") or "").strip()
        deployment = (os.getenv("AZURE_OPENAI_DEPLOYMENT", "") or "").strip()
        api_version = (os.getenv("AZURE_OPENAI_API_VERSION", "") or "").strip()
        if not api_key or not endpoint or not deployment or not api_version:
            return None

        try:
            from langchain_openai import AzureChatOpenAI
        except Exception:
            return None

        return AzureChatOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            api_version=api_version,
            temperature=0,
        )
