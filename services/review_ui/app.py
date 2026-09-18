import json
import os
from typing import Any

import streamlit as st

from services.review_ui.api_client import ReviewApiClient


def _client() -> ReviewApiClient:
    base_url = os.environ.get("REVIEW_API_BASE_URL", "http://localhost:8000")
    existing = st.session_state.get("review_api_client")
    if isinstance(existing, ReviewApiClient):
        return existing
    client = ReviewApiClient(base_url=base_url)
    st.session_state.review_api_client = client
    return client


def _parse_edited_invoice(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("edited invoice must be a JSON object")
    return payload


def main() -> None:
    st.set_page_config(page_title="DocFlow AI Review", layout="wide")
    st.title("DocFlow AI Human Review")
    st.caption(
        "Local demo review console. Reviewer identity is explicit demo input, "
        "not production authentication.",
    )
    client = _client()

    try:
        listing = client.list_reviews()
    except Exception as exc:
        st.error(f"Unable to load review queue: {exc}")
        return

    items = listing.get("items", [])
    st.subheader(f"Needs review ({len(items)})")
    if not items:
        st.info("No documents currently require review.")
        return

    labels = {
        str(item["document_id"]): (
            f"{item['document_id']} | {item.get('decision_reason') or 'needs_review'}"
        )
        for item in items
    }
    selected = st.selectbox(
        "Select document",
        options=list(labels.keys()),
        format_func=lambda value: labels[value],
    )
    if selected is None:
        return

    try:
        detail = client.get_review(selected)
    except Exception as exc:
        st.error(f"Unable to load review detail: {exc}")
        return

    left, right = st.columns(2)
    with left:
        st.markdown("### Document")
        st.write(
            {
                "document_id": detail["document_id"],
                "correlation_id": detail["correlation_id"],
                "status": detail["status"],
                "source_message_id": detail["source_message_id"],
                "content_hash": detail["content_hash"],
                "created_at": detail["created_at"],
                "updated_at": detail["updated_at"],
            },
        )
        st.markdown("### Validation / review reasons")
        st.write(detail.get("validation_issues") or [])
        st.markdown("### Decision")
        st.write(detail.get("decision"))
        if detail.get("source_available"):
            st.link_button(
                "Open source PDF",
                client.source_url(selected),
            )
    with right:
        st.markdown("### Extraction")
        extraction = detail.get("extraction")
        if extraction is None:
            st.warning("No extraction payload available.")
            default_edit = "{}"
        else:
            st.write(
                {
                    "provider": extraction.get("provider"),
                    "model_name": extraction.get("model_name"),
                    "overall_confidence": extraction.get("overall_confidence"),
                    "fallback_reason": extraction.get("fallback_reason"),
                },
            )
            st.json(extraction.get("payload"))
            default_edit = json.dumps(extraction.get("payload"), indent=2)

    st.markdown("### Reviewer action")
    actor = st.text_input("Reviewer identity", value="")
    reason = st.text_area("Reason", value="")
    edited_raw = st.text_area(
        "Edited invoice JSON (optional for approve-with-edit)",
        value=default_edit,
        height=280,
    )

    approve_col, edit_col, reject_col = st.columns(3)
    with approve_col:
        if st.button("Approve without edits", type="primary"):
            if not actor.strip() or not reason.strip():
                st.error("Actor and reason are required.")
            else:
                code, body = client.approve(
                    selected,
                    actor=actor.strip(),
                    reason=reason.strip(),
                )
                if code >= 400:
                    st.error(body)
                else:
                    st.success(body)
                    status = client.get_document_status(selected)
                    st.write(status)
    with edit_col:
        if st.button("Edit and approve"):
            if not actor.strip() or not reason.strip():
                st.error("Actor and reason are required.")
            else:
                try:
                    edited = _parse_edited_invoice(edited_raw)
                except Exception as exc:
                    st.error(f"Invalid edited invoice JSON: {exc}")
                else:
                    if edited is None:
                        st.error("Provide edited invoice JSON.")
                    else:
                        code, body = client.approve(
                            selected,
                            actor=actor.strip(),
                            reason=reason.strip(),
                            edited_invoice=edited,
                        )
                        if code >= 400:
                            st.error(body)
                        else:
                            st.success(body)
                            status = client.get_document_status(selected)
                            st.write(status)
    with reject_col:
        if st.button("Reject"):
            if not actor.strip() or not reason.strip():
                st.error("Actor and reason are required.")
            else:
                code, body = client.reject(
                    selected,
                    actor=actor.strip(),
                    reason=reason.strip(),
                )
                if code >= 400:
                    st.error(body)
                else:
                    st.success(body)
                    status = client.get_document_status(selected)
                    st.write(status)

    st.markdown("### Audit summary")
    st.write(detail.get("audit_summary") or [])


if __name__ == "__main__":
    main()
